<#
.SYNOPSIS
  Guided USB capture of SteelSeries GG controlling an Arctis headset, for
  reverse-engineering its HID control protocol (e.g. Nova Pro Omni, issue #70).

.DESCRIPTION
  SteelSeries GG (Windows-only) is the only software that speaks the headset's
  real DAC protocol. This script captures the USB traffic with USBPcap while you
  toggle each setting in GG one at a time, and timestamps every action so the
  resulting capture can be decoded automatically afterwards.

  Output: a folder on your Desktop containing
    omni.pcapng         the USB capture
    omni-actions.txt    timestamped list of what you changed and when
  Zip that folder and attach it to the GitHub issue.

.NOTES
  Requires Wireshark with the USBPcap component (check "Install USBPcap" in the
  Wireshark installer): https://www.wireshark.org/download.html
  Run from "Windows PowerShell" (right-click > Run as administrator).
#>

$ErrorActionPreference = 'Stop'

function Find-USBPcapCMD {
    $candidates = @(
        "$env:ProgramFiles\USBPcap\USBPcapCMD.exe",
        "${env:ProgramFiles(x86)}\USBPcap\USBPcapCMD.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    return $null
}

Write-Host "=== Arctis Nova Pro Omni — USB capture helper ===" -ForegroundColor Cyan

$usbpcap = Find-USBPcapCMD
if (-not $usbpcap) {
    Write-Host "USBPcap not found." -ForegroundColor Red
    Write-Host "Install Wireshark and tick 'Install USBPcap' in the installer:"
    Write-Host "  https://www.wireshark.org/download.html"
    Write-Host "Then re-run this script."
    exit 1
}

# Confirm the Omni is connected
$omni = Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_1038&PID_2290' } | Select-Object -First 1
if ($omni) {
    Write-Host "Found device: $($omni.FriendlyName)" -ForegroundColor Green
} else {
    Write-Host "WARNING: no 1038:2290 device detected. Plug the Omni in (USB to the base station) before continuing." -ForegroundColor Yellow
}

# Output folder on the Desktop
$outDir = Join-Path ([Environment]::GetFolderPath('Desktop')) 'omni-capture'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$pcap    = Join-Path $outDir 'omni.pcapng'
$actions = Join-Path $outDir 'omni-actions.txt'
"# Arctis Nova Pro Omni capture — action timeline (local time)" | Set-Content $actions

Write-Host ""
Write-Host "STEP 1 — Start the capture" -ForegroundColor Cyan
Write-Host "A capture window will open. In it:"
Write-Host "  1) type the NUMBER of the device tree that lists your 'Arctis Nova Pro Omni'"
Write-Host "  2) when asked for the output file, type exactly:"
Write-Host "       $pcap"
Write-Host "  3) leave that window open and capturing."
Read-Host "Press Enter to open the capture window"

Start-Process -FilePath $usbpcap

Read-Host "When the capture window says it is capturing, come back here and press Enter"

# --- Guided actions -----------------------------------------------------------
# IMPORTANT: make the change in GG FIRST, then press Enter. The timestamp is
# written on Enter, so each command lands in the window (previous Enter, this
# Enter]. Each setting is taken to several KNOWN values so the decoder can spot
# the opcode by the byte that changes between them.
function Do-Action($label) {
    Read-Host ">> Make this change in GG, THEN press Enter:  $label"
    $ts = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff')
    "$ts  $label" | Add-Content $actions
}

Write-Host ""
Write-Host "STEP 2 — Change settings ONE AT A TIME, IN THIS ORDER." -ForegroundColor Cyan
Write-Host "For each line: do the change in SteelSeries GG, WAIT ~1s, then press Enter here."
Write-Host "Change ONLY the one thing asked — nothing else." -ForegroundColor Yellow
Write-Host ""
Write-Host "IMPORTANT: make sure SteelSeries GG is CLOSED right now." -ForegroundColor Yellow
Read-Host "GG closed? Press Enter, then OPEN SteelSeries GG (this records the device init)"
Do-Action "GgStartup = opened SteelSeries GG (device initialization handshake)"
Read-Host "Wait until GG fully shows the Omni, then press Enter"

# Baseline: learn the constant background polling (change nothing)
Read-Host ">> Do NOTHING for ~5 seconds (baseline), then press Enter"
"$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff'))  BASELINE = idle" | Add-Content $actions

# Each setting is taken to >=2 known values so the changing byte = the
# parameter. This list mirrors every function the Nova Pro Wireless DAC exposes
# (see src/arctis_sound_manager/devices/nova_pro_wireless.yaml) so we get the
# full opcode map for the Omni.

# -- Headset output --
Do-Action "Gain = LOW (16 ohm / high-impedance OFF)"
Do-Action "Gain = HIGH (32 ohm / high-impedance ON)"
Do-Action "Volume = 25 percent"
Do-Action "Volume = 75 percent"

# -- Active Noise Cancelling + Transparency --
Do-Action "ANC = OFF"
Do-Action "ANC = ON (full ANC)"
Do-Action "ANC = TRANSPARENCY"
Do-Action "TransparencyLevel = LOW (with ANC on Transparency, slider near min)"
Do-Action "TransparencyLevel = HIGH (slider near max)"

# -- Microphone --
Do-Action "MicVolume = 25 percent"
Do-Action "MicVolume = 100 percent"
Do-Action "MicMute = MUTED"
Do-Action "MicMute = UNMUTED"
Do-Action "Sidetone = OFF"
Do-Action "Sidetone = LOW"
Do-Action "Sidetone = MEDIUM"
Do-Action "Sidetone = HIGH"
Do-Action "MicLed = 0 percent"
Do-Action "MicLed = 100 percent"

# -- Game/Chat mixer --
Do-Action "Mix = full GAME"
Do-Action "Mix = full CHAT"

# -- Power management / wireless --
Do-Action "AutoOff = 10 minutes (Auto Shut-Off)"
Do-Action "AutoOff = 60 minutes"
Do-Action "WirelessMode = Speed (low latency)"
Do-Action "WirelessMode = Range"

# -- Equalizer: source switch, presets, bands --
Do-Action "EqMode = Engine Custom EQ (turn the Engine EQ ON / select Custom)"
Do-Action "EqPreset = Flat"
Do-Action "EqPreset = Bass Boost"
Do-Action "EqBand1 = +10 dB (drag the FIRST/lowest band fully up)"
Do-Action "EqBand1 = -10 dB (drag the SAME first band fully down)"
Do-Action "EqBand5 = +10 dB (drag a MIDDLE band fully up)"
Do-Action "EqBand5 = -10 dB (drag the SAME middle band fully down)"
# The switch between the hardware Custom EQ and Sonar's software EQ — capture
# both directions so we get the opcode that selects the active EQ source.
Do-Action "EqSource = Sonar EQ (switch the active EQ from Custom to Sonar)"
Do-Action "EqSource = Custom EQ (switch the active EQ back from Sonar to Custom)"

# -- Bluetooth (Omni/Nova Pro has BT; capture for completeness) --
Do-Action "BtPowerUp = OFF (Bluetooth power-up state)"
Do-Action "BtPowerUp = ON"
Do-Action "BtAutoMute = OFF"
Do-Action "BtAutoMute = ON"

Write-Host ""
Write-Host "STEP 3 — Stop the capture" -ForegroundColor Cyan
Write-Host "Go to the capture window and press Ctrl+C to stop it."
Read-Host "Press Enter here once the capture window has stopped and $pcap exists"

if (Test-Path $pcap) {
    $zip = "$outDir.zip"
    if (Test-Path $zip) { Remove-Item $zip }
    Compress-Archive -Path $outDir\* -DestinationPath $zip
    Write-Host ""
    Write-Host "Done! Attach this file to the GitHub issue:" -ForegroundColor Green
    Write-Host "  $zip"
} else {
    Write-Host "Could not find $pcap — did the capture save to the right path?" -ForegroundColor Red
    Write-Host "Re-run and make sure to type the output path exactly when prompted."
}
