# Hardware and upstream questions

Six items from `RAPPORT-CHAOS-ASM.md` (2026-08 chaos audit) that code alone
cannot settle: no one on the project owns the hardware, or the answer depends
on an upstream project's release/merge state. For each: what's unknown, why
it matters, the exact thing to ask for, who to ask, and what answer closes it
either way.

This document is meant to be pasted from when answering an issue — each
section stands alone.

---

## HW-3 — Nova 4 / 4X: is `command_interface_index: [3, 0]` correct?

**File:** `src/arctis_sound_manager/devices/nova_4.yaml:8` (header) and `:31`
(the field itself). PIDs `0x12f2` (Nova 4), `0x12f6` (Nova 4X).

**What's unknown:** which USB interface the vendor HID command channel lives
on. The profile currently guesses interface 3, alt-setting 0 — the value
every other recent Arctis dongle uses (Nova 5, Nova 7 Gen 1/Gen 2, Nova Pro
Omni) — but nothing has confirmed it for the Nova 4 specifically.

**What was checked (2026-08-22, this session):** grepped all 127 files under
`~/steelseries-research/decoded-115/` — every Arctis product line SteelSeries
ships a spec for, not just this one — for any USB interface/descriptor
reference (`interface`, `bInterfaceNumber`, `interfaceIndex`, etc.). Zero
hits anywhere except one unrelated comment ("Sync Interface events", a
software concept, not a USB one). **The GG device-spec format itself does not
carry USB interface layout for any headset in the corpus.** This rules out
"read the spec harder" as a path to an answer — it isn't a Nova 4 gap, it's a
format limitation. No code change is possible from this evidence; the
existing "NOT YET CONFIRMED ON HARDWARE" comment in the YAML has been
extended with this finding.

**Why it matters:** if the interface index is wrong, every control write on
this device is silently swallowed — the headset does not error, it just does
nothing. This is exactly what happened to the Nova Pro Omni (issue #70) for
two months before anyone noticed, because a wrong interface index produces no
symptom a user would think to report as "wrong interface."

**Who to ask:** a Nova 4 or Nova 4X owner. There is no existing GitHub issue
to attach this to — if/when one opens (or proactively, in a "which headsets
do you own" call), this is the ask.

**Exact commands to request** (all read-only, no writes to the device):

```bash
lsusb -v -d 1038:12f2   # or 1038:12f6 for the 4X
```

Look for the interface whose `bInterfaceClass` is `3` (HID) and note its
`bInterfaceNumber` and `bAlternateSetting`. Also worth asking for, since it is
zero-risk and answers the question at the same time as a live functional
test:

```bash
# with asm-daemon running and the profile's guess (interface 3) in place
asm-cli device settings   # or: toggle mic mute / sidetone in the GUI
```

**What would settle it:** if `lsusb -v` shows the vendor HID interface at
number 3 — settled, confidence upgraded, comment can drop "NOT YET CONFIRMED".
If it shows a different number, or if a control genuinely has no audible/
visible effect on real hardware with interface 3, that number is the fix —
update `command_interface_index` in `nova_4.yaml:31` to match, the same way
the Nova Pro Omni was corrected for #70.

---

## HW-4 — Nova Elite: does the DAC answer its own status request?

**File:** `src/arctis_sound_manager/devices/nova_elite.yaml`.

**Resolved with a sourced fix (2026-08-22, this session).** The base worry
was real: `status.request` sends `0x01b0`, but until this session nothing in
`response_mapping` parsed a reply starting with `0x01b0` — the combined
synchronous frame, if the DAC sends one, was dropped on the floor, and every
status row stayed blank until the matching control happened to fire its own
individual `0x07xx` async push.

`~/steelseries-research/decoded-115/base_arctis_nova_elite_tx.device` settles
this: it defines `(struct wireless_settings)`, an **incoming** struct at
command `0xB0` with `report_id = (tx_report_id)`, and `tx_report_id` is
defined earlier in the same file as `0x01` — i.e. wire bytes `0x01 0xb0`,
exactly what `status.request` already sends. The struct lists 15 fields in
order (`bt_power_default`, `bt_call_default`, `bt_connection_mode`,
`bt_connection_status`, `headset_batt_level`, `charger_batt_level`,
`transparent`, `mic_mute`, `transparent_anc_mode`, `muted_mic_brightness`,
`inactivity_timer`, `wireless_mode`, `radio_connection_status`,
`charging_status`, `active_noise_cancellation`) — a byte-for-byte, field-for-
field match to the `0x06b0` block already confirmed for the Nova Pro Wireless
and the `0x01b0` block already confirmed for the Nova Pro Omni. Same GameDAC
Gen 2 station firmware family, confirmed from this device's *own* spec file,
not by analogy with those siblings.

**What was changed:** `nova_elite.yaml` gained a `starts_with: 0x01b0` block
mapping 14 of those 15 fields onto the names this profile already uses for
them (taken from its existing `0x07xx` async pushes, not borrowed from the
Omni profile). The 15th field, `bt_connection_mode` (spec offset `0x04`, values
1=off/2=pairing/4=link), is deliberately left unmapped: nothing in this
profile has ever named it, so there is no established consumer to wire it
into — see the profile's own comment at that line.

**What remains open:** this is a spec-sourced, structurally-confident fix,
but it has not been observed on real hardware — no one on the project owns a
Nova Elite. Two things an owner's log would confirm:

1. That the combined `0x01b0` reply actually arrives (some DACs are known to
   accept a status-request command but only answer with individual pushes,
   spec notwithstanding — the report's own framing of this as "an asymmetry,
   not a proven gap" still applies until observed).
2. That the byte values ASM now reads line up with reality — in particular
   whether `bt_connection_mode` (offset `0x04`, currently skipped) is ever
   worth exposing, and whether the existing `headset_power_status` labels at
   offset `0x0e` (`0x01: offline, 0x02: cable_charging, 0x04: standby, 0x08:
   online`) are right. That labeling was not touched by this session — it
   predates this fix, is used identically by the async `0x07b5` push
   (unaffected by this change), and the vendor spec's own labels for that
   field ("HS unpaired & exits pairing" / "…enters pairing" / "HS paired &
   disconnects" / "…connects") don't obviously say "cable_charging" for
   value `0x02`. That mismatch is **not fixed here** — it's outside this
   session's six items and touching status *labels* without a hardware
   read-back is exactly the kind of unsourced protocol change this repo has
   been burned by. Flagging it for whoever picks up the Nova Elite next.

**Who to ask:** a Nova Elite owner (PIDs `0x2244`/`0x2246`/`0x2249`/`0x2270`).

**Exact commands to request:**

```bash
# with the daemon running and this fix in place
journalctl --user -u asm-daemon -f | grep -i "0x01b0\|status"
# or, more directly: a debug build / log line at the point read_hex_str
# is compared against response_mapping (core.py, listen_endpoint_loop)
```

Simplest real-world test: launch `asm-gui` fresh (headset already on,
nothing touched), and check whether the headset/mic/gamedac status rows are
populated **immediately**, before touching any slider. Before this fix they
would have been blank until the first interaction; after it, if the DAC
really answers `0x01b0` combined, they should populate at daemon start.

**What would settle it either way:** rows populated at start with sane
values = confirmed. Rows still blank at start = the DAC does not answer with
a combined frame despite what its own spec declares (firmware behaves
differently from the spec on this point, which happens) — revert to
individual-push-only and note that explicitly, the way the profile already
notes other spec/reality gaps.

---

## INT-1 — In-kernel `hid-steelseries` driver: what changes for ASM

**Files (read, not edited — out of scope for this task; described here
instead):** `src/arctis_sound_manager/core.py:2686` (`kernel_detach`),
`core.py:2997` (`kernel_attach`), `core.py:1397-1502`
(`listen_endpoint_loop`, including the EIO recovery path at `:1477-1495`).

**Status as of 2026-08-22 (this session, web search):** the patch series has
moved since the report's 2026-08-06 citation. Per Phoronix
("SteelSeries Nova 7/5X headsets to be supported by Linux 7.3", 2026-08-06,
quoting HID maintainer discussion): *"That patch is now in the HID
subsystem's 'for-next' Git branch and thus expected to be submitted for the
upcoming Linux 7.3 merge window."* Independently checked (2026-08-22): Linux
7.2 shipped 2026-08-16, so **the 7.3 merge window is open right now** —
first RC due 2026-08-30, stable release expected mid/late October 2026. Per
Patchew's copy of the v3 thread (`patchew.org/linux/...srimanachanta...`),
reviewer Bastien Nocera's only blocking-ish note was that "HID: steelseries:
Add async support and unify device definitions" (patch 04/18) needs further
splitting for reviewability; he called the rest "good... from a cursory
glance" and flagged wanting more eyes specifically on the sound code (patches
10/18 "settings poll infrastructure", 11/18 "sidetone ALSA mixer control").
**This is closer to landing than the report characterized it, and on a
timescale (weeks to for-next, ~2 months to stable) that matters for planning,
not just watching.**

**What this session worked out (read of `core.py`, not a hardware test):**

`kernel_detach()` operates at the **USB interface** level via
`usb_device.detach_kernel_driver(interface)` / `usb.util.claim_interface(...)`
— this is libusb's `USBDEVFS_DISCONNECT`/`USBDEVFS_CLAIMINTERFACE`, which
detaches whatever is bound to the *interface* (today: `usbhid.ko`, which is
what tears down the higher-level `hid-generic` binding on top of it). Because
`hid-steelseries.ko` will bind on the same layer `usbhid.ko` occupies today
(a specific-driver match in `hid_have_special_driver`, not a separate USB
class driver), `kernel_detach()` should mechanically still work against it —
the same ioctl detaches whichever driver is bound, by design, without caring
about its name. **This is not the part that changes.**

What genuinely changes, and is not covered by the existing code:

1. **The boot-race window gets a live occupant instead of a passive one.**
   Today, between device plug-in and `asm-daemon` calling `kernel_detach()`,
   nothing on the vendor interface does anything (`usbhid`+`hid-generic`
   just sit there). Once `hid-steelseries` is bound automatically at
   plug-in (it will be, the moment its module is loaded — no user action),
   that same window has an **actively polling** driver on it (patch 10/18),
   which can read/write device state before ASM ever gets to
   `kernel_detach()`. The failure mode this produces is not "detach fails"
   — it's "ASM's very first `status.request` at `device_init` may race a
   kernel-driver-issued command that already changed a volatile flag on the
   headset."
2. **A second, persistent writer of state, only while `hid-steelseries` is
   bound.** Patches 10/18 and 11/18 add ALSA mixer controls (sidetone at
   minimum) and, per the report, sysfs attributes. While the kernel driver
   holds the interface (i.e., before ASM's daemon starts, or any time
   `kernel_detach()` fails and is not retried — see `_note_status_poll_error`,
   `core.py:3165` area, which already logs but does not escalate to a
   user-facing fix), a user can change sidetone via `alsamixer` and have it
   actually work. The moment ASM successfully claims the interface,
   `hid-steelseries.ko`'s `.remove()` should tear its ALSA controls down
   (normal kernel driver lifecycle) — so this is not a silent-double-write
   race so much as a **surprise**: a control a user was just using
   disappears, and `device_init`'s `settings.*` replay (e.g.
   `nova_elite.yaml:29` `[0x01, 0x8d, 'settings.sonar_enabled']`-style
   entries) then pushes ASM's own remembered value over whatever
   `alsamixer` had just set, with nothing telling the user why.
3. **`is_kernel_driver_active()` cannot name the driver.** libusb's
   `kernel_driver_active` call (what `core.py:2704` uses) returns a bool,
   not a driver name — there is no portable libusb API for "which driver".
   Distinguishing "usbhid got there first" from "hid-steelseries got there
   first" (which changes what remediation makes sense to tell a user) would
   need a Linux-specific read of
   `/sys/bus/usb/devices/.../<interface>/driver` — a capability `core.py`
   does not have today. Worth having once the driver actually ships, not
   before: it is dead code against a driver nobody can install yet.

**The real fix direction (for whoever owns `core.py` when this lands):**
close the boot-race window with a udev rule, not a runtime detach-after-
claim. The standard mechanism other Linux userspace HID tools use for this
exact problem (a kernel driver they don't want auto-claiming a vendor
interface) is `driver_override` on the `hid` bus, written by udev at `add`
time — before any driver has probed — rather than detach-after-bind in
userspace. ASM already ships and manages a udev rules file
(`udev_checker.get_udev_rules_status()`, per the "what held" section of the
report); this is the natural place to add a rule keyed on ASM's known vendor
PIDs once `hid-steelseries`'s ID table is public (patches 02/18, 03/18
already list it). This needs `core.py`/`scripts/` changes and is out of this
task's file scope — described here as the concrete next step, not
implemented.

**Who to watch:** `linux-input`/`hid.git` for-next (merge status),
`torvalds/linux` `drivers/hid/hid-steelseries.c` (once merged, for the
device-ID table and whether `.remove()` genuinely tears down ALSA controls
cleanly — that's the assumption point 2 above rests on and it has not been
read from the actual driver source, only inferred from normal kernel driver
conventions).

**Exact thing to do once it lands (no ASM commit needed to check this,
no headset needed either — a spare Arctis dongle plugged into a VM is
enough, or even just the module loaded with no device):**

```bash
# once a kernel with hid-steelseries is available
modinfo hid_steelseries | grep alias    # confirm which PIDs it claims
lsmod | grep hid_steelseries
dmesg | grep -i steelseries
# with asm-daemon running against a real or spare device:
udevadm info -a -p $(udevadm info -q path -n /dev/bus/usb/BBB/DDD) | grep DRIVER
```

**What would settle it:** `DRIVER=="hid-steelseries"` visible right after
plug-in, followed by `asm-daemon` starting and `kernel_detach()` succeeding
(check the daemon log for "Detaching kernel driver" without a following
EACCES), with no EIO storm afterward — confirms the existing mechanism holds
and only the udev-rule hardening above is worth doing proactively, not
urgently. Repeated EIO after detach, or `hid-steelseries` reappearing in
`lsmod`'s bind list for that device without ASM restarting, means the driver
re-probes on its own timer and the boot-race concern is a running problem,
not just a boot-time one — escalates the udev-rule fix from "worth doing" to
"blocking".

---

## INT-2 — Does ASM's 2 s status poll glitch Nova 5 audio at 32 Hz?

**Files:** `src/arctis_sound_manager/devices/nova_5.yaml:33` (`request:
0xb0`), `core.py:3191` (`_status_poll_loop(period: float = 2.0)` — sends
`request_device_status()` unconditionally every 2 s while a device is
attached; note the report cited `core.py:3125`, which in this checkout is
inside the neighbouring `_xrun_watch_loop`/status-poll-logging block, a few
hundred lines from the loop itself — line-number drift between the report's
checkout and this one, not a wrong claim).

**Corroboration (2026-08-22, this session, via `gh` — read-only, no
reproduction attempted, no hardware touched):**

- `Sapd/HeadsetControl#452`, "SteelSeries Arctis Nova 5 sound glitching",
  opened 2026-01-07, **still open**, no comments past 2026-03-29 (i.e.
  nothing has changed since the report's own reading of it). Maintainer
  (Sapd), 2026-01-08: *"-b just sends over HID... a small status packet...
  probably a hardware defect."* Three independent "same problem" replies:
  BlueKnight137 (2026-02-25), WondenanGw (2026-03-03), Ferohers (2026-03-29).
- Fetched the exact code HeadsetControl's `-b` flag runs
  (`lib/devices/protocols/steelseries_protocol.hpp`, commit
  `d59c696898afc4b46639cc4d1f3b2be8cdf0673e`, the version the maintainer
  linked): the Nova-family status request is `std::array<uint8_t, 2>
  request { 0x00, 0xb0 }` — report id `0x00`, command `0xb0`. This is
  **byte-for-byte identical** to what `nova_5.yaml:33` declares
  (`request: 0xb0`, with this profile's `command_padding` using report id
  `0x00`). The mechanism match the report called "exact" is confirmed at the
  byte level, not just by description.

**Why it matters, sharpened:** HeadsetControl's `-b` sends this packet
**once** and the glitch is already reproducible. `_status_poll_loop` sends
the identical packet **every 2 seconds, unconditionally, for the entire time
a Nova 5/5X is connected to ASM** — for as long as `asm-daemon` runs, not
just once. If the defect is real and firmware-level (which the maintainer,
who did not go further than "probably," has not confirmed either), ASM is
strictly worse than the reproduction case already on file, and continuously
rather than on-demand.

**Who to ask:** a Nova 5 or Nova 5X owner (PIDs `0x2232`, `0x2253`, `0x2255`,
`0x2264`) — no existing ASM issue to attach this to; if one opens, or
proactively to a known owner:

**Exact test to request:**

```bash
# Play a steady 32 Hz test tone (e.g. `speaker-test -t sine -f 32`, or any
# sub-bass test track) through the headset with asm-daemon running normally.
# Listen for a glitch/click recurring roughly every 2 seconds.
# Then, to isolate the poll specifically from anything else ASM does:
systemctl --user stop asm-daemon    # or the dinit/other equivalent
# replay the same tone with the daemon stopped entirely — no HID traffic at all
```

If it glitches only while the daemon runs and stops when the daemon is
stopped, that isolates it to *something* ASM does — not proof it's the poll
specifically (device_init, EQ pushes, and other periodic HID traffic are
also daemon-only). The decisive version:

```bash
# temporary local test only — NOT a proposed default, and this session did
# not make this change (core.py is out of scope for this task)
# raise `period` in _status_poll_loop from 2.0 to e.g. 10.0 and re-test
```

**What would settle it either way:** glitch present at 2 s period, absent
(or clearly less frequent) at a much longer period, with everything else
unchanged = confirms the poll is the trigger, and `period` becomes tunable
per-device (or gated on "radio link unchanged" as the report suggests) as
the fix. Glitch present regardless of period, or present with the daemon
stopped entirely = rules out the poll, points back to "hardware defect"
exactly as HeadsetControl's maintainer guessed, and ASM has nothing to fix.

---

## INT-3 — Nova Elite OLED: what the ggoled report-descriptor dump says

**File:** `src/arctis_sound_manager/devices/nova_elite.yaml` (`oled:`
section) — **updated in this session**, see below.

**What was in `JerwuQu/ggoled#26` (fetched in full via `gh`, 2026-08-22) —**
the thread moved substantially past what the report describes, which only
captured its 2025-12-18 state:

- 2025-12-18, JerwuQu (maintainer): *"The report descriptors look a bit
  different so I have low hopes it would work as-is."* — this is the quote
  the report cites, and it was the state of the art for seven months.
- 2026-02-20, JerwuQu: still no support, does not own the hardware, asks
  for a volunteer.
- **2026-07-19, macrooli (external contributor, owns a real Nova Elite):**
  got display output working, opened `JerwuQu/ggoled#35` with the fix,
  **merged the same day**, tested against real hardware (`clear`, `fill`,
  `text`, `img`, `brightness`, `return`, and the `ggoled_app` tray daemon,
  all confirmed working). Two concrete, hardware-derived facts:
  1. *"The base station (PID `0x2244`) exposes its OLED/info HID
     collections on interface 3, not interface 4 like the Nova Pro.
     Interface 4 here is an unrelated consumer-control/media-key
     interface."*
  2. *"The Nova Elite declares report ID 1 for the OLED collection (Col01)
     and report ID 7 for the info collection (Col02) — sending report ID 6
     fails with `HidD_SetFeature: The parameter is incorrect` on
     Windows."*
  3. **Known gap, stated by macrooli themselves:** the info-event parsing
     (battery/volume readouts) in ggoled still assumes report ID 6 in
     several match arms and was not updated for the Elite — screen drawing
     works, event *reading* on ggoled's side is not confirmed correct.

**What was changed:** `nova_elite.yaml`'s `oled:` block went from
`interface: 4, report_id: 0x06` (a placeholder, explicitly copied from the
Nova Pro Wireless and flagged "Tentative" in the profile's own prior
comment) to `interface: 3, report_id: 0x01`, citing this issue/PR. Checked
against `oled_manager.py` before making the change: `wvalue`'s low byte is
recomputed per-packet from `report_id` (`_compute_wvalue`, `oled_manager.py`)
— only its high byte (report type) is read from YAML, so `wvalue: 0x0300`
needed no change. A test locks this
(`tests/test_nova_elite_wireless_settings_reply.py::test_oled_transport_matches_the_ggoled_hardware_report`).

**What remains open:**

1. This is confirmed for PID `0x2244` only. `0x2246`/`0x2270` are, per the
   profile's own existing comments, the same physical base station
   enumerating under a different USB port path or colorway — not a
   different design — so this session applied the fix to the whole profile.
   That carry-over is this agent's inference, not something macrooli tested.
   `0x2249` (ASM's own secondary-HID addition, not present in the upstream
   ggoled project at all) is unconfirmed either way.
2. ASM's own status/battery reads come from the `0x01b0`/`0x07xx` HID frames
   (see HW-4 above), a different collection from ggoled's OLED/info split —
   so macrooli's "info-event parsing still wrong" gap should not apply to
   ASM's battery reporting. That's an inference from the two collections
   being separate report IDs, not a confirmed fact — worth a real check.
3. Whether ASM's own vendor-command channel (also nominally "interface 3",
   per `nova_elite.yaml`'s existing header comment sourced from issue #100)
   and the OLED Col01 (also interface 3, report ID 1 per this fix) can be
   driven **concurrently** without one starving the other has not been
   checked. A single HID interface commonly carries multiple top-level
   collections without conflict, but this specific device has not been
   observed doing both at once.

**Who to ask:** a Nova Elite owner, ideally macrooli directly (contributor
on the upstream `ggoled` project, demonstrably has the hardware and has
already done a USB descriptor dump) or `JerwuQu/ggoled#26`/`#35` for anyone
else who shows up there.

**Exact commands to request:**

```bash
# Functional test — no capture needed, this already has a real answer from
# macrooli for frame drawing. What's still open is ASM specifically:
asm-cli device settings          # confirm status/battery still populate correctly
# and, with a screen-capable build, confirm the OLED actually draws now
# (it should, per this fix) vs. before (it should not have, per the old
# interface/report_id guess)
```

If it still doesn't draw with `interface: 3, report_id: 0x01`, the next step
macrooli themselves suggest is a `ggoled dump-devices`-style descriptor dump
(they used exactly this to confirm the interface/report-ID numbers) —
translated to ASM's world, that's `lsusb -v -d 1038:2244` plus a raw HID
report descriptor dump (`sudo usbhid-dump` or reading
`/sys/kernel/debug/hid/<id>/rdesc` if `hid-generic` is bound) rather than
guessing further.

**What would settle it:** OLED draws correctly with the new numbers =
confirmed, drop "Tentative" from the profile comment entirely. Still blank
or errors = the numbers need a fresh capture from ASM's own transport (`lsusb
-v` + a HID report descriptor dump), since ggoled's SET_REPORT semantics and
ASM's `ctrl_transfer` call are not guaranteed identical even given the same
interface/report-ID numbers.

---

## INT-4 — WirePlumber 0.5.15: does #181's permission repair still do anything?

**Files (read, not edited):** `src/arctis_sound_manager/pw_utils.py:109-188`
(`grant_link_permissions`, the #181 fix), `tests/test_link_permissions.py`
(pins the `pw-cli -- permissions <id> -1 rwxml` argv shape).

**Installed version, checked directly on this machine (2026-08-22, Python
subprocess call, not shell `grep`/`pacman -Q` piped through anything that
could lie):**

```
$ wireplumber --version
Compiled with libwireplumber 0.5.15
Linked with libwireplumber 0.5.15
$ pacman -Q wireplumber
wireplumber 0.5.15-1.1
```

This machine already runs the exact version in question.

**Changelog, checked directly (2026-08-22, official WirePlumber docs,
`pipewire.pages.freedesktop.org/wireplumber/resources/releases.html` — no
release dates given in that page itself):**

- **0.5.15:** *"Added new `WpPermissionManager` API that centralizes access
  control for clients... client access scripts have been completely
  refactored to use the new API with a select-access event and a priority
  fallback mechanism, supporting configuration, flatpak, snap, portal, and
  default access levels."* And: *"Fixed portal clients to be un-gated
  immediately after permission setup, preventing them from remaining
  blocked."*
- **0.5.12:** *"Added automatic muting of ALSA devices when a running node
  is removed, helping prevent loud audio on speakers when headsets are
  unplugged."*

**What this session worked out (read of `pw_utils.py`, no reproduction —
this desktop is not a portal/flatpak-restricted session, so the #181
symptom cannot be reproduced here without one):**

`grant_link_permissions()` operates at the **PipeWire core object-permission
table** level, via `pw-cli -- permissions <client-id> -1 rwxml` against the
manager socket directly. That table is exactly what WirePlumber's
access-control scripts populate for a client **at connection time** — #181
exists because that initial grant was missing the `l` (link) flag, or was
scoped too narrowly, on systems (SteamOS observed) where clients start
`access=restricted`. WirePlumber 0.5.15's *"portal clients... remaining
blocked"* fix sounds like exactly this class of bug, but at a different
layer: whether it is the same root cause as #181's SteamOS symptom, a
different bug with the same visible effect, or unrelated (SteamOS's
restriction may not go through the portal path at all) cannot be determined
by reading either side's source alone.

Two live questions this raises, neither answerable without a genuinely
restricted client to test against:

1. **Does #181's manual grant still need to run at all** on WirePlumber
   ≥0.5.15, or does the portal-client fix mean access-control now grants the
   right permissions from the start on the systems that used to need the
   workaround? If so, `grant_link_permissions()` becomes a no-op that fires,
   finds nothing to grant, and logs nothing — harmless, but worth confirming
   rather than assuming.
2. **Could the new centralized `WpPermissionManager` re-assert the
   permissions ASM just set**, undoing the manual `pw-cli permissions` grant
   on some later event (a metadata change, a client re-evaluation)? ASM's
   fix operates entirely outside WirePlumber's own bookkeeping — it doesn't
   know the grant happened. This is the more concerning of the two because a
   regression here would look identical to #181 itself: a link refused,
   ASM's log showing "granted... and retrying" but the retry still failing
   next time.

Separately, the 0.5.12 ALSA auto-mute is a **distinct** mechanism worth
knowing about independent of #181: if a future report says a headset comes
back muted after a power cycle (node removed and re-added), the first
suspect should be WirePlumber's own auto-mute, not ASM's routing logic —
worth a code comment near wherever ASM currently investigates that class of
report, not a fix, since nothing is currently broken by it.

**Who to ask:** a SteamOS, Bazzite, or other portal/flatpak-sandboxed ASM
user — i.e., whoever the next #181-shaped report comes from. This is a
watch-item, not tied to a specific open issue right now.

**Exact commands to request** (all read-only PipeWire introspection, no
USB/hardware access):

```bash
wireplumber --version                    # confirm ≥0.5.15
pw-cli info 0 | grep -i version           # PipeWire core version, for context
# while reproducing a routing failure:
journalctl --user -u asm-daemon | grep -i "181\|permission"
pw-dump | python3 -c "
import json,sys
d = json.load(sys.stdin)
for o in d:
    if o.get('type') == 'PipeWire:Interface:Client':
        print(o['id'], (o.get('info') or {}).get('props', {}).get('pipewire.access'),
              (o.get('info') or {}).get('permissions'))
"
```

**What would settle it either way:** on a portal/flatpak-restricted system
with WirePlumber ≥0.5.15, a client that used to need #181's manual grant now
shows full `rwxml`-equivalent permissions **without** ASM's log line firing
= the upstream fix subsumed #181, the workaround is now inert (fine to leave
in — it costs nothing when it finds nothing to do — but worth noting in the
next changelog that touches this area). ASM's log line still fires and still
grants something = #181 is still load-bearing, unchanged. A link that gets
granted, works once, and is refused again on a later relink with no new
daemon log line = evidence for the re-assertion concern (point 2 above) and
worth its own issue.
