# RAPPORT-CHAOS-ASM

Hostile audit of Arctis Sound Manager, `develop` @ `d7328d4` (v1.4.4 released 2026-08-21).
Seven agents: hardware profiles, distro/init/session matrix, state drift, external tooling,
packaging lifecycle, field intelligence (web), and one adversary executing live attacks.

**37 findings.** 20 `CONFIRMED` (reproduced here, with the command and its output), 16
`PLAUSIBLE` (reasoned from code, not reproduced), 1 `REPORTED` (documented on the web by
someone else). Every `file:line` below was read; every claim an agent made about a missing
`try`, a missing dependency or a spec offset was re-verified by the coordinator before it
was kept — two agent claims did not survive that check and are corrected in place.

**Machine state after the live attacks:** all six services (`arctis-manager`,
`arctis-video-router`, `arctis-stream-guard`, `filter-chain`, `pipewire`, `wireplumber`)
`active`; no leftover nodes; the six `Arctis_*` nodes present; repository clean at `d7328d4`.
One residual file change is declared in **Not restored**.

---

## 1. Summary table

Severity order: no sound and no recourse first, then whole-family over isolated, then
degraded secondary function.

| ID | Title | Family | Verdict | Hardware / environment | Location | Effort |
|---|---|---|---|---|---|---|
| **PKG-1** | GitHub-release `.deb` omits `python3-pil`; the import chain kills the daemon before its own dependency check can run | 8 packaging | CONFIRMED | Every Debian/Ubuntu user installing the `.deb` from Releases | `debian/build-deb.sh:146` | S |
| **CHA-1** | A duplicate `node.name` hijacks a channel; the watchdog enforces the hijack and logs "2/2 channels linked" | 1 state drift | CONFIRMED | All | `pw_utils.py:1036`, `:1216` | M |
| **CHA-6** | The Output channel has two sources of truth; the live link follows the generated conf, the UI follows the setting | 1 state drift | CONFIRMED | All — **already diverged on the author's own machine** | `sonar_to_pipewire.py:2809` | M |
| **CHA-5** | A corrupt per-device settings file makes the headset never configure, while the daemon reports healthy | 1 state drift | CONFIRMED | All | `settings.py:44`, `:49`, `:88` | S |
| **CHA-7** | The repair path for a missing EQ node regenerates it as a flat bypass, discarding the user's entire curve | 10 silent regression | CONFIRMED | All | `sonar_to_pipewire.py:2655`, `:1959` | M |
| **HW-1** | Nova 7 Gen 1 / 7P Gen 1 decode four status bytes the vendor spec never defines; `micro_autoswitch` is dead on the whole generation | 3 hardware | CONFIRMED | 6 PIDs: 0x2202 0x2206 0x223a 0x227a 0x22a4 0x220a | `nova_7_discrete_battery.yaml:42` | S |
| **CHA-2** | `SetSetting("pipewire_quantum", …)` accepts any integer, applies it system-wide, and persists it across reboots | 5 permissions | CONFIRMED | All | `dbus_service.py:452`, `:461` | S |
| **CHA-3** | Stream Guard destroys PipeWire objects by a stale global id, and PipeWire recycles ids within seconds | 1 state drift | CONFIRMED | All, worst while Discord churns the graph | `stream_guard.py:183`, `scripts/stream_guard.py:114` | M |
| **CHA-11** | `_pw_dump()` returns an empty graph on timeout — the churn the code's own docstring blames for random dropouts | 1 state drift | PLAUSIBLE | All, under load | `pw_utils.py:518` | S |
| **CHA-4** | Running `asm-daemon` by hand reaps the live daemon's loopbacks 36 lines before the single-instance guard fires | 6 lifecycle | PLAUSIBLE | All — and the error message invites it | `scripts/daemon.py:207` vs `:241` | S |
| **SD-1** | The Output channel's external device has no daemon-side fallback when it disappears | 1 state drift | PLAUSIBLE | Bluetooth speaker / HDMI monitor as Output | `sonar_to_pipewire.py:2809-2929` | M |
| **ENV-1** | Distrobox installers still write the old tray unit name, so the Clips shortcut can never bind | 7 environment | CONFIRMED | Bazzite, SteamOS, Silverblue | `scripts/distrobox/_common.sh:337` | S |
| **ENV-3** | The post-upgrade restart is a silent no-op on dinit: new GUI, old daemon | 7 environment | CONFIRMED | Artix, Arch+dinit | `runtime_staleness.py:78` | S |
| **HW-2** | `percentage()` clamps only inside the `round_to` branch — a misfiled PID renders 76 % as 1900 % | 3 hardware | CONFIRMED | Any future misfiled PID | `status_parser_fn.py:16` | XS |
| **CHA-9** | The orphan reaper decides what to SIGKILL from `argv[0]`, which the target process chooses | 1 state drift | CONFIRMED | All | `loopback_manager.py:448` | S |
| **CHA-8** | `SetSetting` skips type validation entirely for every setting whose default is `None` | 5 permissions | CONFIRMED | All | `dbus_service.py:452` | S |
| **CHA-10** | `inf`/`nan` travel from a shared preset into the filter-chain config unclamped | 10 silent regression | CONFIRMED | All, remote content | `sonar_to_pipewire.py:554` | S |
| **CHA-12** | `hrir_id` is not validated against the catalogue and accepts `../` | 10 silent regression | CONFIRMED | All | `hrir_catalog.py:105` | XS |
| **CHA-13** | `SendEqCommand` validates the list length and nothing else; the values reach the headset | 3 hardware | PLAUSIBLE | All | `dbus_service.py:332`, `core.py:2106` | S |
| **EXT-1** | `_ToggleWorker.run()` has no global guard: an exception leaves the button frozen and the streams orphaned | 9 GUI | PLAUSIBLE | All | `gui/equalizer_page.py:269` | S |
| **ENV-2** | A blocking `subprocess.run` on the GUI thread waits on a nested elevation prompt (open #200) | 9 GUI | PLAUSIBLE | Nobara / any KDE distro | `gui/system_deps_dialog.py:463` | M |
| **INT-1** | An in-kernel `hid-steelseries` driver is coming for 25+ Arctis models and will claim the same interface | 3 hardware | PLAUSIBLE | 25+ PIDs ASM already supports | LKML v3, 2026-02-27 | L |
| **INT-2** | The status packet ASM sends every 2 s reportedly glitches Nova 5 audio at 32 Hz | 3 hardware | PLAUSIBLE | Nova 5 / 5X | `nova_5.yaml:33`, `core.py:3125` | M |
| **GUI-1** | `ClipsPage`'s two timers are never stopped and re-probe PulseAudio once a second | 9 GUI | PLAUSIBLE | All | `clips_page.py:612`, `:1311` | S |
| **PKG-2** | A build failure of the *hard* AUR dependency is treated exactly like the optional one | 8 packaging | PLAUSIBLE | Arch pacman repo | `.github/workflows/pacman-repo.yaml:83` | S |
| **PKG-3** | `filter-chain.service` is copied into `$HOME` once and never migrated | 2 stale copies | PLAUSIBLE | Fedora, Ubuntu | `scripts/setup.py:256` | S |
| **SD-2** | Filter-chain crash-loop detection is systemd-only, so safe mode never arms on dinit | 7 environment | PLAUSIBLE | Artix, Arch+dinit | `service_control.py:259` | M |
| **HW-3** | Nova 4 / 4X interface index is a documented guess; if wrong, every control is silently inert | 3 hardware | PLAUSIBLE | Nova 4 (0x12f2), 4X (0x12f6) | `nova_4.yaml:8` | S |
| **EXT-2** | `_setup_dinit_services()` never catches `FileNotFoundError` around ~12 raw `dinitctl` calls | 4 shell-out | PLAUSIBLE | dinit | `scripts/setup.py:92` | S |
| **ENV-4** | Distrobox bind-mounts carry no `:z`/`:Z`, so SELinux can deny them silently | 5 permissions | PLAUSIBLE | Silverblue, Fedora-derived | `scripts/distrobox/bazzite.sh:100` | S |
| **HW-4** | Nova Elite maps no combined synchronous status reply, unlike both its protocol siblings | 3 hardware | PLAUSIBLE (low) | Nova Elite | `nova_elite.yaml:77` | S |
| **INT-3** | An external hardware-backed capture for the Nova Elite OLED exists and ASM has not used it | 3 hardware | REPORTED | Nova Elite | ggoled #26, 2025-12-17 | S |
| **INT-4** | WirePlumber 0.5.15 centralised client permissions in the same window as the #181 fix | 7 environment | PLAUSIBLE | Arch/CachyOS today | `pw_utils.py` #181 paths | S |
| **PKG-4** | The Terra/Nobara repackaging is a real channel with zero delivery visibility | 8 packaging | PLAUSIBLE | Nobara | `scripts/verify_release_delivery.py:203` | M |
| **EXT-3** | `_shell_quote()` is not real shell quoting and feeds `pkexec sh -c` | 4 shell-out | PLAUSIBLE | All | `gui/system_deps_dialog.py:460` | XS |
| **SD-3** | The pyudev hotplug observer has no liveness check once selected | 6 lifecycle | PLAUSIBLE (low) | Distrobox, sandboxed sessions | `usb_devices_monitor.py:84` | M |
| **PKG-5** | The same `.deb` also omits `python3-babel`: wrong plural forms in every non-English locale | 8 packaging | CONFIRMED | Debian/Ubuntu `.deb` users | `debian/build-deb.sh:146` | XS |

Effort: XS = a few lines · S = one function · M = one module or a contract change · L = design work.

---

## 2. Findings

### PKG-1 — The `.deb` on every GitHub Release cannot start, and the dependency checker built to catch this is bypassed by import order

**What breaks:** `asm-daemon` and `asm-gui` die with a bare `ModuleNotFoundError: No module
named 'PIL'` on first launch, with no diagnostic and no self-heal offer.

**Reproduction:** install the `.deb` attached to any GitHub Release on a Debian/Ubuntu system
without Pillow already present, then start the daemon. The README documents this install path
and issue #163 shows a user following it.

**Cause:** `debian/build-deb.sh:146` hand-writes a `DEBIAN/control` heredoc whose `Depends:`
line reads, verbatim:

```
python3 (>= 3.10), python3-pyside6.qtcore | python3-pip, … python3-pyudev, python3-usb,
python3-ruamel.yaml, pipewire, pipewire-pulse, wireplumber, libusb-1.0-0
```

No `python3-pil`, no `pulseaudio-utils`, no `python3-babel`, no `curl` — while the *other*
Debian path, `debian/control:20`, used by `debian/rules` for the PPA, carries them. CI attaches
this `.deb` to every tag (`.github/workflows/release.yaml:49`).

The failure is unrecoverable because the import is unconditional and top-level:
`scripts/daemon.py:12` → `core.py:31` → `oled_manager.py:16` `from PIL import Image`. The
interpreter fails resolving `daemon.py`'s own import block, so `verify_setup()` never runs, so
`system_deps_checker.py:942` — which *has* a Pillow check with `dnf`/`apt-get`/pip auto-fix
commands, written for exactly this — is never reached. **The safety net exists and is jumped
over.** `CoreEngine` pulls in the OLED manager regardless of whether the connected headset
even has a screen.

`scripts/check-packaging-drift.py:183` only reads `debian/control`; it never looks at
`build-deb.sh`'s heredoc, so the "must print OK before a release" gate in `CLAUDE.md` cannot
see this drift.

**Verdict: CONFIRMED** — coordinator read both `Depends:` lines and the three-file import chain.

**Fix direction:** generate `build-deb.sh`'s `Depends:` from the same source of truth
`check-packaging-drift.py` already uses, and teach that script to scan the heredoc. Separately,
defer or guard the `PIL` import so a missing optional dependency routes into
`system_deps_checker` instead of killing the interpreter — that stops the whole class, not just
Pillow. Missing `pulseaudio-utils` is the #117 crash, fixed for RPM and never carried here.

---

### CHA-1 — A duplicate node name hijacks a channel, and the watchdog makes the hijack permanent while logging success

**What breaks:** any process on the session can create a PipeWire node named
`effect_input.sonar-media-eq`; within one watchdog tick ASM tears down the correct link, links
the channel into the impostor, and reports `2/2 channels linked` forever. Silent, permanent,
self-maintained loss of that channel.

**Reproduction (live):**

```bash
pw-loopback --capture-props='node.name=effect_input.sonar-media-eq media.class=Audio/Sink \
  audio.channels=2 audio.position=[FL FR] node.description=IMPOSTOR' \
  --playback-props='node.name=chaos_impostor_out node.autoconnect=false' &
```

After 14 s:

```
node named effect_input.sonar-media-eq: id 57  desc 'Sonar Media EQ'
node named effect_input.sonar-media-eq: id 264 desc 'IMPOSTOR'
LINK Arctis_Media_sink_out -> effect_input.sonar-media-eq id 264 desc 'IMPOSTOR'
journal: ensure_loopback_link: 'Arctis_Media_sink_out' → 'effect_input.sonar-media-eq' (2/2 channels linked)
```

**Cause:** `pw_utils.py:1036` (and identically `:1216` in `ensure_capture_link`) builds
`node_ids[name] = obj["id"]` — a plain dict keyed by `node.name`. PipeWire does not enforce
uniqueness of `node.name`: this is last-writer-wins, and the newest node normally wins.
`ensure_loopback_link` then classifies the link to the *real* EQ as a stray, destroys it, links
the impostor, and returns `True`, so the watchdog clears `_none_ticks` and never escalates.

The observation that makes this worth fixing: `loopback_manager.py:377-386` shows the author
reasoning carefully about name ambiguity on the *process* side (`target.object=Arctis_Game` vs
`node.name=Arctis_Game`). The same reasoning was never applied on the *graph* side, where
`node.name` is treated as a primary key.

**Verdict: CONFIRMED** (live, restored: impostor killed, link back to id 57).

**Fix direction:** resolve the target once and keep the object id, or match on `object.serial`
captured at creation; when a name resolves to more than one node, refuse to link and log it
loudly — a duplicate ASM node name is always a fault.

---

### CHA-6 — The Output channel has two sources of truth, and they had already diverged on the author's own machine

**What breaks:** the live link is enforced from the generated conf on disk; the conf is
generated from the setting in YAML. When they disagree the conf wins day to day — until any
unrelated escalation regenerates it, at which point the Output channel silently jumps to a
different device, with no user action and no message.

**Reproduction (live, on this machine's real pre-existing state):**
`general_settings.yaml` said `external_output_device: Arctis Nova Pro Wireless`, while
`sonar-output-eq.conf` said `node.target = "alsa_output.pci-0000_09_00.1.hdmi-stereo"` (the TV)
— and the live link went to the TV. Truncating an *unrelated* conf to trigger an escalation
produced, uninvited:

```
[WARNING] sonar_to_pipewire: sonar-output-eq.conf has wrong target
          (expected 'alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo') — regenerating
```

and the Output channel moved from the TV to the headset.

**Cause:** `sonar_to_pipewire.py:2809` reads the target *out of the conf*
(`_CONF_TARGET_RE.search(content)`), documenting the assumption "the conf is rewritten whenever
the user changes the external output, so it stays the single source of truth", while
`_resolve_external_output()` (`:470`) reads `external_output_device` from
`general_settings.yaml`. **That assumption holds in one direction only.** Nothing rewrites the
conf when the setting changes through `SetSetting` over D-Bus, a hand-edit, a config restore, a
settings sync or a package upgrade, and nothing reconciles the two.

**Verdict: CONFIRMED** (live; the divergence pre-existed the attack).

**Fix direction:** one owner. Make the setting authoritative and rewrite the conf on every
write path, or store the resolved target once where both readers look. Either way, log the
reconciliation when they differ.

---

### CHA-5 — A corrupt per-device settings file stops the headset ever being configured, while the daemon looks healthy

**What breaks:** `~/.config/arctis_manager/settings/1038_12e0.yaml` truncated, or holding a
non-integer, makes `DeviceSettings.read_from_file()` raise. The USB monitor's callback guard
swallows it, so the daemon stays `active` while `configure_virtual_sinks()` aborts on every
device event, forever: no virtual sinks, no loopbacks, no audio path, no explanation.

**Reproduction (in-process, against the shipped module):**

```
RAISES  truncated mid-write   -> ParserError: while parsing a flow mapping … expected ',' or '}'
RAISES  value is a string     -> ValueError: invalid literal for int() with base 10: 'hello'
RAISES  value is a list       -> TypeError: int() argument must be … not 'list'
RAISES  top level is a scalar -> TypeError: 'int' object is not iterable
```

**Cause:** `settings.py:44` (`raw = yaml.load(settings_file) or {}`) and `:49`
(`self.settings[key] = int(raw[key])`) — no `try`, no shape check, no clamping; called
unguarded at `core.py:1777`. The truncation needs no exotic failure, because the *writer* is
non-atomic too (`settings.py:88`, a bare `yaml.dump`).

The reason this is cheap to fix: the sibling class already does everything right.
`GeneralSettings.write_to_file()` (`settings.py:358`) is a textbook tmp+fsync+rename, and
`GeneralSettings.read_from_file()` (`:328`) already contains the exact repair — "YAML corrupt /
partial write from a previous crash. Backup the broken file … and fall back to defaults instead
of crashing the daemon at startup." **The fix is written; it was never applied to the class that
writes at runtime.**

**Verdict: CONFIRMED.**

**Fix direction:** lift the backup-and-default handler and the atomic write into a shared helper
used by both classes; skip individual keys that fail `int()` rather than aborting the load.

---

### CHA-7 — The repair for a missing EQ node silently discards the user's entire EQ curve

**What breaks:** when a channel's filter-chain node is absent, `ensure_sonar_eq_configs()`
"regenerates" the conf — as a flat bypass. Every band, macro and boost on that channel is gone
permanently, with a log line that says "regenerating" and nothing that says the EQ was discarded.

**Reproduction (live):** truncated `sonar-media-eq.conf` to 55 %, restarted `filter-chain`,
waited for the watchdog:

```
before: grep -c "type = builtin" sonar-media-eq.conf → 14
[WARNING] sonar_to_pipewire: sonar-media-eq.conf has wrong channel count (expected 8) — regenerating
after:  grep -c "type = builtin" sonar-media-eq.conf → 1
```

Ten band filters, three macro filters and the boost node, gone. Routing recovered in ~40 s; the
EQ never did.

**Cause:** `sonar_to_pipewire.py:2655` — `_bypass_conf(...)` is the only regeneration primitive.
The author is explicitly aware for one trigger (the `_CONF_VERSION` scope note says a version
bump must not trigger it) — but the *other* triggers (missing file, wrong channel count, wrong
target) fire on the same destructive path, unattended. The precondition is one non-atomic write:
`sonar_to_pipewire.py:1959`, `path.write_text(text)`, while `stream_guard.save_config`
(`stream_guard.py:113`) and `GeneralSettings.write_to_file` both do tmp+rename correctly.

**Verdict: CONFIRMED.**

**Fix direction:** make `_write_conf` atomic like its two well-behaved siblings; back up the
existing conf before regenerating and say so in the log; better, keep EQ state where it is
authoritative (the preset/macro JSON) so a rebuild is lossless.

---

### HW-1 — Nova 7 Gen 1 and 7P Gen 1 decode four bytes the vendor spec never defines, and one of them drives a live feature

**What breaks:** `bluetooth_connection`, `bluetooth_power_status`, `bluetooth_auto_mute` show
fabricated values across the whole Gen 1 Nova 7 line, and `micro_autoswitch` — "switch my mic
input when the headset mic is muted" — is permanently dead or erratic on the same hardware,
while the GUI accepts the setting.

**Affected:** `nova_7_discrete_battery.yaml` (0x2202, 0x2206, 0x223a, 0x227a, 0x22a4) and
`nova_7p_discrete_battery.yaml` (0x220a). Six PIDs, one whole generation.

**Cause:** `nova_7_discrete_battery.yaml:42` and `nova_7p_discrete_battery.yaml:33` map, off the
`0xb0` frame, `bluetooth_connection: 0x06`, `bluetooth_power_status: 0x07`,
`bluetooth_auto_mute: 0x08`, `mic_status: 0x09`. But
`~/steelseries-research/decoded-115/base_arctis_nova_7_tx.device:214-235` — the vendor's own
spec for *this* family, not an analogy with a sibling — defines seven fields ending at ASM
offset `0x05`. There is no field 8, 9, 10 or 11, not even padding.
`arctis_nova_7p_tx.device:8` includes the same base struct, so the P variant inherits the gap.

Running the real parser on a frame with only the six real bytes filled returns
`bluetooth_power_status: 'on'` and `mic_status: 'unmuted'` from zero bytes, regardless of the
device's actual state.

**Live consequence, not cosmetic:** `core.py:2277` builds `mic_muted` from this same
`mic_status`, and `core.py:107` (`resolve_mic_autoswitch_target`) drives `micro_autoswitch`
from it.

**Verdict: CONFIRMED** for the root cause (spec read directly). The exact displayed string is
PLAUSIBLE: zero-filled is the likely case, but leftover firmware buffer content would be worse
— flickering wrong values rather than a stuck one.

**Fix direction:** drop the four keys from both profiles, exactly as commit `7eeee6f` did for
the Gen-2 siblings, and extend `tests/test_status_offsets_vs_spec.py` to cover these two files —
that test locked the conclusion for the Gen-2 family and never reached Gen 1. Corroboration:
`bluetooth_startup`/`bt_call_default` are `api-write`-only in the same spec, so GG cannot read
these either.

**To confirm on hardware:** ask a Nova 7 (Gen 1, non-P) or 7P (Gen 1) owner to capture the raw
`0xb0` reply while toggling Bluetooth and muting the boom mic, and report whether bytes 6–9 ever
change.

---

### CHA-2 — One D-Bus call sets PipeWire's global quantum to any value, and it survives reboots

**What breaks:** any process in the session sets `clock.force-quantum` system-wide — including
`1` — and it is written into `general_settings.yaml`, from where `CoreEngine.start()` re-applies
it at every daemon start. Every application on the machine loses working audio, and nothing in
ASM's UI can express the value that caused it.

**Reproduction (live):**

```
gdbus call --session --dest name.giacomofurlan.ArctisManager.Next \
  --object-path /name/giacomofurlan/ArctisManager/Next/Settings \
  --method …Settings.SetSetting pipewire_quantum "8192"   → (true,)
pw-metadata -n settings 0 clock.force-quantum → value:'8192'

…SetSetting pipewire_quantum "true"                        → (true,)
general_settings.yaml → pipewire_quantum: true
journal: Stability mode: forcing PipeWire quantum to 1 (system-wide)
```

`"true"` gets through because `isinstance(True, int)` is `True`, so the one type check passes and
`int(True) == 1`. The UI only offers `{0, 1024, 2048}` (`settings.py:283`).

**Cause:** `dbus_service.py:452` — the only validation is
`not isinstance(value, type(config.default_value))`, a *type* check with no range — feeding
`apply_force_quantum(int(value))` at `:461`. `apply_force_quantum`'s own docstring
(`pw_utils.py:191`) says this is a global setting and "that is why the setting behind it defaults
to off and is the user's call" — the tacit assumption being that the only caller is a
three-button group. It is also a public session-bus method, a hand-edited YAML file, and a
settings file restored from a backup.

**Verdict: CONFIRMED** (restored: `SetSetting pipewire_quantum 0`, verified).

**Fix direction:** validate against the `ConfigSetting`'s declared domain (`values_mapping` keys
for `BUTTON_GROUP`, `min`/`max` for `SLIDER`) at the D-Bus boundary *and* on read from YAML;
use `type(value) is int` so `bool` is rejected where `int` is meant.

---

### CHA-3 — Stream Guard destroys objects by a stale id, and PipeWire recycles ids in seconds

**What breaks:** the guard takes a `pw-dump`, computes link ids to cut, then destroys them by id
in a separate step. On a churning graph — exactly when the guard runs, because Discord relinks
continuously — an id can name a different object by the time `pw-cli destroy` reaches it,
including one of ASM's own nodes.

**Reproduction (live, two halves).** Id recycling across three short-lived loopbacks:

```
run 1: node 291 chaos_probe_out_1 | node 365 chaos_probe_1
run 2: node 365 chaos_probe_out_2 | node 351 chaos_probe_2   ← id 365 reused ~2 s later
```

Cross-client destroy succeeding, and taking the owning process with it:

```
pw-cli destroy 265 ; destroy rc=0 ; chaos_victim still present: []   (owning pw-loopback gone)
```

So a stale id landing on `Arctis_Game` does not merely unlink it — it kills the sink, scatters
every pinned stream, and the watchdog rebuilds it as a *new* sink up to 5 s later. Three such
events in 60 s trip `_FLAP_THRESHOLD` and suppress the channel for 60–300 s (`core.py:669`).

**Cause:** `stream_guard.py:183` (`doomed.append(link_id)`) feeding
`scripts/stream_guard.py:114` (`pw-cli destroy <lid>`) with no re-identification between the
dump and the call. The contrast is the useful part: `loopback_manager.py:491-503` does exactly
the right thing for PIDs — *"`os.kill(pid, 0)` only proves a process holds this pid, not that it
is still the one we signalled … Re-reading the cmdline closes that window"* — and the identical
hazard for PipeWire globals was not carried across.

**Verdict: CONFIRMED.**

**Fix direction:** destroy links by their `(output-port, input-port)` pair via `pw-link -d`,
which is content-addressed, or re-read the object and confirm it is still a Link with the same
endpoints. A destroy that fails because the object is gone is fine; one that succeeds on the
wrong object is not.

---

### CHA-11 — `_pw_dump()` still returns an empty graph on timeout, and the code documents what that causes

**What breaks:** a `pw-dump` slower than 3 s — heavy load, a stalled session manager, a very
large graph — makes `_pw_dump()` return `[]`. Three such ticks and the watchdog recreates the
loopbacks: sinks destroyed, pinned streams scattered, and three recreations inside 60 s put the
channel into a 60–300 s cooldown.

**Cause:** `pw_utils.py:518` — `timeout=3`, then `except Exception: logger.warning(...); return []`.
The docstring fifty lines above (`_parse_pw_dump_output`, `:469`) states the consequence in the
author's own words: *"The loopback watchdog … reads an empty dump as 'every loopback/EQ target is
gone' and escalates to recreating loopbacks and restarting the filter-chain service — tearing
down and rebuilding the exact audio path that was actually fine. That churn is the mechanism
behind ASM's random audio dropouts."* That analysis fixed **one** producer of an empty dump
(concatenated JSON documents). The `TimeoutExpired` producer three lines up is untouched, as is a
non-zero exit and a `pw-dump` missing from `PATH` (`_abs_exe` caches the miss for the process's
lifetime, `pw_utils.py:33`).

**Verdict: PLAUSIBLE** — not reproduced, because manufacturing a 3 s `pw-dump` means interfering
with the live PipeWire the user was using.

**Fix direction:** distinguish "the graph is empty" from "I could not read the graph": return
`None` on failure and make every caller treat `None` as "no information, do nothing this tick" —
the discipline `get_xrun_counts` already applies (`pw_utils.py:238`).

---

### CHA-4 — Running `asm-daemon` by hand destroys the running daemon's chain before the single-instance guard fires

**What breaks:** the second daemon writes `clock.force-quantum` and reaps the first daemon's live
`pw-loopback` processes, and only then discovers the D-Bus name is taken and dies. The daemon's
own error message invites the user to do exactly this.

**Cause:** `scripts/daemon.py:207` (`asyncio.create_task(core_engine.start())`) versus `:241`
(`await dbus_manager.start(core_engine)` — the guard). `CoreEngine.start()` (`core.py:1226`) is a
**plain synchronous method**, so `usb_devices_monitor.start()` and `apply_force_quantum(...)`
execute at line 207, before the task exists. The USB monitor drives
`configure_virtual_sinks()` → `setup_loopbacks()` → `LoopbackManager.start()` →
`_reap_orphan_loopbacks_unlocked()`, whose exclude set is `{os.getpid()} | self._handles` — empty
in a fresh process — so the *running* daemon's healthy `Arctis_Game/Chat/Media` loopbacks match
the sweep and are SIGTERM/SIGKILLed. Only then does `dbus_service.py:688` raise with "Another
asm-daemon is probably running — stop it with `systemctl --user stop arctis-manager.service`".

**Verdict: PLAUSIBLE.** The ordering and the reaper's selection were both confirmed by reading
plus the CHA-9 experiment (the scan selects the live daemon's `Arctis_Game` pid); the end-to-end
sequence was deliberately not executed, because the user was mid-game.

**Fix direction:** acquire the bus name — or a `flock` on a runtime lockfile — as the first thing
in `main_async()`, before `CoreEngine()` is constructed.

---

### SD-1 — The Output channel has no daemon-side fallback when its device disappears

**What breaks:** applications routed to the Output channel play into a dead end — no sound, no
error, indefinitely — when the chosen device vanishes and the tray GUI is not running.

**Cause:** `sonar_to_pipewire.py:2809-2929`: the "output" hop checks whether the baked target is
in the graph and, when it is not, is **skipped with no `else` branch** — never retried, never
redirected, and never counted as a failure, so the watchdog's escalation counters never fire
either. `_resolve_external_output` (`:470`) falls through to `("", 2, "FL FR")` for a name it
cannot find. The only code that falls back to the headset is `OutputSelector.refresh`
(`gui/output_selector.py:190`), on a 5 s `QTimer` **in the GUI process**.

The asymmetry is the argument: Game/Chat/Media go through `channel_destination()`
(`sonar_to_pipewire.py:381`), which *does* fall back to the physical headset the moment the saved
device is absent, re-evaluated on every watchdog tick. The dedicated Output channel — whose whole
purpose is a volatile device like a Bluetooth speaker or a monitor — is the one link the daemon
does not own end to end.

**Verdict: PLAUSIBLE** (dormant on this machine, where `external_output_device` is the headset).

**Fix direction:** give the "output" hop the same `channel_destination()`-style fallback, or at
minimum escalate a configured-but-absent target the way `_TARGET_ABSENT_TICKS` already does.

---

### ENV-1 — Distrobox installs can never bind the Clips shortcut, because they still write the old unit name

**What breaks:** on every Distrobox install — the documented path for Bazzite, SteamOS and
Silverblue — the Clips global shortcut cannot bind. The portal answers `NotAllowed: An app id is
required`, the exact symptom the native fix eliminated.

**Cause:** `autostart.py:19` renamed the tray unit to `app-ArctisManager.service` because
`xdg-desktop-portal` derives an app id for a non-sandboxed process only from a cgroup shaped
`app-<AppID>[-<random>].service`. The Distrobox generators still hand-write the old name:
`scripts/distrobox/_common.sh:337`, `bazzite.sh:268`, `silverblue.sh:214`, `steamos.sh:273`.

`steamos.sh` and `_common.sh` were edited on 2026-08-18 (`7044db9`), **three days after** the
rename landed (`784093a`, 2026-08-15) — a maintained file that missed it. Tests cover the native
rename (`tests/test_autostart.py`, `test_autostart_unit_migration.py`); none reference
`scripts/distrobox/`.

**Verdict: CONFIRMED** (coordinator re-verified with `rtk proxy grep`, since plain `grep` is
unreliable on this machine).

**Fix direction:** render the unit name from one template shared with `service_control._SERVICE_MAP`
/ `autostart._GUI_SERVICE`, or add a test asserting every `scripts/distrobox/*.sh` contains the
literal `app-ArctisManager.service`.

---

### ENV-3 — After an upgrade on dinit, "Restart Now" restarts the GUI and not the daemon

**What breaks:** the user clicks the post-upgrade restart banner, the window relaunches showing
the new version, and the daemon keeps running the old code indefinitely — a new GUI talking to an
old daemon, the exact inversion `runtime_staleness.py`'s own docstring exists to prevent.

**Reproduction:**

```
$ .venv/bin/python -c "… with mock.patch('shutil.which', return_value=None): print(rs.restart_user_services())"
None
```

No exception, no log line.

**Cause:** `runtime_staleness.py:78` — `systemctl = shutil.which("systemctl"); if not systemctl:
return`. No dinit branch, no call into `service_control.restart()` (which handles both init
systems and is the module every other call site funnels through), and not even the
`logger.warning` `service_control` emits in the same situation. Called from
`gui/home_page.py:1350`, which then still `execv`s a fresh GUI. The same gap exists in
`scripts/restart-user-services.sh:21`, wired into `debian/postinst:12`, `aur/*.install:82` and
the RPM `%post`.

**Verdict: CONFIRMED.**

**Fix direction:** call `service_control.restart(...)` instead of hand-rolling `systemctl`; give
the shell hook a `dinitctl` branch or route it through the same single source of truth.

---

### HW-2 — `percentage()` clamps only inside the `round_to` branch

**What breaks:** a battery or mix reading on the wrong scale renders as an absurd percentage
instead of being clamped.

**Reproduction (coordinator, no hardware needed):**

```
percentage(perc_min=0, perc_max=4, value=76)              → 1900
percentage(perc_min=0, perc_max=4, value=76, round_to=10) → 100
```

**Cause:** `status_parser_fn.py:16-44` — `result = max(0, min(100, result))` sits inside
`if round_to > 1:`. Every profile calling `percentage()` without `round_to` (`media_mix`,
`chat_mix`, `station_volume`, most `headset_battery_charge` entries) is unclamped.

This is the "real 76 % rendered as 1900 %" bug that commit `7eeee6f` fixed by moving one misfiled
PID. The misfiled PID was moved; the missing clamp was never added, so any future PID filed onto
the wrong sibling profile — the exact origin that commit message itself cites, "a bare user
report" — reproduces it instantly.

**Verdict: CONFIRMED.**

**Fix direction:** clamp unconditionally, before the `round_to` snap. Behaviour-neutral for every
currently-correct profile.

---

### CHA-9 — The orphan reaper decides what to SIGKILL from `argv[0]`, which the target chooses

**What breaks:** ASM SIGTERMs and, 2 s later, SIGKILLs a process that has nothing to do with
PipeWire, if that process declares itself as `pw-loopback`.

**Reproduction (live, scan only — no signal sent):** a Python process `exec`'d with
`argv[0] = "/opt/vendor/bin/pw-loopback"` and matching `--capture-props`:

```
would kill pid=3444   node.name=Arctis_Game  REAL EXECUTABLE = /usr/bin/pw-loopback
would kill pid=122508 node.name=Arctis_Game  REAL EXECUTABLE = /usr/bin/python3.14
```

**Cause:** `loopback_manager.py:448` — `if not (exe == "pw-loopback" or exe.endswith("/pw-loopback"))`
where `exe = argv[0]` from `/proc/<pid>/cmdline`. `/proc/<pid>/exe`, the kernel's answer rather
than the process's, is in the same directory and never consulted. The same function is careful
about a *pid* being reassigned (`:491`) but not about the *identity* it matches on being
self-declared.

**Verdict: CONFIRMED.**

**Fix direction:** confirm `os.readlink(f"/proc/{pid}/exe")` resolves to the same binary as
`shutil.which("pw-loopback")`, and ideally that the uid matches, before treating a process as ours.

---

### CHA-8 — `SetSetting` skips type validation entirely when a setting's default is `None`

**What breaks:** every `SELECT` setting is declared with `None` as its default
(`settings.py:274`), so `external_output_device`, `redirect_audio_on_disconnect_device`,
`generic_output_device` and `generic_input_device` accept any JSON value at all, and it is
written to YAML.

**Reproduction (live):**

```
SetSetting external_output_device "12345"                → (true,)
SetSetting external_output_device '[1,2,{"a":null}]'     → (true,)
SetSetting redirect_audio_on_disconnect_device '{"x":1}' → (true,)
```

resulting in `external_output_device: [1, 2, {a: null}]` in `general_settings.yaml`.

**Cause:** `dbus_service.py:452` — `if config.default_value is not None and not isinstance(...)`.
The check was written for settings carrying a meaningful default; the ones carrying `None` are
exactly those whose value is a free-form device name. `_read_external_output_setting`
(`sonar_to_pipewire.py:466`) does have `isinstance(value, str)` and survives — one consumer out
of several, and the poisoned value stays in the file the others read.

**Verdict: CONFIRMED** (restored via the same method; file diffed against backup).

**Fix direction:** derive the expected type from `SettingType` — `SELECT` → `str | None`,
`TOGGLE` → `bool`, `SLIDER`/`BUTTON_GROUP` → `int` within the declared domain — not from the
default value's runtime type.

---

### CHA-10 — `inf` and `nan` travel from a shared preset into the filter-chain config

**What breaks:** nothing between an imported preset and the generated PipeWire config validates a
band's frequency, Q or gain. `Freq = inf  Q = 0.7071  Gain = nan` is written verbatim, and
PipeWire creates the node with no diagnostic at all.

**Reproduction:** a preset carrying `"frequency": 1e400, "gain": NaN` parses to
`EqBand(freq=inf, gain=nan, q=-0.0)` — Python's `json` accepts `Infinity`/`NaN` literals by
default and `1e400` becomes `inf` — and `generate_sonar_eq_conf` emits
`control = { Freq = inf  Q = 0.7071  Gain = nan }`. Loading an equivalent chain produced the node
with nothing in `journalctl -u filter-chain`. **There is no backstop below ASM.**

**Cause:** `sonar_to_pipewire.py:554` — bare `str()` of caller-supplied floats. Every neighbouring
value *is* disciplined (`boost_db` clamped at `:1194`, `level` clamped in `_sc4m_node` at `:560`,
`:.1f` elsewhere); the band literals are the one place that trusts its input.
`_parse_preset_data` (`gui/sonar_page.py:128`) does `float(f.get("frequency", 1000))` with no
range check and no `try`.

This matters more than a local bug because presets are **shared**: the import dialog and the
`asm-presets` feature make this remote, third-party content.

**Verdict: CONFIRMED** that unvalidated `inf`/`nan` reach the conf and PipeWire accepts them.
**PLAUSIBLE**, explicitly not measured, that a NaN in a biquad's feedback path poisons the channel
until the filter-chain restarts — the agent's measurement rig was invalid and it said so.

**Fix direction:** clamp in `EqBand` construction (`20 ≤ freq ≤ 24000`, `0.1 ≤ q ≤ 10`,
`-30 ≤ gain ≤ 30`) and reject non-finite values with `math.isfinite`, at the parse boundary, once,
for every producer.

---

### CHA-12 — `hrir_id` is not validated against the catalogue

**What breaks:** `SetSetting("hrir_id", …)` checks only `isinstance(value, str)`
(`dbus_service.py:433`), then `apply_hrir_choice` copies `hrir_assets/<value>.wav` over the HeSuVi
convolver's WAV. `<value>` may contain `../`.

**Reproduction:** `package_hrir_path("../../../../../../tmp/…/sine")` →
`/home/loteran/.../hrir_assets/../../../../../../tmp/…/sine.wav`.

**Cause:** `hrir_catalog.py:105` — `p = _HRIR_DIR / f"{hrir_id}.wav"; return p if p.exists() else None`.
No `resolve()`, no `is_relative_to(_HRIR_DIR)`, no membership check against `list_hrir_options()`.
No privilege boundary is crossed, so this is a robustness problem, not a security one: an arbitrary
file becomes the HRIR, the convolver fails to load,
`effect_input.virtual-surround-7.1-hesuvi` never appears, and Spatial Audio silences Game and
Media — which is issue #100, the failure `ensure_hrir_materialized` was written to prevent.

**Verdict: CONFIRMED.**

**Fix direction:** accept only ids present in the catalogue; fall back to `_DEFAULT_HRIR_ID` and
log when a saved id is unknown.

---

### CHA-13 — `SendEqCommand` validates the list length and nothing else, and the values reach the headset

**What breaks:** `dbus_service.py:332` checks `isinstance(bands, list) and len(bands) == 10`, with
no range check. `core.py:2106` then sends `list(command) + [b + shift for b in bands]` with
`shift = hardware_eq_zero - 20`, so an out-of-domain value is shifted and written to the HID
device. The array is persisted to `eq_bands.json` (`dbus_service.py:336`) *before* the send, so
it is replayed by `_apply_stored_eq()` at every daemon start.

**Verdict: PLAUSIBLE — deliberately not reproduced.** This writes to real headset firmware, which
was outside the adversary's limits.

**Fix direction:** clamp each band to the profile's declared domain in `send_eq_command`, and
validate before persisting, not after.

---

### EXT-1 — The EQ mode toggle has no global guard: one exception freezes the button and orphans every stream

*(Corrected: the agent reported "no `try`/`except` anywhere in `run()`, confirmed". That is false —
`equalizer_page.py:318-330` has two. What survives verification is below.)*

**What breaks:** `_ToggleWorker.run()` (`gui/equalizer_page.py:269-331`) emits `done` only at line
331, and its two `try` blocks cover only the last two steps (`reapply_routing_overrides`,
`notify-send`). Everything before — `_apply_yaml`, `ensure_sonar_eq_configs`,
`STATE_FILE.write_text`, `_snapshot_streams`, `sc.restart("filter-chain")`,
`DbusWrapper.recreate_loopbacks_game_media_sync()`, `_restore_streams` — is unguarded. The toggle
button is disabled before the worker starts and re-enabled only in `_on_toggle_done`, which fires
off `done`.

So an exception after the filter-chain restart succeeded but before `_restore_streams()` completes
leaves the button stuck on "restarting_audio" forever **and** every stream that was playing parked
on nodes the restart tore down — silent audio loss on the user's own click.

The four `pw-metadata` calls at `:239`, `:241`, `:255`, `:257` have neither a `try` nor a
`shutil.which()` guard, unlike every other `pw-metadata` site (`pw_utils.py:207`, `pactl.py:269`).

The contrast: the near-identical `_ApplyWorker.run()` (`gui/sonar_page.py:414`) wraps its entire
body and emits `done(False)` on exception (`:717`). `_ToggleWorker` is the outlier. No test covers
it.

**Verdict: PLAUSIBLE** (code-read, not reproduced).

**Fix direction:** wrap `run()` the way `_ApplyWorker` already is; add `which()` guards to the four
calls.

---

### ENV-2 — A blocking subprocess on the GUI thread waits on a nested elevation prompt (open #200)

**What breaks:** clicking "Install" for the BLOCKING "udev rules" check runs a synchronous
`subprocess.run(argv, timeout=120)` on the Qt **main thread**, from inside a `QProcess.finished`
slot — and the command elevates itself, opening a second prompt. The window cannot repaint for as
long as that takes, up to two minutes: indistinguishable from a crash.

**Chain:** `system_deps_checker.py:815` gives that check the command
`["asm-cli","udev","write-rules","--force","--reload"]`; `gui/system_deps_dialog.py:59` classifies
`asm-cli` as "must run un-elevated" so it is not in the dialog's own `pkexec` batch;
`gui/system_deps_dialog.py:463` runs it blocking on the GUI thread; `scripts/cli.py:165` finds it
cannot write `/etc/udev/rules.d` and calls `sudo_it()` (`:107`), which on KDE tries `kdesu` then
`pkexec` with another blocking `subprocess.run(check=True)` and **no timeout**.

**Verdict: PLAUSIBLE.** The blocking chain is confirmed by reading; that it explains #200
("GUI becomes unresponsive after installing prompted dependencies", Nobara, open) is a strong but
unverified inference — no display was available to reproduce a live freeze.

**What would settle it — ask the reporter:** `which kdesu kdesudo pkexec`, `ps -eLf | grep -E
'asm-gui|asm-cli|kdesu|pkexec'` captured *during* the freeze, and whether a second password prompt
appeared behind the main window.

**Fix direction:** never call `subprocess.run` synchronously from a `QProcess.finished` slot; route
those commands through another `QProcess` or a worker thread.

---

### INT-1 — An in-kernel `hid-steelseries` driver is coming for 25+ Arctis models

**What would break:** the kernel binds its own driver to the same vendor HID interface `core.py`
detaches and claims, reviving kernel-wide — and by design this time — the "usbhid rebind → EIO
loop → device looks offline" failure ASM already documented for the generic driver. It also
exposes ALSA mixer controls (sidetone, mic-mute, mic-volume, volume-limiter, ChatMix) and sysfs
attributes, i.e. a second, kernel-resident writer of state ASM assumes it alone owns.

**Reported:** LKML, Sriman Achanta, "[PATCH v3 00/18] HID: steelseries: Add support for Arctis
headset lineup", **2026-02-27**, <https://lkml.org/lkml/2026/2/27/1997>. Patches 02–03/18 add
`hid-ids.h` entries and `hid_have_special_driver` rows — the mechanism that decides which driver
claims the interface — for PIDs ASM already ships profiles for, including 0x2202, 0x220a, 0x2206,
0x22a4, 0x223a, 0x227a, 0x2232, 0x2253, 0x2269, 0x226d, 0x12e0, 0x12e5, 0x1260, 0x12d5, 0x12c2.
Not merged at review time (Bastien Nocera asked for further splitting). Phoronix, **2026-08-06**,
<https://www.phoronix.com/news/SteelSeries-Nova-7-5X-2026>, reports a patch queued ahead of
Linux 7.3 adding 14 of these PIDs.

**Inference:** ASM has no defence — no blacklist or modprobe handling anywhere in `scripts/`,
only the detach/claim dance in `core.py`, designed against generic usbhid rather than an actively
polling driver. Rolling distros (Arch/CachyOS) hit it first.

**Verdict: PLAUSIBLE** — architectural collision, not yet observed against ASM.

**To confirm:** watch `hid.git` for-next / `linux-input` for merge status; when it lands, boot that
kernel in a VM with a Nova 7 or Arctis 7+ and check `lsmod | grep hid_steelseries` and `dmesg` for
claim conflicts while `asm-daemon` runs. The likely answer is a udev rule unbinding the kernel
driver from the vendor interface at device-add, or a documented `modprobe.d` entry — decided
*before* that kernel reaches non-rolling distros.

---

### INT-2 — The status packet ASM sends every 2 s reportedly glitches Nova 5 audio

**What would break:** a Nova 5 / 5X user playing anything with sub-bass content hears a recurring
glitch roughly every 2 seconds, indefinitely, for as long as `asm-daemon` runs.

**Reported:** Sapd/HeadsetControl #452, "SteelSeries Arctis Nova 5 sound glitching", opened
**2026-01-07**, <https://github.com/Sapd/HeadsetControl/issues/452>. A 32 Hz bass tone distorts
while HeadsetControl runs; the reporter confirms `headsetcontrol -b` — a *single* status query —
reproduces it. Maintainer, **2026-01-08**: *"-b just sends over HID … a small status packet …
probably a hardware defect."* Three independent confirmations between **2026-02-25** and
**2026-03-29**. Still open; no Linux-specific report.

**Inference for ASM:** `nova_5.yaml:33` declares `request: 0xb0`, and `core.py:3125`
`_status_poll_loop(period: float = 2.0)` sends it unconditionally, not throttleable per device.
If the defect is firmware-level, ASM triggers it **continuously** rather than once — strictly
worse than the reported reproduction.

**Verdict: PLAUSIBLE** (mechanism match exact; no ASM report yet).

**To confirm:** ask a Nova 5/5X owner to run ASM with a 32 Hz test tone and listen for glitching in
sync with the ~2 s poll; then check whether raising `period`, or gating the poll while the radio
link has not changed, removes it.

---

### GUI-1 — `ClipsPage`'s timers are never stopped, and re-probe PulseAudio once a second

**What breaks:** `ClipsPage` starts two `QTimer`s in its constructor — `:612` at 1000 ms and `:622`
at 5000 ms — and nothing ever stops them. `closeEvent` exists in exactly one file in `gui/`
(`clip_editor.py`, verified with `rtk proxy grep`), no widget sets `WA_DeleteOnClose`, and
`scripts/gui.py:218` sets `setQuitOnLastWindowClosed(False)`, so closing the window only hides it
and the page lives on in the tray process. The page is built eagerly at window construction
(`main_app.py:369`), so this runs for a user who never opens the Clips tab.

The cost is real in two states: `_update_status()` (`:1311`) calls `detect_game()` — a PulseAudio
round trip — on **every 1 s tick** while a capture has no `_game_label` yet, and `_poll_game()`
(`:865`) does the same every 5 s whenever Clips autostart is on (it returns early only on
`self._closing or not self._autostart.isChecked()`, `:883`).

This is the twin of #182, which was fixed for the home page only.

**Verdict: PLAUSIBLE** (read, not reproduced — would need a live capture).

**Fix direction:** stop both timers on hide and restart on show; cache the game label instead of
re-probing per tick.

---

### The remaining findings, in brief

**PKG-2** — `.github/workflows/pacman-repo.yaml:83` marks the AUR-dependency build
`continue-on-error: true`, and the loop at `:115` treats `python-pulsectl` — a hard dependency, per
the file's own comment at `:111` — exactly like the optional `noise-suppression-for-voice`: a
failure only warns. `verify_release_delivery.py:137` checks only that `arctis-sound-manager` is in
the repo database, not its AUR-only dependencies, so the audit reports green while a fresh install
is unresolvable. This is the #178 pattern, for the package ASM cannot run without. *PLAUSIBLE.*
**Ask yourself:** has `python-pulsectl`'s build ever emitted a `::warning::` in a past run?

**PKG-3** — `scripts/setup.py:256` `_ensure_filter_chain_service()` copies
`scripts/filter-chain.service` into `~/.config/systemd/user/` once; on later runs the
`list-unit-files` check finds the copy and returns without ever diffing it against the packaged
one. No version marker — the "local copy wins, never migrated" shape that cost you months on device
profiles. Dormant only because that file has never been revised (one commit, `118e649`). *PLAUSIBLE.*
The mechanism it lacks already exists next door: `# ASM-CONF-VERSION` + `check_and_fix_stale_configs()`.

**SD-2** — `service_control.py:259` `nrestarts()` returns `None` on non-systemd by design, so
`sonar_to_pipewire.py:1013`'s check 2 (`n_restarts >= 3`) is unreachable on dinit. A filter-chain
that crash-loops while appearing active never arms safe mode there. *PLAUSIBLE.*

**HW-3** — `nova_4.yaml:8` states in its own header that `command_interface_index: [3, 0]` is "NOT
YET CONFIRMED ON HARDWARE" for Nova 4 (0x12f2) and 4X (0x12f6), and that the vendor spec describes
opcodes but not USB interface layout. If it is wrong, every control is silently inert — the Nova Pro
Omni (#70) precedent, wrong for two months. *PLAUSIBLE, self-disclosed.* **To confirm:** ask an owner
for the USB interface descriptors and whether any control has an audible effect.

**EXT-2** — `scripts/setup.py:92-200` `_setup_dinit_services()` makes ~12 raw `dinitctl` calls
guarded only by `except subprocess.TimeoutExpired`, never `FileNotFoundError`, bypassing
`service_control._run()` (`:103`) which handles it correctly. On a dinit box where `dinitctl` is
absent from `asm-setup`'s PATH, setup dies with an uncaught traceback partway through. *PLAUSIBLE.*

**ENV-4** — the Distrobox `--volume` mounts for `/run/asm-hidraw`, `/dev/bus/usb` and the PipeWire
sockets carry `rslave` (a propagation flag) but no `:z`/`:Z` SELinux relabel option
(`bazzite.sh:100-106` and siblings). On an enforcing host the container can be denied access while
install and health check both report success. *PLAUSIBLE.* **To confirm:** `sudo ausearch -m avc -ts
recent | grep -i asm` and `getenforce` from a Silverblue reporter.

**HW-4** — `nova_elite.yaml:77-142` maps only individual `0x07xx` async pushes, with no
`starts_with: 0x01b0` combined synchronous reply, unlike `nova_pro_wireless.yaml:88` (`0x06b0`) and
`nova_pro_omni.yaml:129` (`0x01b0`). If this DAC does answer its own status request with a combined
frame, that frame is dropped and every status row stays blank until each control pushes once.
*PLAUSIBLE, low confidence — an asymmetry, not a proven gap.*

**INT-3** — JerwuQu/ggoled #26 (opened **2025-12-17**, still open,
<https://github.com/JerwuQu/ggoled/issues/26>) carries a real Windows HID report-descriptor dump for
Nova Elite PIDs 0x2244/0x2249; the maintainer's read on **2025-12-18** was *"the report descriptors
look a bit different so I have low hopes it would work as-is"*. `nova_elite.yaml` already flags its
own OLED block as tentative — this is the first external, hardware-backed datapoint confirming that
doubt. *REPORTED.* Note the same agent **cleared** the Nova Pro Omni: ggoled #33 converged
independently on interface 3 / report id 1, which is what `nova_pro_omni.yaml` already documents as
confirmed.

**INT-4** — WirePlumber 0.5.15 (**2026-06-18**) introduced a centralised `WpPermissionManager` for
client access control and fixed portal clients being un-gated after permission setup. It is already
the version on Arch/CachyOS, and the #181 permission-repair work landed in the same window — worth a
deliberate re-test rather than assuming it still applies. Also 0.5.12 added automatic ALSA muting
when a node is removed: suspect that first if a "mysteriously muted after power-cycle" report ever
appears, since WirePlumber may now be the one muting. *Watch-item.*

**PKG-4** — Nobara ships ASM enabled by default through a Terra-maintained spec you do not control,
and `scripts/verify_release_delivery.py:203`'s `CHANNELS` cannot see it. Hard-dependency promotions
(e.g. `ladspa-swh-plugins` from `Recommends:` to `Requires:`, made precisely because existing users
would otherwise stay broken) reach Nobara only when Terra catches up, and nothing would surface a
lagging spec. *PLAUSIBLE, structural.*

**EXT-3** — `gui/system_deps_dialog.py:460` `_shell_quote()` quotes an argument only if it contains a
literal space, never escapes embedded quotes, and feeds `pkexec sh -c`. Every current input is a
hardcoded package name, so there is no live injection path — but it *looks* like a quoting function,
guards a privilege-escalated batch, and `scripts/cli.py` already does the same job correctly with
`shlex.quote`. *PLAUSIBLE, landmine for the next contributor.*

**SD-3** — `usb_devices_monitor.py:84` picks the pyudev backend once and never checks afterwards that
events are still being delivered, with no fallback to the polling loop that exists for this. If the
netlink observer dies (a udevd restart in a sandboxed session, a container namespace change), replug
and power-cycle stop being noticed at all — and nothing logs the absence of expected events.
*PLAUSIBLE, low confidence.*

**PKG-5** — the same `build-deb.sh` `Depends:` also omits `python3-babel`. This one degrades
gracefully: `i18n.py:40` catches the `ImportError` and falls back to an English-only plural rule with
a warning. Wrong plural forms in every non-English locale, no crash. *CONFIRMED*, listed separately
so it is not conflated with PKG-1.

---

## 3. Blind spots

What the team could not reach, and why. These are not "nothing found" — they are "not looked at",
and they are where the next report from a user will come from.

- **Every headset except the Nova Pro Wireless.** Nineteen of twenty profiles were audited by
  reading them against the vendor specs; none was exercised against hardware. HW-1, HW-3, HW-4 and
  CHA-13 all end at a question only an owner can answer. The three commands to ask for are written
  into each finding.
- **Reddit.** `field-intel`'s JSON search returned **403 to every query**, including site-restricted
  ones. There are no Reddit findings in this report, and that means *could not reach*, not *nothing
  there*. The AUR page was fetched successfully: 1.4.4-1, zero comments.
- **Every distribution except CachyOS.** SteamOS, Bazzite, Silverblue, Nobara, Ubuntu and dinit
  findings (ENV-1 through ENV-4, SD-2, EXT-2, PKG-2) are code-level; none was run on the target
  system. ENV-1 and ENV-3 are `CONFIRMED` only because they are provable by reading — a name that
  does not match, a function that returns early.
- **The GUI under a real display.** ENV-2's freeze (#200) and GUI-1's timers were read, not seen. No
  Qt event loop was driven during this audit.
- **The HID write path.** CHA-13 stops at the D-Bus boundary by design: the adversary was forbidden
  from writing to real firmware. Whether an out-of-range band is clamped by the headset, ignored, or
  latched is unknown, and issue #70's history says "silently ignored" is a real possibility.
- **The NaN-biquad audio effect.** The adversary's measurement rig was invalid — the unity control
  peaked *lower* than the NaN runs, so the tap was not capturing the chain's output. It reported the
  conf-level fact and refused to claim the audio result. Whether a NaN poisons the channel until the
  filter-chain restarts is open.
- **Whether PipeWire clamps `force-quantum`.** CHA-2 rests on acceptance and persistence, not on the
  audible result: `pw-top -b -n 1`'s first batch reports `QUANT 0` for every node, so the attempt to
  measure whether `force-quantum = 1` degrades or destroys audio produced nothing usable.
- **A second daemon, end to end.** CHA-4's sequence was not executed — the user was mid-game. The
  ordering and the reaper's selection are established; the destruction is inferred from them.

---

## 4. What held

Attacks that found nothing, and the code observation that inspired each. A failed attack on a sound
hypothesis says more about where the code is solid than a successful one on a known case.

**Attacked by the adversary, and defended:**

- **A malformed `.conf` fragment does not take the whole filter-chain down.** Inspired by the
  non-atomic `path.write_text` at `sonar_to_pipewire.py:1959`: one truncated fragment should have
  failed the merged parse and killed every channel. PipeWire rejects the fragment alone
  (`error in config '…zz-chaos-truncated.conf': Mismatched bracket`) and the other 14 `effect_*`
  nodes loaded normally. This bounds CHA-7's blast radius to one channel — a real margin in the design.
- **Locale cannot corrupt the generated configs.** Inspired by the bare `{gain}` interpolation at
  `:554`. Python float formatting is locale-independent, and `grep -rn "setlocale\|LC_NUMERIC" src/`
  returns nothing — `LC_ALL=de_DE.UTF-8` cannot turn `Gain = -3.5` into `Gain = -3,5`. Sound
  hypothesis, correct code. Independently confirmed by `external-tooling`.
- **A Turkish locale cannot break the name comparisons.** Inspired by `_LINK_DENIED in err.lower()`
  and `"hdmi" in s.name.lower()`. `str.lower()` is not locale-sensitive in Python.
- **A hostile string cannot be injected into a config through a preset.** Inspired by the same
  unquoted `{freq}`. A preset carrying `"frequency": "1000 } Gain = 99 } } , { type = builtin …"` is
  stopped by `float()` in `_parse_preset_data` — an accidental but effective injection barrier. (It
  is not a *validity* barrier: see CHA-10.)
- **Destroying only a loopback's capture node leaves no invisible zombie.** Inspired by
  `is_running()` (`loopback_manager.py:654`) checking `proc.poll()` only, while
  `ensure_loopback_link` looks only at the playback node — the hope was to kill the sink the user
  sees while ASM reported perfect health. `pw-loopback` exits when its capture node is destroyed, so
  `restart_dead()` catches it within 5 s.
- **`_capture_node_name` resists the two confusions it was written for** (`loopback_manager.py:377`):
  `target.object=Arctis_Game` vs `node.name=Arctis_Game`, and `Arctis_Game_sink_out` vs
  `Arctis_Game`. The weakness is one level up, in `argv[0]` (CHA-9), not here.
- **`stream_guard.load_config` is properly hardened.** Inspired by `CONFIG_FILE` being user-writable
  JSON that drives a link-destroying daemon. It type-checks the top level and `channels`, drops
  unknown channel names (`stream_guard.py:63`) and fails **closed**. This is what the rest of the
  config surface should look like.
- **`_terminate_orphan_pid` cannot SIGKILL a recycled PID** (`loopback_manager.py:497`): it re-reads
  `/proc/<pid>/cmdline` and compares the node name before escalating. Correct — and the direct
  contrast that makes CHA-3 worth fixing.

**Verified sound by the other agents:**

- **The #181 fix is right, including the part that is easy to get wrong.** Live:
  `pw-cli -- permissions 999999999 -1 rwxml` → error on stderr, **exit 0** — and `pw_utils.py:177`
  checks `returncode == 0 and not err`, so the lying exit status does not fool it.
  `tests/test_link_permissions.py` pins the argv *shape* (`assert argv.index('--') < argv.index('-1')`),
  which is your own heuristic applied correctly.
- **`pw-cli`, `pactl` and `systemctl` output is not localised** — byte-identical under `de_DE.UTF-8`
  and `C`. The substring parsing scattered across `equalizer_page`, `sonar_page` and
  `service_control` is safe.
- **`clip_thumbs._extract:149` does not trust ffmpeg's exit code** ("ffmpeg exits 0 after encoding
  nothing when a seek lands past the end") and checks the output file size instead — the
  exit-0-with-failure class, handled.
- **`clip_library._trash:348`** uses `shutil.which` + `--` before a user-controlled path: the #181
  pattern applied *preventatively*, to a clip filename that starts with `-`.
- **Orphan `pw-loopback` reaping (#84) has a real guarantor** — `loopback_manager.py:406-512` scans
  `/proc` on every `start()`/`restart_dead()`, with a re-identify-before-SIGKILL race guard.
- **Every loopback→EQ→HeSuVi→physical hop is re-checked every tick** with escalation counters feeding
  `ensure_filter_chain_healthy()`. This is the best-covered part of the codebase.
- **HeSuVi conf staleness is versioned** (`# ASM-CONF-VERSION`, `_CONF_VERSION = 4`) and checked on
  every daemon start, deliberately scoped to avoid flattening user EQs — a correctly reasoned
  trade-off, and the mechanism PKG-3 lacks.
- **The device-profile shadowing bug is properly fixed** — `device_override_reconcile.py` runs on
  every daemon start.
- **udev rules are diffed against the current device set** (`udev_checker.get_udev_rules_status()`),
  wired in as a BLOCKING check: the "generated file never regenerated after upgrade" trap, handled.
- **Two daemons racing for the D-Bus name fail loudly** (`dbus_service.py:676`) with a remediation
  command — though see CHA-4 for what happens 36 lines earlier.
- **#132 (non-ASCII filenames) is genuinely fixed** and locked by `tests/test_repo_ascii_filenames.py`
  asserting via `git ls-files -z`; independently re-verified with a Python walk.
- **Product-ID coverage has no gaps:** all 54 PIDs across the 20 profiles were diffed against every
  non-bootloader PID in the 90 vendor `.device` files. Zero missing.
- **The sidetone hardcoded-state-byte bug (#161/#201) is fixed everywhere**, not only where it was
  reported — all 15 profiles exposing sidetone derive the state from the setting.
- **`autostart.py` branches across systemd / dinit / XDG / Hyprland / Sway** with a `~/.xprofile`
  fallback and a migration for the unit rename — thorough and tested (the Distrobox scripts, ENV-1,
  are the gap).
- **No hardcoded UID** anywhere; every runtime-dir resolution goes through `os.getuid()` /
  `$XDG_RUNTIME_DIR`.
- **`pw_quirks._wireplumber_version()`** gates a Lua5-only fragment on WirePlumber ≥ 0.5 and logs at
  `info` when it cannot apply — the #154 lesson, applied.
- **Uninstall cleanup is consistent across `.deb`, `.rpm` and AUR**, distinguishes upgrade from
  removal, and preserves `~/.config/arctis_manager/profiles/`.
- **`config.py:126` `ConfigSetting.get_update_sequence` is dead code** — it would raise on any
  `'value.enabled'` token, but nothing calls it (`core.py` uses its own `_resolve_update_sequence`).
  A maintenance trap, not a live bug.

---

## 5. Not restored

Everything the adversary changed was put back and verified — filter-chain confs byte-identical by
`cmp`, `routing_overrides.json` byte-identical, `clock.force-quantum` back to `0`, no leftover nodes
or processes, the Output channel back on the TV, the DualSense haptics link back in place, all
services `active`, repository clean at `d7328d4`. Two exceptions, declared:

1. **`general_settings.yaml` gained one line: `pipewire_quantum: 0`.** It was absent before (the
   class default is already `0`), so the file is semantically identical but not byte-identical.
   Deleting the line would only bring it back on the next write, since the daemon holds it in memory.
2. **Two `filter-chain.service` restarts and one extra `pw-metadata` write are in the journal.**
   Visible history, nothing persistent.

One incidental observation from the restore, worth its own look: the video router had learned
`"pw-cat": "effect_input.chaos-nan"` in `routing_overrides.json` from a probe node that no longer
exists. The adversary removed it. **Nothing prunes overrides pointing at nodes that are gone** —
`_reachable()` (`video_router.py:486`) stops them being *applied*, but the file accumulates them
indefinitely.

Full pre-session backups of `~/.config/arctis_manager` and `~/.config/pipewire` are under
`/tmp/claude-1000/-home-loteran/c90b6d6c-97ae-4298-be19-10e753dfb2bc/scratchpad`.

---

## 6. Two corrections to the team's own work

Kept visible on purpose — both are the exact failure mode this report exists to catch.

1. **`external-tooling` claimed `_ToggleWorker.run()` has "no `try`/`except` anywhere in the method
   — confirmed by grepping".** It has two (`equalizer_page.py:318-330`). The finding survives in
   corrected form (EXT-1: no *global* guard, `done` emitted only at `:331`, four unguarded
   `pw-metadata` calls), but the justification was wrong, and the likely cause is the same one
   `state-drift` hit twice independently: **plain `grep` and `ls` return falsified output on this
   machine.** Every load-bearing claim in this report was re-checked with `rtk proxy` or Python.
2. **`hardware-matrix` initially reported the exact displayed string for HW-1 as CONFIRMED.** It
   corrected itself: zero-filled unused bytes are the *likely* case, not a measured one, and leftover
   firmware buffer content would produce flickering wrong values instead of a stuck one. The
   distinction is preserved in the finding.
