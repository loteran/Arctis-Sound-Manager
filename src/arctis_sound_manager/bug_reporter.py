# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Bug reporting utilities — system info, crash file I/O, GitHub URL.
No Qt imports: safe to use in daemon (headless).
"""
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

CRASH_REPORT_FILE = Path.home() / '.config' / 'arctis_manager' / 'crash_report.json'
GITHUB_ISSUES_URL = 'https://github.com/loteran/Arctis-Sound-Manager/issues/new'


def _python_lib_versions() -> dict[str, str]:
    """Versions of the Python libs that most often cause runtime weirdness.
    Backend mismatches (e.g. system pulsectl vs pipx pulsectl) usually show
    up here before they show up anywhere else."""
    from importlib.metadata import PackageNotFoundError, version
    # Mapping: import-friendly label → distribution name on PyPI.
    libs = {
        'pulsectl':    'pulsectl',
        'pyudev':      'pyudev',
        'pyusb':       'pyusb',
        'dbus-next':   'dbus-next',
        'ruamel-yaml': 'ruamel.yaml',
        'pyside6':     'PySide6',
        'pillow':      'pillow',
    }
    out: dict[str, str] = {}
    for label, dist in libs.items():
        try:
            out[label] = version(dist)
        except PackageNotFoundError:
            out[label] = '(not installed)'
        except Exception as e:
            out[label] = f'(error: {e!r})'
    return out


def _owning_package() -> str | None:
    """Name, version and vendor of the distro package owning this module.

    Answers "is this our build?" in one line of a bug report. ASM is
    repackaged elsewhere under other names, and those builds don't
    necessarily declare the same dependencies — a crash on a missing pactl
    or a silent HeSuVi can come from the packaging rather than the code.
    """
    module_path = str(Path(__file__).resolve())
    queries = (
        ('rpm', ['rpm', '-qf', '--qf', '%{NAME} %{VERSION}-%{RELEASE} vendor=%{VENDOR}',
                 module_path]),
        ('pacman', ['pacman', '-Qoq', module_path]),
        ('dpkg', ['dpkg', '-S', module_path]),
    )
    for manager, cmd in queries:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        except Exception:
            continue
        if r.returncode != 0 or not r.stdout.strip():
            continue
        out = r.stdout.strip().splitlines()[0]
        if manager == 'dpkg':
            out = out.split(':', 1)[0]
        return f'owned-by[{manager}]={out}'
    return None


def _detect_install_methods() -> list[str]:
    """Surface every install method present on this system at once.

    Single most common source of "I just upgraded but nothing changed":
    the user has rpm + pipx (or apt + pipx) in parallel, /usr/bin/asm-daemon
    masks the pipx one or vice-versa, and the version they SEE in journalctl
    is not the version they THINK they upgraded.
    """
    methods: list[str] = []
    cmds = (
        ('rpm',    ['rpm', '-q', '--qf', '%{VERSION}', 'arctis-sound-manager']),
        ('pacman', ['pacman', '-Q', 'arctis-sound-manager']),
        ('apt',    ['dpkg-query', '-W', '-f=${Version}', 'arctis-sound-manager']),
        ('pipx',   ['pipx', 'list', '--short']),
    )
    for name, cmd in cmds:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if r.returncode != 0:
                continue
            out = r.stdout.strip()
            if name == 'pipx':
                if 'arctis-sound-manager' not in out:
                    continue
                ver = next(
                    (line.split()[1] for line in out.splitlines()
                     if line.startswith('arctis-sound-manager')),
                    '?',
                )
            elif name == 'pacman':
                ver = out.split()[1] if out else '?'
            else:
                ver = out or '?'
            methods.append(f'{name}={ver}')
        except Exception:
            pass

    # Which package actually owns the running code, and who built it. ASM is
    # redistributed under other names — Fedora's Terra ships it as
    # python3-arctis-sound-manager — and those builds can carry different
    # dependencies from ours, so a report describing behaviour we can't
    # reproduce may simply be describing a different package (#146, #140).
    owner = _owning_package()
    if owner:
        methods.append(owner)

    # Every asm-daemon binary in PATH (catches pip --user installs that
    # don't show up in any package manager). Canonicalise each hit before
    # counting: on usr-merged distros /bin is a symlink to /usr/bin and both
    # are on PATH, so `command -v -a` lists the same physical binary twice.
    # Reporting that as a duplicate would send users chasing a nonexistent
    # second install (issue #114) — dedupe by resolved path first.
    try:
        r = subprocess.run(
            ['bash', '-c', 'command -v -a asm-daemon'],
            capture_output=True, text=True, timeout=2,
        )
        bins = [b for b in r.stdout.strip().splitlines() if b]
        distinct = sorted({os.path.realpath(b) for b in bins})
        if len(distinct) > 1:
            methods.append(f'asm-daemon binaries in PATH: {distinct}')
    except Exception:
        pass
    return methods


def _run_out(cmd: list[str], timeout: float = 5.0) -> str:
    """Run *cmd* and return its stdout, stripped.

    Returns '' when the binary is missing, the command times out, or it
    raises. stdout is returned even on a non-zero exit code because tools
    like `systemctl is-active` exit non-zero while still printing the state
    ('inactive', 'failed') we want to report.
    """
    if not cmd or not shutil.which(cmd[0]):
        return ''
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ''


_ARCTIS_PATTERNS = ('arctis', '1038', 'steelseries')


def _detect_container_env() -> str:
    """Distrobox / Flatpak / Snap / docker / native detection.

    Inside a Distrobox the daemon only reaches PipeWire through forwarded
    sockets — knowing we are in a container is the first question a
    maintainer asks when virtual outputs are missing (issue #74).
    """
    if os.environ.get('FLATPAK_ID'):
        return f"flatpak (FLATPAK_ID={os.environ['FLATPAK_ID']})"
    if os.environ.get('SNAP'):
        return f"snap (SNAP={os.environ['SNAP']})"
    container = os.environ.get('container', '')
    if (container == 'distrobox'
            or os.environ.get('DISTROBOX_ENTER_PATH')
            or os.environ.get('CONTAINER_ID')):
        name = os.environ.get('CONTAINER_ID', '?')
        return f'distrobox (container={container or "?"}, CONTAINER_ID={name})'
    if container:
        return f'container ({container})'
    if Path('/.dockerenv').exists():
        return 'docker'
    return 'native'


# Node names ASM creates or drives. Used to flag our own nodes in the audio
# graph, and to decide which routing props are worth printing.
_ASM_NODE_FRAGMENTS = ('Arctis_', 'sonar-', 'virtual-surround',
                       'effect_input', 'effect_output')
# A real device is never one of ours, however it is named: the headset's own
# sink is "alsa_output.usb-SteelSeries_Arctis_7_-00" and would otherwise match
# on "Arctis_". Telling the two apart is the whole point of the graph section.
_DEVICE_PREFIXES = ('alsa_output.', 'alsa_input.', 'bluez_output.', 'bluez_input.')
# Routing properties that decide where audio goes and whether a node keeps a
# device awake. Printed for ASM's own nodes only, to keep the section short.
_ROUTING_PROPS = ('target.object', 'node.target', 'node.passive',
                  'node.pause-on-idle', 'node.linger', 'node.autoconnect')


def _is_asm_node(name: str) -> bool:
    if name.startswith(_DEVICE_PREFIXES):
        return False
    return any(f in name for f in _ASM_NODE_FRAGMENTS)


def _pw_objects() -> list | None:
    """Parsed `pw-dump`, or None when unavailable/unparseable.

    Fetched once and shared by the graph sections below, so a report does not
    pay for three separate dumps of a large graph.
    """
    if not shutil.which('pw-dump'):
        return None
    raw = _run_out(['pw-dump'], timeout=8.0)
    try:
        objects = json.loads(raw)
    except Exception:
        return None
    return objects if isinstance(objects, list) else None


def _audio_graph(objects: list | None) -> str:
    """Every audio node with its state, then every link between them.

    This is the section that answers "why is my headset behaving like this",
    and it exists because its absence cost two wrong diagnoses on #180:

    - **State** (running / idle / suspended) is what distinguishes a device
      being actively driven from one merely connected. A headset that never
      reaches its inactivity timeout looks identical to a healthy one in a
      report that omits it.
    - **Links** say *which* node holds a device. Knowing something keeps the
      headset awake is not actionable; knowing it is the HeSuVi output is.
    - **Routing props** on our own nodes (target.object, node.passive,
      node.pause-on-idle…) say whether the config on disk actually reached
      the running graph, which is exactly the gap in #100 and #102.

    Unlike the Arctis-only filter used elsewhere here, this lists the whole
    audio graph: a second, unrelated output sitting in the same state as the
    headset is the difference between "ASM holds this device" and "this
    machine never suspends anything", and that comparison is impossible if
    non-Arctis nodes are filtered out.
    """
    if objects is None:
        return '(pw-dump unavailable or unparseable — install pipewire-utils?)'

    names: dict[int, str] = {}
    nodes: list[tuple[int, str, str, str, str]] = []
    for obj in objects:
        if obj.get('type') != 'PipeWire:Interface:Node':
            continue
        info = obj.get('info') or {}
        props = info.get('props') or {}
        name = props.get('node.name') or props.get('device.name', '?')
        names[obj.get('id', -1)] = name
        mclass = props.get('media.class', '')
        # Substring, not startswith: the nodes that matter most are
        # "Stream/Output/Audio" (effect_output.*, Arctis_*_sink_out).
        if 'Audio' not in mclass:
            continue
        # Routing props for our own nodes, and for any application stream that
        # pins itself somewhere. An app pin is what #185 was about: a soundboard
        # feeding a virtual microphone gets dragged onto an Arctis channel,
        # breaking the other app's feature rather than merely relocating sound.
        # Without the pin in the report there is no way to see that from here.
        extra = ''
        wanted = _ROUTING_PROPS if _is_asm_node(name) else ('target.object', 'node.target')
        kept = [f'{k}={props[k]}' for k in wanted if k in props]
        # Which client owns the node decides whether a link is even allowed.
        # PipeWire refuses one when the client owning either end cannot see the
        # other node, which is a check between the two owning clients and not
        # between the user and the nodes: ports list fine while linking fails
        # with EPERM (#181). A node with no owner is exempt from that check
        # entirely, so the presence or absence of this field is itself the
        # answer, and it is cross-referenced with the client table below.
        owner = props.get('client.id')
        if owner is not None:
            kept.append(f'client.id={owner}')
        if kept:
            app = props.get('application.name')
            label = f'app={app} ' if app and not _is_asm_node(name) else ''
            extra = '  [' + label + ' '.join(kept) + ']'
        nodes.append((obj.get('id', -1), info.get('state', '?'), mclass, name, extra))

    links: list[tuple[int, str, str, str]] = []
    for obj in objects:
        if obj.get('type') != 'PipeWire:Interface:Link':
            continue
        info = obj.get('info') or {}
        links.append((
            obj.get('id', -1),
            info.get('state', '?'),
            names.get(info.get('output-node-id'), '?'),
            names.get(info.get('input-node-id'), '?'),
        ))

    # Two nodes answering to one name is not a cosmetic duplicate: every route
    # ASM sets is a name (target.object=effect_input.sonar-media-eq), so a name
    # carried by two nodes makes every one of those routes ambiguous, and the
    # loopback that cannot resolve it silently links to nothing. What the user
    # sees is a channel whose meters move while the headset stays silent — the
    # audio reaches the virtual sink and stops there (#205).
    #
    # Only routing targets are counted. Application streams legitimately share
    # a node.name (two browser windows are both "librewolf"), and flagging
    # those would bury the one duplicate that matters under noise.
    dupes: dict[str, list[int]] = {}
    for nid, _state, mclass, name, _extra in nodes:
        if _is_asm_node(name) or 'Sink' in mclass or 'Source' in mclass:
            dupes.setdefault(name, []).append(nid)
    dupes = {n: ids for n, ids in dupes.items() if len(ids) > 1}

    out: list[str] = []
    if dupes:
        out.append('-- ⚠️ DUPLICATE NODE NAMES (every route through these is ambiguous) --')
        for name, ids in sorted(dupes.items()):
            out.append(f'       {name}  ->  ids {", ".join(str(i) for i in sorted(ids))}')
        out.append('       Cross-reference client.id below: one owning process per')
        out.append('       copy means the same config is loaded twice (a second')
        out.append('       filter-chain instance, or filters loaded by the pipewire')
        out.append('       daemon itself on top of filter-chain.service).')
        out.append('')

    out.append('-- audio nodes (id, state, class, name) --')
    if nodes:
        for nid, state, mclass, name, extra in sorted(nodes, key=lambda n: n[3]):
            mark = ' <-- ASM' if _is_asm_node(name) else ''
            out.append(f'{nid:>6}  {state:<10} {mclass:<22} {name}{mark}{extra}')
    else:
        out.append('(no audio nodes — PipeWire sees no audio devices at all)')

    out.append('')
    out.append('-- links (id, state, source -> destination) --')
    if links:
        for lid, state, src, dst in sorted(links, key=lambda l: (l[3], l[2])):
            out.append(f'{lid:>6}  {state:<10} {src}  ->  {dst}')
    else:
        out.append('(no links — nothing is connected to anything)')
    return '\n'.join(out)


def _pw_clients(objects: list | None) -> str:
    """PipeWire clients, with the access level each was granted.

    The other half of "why was this link refused". PipeWire decides that from
    the permissions of the clients owning the two nodes, so the node table
    alone cannot answer it: this maps the ``client.id`` shown there to what
    that client was actually allowed.

    ``pipewire.access`` is what module-access assigned (typically "default" on
    the normal socket, "unrestricted" on the manager one). Any ``pipewire.sec.*``
    field means the client came in through a security context and is
    restricted, which on its own explains a refused link (#181).
    """
    if objects is None:
        return '(pw-dump unavailable)'
    rows = []
    for obj in objects:
        if obj.get('type') != 'PipeWire:Interface:Client':
            continue
        props = (obj.get('info') or {}).get('props') or {}
        sec = ' '.join(f'{k}={v}' for k, v in props.items()
                       if k.startswith('pipewire.sec.'))
        rows.append(
            f"{obj.get('id', -1):>6}  "
            f"access={props.get('pipewire.access', '?'):<14} "
            f"{props.get('application.process.binary') or props.get('application.name', '?')}"
            + (f'  [{sec}]' if sec else '')
        )
    if not rows:
        return '(no clients — pw-dump returned none)'
    return '\n'.join(sorted(rows))


def _alsa_pcm_state() -> str:
    """What the kernel thinks each PCM is doing.

    The layer below PipeWire: a PCM still open here while the graph looks
    idle means something holds the device at the ALSA level, which no
    PipeWire-side view can show.
    """
    lines = []
    try:
        for status in sorted(Path('/proc/asound').glob('card*/pcm*/sub*/status')):
            try:
                first = status.read_text(errors='replace').strip().splitlines()
                lines.append(f'{status}: {first[0] if first else "(empty)"}')
            except OSError as exc:
                lines.append(f'{status}: (could not read: {exc!r})')
    except Exception as exc:
        return f'(could not enumerate /proc/asound: {exc!r})'
    return '\n'.join(lines) if lines else '(no PCM status files — no ALSA cards?)'


def _arctis_pw_nodes(objects: list | None = None) -> str:
    """PipeWire objects matching the Arctis (node name, 'steelseries', or
    vendor id 1038). Empty result while USB sees the device means PipeWire
    never created the ALSA nodes — the issue #74 Distrobox failure mode.

    Prefers `pw-dump`; falls back to `pactl list sinks` when pipewire-utils
    is not installed.
    """
    if objects is None:
        objects = _pw_objects()
    if objects is not None:
        lines = []
        for obj in objects:
            blob = json.dumps(obj).lower()
            if not any(p in blob for p in _ARCTIS_PATTERNS):
                continue
            info = obj.get('info') or {}
            props = info.get('props') or {}
            lines.append(
                f"id={obj.get('id')} "
                f"state={info.get('state', '?')} "
                f"name={props.get('node.name') or props.get('device.name', '?')} "
                f"class={props.get('media.class', '?')} "
                f"desc={props.get('node.description') or props.get('device.description', '')}"
            )
        return '\n'.join(lines)
    raw = _run_out(['pactl', 'list', 'sinks'], timeout=5.0)
    blocks = re.split(r'\n(?=Sink #)', raw)
    kept = [b for b in blocks if any(p in b.lower() for p in _ARCTIS_PATTERNS)]
    return '\n'.join(kept).strip()


#: SteelSeries. Matched as a string because that is how sysfs stores it.
_STEELSERIES_VID = '1038'

# A HID-bus device id, e.g. "0003:1038:12E0.0005" (bus type : vendor : product
# . instance). usbhid creates one of these *inside* a USB interface's own
# sysfs directory once it binds — see kernel_driver_for_interface below.
_HID_CHILD_RE = re.compile(
    r'^[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}$'
)


def kernel_driver_for_interface(interface_dir: Path) -> str | None:
    """Name of the driver actually bound to a USB interface, or None.

    Background (INT-1 in docs/HARDWARE-QUESTIONS.md): a USB interface that
    exposes a HID collection is always transported by usbhid.ko — that is
    the generic USB-class driver for interface class 3, and it is what
    ``<interface>/driver`` points to whether the interface ends up driven by
    hid-generic, a vendor driver (hid-playstation, hid-logitech-hidpp, and
    from Linux 7.3 hid-steelseries), or nothing at all. Reading that symlink
    therefore never distinguishes anything ASM would want to tell a user
    apart — it always says "usbhid".

    The decision that matters happens one layer up, on the *hid* bus: usbhid
    creates a child device inside the interface's own sysfs directory (id
    format "<bus>:<vid>:<pid>.<instance>", e.g. "0003:1038:12E0.0005"), and
    *that* device's ``driver`` symlink names hid-generic / hid-steelseries /
    whichever vendor driver actually claimed it. This walks down to that
    child and reads its driver, falling back to the interface's own driver
    (or None) when no such child exists yet — e.g. mid-boot-race, or a non-
    HID interface such as the Nova-family audio-class interfaces.

    Pure filesystem read: needs no USB permissions ASM might not have, which
    is what makes it useful from exactly the failure paths (EACCES on
    detach, an EIO storm) where pyusb itself cannot be trusted to answer.
    """
    if not interface_dir.is_dir():
        return None
    hid_child: Path | None = None
    try:
        children = sorted(interface_dir.iterdir())
    except OSError:
        return None
    for child in children:
        if _HID_CHILD_RE.match(child.name):
            hid_child = child
            break
    target = hid_child or interface_dir
    try:
        return Path(os.readlink(target / 'driver')).name
    except OSError:
        return None


def find_interface_sysfs_dir(
    sys_root: Path, device_dir_name: str, interface_number: int,
) -> Path | None:
    """Locate a USB interface's sysfs directory among *sys_root*'s entries.

    Interface directories are named "<device_dir_name>:<config>.<interface>"
    (e.g. "1-6:1.3" for interface 3 of device "1-6") — the config number is
    not something callers of this function are expected to know or care
    about, so it is wildcarded rather than assumed to be 1.
    """
    try:
        matches = sorted(sys_root.glob(f'{device_dir_name}:*.{interface_number}'))
    except OSError:
        return None
    return matches[0] if matches else None


def usage_page_of_descriptor(raw: bytes) -> int | None:
    """The usage page a HID report descriptor opens with, if it does.

    `05 xx` is the short form, `06 xx xx` the long one, little-endian.
    Vendor-defined pages are 0xff00 and up, and that is the whole point - it
    is what separates a device's control channel from the consumer-control
    interface carrying its media keys.
    """
    if len(raw) >= 3 and raw[0] == 0x06:
        return raw[1] | (raw[2] << 8)
    if len(raw) >= 2 and raw[0] == 0x05:
        return raw[1]
    return None


def usage_page_for_interface(iface_dir: Path) -> int | None:
    """The usage page declared by the HID device under *iface_dir*, if any.

    Read from the descriptor the kernel already parsed and published, so this
    needs no access to the device and disturbs no driver.
    """
    try:
        for descriptor in sorted(iface_dir.glob('*/report_descriptor')):
            page = usage_page_of_descriptor(descriptor.read_bytes())
            if page is not None:
                return page
    except OSError:
        return None
    return None


def interface_driver_name(
    sys_root: Path, device_dir_name: str, interface_number: int,
) -> str | None:
    """Convenience: locate *interface_number* under *device_dir_name* and
    report whatever driver currently holds it. See kernel_driver_for_interface."""
    iface_dir = find_interface_sysfs_dir(sys_root, device_dir_name, interface_number)
    if iface_dir is None:
        return None
    return kernel_driver_for_interface(iface_dir)


def _usb_access(
    rules_paths: list[str] | None = None,
    sys_root: Path = Path('/sys/bus/usb/devices'),
    dev_root: Path = Path('/dev/bus/usb'),
) -> str:
    """Why opening the headset succeeds or fails, from the kernel's side.

    Every other USB section in this report goes through pyusb, which is
    exactly what fails when permissions are wrong, so a permission bug made
    the report blank in the one place that mattered (#190: "asked for the
    permission every time" and "headset reads as not connected", with nothing
    in the report to say why).

    Nothing here needs access to the device:

      * the rules file's own mode: it was once installed 0600 by a `pkexec cp`
        and udev silently ignored it, so the dialog came back at every login;
      * every SteelSeries device the kernel enumerated, straight from sysfs,
        so a headset that pyusb cannot open is still listed with its PID;
      * the /dev/bus/usb node's owner, mode and ACL. `TAG+="uaccess"` grants
        access by adding an ACL entry for the logged-in user, so `user:1000:rw-`
        present or absent is the difference between "the rules are fine but ran
        too late for this device" and "the rules never matched it";
      * whether this process can in fact write to the node, which is the
        question the popup is really asking.

    *sys_root* and *dev_root* exist so the tests can point this at a fixture
    tree; nothing else should pass them.
    """
    out: list[str] = []

    for p in (rules_paths or []):
        try:
            st = os.stat(p)
            mode = oct(st.st_mode & 0o777)
            flag = '' if (st.st_mode & 0o044) else '   <-- not world-readable: udev ignores it'
            out.append(f'{p}: mode {mode} uid {st.st_uid} gid {st.st_gid}{flag}')
        except OSError as e:
            out.append(f'{p}: cannot stat ({e})')

    found = 0
    try:
        entries = sorted(sys_root.iterdir()) if sys_root.is_dir() else []
    except OSError as e:
        entries = []
        out.append(f'(cannot list {sys_root}: {e})')

    for dev in entries:
        try:
            vid = (dev / 'idVendor').read_text().strip()
        except OSError:
            continue
        if vid.lower() != _STEELSERIES_VID:
            continue
        found += 1

        def _read(name: str) -> str:
            try:
                return (dev / name).read_text().strip()
            except OSError:
                return '?'

        pid = _read('idProduct')
        product = _read('product')
        busnum, devnum = _read('busnum'), _read('devnum')
        out.append('')
        out.append(f'{dev.name}: {vid}:{pid} {product}')

        # Which driver holds each of this device's USB interfaces. Not just
        # "is a kernel driver active" (pyusb's is_kernel_driver_active() can
        # only answer that as a bool) but *which one* — the difference
        # between the usbhid/hid-generic pairing ASM has always raced against
        # and, from Linux 7.3, hid-steelseries actively polling the same
        # interface (INT-1 in docs/HARDWARE-QUESTIONS.md). Pure sysfs reads,
        # so this is reported even when the /dev node below is gone or
        # unreadable — exactly the case a permission bug needs it most.
        interfaces = sorted(sys_root.glob(f'{dev.name}:*'))
        if interfaces:
            out.append('  interfaces:')
            for iface_dir in interfaces:
                try:
                    num = (iface_dir / 'bInterfaceNumber').read_text().strip()
                except OSError:
                    num = '?'
                drv = kernel_driver_for_interface(iface_dir)
                # Class and usage page: which interface is the vendor control
                # channel, straight from the hardware. Their absence is what
                # made #216 and #217 undiagnosable from a report - the profiles
                # name an interface by number, and nothing here said what that
                # interface actually was.
                try:
                    cls = (iface_dir / 'bInterfaceClass').read_text().strip()
                except OSError:
                    cls = '??'
                page = usage_page_for_interface(iface_dir)
                page_txt = 'not readable' if page is None else f'0x{page:04x}'
                if page is not None and page >= 0xFF00:
                    page_txt += ' (vendor)'
                out.append(f'    {iface_dir.name} (bInterfaceNumber {num}): '
                          f'class=0x{cls} usage_page={page_txt} '
                          f'driver={drv or "(none — unclaimed)"}')

        if not (busnum.isdigit() and devnum.isdigit()):
            out.append('  (no bus/dev number: cannot locate the device node)')
            continue

        node = str(dev_root / f'{int(busnum):03d}' / f'{int(devnum):03d}')
        try:
            st = os.stat(node)
            out.append(
                f'  node {node}: mode {oct(st.st_mode & 0o777)} '
                f'uid {st.st_uid} gid {st.st_gid}'
            )
        except OSError as e:
            out.append(f'  node {node}: cannot stat ({e})')
            continue

        # The ACL is the whole answer for uaccess-based rules: `user::rw-` is
        # the owner's own permission bits and says nothing, while a *named*
        # entry (`user:1000:rw-`) is what uaccess adds for the seat's user.
        acl = _run_out(['getfacl', '-p', node], timeout=5.0)
        if not acl:
            out.append('  acl: getfacl unavailable (install acl to include this)')
        else:
            named = [
                ln.strip() for ln in acl.splitlines()
                if ln.strip().startswith('user:') and not ln.strip().startswith('user::')
            ]
            if named:
                for entry in named:
                    out.append(f'  acl {entry}')
            else:
                out.append('  acl: no named user entry  <-- uaccess never tagged this device')

        writable = os.access(node, os.W_OK)
        out.append(
            f'  writable by this process: {"yes" if writable else "NO  <-- this is the popup"}'
        )

        # udev's own view: which rules matched, and whether the tag stuck.
        tags = _run_out(
            ['udevadm', 'info', '--query=property', f'--path={sys_root / dev.name}'],
            timeout=5.0,
        )
        for line in tags.splitlines():
            if line.startswith(('TAGS=', 'CURRENT_TAGS=', 'ID_VENDOR_ID=', 'ID_MODEL_ID=')):
                out.append(f'  {line}')

    if not found:
        out.append('')
        out.append(
            f'No SteelSeries device (vendor {_STEELSERIES_VID}) is enumerated on '
            f'this system, the dongle is unplugged, or the kernel did not bind it.'
        )

    return '\n'.join(out).strip()


def _device_status_dump() -> str:
    """The daemon's GetStatus payload, pretty-printed, or why it is missing.

    Asked over D-Bus rather than read from a file: this is what the daemon
    decoded from the device a moment ago, which is exactly the thing a status
    bug is about. A daemon that is not running is itself worth reporting, so
    the failure is written into the report instead of being swallowed.
    """
    import json

    from arctis_sound_manager.constants import (DBUS_BUS_NAME,
                                                DBUS_STATUS_INTERFACE_NAME,
                                                DBUS_STATUS_OBJECT_PATH)

    out = _run_out([
        'busctl', '--user', '--json=short', 'call',
        DBUS_BUS_NAME, DBUS_STATUS_OBJECT_PATH,
        DBUS_STATUS_INTERFACE_NAME, 'GetStatus',
    ])
    if not out:
        return '(no reply — is the arctis-manager daemon running?)'

    # busctl --json=short wraps the reply as {"type":"s","data":["<json>"]};
    # unwrap it so the report shows the status itself rather than two layers of
    # quoting. Anything unexpected is printed as it came back.
    try:
        payload = json.loads(out)['data'][0]
        return json.dumps(json.loads(payload), indent=2, sort_keys=True)
    except (KeyError, IndexError, TypeError, ValueError):
        return out


def collect_system_info() -> dict:
    info: dict = {}

    try:
        from arctis_sound_manager.utils import project_version
        info['version'] = project_version()
    except Exception:
        info['version'] = 'unknown'

    info['python'] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    info['kernel'] = platform.release()
    info['python_libs'] = _python_lib_versions()
    info['install_methods'] = _detect_install_methods()

    # Distro name
    try:
        r = subprocess.run(['lsb_release', '-d'], capture_output=True, text=True, timeout=2)
        info['distro'] = r.stdout.split(':', 1)[1].strip() if r.returncode == 0 else ''
    except Exception:
        info['distro'] = ''
    if not info['distro']:
        try:
            for line in Path('/etc/os-release').read_text().splitlines():
                if line.startswith('PRETTY_NAME='):
                    info['distro'] = line.split('=', 1)[1].strip().strip('"')
                    break
        except Exception:
            info['distro'] = platform.system()

    # PipeWire version
    try:
        r = subprocess.run(['pipewire', '--version'], capture_output=True, text=True, timeout=2)
        info['pipewire'] = r.stdout.strip().splitlines()[0] if r.returncode == 0 else 'not found'
    except Exception:
        info['pipewire'] = 'unknown'

    # Recent daemon logs (last 100 lines from journald)
    try:
        r = subprocess.run(
            ['journalctl', '--user', '-u', 'arctis-manager.service',
             '-n', '100', '--no-pager', '--output=short'],
            capture_output=True, text=True, timeout=5
        )
        info['logs'] = r.stdout.strip() if r.returncode == 0 else ''
    except Exception:
        info['logs'] = ''

    # USB HID device info (interfaces, endpoints)
    try:
        r = subprocess.run(
            ['asm-cli', 'tools', 'arctis-devices'],
            capture_output=True, text=True, timeout=5
        )
        info['usb_hid'] = r.stdout.strip() if r.returncode == 0 else r.stderr.strip()
    except Exception:
        info['usb_hid'] = ''

    # What the daemon actually decodes from the device: the battery level, the
    # power status, and every other status variable this model reports.
    #
    # Added after a Discord report ("battery just shows offline on my DAC")
    # that could not be settled from a bug report: the USB section says which
    # device is plugged in, but nothing said what ASM reads out of it — and the
    # difference between "this model has no battery" and "the battery is not
    # being decoded" is the whole answer. Redacted of nothing: the payload is
    # levels and mode names, no identifiers.
    info['device_status'] = _device_status_dump()

    # PipeWire audio cards
    try:
        r = subprocess.run(
            ['pactl', 'list', 'cards', 'short'],
            capture_output=True, text=True, timeout=5
        )
        info['pw_cards'] = r.stdout.strip() if r.returncode == 0 else ''
    except Exception:
        info['pw_cards'] = ''

    # Full sink list — useful when troubleshooting multi-device routing (issue #20).
    try:
        r = subprocess.run(['pactl', 'list', 'sinks', 'short'],
                           capture_output=True, text=True, timeout=5)
        info['pw_sinks'] = r.stdout.strip() if r.returncode == 0 else ''
    except Exception:
        info['pw_sinks'] = ''

    # WirePlumber state — catches priority/routing decisions made above the PA layer.
    try:
        r = subprocess.run(['wpctl', 'status'], capture_output=True, text=True, timeout=5)
        info['wpctl'] = r.stdout.strip() if r.returncode == 0 else ''
    except Exception:
        info['wpctl'] = ''

    # WirePlumber restore-stream state — an entry that pins an Arctis loopback
    # (Arctis_*_sink_out) to the physical ALSA sink is re-applied at every
    # recreate and drives the endless mislink loop on WirePlumber 0.5.x (#100).
    info['wp_restore_stream_arctis'] = ''
    try:
        _rs = Path.home() / '.local' / 'state' / 'wireplumber' / 'restore-stream'
        if _rs.is_file():
            _hits = [
                ln for ln in _rs.read_text(errors='replace').splitlines()
                if 'Arctis' in ln and ('target' in ln or 'alsa_output' in ln)
            ]
            info['wp_restore_stream_arctis'] = '\n'.join(_hits[:40])
    except Exception:
        info['wp_restore_stream_arctis'] = ''

    # Filter-chain safe mode + config presence (issue #88). Safe mode gates
    # EQ config regeneration (sonar_to_pipewire.ensure_sonar_eq_configs): while
    # it is armed ASM will NOT recreate missing sonar-*-eq.conf, so the EQ nodes
    # never load and every loopback orphans on an absent target = no audio. This
    # is invisible without surfacing the marker, so collect it plus the active
    # and ASM-disabled config directories to tell "safe mode armed" apart from
    # "user moved the configs away".
    info['filter_chain_safe_mode'] = ''
    info['filter_chain_conf_active'] = ''
    info['filter_chain_conf_disabled'] = ''
    try:
        _marker = Path.home() / '.config' / 'arctis_manager' / 'filter_chain_safe_mode.json'
        if _marker.is_file():
            info['filter_chain_safe_mode'] = (
                'ARMED — EQ config regeneration is suppressed\n'
                + _marker.read_text(errors='replace').strip()
            )
        else:
            info['filter_chain_safe_mode'] = 'not armed'
    except Exception:
        info['filter_chain_safe_mode'] = ''
    try:
        _cdir = Path.home() / '.config' / 'pipewire' / 'filter-chain.conf.d'
        if _cdir.is_dir():
            info['filter_chain_conf_active'] = '\n'.join(
                sorted(p.name for p in _cdir.glob('*.conf'))
            )
        _ddir = _cdir.parent / 'filter-chain.conf.d.disabled'
        if _ddir.is_dir():
            info['filter_chain_conf_disabled'] = '\n'.join(
                sorted(p.name for p in _ddir.glob('*.conf'))
            )
    except Exception:
        pass

    # What the *pipewire daemon itself* was told to load, which until #205 no
    # report showed. filter-chain.conf.d/ is read by filter-chain.service;
    # pipewire.conf.d/ is read by the daemon. A filter declared in both — or a
    # drop-in that pulls the same graph in a second time — loads every EQ node
    # twice under one name, and from there every target.object ASM sets is
    # ambiguous. The duplicate shows up in the graph section above; this is
    # where it is explained.
    info['pipewire_conf_d'] = ''
    try:
        _seen: list[str] = []
        for _root, _label in (
            (Path.home() / '.config' / 'pipewire', '~/.config/pipewire'),
            (Path('/etc/pipewire'), '/etc/pipewire'),
        ):
            _dropin = _root / 'pipewire.conf.d'
            if not _dropin.is_dir():
                continue
            for _f in sorted(_dropin.glob('*.conf')):
                try:
                    _body = _f.read_text(errors='replace')
                except Exception:
                    _body = ''
                _flag = ''
                if 'libpipewire-module-filter-chain' in _body:
                    _flag = '   [!] loads a filter-chain in the pipewire daemon'
                elif 'libpipewire-module-loopback' in _body:
                    _flag = '   (loopback)'
                _seen.append(f'{_label}/pipewire.conf.d/{_f.name}{_flag}')
        info['pipewire_conf_d'] = '\n'.join(_seen)
    except Exception:
        info['pipewire_conf_d'] = ''

    # --- PipeWire runtime / container diagnostics (issue #74) ----------------
    # When ASM runs inside Distrobox/Flatpak, PipeWire is only reachable
    # through forwarded sockets. These fields show whether the sockets are
    # actually passed through and whether PipeWire sees the headset at all.
    info['pipewire_runtime_dir'] = os.environ.get('PIPEWIRE_RUNTIME_DIR', '<unset>')
    info['pulse_server'] = os.environ.get('PULSE_SERVER', '<unset>')
    info['container_env'] = _detect_container_env()

    info['pw_sources'] = _run_out(['pactl', 'list', 'sources', 'short'])

    info['filter_chain_status'] = (
        _run_out(['systemctl', '--user', 'is-active', 'filter-chain']) or 'unknown'
    )

    # The default output decides how much of ASM is in play at all: the router
    # only adopts new applications while an Arctis sink holds it. When it is the
    # headset's own hardware device instead of a channel, everything keeps
    # working and playing, yet no application reaches Game/Chat/Media and the
    # mixer looks empty. That is invisible in a report unless the default is
    # stated, and finding it out cost a round of questions with a user whose
    # setup was perfectly deliberate.
    info['default_sink'] = _run_out(['pactl', 'get-default-sink']) or 'unknown'

    # Sections the CLI dump had and this one did not. Keeping two generators
    # that drift apart is how #181 ended up with a report that could not answer
    # the question it was filed about, so the unique halves are shared rather
    # than reimplemented.
    #
    # The settings are the valuable half: whether Spatial Audio is on, which
    # HRIR, whether the headset is made the default output on connect, the
    # forced quantum. Every one of those has been asked by hand in a recent
    # issue. Redaction (city, tokens, e-mail) happens inside _section_settings.
    #
    # Imported here rather than at module scope: diagnose imports this module,
    # so a top-level import would be circular.
    try:
        from arctis_sound_manager.diagnose import _section_settings, _section_yamls
        info['asm_settings'] = _section_settings()
        info['device_yaml_overrides'] = _section_yamls()
    except Exception as exc:
        info['asm_settings'] = f'(could not collect: {exc!r})'
        info['device_yaml_overrides'] = ''
    info['pw_service_status'] = ' '.join(
        f"{unit}={_run_out(['systemctl', '--user', 'is-active', unit]) or 'unknown'}"
        for unit in ('pipewire', 'pipewire-pulse')
    )

    # One dump, shared by both graph sections: a large graph is expensive to
    # serialise and this function is called more than once per report.
    _pw_objs = _pw_objects()
    info['pw_arctis_nodes'] = _arctis_pw_nodes(_pw_objs)
    info['pw_audio_graph'] = _audio_graph(_pw_objs)
    info['pw_clients'] = _pw_clients(_pw_objs)
    info['alsa_pcm_state'] = _alsa_pcm_state()
    # Which nodes are actually processing audio, as opposed to merely being
    # connected. Also carries the xrun counters (#183).
    info['pw_top'] = _run_out(['pw-top', '-b', '-n', '1'], timeout=15.0) or \
        '(pw-top unavailable — install pipewire-utils?)'

    info['journalctl_pipewire'] = _run_out(
        ['journalctl', '--user', '-u', 'pipewire', '-n', '20', '--no-pager'],
    )
    info['journalctl_filter_chain'] = _run_out(
        ['journalctl', '--user', '-u', 'filter-chain', '-n', '80', '--no-pager'],
    )

    # A filter-chain SIGSEGV (issue #88) leaves a coredump whose backtrace names
    # the offending module — the single most useful artifact to locate the crash.
    # coredumpctl is systemd-only; _run_out returns '' when it's absent.
    _coredump_raw = _run_out(
        ['coredumpctl', 'info', '--no-pager', 'pipewire'], timeout=10.0,
    )
    info['coredump_filter_chain'] = (
        '\n'.join(_coredump_raw.splitlines()[-200:]) if _coredump_raw else ''
    )

    # The generated filter-chain configs themselves: a LADSPA plugin referenced
    # here but absent on the host is the most likely segfault cause.
    _fc_conf_dir = Path.home() / '.config' / 'pipewire' / 'filter-chain.conf.d'
    _fc_conf_entries: list[str] = []
    if _fc_conf_dir.is_dir():
        try:
            for _p in sorted(_fc_conf_dir.iterdir()):
                if not _p.is_file():
                    continue
                try:
                    _fc_content = _p.read_text(encoding='utf-8', errors='replace')
                except OSError as _e:
                    _fc_content = f'(could not read: {_e!r})'
                _fc_conf_entries.append(
                    f'### {_p.name}\n```\n{_fc_content.strip()}\n```'
                )
        except OSError:
            pass
    info['filter_chain_confs'] = '\n\n'.join(_fc_conf_entries)

    # udev rules: which paths exist + the actual content of the active file.
    # The ASM checker's own verdict on whether the rules are valid is also useful.
    try:
        from arctis_sound_manager.constants import UDEV_RULES_PATHS
        from arctis_sound_manager.udev_checker import is_udev_rules_valid
        rules_present = [p for p in UDEV_RULES_PATHS if Path(p).exists()]
        info['udev_paths'] = rules_present
        info['udev_valid'] = bool(is_udev_rules_valid())
        if rules_present:
            try:
                info['udev_content'] = Path(rules_present[0]).read_text()
            except Exception as e:
                info['udev_content'] = f'(could not read {rules_present[0]}: {e!r})'
        else:
            info['udev_content'] = ''
    except Exception as e:
        info['udev_paths'] = []
        info['udev_valid'] = None
        info['udev_content'] = f'(udev probe failed: {e!r})'

    # What the kernel actually granted us on the device node (see _usb_access).
    info['usb_access'] = _usb_access(info.get('udev_paths', []))

    # USB monitor backend (pyudev event-driven vs polling fallback) — straight
    # from the module so we don't have to instantiate a second monitor.
    try:
        from arctis_sound_manager.usb_devices_monitor import _PYUDEV_AVAILABLE
        info['usb_monitor_backend'] = 'pyudev' if _PYUDEV_AVAILABLE else 'polling'
    except Exception:
        info['usb_monitor_backend'] = 'unknown'

    # D-Bus session info — ASM is dead in the water without a session bus.
    info['dbus_session'] = (
        os.environ.get('DBUS_SESSION_BUS_ADDRESS')
        or (f'/run/user/{os.getuid()}/bus'
            if Path(f'/run/user/{os.getuid()}/bus').exists()
            else '<not set>')
    )
    info['session_type'] = os.environ.get('XDG_SESSION_TYPE', '<unset>')

    # How Clips can get frames off this screen, which is the whole of "Clips
    # will not record here" (#214). Collected only when Clips is switched on:
    # an install that never enabled it must not be told GStreamer is missing,
    # and must not pay for the import either (see test_clips_opt_in).
    info['clips_capture'] = ''
    try:
        from arctis_sound_manager.system_deps_checker import clips_enabled
        if clips_enabled():
            lines = [
                f"desktop:  {os.environ.get('XDG_CURRENT_DESKTOP', '<unset>')}",
                f"display:  DISPLAY={os.environ.get('DISPLAY', '<unset>')} "
                f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY', '<unset>')}",
            ]
            # Which backend is installed decides more than the desktop's name:
            # xdg-desktop-portal-gtk is the one with no ScreenCast at all.
            found = [b for b in ('xdg-desktop-portal', 'xdg-desktop-portal-gtk',
                                 'xdg-desktop-portal-kde', 'xdg-desktop-portal-gnome',
                                 'xdg-desktop-portal-wlr', 'xdg-desktop-portal-hyprland')
                     if shutil.which(b) or Path(f'/usr/lib/{b}').exists()
                     or Path(f'/usr/libexec/{b}').exists()]
            lines.append(f"portal backends: {', '.join(found) or 'none found'}")

            from arctis_sound_manager.clip_capture import (
                screencast_portal_available, x11_capture_region)
            portal = screencast_portal_available()
            lines.append(f"ScreenCast portal: {'available' if portal else 'ABSENT'}")
            if not portal:
                lines.append(f"→ X11 capture, region: {x11_capture_region() or 'whole screen'}")
                lines.append(f"  xrandr={'yes' if shutil.which('xrandr') else 'no'} "
                             f"xdotool={'yes' if shutil.which('xdotool') else 'no'}")

            if shutil.which('gst-inspect-1.0'):
                def _has(el: str) -> bool:
                    return subprocess.run(['gst-inspect-1.0', el],
                                          capture_output=True, timeout=10).returncode == 0
                els = [e for e in ('ximagesrc', 'pipewiresrc', 'videorate',
                                   'videoconvert', 'h264parse', 'matroskamux',
                                   'opusenc', 'pulsesrc') if not _has(e)]
                lines.append(f"missing GStreamer elements: {', '.join(els) or 'none'}")
                enc = [e for e in ('nvh264enc', 'vah264enc', 'x264enc') if _has(e)]
                lines.append(f"encoders: {', '.join(enc) or 'NONE — cannot encode'}")
            else:
                lines.append("gst-inspect-1.0 missing — GStreamer tools not installed")
            info['clips_capture'] = "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        info['clips_capture'] = f"(could not probe: {exc})"
    info['desktop'] = os.environ.get('XDG_CURRENT_DESKTOP', '<unset>')

    # ── Gamescope / Steam Game Mode detection ─────────────────────────────────
    # Under Bazzite (and other Steam Deck / Gamescope setups) the WirePlumber
    # routing policy keeps changing, which can trigger sustained loopback flapping
    # (issue #90).  Detecting this session upfront helps triage.
    _gamescope_by_proc = bool(_run_out(['pgrep', '-x', 'gamescope']))
    _desktop_val = (
        os.environ.get('XDG_CURRENT_DESKTOP', '')
        + ' '
        + os.environ.get('XDG_SESSION_DESKTOP', '')
    ).lower()
    _gamescope_by_env = 'gamescope' in _desktop_val
    if _gamescope_by_proc and _gamescope_by_env:
        info['gamescope_session'] = 'yes (process found + XDG env match)'
    elif _gamescope_by_proc:
        info['gamescope_session'] = 'yes (gamescope process found)'
    elif _gamescope_by_env:
        info['gamescope_session'] = 'yes (XDG_CURRENT_DESKTOP/XDG_SESSION_DESKTOP contains "gamescope")'
    else:
        info['gamescope_session'] = 'no'

    # ── Loopback watchdog activity summary ────────────────────────────────────
    # Count occurrences of key watchdog log patterns in the already-captured
    # arctis-manager journal so maintainers can instantly see if flapping is
    # happening without grepping through the full log.
    _WATCHDOG_KEYWORDS = (
        '_loopback_watchdog',
        'restarted dead',
        'mislinked',
        'orphaned',
        'flapping',
        'backing off',
    )
    _log_text = info.get('logs', '')
    if _log_text:
        _activity: dict[str, int] = {}
        for _kw in _WATCHDOG_KEYWORDS:
            _count = _log_text.lower().count(_kw.lower())
            if _count:
                _activity[_kw] = _count
        info['loopback_watchdog_activity'] = _activity
    else:
        info['loopback_watchdog_activity'] = {}

    return info


def format_bug_report(traceback_str: Optional[str] = None) -> str:
    info = collect_system_info()

    lines = [
        '## Environment',
        f'- **ASM version**: {info.get("version", "unknown")}',
        f'- **Python**: {info.get("python", "unknown")}',
        f'- **OS**: {info.get("distro", "unknown")} (kernel {info.get("kernel", "?")})',
        f'- **PipeWire**: {info.get("pipewire", "unknown")}',
        f'- **Desktop / Session**: {info.get("desktop", "?")} / {info.get("session_type", "?")}',
        f'- **D-Bus session**: `{info.get("dbus_session", "?")}`',
        f'- **USB monitor backend**: {info.get("usb_monitor_backend", "?")}',
        f'- **Container environment**: {info.get("container_env", "?")}',
        f'- **PIPEWIRE_RUNTIME_DIR**: `{info.get("pipewire_runtime_dir", "?")}`',
        f'- **PULSE_SERVER**: `{info.get("pulse_server", "?")}`',
        f'- **PipeWire services**: {info.get("pw_service_status", "?")}',
        f'- **filter-chain.service**: {info.get("filter_chain_status", "?")}',
        f'- **Default output**: `{info.get("default_sink", "?")}`',
        f'- **Gamescope session**: {info.get("gamescope_session", "?")}',
        '',
    ]

    # Gamescope / Game Mode section — only when detected, so regular desktop
    # reports stay uncluttered.
    if info.get('gamescope_session', 'no') != 'no':
        lines += [
            '## Gamescope / Steam Game Mode',
            '<!-- Gamescope session detected.  WirePlumber routing policy in Game Mode',
            '     can repeatedly mis-route loopbacks, causing audio cuts (issue #90). -->',
            f'- **Detection**: {info.get("gamescope_session", "?")}',
            f'- **XDG_CURRENT_DESKTOP**: `{os.environ.get("XDG_CURRENT_DESKTOP", "<unset>")}`',
            f'- **XDG_SESSION_DESKTOP**: `{os.environ.get("XDG_SESSION_DESKTOP", "<unset>")}`',
            '',
        ]

    # Loopback watchdog activity — only shown when there is something to report.
    _watchdog_activity = info.get('loopback_watchdog_activity', {})
    if _watchdog_activity:
        lines += [
            '## Loopback watchdog activity (from recent daemon logs)',
            '<!-- Non-zero counts here indicate the watchdog had to intervene.',
            '     High "flapping" or "backing off" counts = issue #90 (Gamescope). -->',
            '```',
            *[f'{kw}: {count}' for kw, count in sorted(_watchdog_activity.items())],
            '```',
            '',
        ]

    methods = info.get('install_methods', [])
    if methods:
        lines += [
            '## ASM installation(s) detected',
            '<!-- More than one entry below = duplicate install. Run scripts/uninstall.sh to clean up. -->',
            '```',
            *(f'- {m}' for m in methods),
            '```',
            '',
        ]

    libs = info.get('python_libs', {})
    if libs:
        lines += [
            '## Python library versions',
            '```',
            *(f'{k}: {v}' for k, v in libs.items()),
            '```',
            '',
        ]

    if traceback_str:
        lines += [
            '## Crash traceback',
            '```',
            traceback_str.strip(),
            '```',
            '',
        ]

    device_status = info.get('device_status', '')
    if device_status:
        lines += [
            '## Device status (what the daemon decodes)',
            '```json',
            device_status,
            '```',
            '',
        ]

    usb_hid = info.get('usb_hid', '')
    if usb_hid:
        lines += [
            '## USB HID devices',
            '```',
            usb_hid,
            '```',
            '',
        ]

    pw_cards = info.get('pw_cards', '')
    if pw_cards:
        lines += [
            '## PipeWire audio cards',
            '```',
            pw_cards,
            '```',
            '',
        ]

    pw_sinks = info.get('pw_sinks', '')
    if pw_sinks:
        lines += [
            '## PipeWire sinks',
            '```',
            pw_sinks,
            '```',
            '',
        ]

    pw_sources = info.get('pw_sources', '')
    if pw_sources:
        lines += [
            '## PipeWire sources',
            '```',
            pw_sources,
            '```',
            '',
        ]

    # Always shown: an EMPTY node list while USB sees the headset is exactly
    # the signal that PipeWire never created the ALSA nodes (issue #74).
    lines += [
        '## PipeWire — Arctis nodes',
        '<!-- Empty while the USB section above shows the headset = PipeWire',
        '     does not see the device (common in Distrobox when the PipeWire',
        '     sockets are not forwarded into the container). -->',
        '```',
        info.get('pw_arctis_nodes', '') or '(none — PipeWire does not see any Arctis node)',
        '```',
        '',
        '## Audio graph — node states and links',
        '<!-- The whole audio graph, not just Arctis nodes, on purpose: a second',
        '     unrelated output in the same state as the headset distinguishes "ASM',
        '     holds this device" from "this machine never suspends anything".',
        '     state=running with nothing playing means something is driving the',
        '     device; the links say what. Routing props in [brackets] show whether',
        '     the on-disk config reached the running graph. -->',
        '```',
        info.get('pw_audio_graph', '') or '(not collected)',
        '```',
        '',
        '## PipeWire clients and their access level',
        '<!-- Cross-reference with client.id in the graph above. A link is',
        '     refused when the client owning either end cannot see the other',
        '     node, so this is where a refused link is explained. Any',
        '     pipewire.sec.* field means a security context is restricting',
        '     that client. -->',
        '```',
        info.get('pw_clients', '') or '(not collected)',
        '```',
        '',
        '## Nodes actually processing audio (`pw-top`)',
        '<!-- Connected is not the same as running. The ERR column is the xrun',
        '     counter: sustained xruns on the surround chain are audible as',
        '     crackling (#183). -->',
        '```',
        info.get('pw_top', '') or '(not collected)',
        '```',
        '',
        '## ALSA PCM state (kernel view)',
        '<!-- The layer below PipeWire. A PCM still open here while the graph',
        '     looks idle means something holds the device at the ALSA level. -->',
        '```',
        info.get('alsa_pcm_state', '') or '(not collected)',
        '```',
        '',
        '## ASM settings (redacted)',
        '<!-- Spatial Audio, HRIR choice, "make the headset the default output",',
        '     forced quantum: each of these has had to be asked for by hand in a',
        '     recent issue. City, tokens and e-mail are stripped. -->',
        '```json',
        info.get('asm_settings', '') or '(not collected)',
        '```',
        '',
    ]

    yaml_overrides = info.get('device_yaml_overrides', '')
    if yaml_overrides:
        lines += [
            '## User device YAML overrides',
            '<!-- A stale copy here shadows the packaged profile and can hide a',
            "     product id added by a later release (#146). Usually empty. -->",
            '```',
            yaml_overrides,
            '```',
            '',
        ]

    wpctl = info.get('wpctl', '')
    if wpctl:
        lines += [
            '## WirePlumber (`wpctl status`)',
            '```',
            wpctl[-3000:],
            '```',
            '',
        ]

    restore_stream = info.get('wp_restore_stream_arctis', '')
    if restore_stream:
        lines += [
            '## WirePlumber restore-stream — Arctis targets',
            '<!-- A stored target pointing at alsa_output...analog-stereo here is the',
            '     restore-stream poison that pins the loopback to the physical sink',
            '     and drives the endless mislink loop (#100). Fix: stop wireplumber,',
            '     remove the Arctis lines from ~/.local/state/wireplumber/restore-stream,',
            '     restart wireplumber. -->',
            '```',
            restore_stream,
            '```',
            '',
        ]

    safe_mode = info.get('filter_chain_safe_mode', '')
    active_conf = info.get('filter_chain_conf_active', '')
    disabled_conf = info.get('filter_chain_conf_disabled', '')
    if safe_mode or active_conf or disabled_conf:
        # Flag the common failure: EQ nodes can't load if their .conf is not in
        # the active dir. If any sonar-*-eq.conf is missing here, the loopbacks
        # will orphan on an absent target and there will be no audio (#88).
        _expected_eq = {
            'sonar-game-eq.conf', 'sonar-chat-eq.conf',
            'sonar-media-eq.conf', 'sonar-output-eq.conf',
        }
        _present = set(active_conf.splitlines())
        _missing = sorted(_expected_eq - _present)
        lines += [
            '## Filter-chain safe mode & config presence',
            '<!-- Safe mode ARMED suppresses EQ config regeneration (#88): missing',
            '     sonar-*-eq.conf below means those EQ nodes never load and every',
            '     loopback orphans on an absent target = no audio. Recovery: reset',
            '     safe mode from the app (re-enables EQ), then restart the daemon. -->',
            f'- **Safe mode**: {safe_mode or "(unknown)"}',
        ]
        if _missing:
            lines.append(
                f'- ⚠️ **Missing EQ configs (no audio on these channels)**: `{", ".join(_missing)}`'
            )
        lines += [
            '',
            '`filter-chain.conf.d/` (active — read by `filter-chain.service`):',
            '```',
            active_conf or '(empty — no ASM filter-chain configs loaded)',
            '```',
            '`filter-chain.conf.d.disabled/` (moved aside by ASM safe mode):',
            '```',
            disabled_conf or '(none)',
            '```',
            '',
            '`pipewire.conf.d/` (read by the pipewire daemon itself):',
            '<!-- A filter-chain declared here loads IN ADDITION to the ones',
            '     filter-chain.service already loads from the directory above.',
            '     Both copies answer to the same node.name, so every route ASM',
            '     sets by name becomes ambiguous and the audio stops at the',
            '     virtual sink: meters move, headset stays silent (#205).',
            '     Check this against the duplicate-name warning in the graph. -->',
            '```',
            info.get('pipewire_conf_d', '') or '(no drop-ins)',
            '```',
            '',
        ]

    clips_capture = info.get('clips_capture', '')
    if clips_capture:
        lines += [
            '## Clips — how it can capture this screen',
            '<!-- Clips takes one of two routes and they fail for different',
            '     reasons. With a ScreenCast portal it asks the portal, which is',
            '     what gives window and multi-monitor selection. Without one —',
            '     xdg-desktop-portal-gtk, so XFCE/MATE/Cinnamon — it captures X11',
            '     directly. Neither works on Wayland with no portal (#214). -->',
            '```',
            clips_capture,
            '```',
            '',
        ]

    udev_paths = info.get('udev_paths', [])
    if udev_paths or info.get('udev_content'):
        valid = info.get('udev_valid')
        valid_str = '✅ valid' if valid else ('❌ invalid/missing' if valid is False else '?')
        lines += [
            '## udev rules',
            f'- `is_udev_rules_valid()`: {valid_str}',
            f'- Paths present: `{udev_paths}`',
            '```',
            info.get('udev_content', '')[:6000] or '(no rules file present on disk)',
            '```',
            '',
        ]

    usb_access = info.get('usb_access', '')
    if usb_access:
        lines += [
            '## USB device access',
            '<!-- Read straight from sysfs and the device node, so this section',
            '     still fills in when ASM cannot open the device at all, which is',
            '     the case every permission report is about (#190).',
            '     "writable ... NO" with a rules file present and no named ACL',
            '     entry = the rules did not reach THIS device (udev ran after the',
            '     dongle enumerated); replugging it, or `udevadm trigger`, applies',
            '     them. No SteelSeries device listed at all = nothing for the',
            '     rules to match, and the problem is upstream of ASM. -->',
            '```',
            usb_access[:6000],
            '```',
            '',
        ]

    logs = info.get('logs', '')
    if logs:
        lines += [
            '## Recent daemon logs',
            '```',
            logs[-4000:],
            '```',
            '',
        ]

    jc_pw = info.get('journalctl_pipewire', '')
    if jc_pw:
        lines += [
            '## PipeWire logs (`journalctl --user -u pipewire`, last 20)',
            '```',
            jc_pw[-3000:],
            '```',
            '',
        ]

    jc_fc = info.get('journalctl_filter_chain', '')
    if jc_fc:
        lines += [
            '## filter-chain logs (`journalctl --user -u filter-chain`, last 80)',
            '```',
            jc_fc[-6000:],
            '```',
            '',
        ]

    coredump = info.get('coredump_filter_chain', '')
    if coredump:
        lines += [
            '## filter-chain coredump backtrace (`coredumpctl info pipewire`)',
            '<!-- Captured from the systemd coredump store. Empty on non-systemd',
            '     distros or when no coredump was recorded. -->',
            '```',
            coredump[-6000:],
            '```',
            '',
        ]

    fc_confs = info.get('filter_chain_confs', '')
    if fc_confs:
        lines += [
            '## ASM filter-chain configs (`~/.config/pipewire/filter-chain.conf.d/`)',
            '<!-- A LADSPA plugin referenced here but absent on the host filesystem',
            '     is the most likely segfault cause (issue #88). -->',
            '',
            fc_confs,
            '',
        ]

    lines += [
        '## Steps to reproduce',
        '<!-- Describe what you were doing when the bug occurred -->',
        '',
        '## Expected behavior',
        '',
        '## Actual behavior',
    ]

    return '\n'.join(lines)


def format_bug_report_short(traceback_str: Optional[str] = None,
                            attachment_path: Optional[Path] = None) -> str:
    """Compact issue-body version of the report — fits in GitHub's URL params.

    The full report (USB tree, udev rules content, sinks, wpctl, journalctl)
    is too large for `?body=` (browsers cap query strings around 8 kB and
    GitHub silently truncates). Keep the URL body short and ask the user to
    drop the diagnostic file as an attachment in the issue editor.
    """
    info = collect_system_info()
    libs = info.get('python_libs', {})
    methods = info.get('install_methods', [])

    lines = [
        '## Environment',
        f'- **ASM version**: {info.get("version", "unknown")}',
        f'- **Python**: {info.get("python", "unknown")}',
        f'- **OS**: {info.get("distro", "unknown")} (kernel {info.get("kernel", "?")})',
        f'- **PipeWire**: {info.get("pipewire", "unknown")}',
        f'- **Desktop / Session**: {info.get("desktop", "?")} / {info.get("session_type", "?")}',
        f'- **USB monitor backend**: {info.get("usb_monitor_backend", "?")}',
        f'- **Container environment**: {info.get("container_env", "?")}',
        f'- **Install methods**: {", ".join(methods) or "?"}',
        '',
        '## Library versions',
        ', '.join(f'{k}={v}' for k, v in libs.items() if not v.startswith('(')),
        '',
    ]

    if traceback_str:
        # Last 30 lines is enough to identify the failing frame; the full
        # traceback is in the attachment.
        tb_short = '\n'.join(traceback_str.strip().splitlines()[-30:])
        lines += [
            '## Crash traceback (last 30 lines)',
            '```',
            tb_short,
            '```',
            '',
        ]

    if attachment_path is not None:
        lines += [
            '## Full diagnostic',
            f'> Drag-and-drop **`{attachment_path.name}`** into the issue editor below.',
            f'> File location on disk: `{attachment_path}`',
            '> Contains: USB tree, udev rules, PA/PW sinks, WirePlumber state, journalctl logs.',
            '',
        ]

    lines += [
        '## Steps to reproduce',
        '<!-- Describe what you were doing when the bug occurred -->',
        '',
        '## Expected behavior',
        '',
        '## Actual behavior',
    ]

    return '\n'.join(lines)


def write_full_report_to_file(traceback_str: Optional[str] = None) -> Path:
    """Write the full bug report (the heavy one) to a temp-ish path the user
    can drag-and-drop into the GitHub issue editor. Returns the path."""
    target_dir = Path.home() / '.cache' / 'arctis-sound-manager' / 'reports'
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    path = target_dir / f'bug-report-{stamp}.md'
    path.write_text(format_bug_report(traceback_str), encoding='utf-8')
    return path


def is_gh_cli_ready() -> bool:
    """True iff `gh` CLI is installed AND authenticated. The auth check is
    a quick `gh auth status` — exits non-zero when no token is configured."""
    if not _which('gh'):
        return False
    try:
        r = subprocess.run(
            ['gh', 'auth', 'status'],
            capture_output=True, text=True, timeout=4,
        )
        return r.returncode == 0
    except Exception:
        return False


def _which(cmd: str) -> bool:
    try:
        r = subprocess.run(['which', cmd], capture_output=True, text=True, timeout=2)
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return False


def submit_via_gh_cli(title: str, short_body: str, full_report_path: Path,
                     repo: str = 'loteran/Arctis-Sound-Manager') -> Optional[str]:
    """File the issue end-to-end via `gh` CLI:
      1. Upload the full diagnostic as a SECRET gist (not searchable, but
         accessible to anyone with the URL — same visibility as the issue).
      2. Append the gist URL to the short body.
      3. Create the issue.
      4. Return the new issue URL.

    Returns None on any failure so the caller can fall back to the manual
    drag-and-drop flow.
    """
    try:
        gist = subprocess.run(
            ['gh', 'gist', 'create', '--filename', full_report_path.name,
             '--desc', f'Arctis Sound Manager — {title}',
             str(full_report_path)],
            capture_output=True, text=True, timeout=15, check=True,
        )
        gist_url = gist.stdout.strip().splitlines()[-1].strip()
    except Exception:
        return None
    if not gist_url.startswith('https://'):
        return None

    body_with_link = (
        f'{short_body}\n\n'
        f'## Full diagnostic (gist)\n'
        f'{gist_url}\n'
    )
    try:
        issue = subprocess.run(
            ['gh', 'issue', 'create', '--repo', repo,
             '--label', 'bug', '--title', title, '--body', body_with_link],
            capture_output=True, text=True, timeout=15, check=True,
        )
        for line in issue.stdout.strip().splitlines():
            line = line.strip()
            if line.startswith('https://') and '/issues/' in line:
                return line
    except Exception:
        return None
    return None


def github_issue_url(title: str, body: Optional[str] = None) -> str:
    """Build a `new issue` URL. *body* is encoded as a query param when given;
    keep it under ~6 kB or browsers / GitHub will truncate."""
    params = f'labels=bug&title={quote(title)}'
    if body:
        params += f'&body={quote(body)}'
    return f'{GITHUB_ISSUES_URL}?{params}'


def write_crash_report(exc_type, exc_value, exc_tb, source: str = 'gui') -> None:
    try:
        tb_str = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        report = {
            'timestamp': datetime.now().isoformat(),
            'source': source,
            'traceback': tb_str,
        }
        CRASH_REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        CRASH_REPORT_FILE.write_text(json.dumps(report, indent=2))
    except Exception:
        pass


def read_crash_report() -> Optional[dict]:
    try:
        if CRASH_REPORT_FILE.exists():
            return json.loads(CRASH_REPORT_FILE.read_text())
    except Exception:
        pass
    return None


def clear_crash_report() -> None:
    try:
        CRASH_REPORT_FILE.unlink(missing_ok=True)
    except Exception:
        pass
