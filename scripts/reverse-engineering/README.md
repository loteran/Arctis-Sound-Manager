# Reverse-engineering a SteelSeries headset's control protocol

Some Arctis devices use HID DAC opcodes that differ from the ones already mapped
in `src/arctis_sound_manager/devices/*.yaml`. When a new device is recognised
but its controls have no effect (UI shows up, nothing happens), the opcodes need
to be captured from **SteelSeries GG**, which is the only software that speaks
the real protocol — and it is **Windows-only**.

This folder has the helpers used for that (first applied to the Arctis Nova
Pro Omni, issue #70), plus discovery scripts for problems that need no Windows
step at all — see §3.

## 1. Capture on Windows — `capture-omni-windows.ps1`

For the user with the device (needs Windows, or a Windows dual-boot).

1. Install **Wireshark** and tick **"Install USBPcap"** in the installer:
   <https://www.wireshark.org/download.html>
2. Plug the headset's base station into USB.
3. Right-click **Windows PowerShell → Run as administrator**, then run the
   script. It will:
   - open a capture window (you pick the device tree showing the headset and an
     output filename),
   - walk you through changing **one setting at a time** in SteelSeries GG,
     timestamping each change,
   - zip the result to `Desktop\omni-capture.zip`.
4. Attach that zip to the GitHub issue.

The zip contains `omni.pcapng` (USB capture) and `omni-actions.txt` (what you
changed and when).

## 2. Decode on Linux — `parse_steelseries_capture.py`

For the maintainer, on the `.pcapng`:

```bash
sudo pacman -S wireshark-cli            # provides tshark (Arch/CachyOS)
python3 parse_steelseries_capture.py omni.pcapng omni-actions.txt
```

It extracts every host→device HID payload starting with the SteelSeries report
id `0x06`, groups them by opcode, and — using the action log — lists which bytes
were sent right after each setting change. From that you read off the
`update_sequence` opcodes for the device YAML (gain, EQ bands, sidetone, ANC,
volume, mic, …).

Pass `--report-id XX` if a device uses a different leading report id.

## 3. Find a missing status field without Windows — `gamebuds_battery_probe.py`

Some families have no `status.request` at all — nobody has ever captured what
they push, so battery and connection state are simply unavailable (the
GameBuds, issue #202: `devices/gamebuds.yaml` says so at the top). This
family, and any other in the same position, does not need Windows, Wireshark,
or an understanding of HID to investigate: on Linux, whatever the device
sends on its own can be listened to directly, and a byte that only changes
alongside a real state (in the case vs. out, muted vs. not) tends to stand
out from the rest once you have it side by side with what you were doing at
the time.

```bash
python3 gamebuds_battery_probe.py                       # passive capture only (writes nothing)
python3 gamebuds_battery_probe.py --send-status-opcodes  # also try opcodes borrowed from other families (writes to the device)
```

Two phases, and only the first runs unless asked:

1. **Listen.** It walks you through a short scripted sequence — case in, case
   out, mute the mic, idle — pausing at each step so you can do the thing,
   and records every frame the buds send with a timestamp and the action that
   was happening. A byte that stays put while one action holds and lands on a
   different, still-steady value once you switch actions is worth a second
   look; that pattern is exactly what a case-state or battery byte produces
   and what a counter byte does not.
2. **Ask, opt-in only (`--send-status-opcodes`).** Sends the status-request
   opcodes already known from other Arctis families and records which ones
   get a reply. This **writes to the device** — it is not known to be
   harmless on an unprobed family, only unlikely to be harmful, and the
   script says so before doing it. A reply proves an opcode works; it proves
   nothing about what any byte in that reply means. Do not carry a
   `response_mapping` over from the family the opcode came from — see
   `nova_7_discrete_battery.yaml`'s comment for what that mistake cost once
   already.

The ASM daemon holds the same USB interface whenever it runs, so the script
checks for it first, tells you the one command to stop it, and offers to stop
and restart it for you — or refuses to run rather than fight it for the
interface, the same failure mode discussion #203's boot-race analysis is
about.

Needs only pyusb, and runs from a checkout with ASM not installed — see the
script's own header for the exact command. Every run is saved to a
timestamped file in the working directory and ends with a short summary
meant to be pasted straight into the issue.

## 4. Watch a value the device only answers on request — `arctis7_chatmix_probe.py`

Not every state a headset knows arrives on its own. The Arctis 7 dongle's spec
declares its ChatMix dial as a read — out `[0x06, 0x24]`, back
`[0x06, 0x24, game, chat]` — and pushes nothing when the dial turns, so a
passive listen (§3) sees nothing however long it runs and however far you turn
it. ASM asked once at startup and never again, which is why the mix it showed
was frozen wherever the dial happened to be when the daemon started (#220).

```bash
systemctl --user stop arctis-manager     # it holds the interface
python3 arctis7_chatmix_probe.py         # sweep the dial slowly, then Ctrl-C
systemctl --user start arctis-manager
```

It asks once per 250 ms and prints a line only when the bytes change, so a
full sweep of the dial is a handful of lines rather than hundreds of identical
ones — short enough to paste straight into the issue. It tries the interface
the profile names first, then any other HID interface with an IN endpoint,
because the USB layout is in no SteelSeries specification and a profile shared
by several products can name an interface only some of them have (#213).

Read-only: one query per pass, no setting written. Needs only pyusb, and is a
single self-contained file — it can be downloaded on its own rather than
cloning the repository. Adapting it to another opcode is a matter of changing
`QUERY` and `PRODUCTS`.
