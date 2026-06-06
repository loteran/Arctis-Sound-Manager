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
function Do-Action($label) {
    Read-Host ">> In SteelSeries GG: $label  — then press Enter"
    $ts = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff')
    "$ts  $label" | Add-Content $actions
    Start-Sleep -Milliseconds 400
}

Write-Host ""
Write-Host "STEP 2 — Toggle settings ONE AT A TIME (go slowly, wait ~1s each)" -ForegroundColor Cyan
Write-Host "Open SteelSeries GG (the Engine/Sonar view for the Omni) next to this window."
Read-Host "Press Enter when GG is open and showing the Omni"

Do-Action "EQ: select preset 'Flat' (or band 1)"
Do-Action "EQ: select preset 'Bass Boost'"
Do-Action "EQ: select preset 'Smiley' / another preset"
Do-Action "EQ: drag ONE band fully UP (max gain)"
Do-Action "EQ: drag the SAME band fully DOWN (min gain)"
Do-Action "Sidetone (mic monitoring): set to OFF"
Do-Action "Sidetone: set to HIGH"
Do-Action "Active Noise Cancelling: set to OFF"
Do-Action "Active Noise Cancelling: set to ON"
Do-Action "Active Noise Cancelling: set to TRANSPARENCY"
Do-Action "Volume: set to 50%"
Do-Action "Volume: set to 100%"
Do-Action "Gain / Output: set to LOW (16 ohm)"
Do-Action "Gain / Output: set to HIGH (32 ohm)"
Do-Action "Mic volume: set to 50%"
Do-Action "Mic volume: set to 100%"
Do-Action "Game/Chat mix: full GAME"
Do-Action "Game/Chat mix: full CHAT"

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
