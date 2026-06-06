# Reverse-engineering a SteelSeries headset's control protocol

Some Arctis devices use HID DAC opcodes that differ from the ones already mapped
in `src/arctis_sound_manager/devices/*.yaml`. When a new device is recognised
but its controls have no effect (UI shows up, nothing happens), the opcodes need
to be captured from **SteelSeries GG**, which is the only software that speaks
the real protocol — and it is **Windows-only**.

This folder has the two helpers used for that (first applied to the Arctis Nova
Pro Omni, issue #70).

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
