# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Help page — user manual, language follows the app I18n setting.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from arctis_sound_manager.gui.components import DividerLine
from arctis_sound_manager.gui.report_dialog import ReportBugDialog
from arctis_sound_manager.i18n import I18n
import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.gui.theme import (
    ACCENT,
    BG_BUTTON,
    BG_BUTTON_HOVER,
    BG_CARD,
    BG_MAIN,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

# ── Help content (all languages) ──────────────────────────────────────────────

HELP_CONTENT: dict[str, dict] = {
    "en": {
        "title": "Help",
        "subtitle": "Arctis Sound Manager — User Manual",
        "sections": [
            {
                "heading": "Overview",
                "body": (
                    "Arctis Sound Manager is a Linux application for configuring SteelSeries Arctis "
                    "headsets. It communicates with your device via USB HID and exposes settings "
                    "through a graphical interface and a system tray icon.\n\n"
                    "The app runs as a background service (asm-daemon) managed by systemd, and a "
                    "GUI front-end (asm-gui) that connects to it over D-Bus."
                ),
            },
            {
                "heading": "Getting started",
                "body": (
                    "1. Connect your SteelSeries headset or DAC via USB.\n"
                    "2. Launch the app from your application menu or run asm-gui in a terminal.\n"
                    "3. The system tray icon appears automatically. Click it to open the menu.\n"
                    "4. The main window opens immediately on first launch. Closing it does not "
                    "quit the tray — the app keeps running in the background.\n"
                    "5. To open the window again, click the tray icon → Open App, or re-run "
                    "asm-gui from the launcher."
                ),
            },
            {
                "heading": "Home",
                "body": (
                    "The Home page shows the headset status (Online / Offline / Charging, battery "
                    "level for the headset and the base station's spare battery) and the channel "
                    "mixer.\n\n"
                    "── Profiles bar ──\n"
                    "Click a profile chip to instantly restore all saved settings. "
                    "Use \"＋ Save current settings\" to create a new profile. "
                    "(See the Profiles section below for details.)\n\n"
                    "── Channel cards ──\n"
                    "• Mic — the volume of what your apps record, which microphone feeds it, "
                    "and which apps are recording\n"
                    "• Master — the headset's own output. On a base station or GameDAC, a "
                    "\"DAC wheel\" gauge shows where its volume wheel sits\n"
                    "• Game — games and general audio (Arctis_Game)\n"
                    "• Chat — voice apps: Discord, TeamSpeak… (Arctis_Chat)\n"
                    "• Media — browsers and video players, moved there automatically (Arctis_Media)\n"
                    "• Aux — a spare channel, off unless you turn it on\n"
                    "• Output — an external device: HDMI, speakers, another sound card. 5.1 / 7.1 "
                    "is passed through as is\n\n"
                    "Each card shows the name of the device it plays on; click it to pick "
                    "another one. Under the slider, an EQ preset picker lists the favourites "
                    "saved for that channel on the Equalizer page and applies one right away.\n\n"
                    "── Showing and hiding cards ──\n"
                    "The \"Channels ▾\" menu lists every card. Untick the ones you do not use: "
                    "hiding a card only removes it from the page, the channel keeps working. "
                    "Turning Aux on or off there creates or removes the channel itself.\n\n"
                    "── ChatMix bar ──\n"
                    "Balances Chat against the other channels, like the headset's ChatMix dial, "
                    "which it follows in real time. The checkboxes under it choose which of Game, "
                    "Media and Aux take part. The mix fades the quieter side the way SteelSeries "
                    "Sonar does.\n\n"
                    "── Applications ──\n"
                    "Each card shows the applications playing through it as icons. Hover an icon "
                    "for the app's name. Drag it onto another card to move it, or click it and "
                    "pick a channel. Apps that are on none of the channels are listed under "
                    "\"Other applications\". Your choices are remembered and restored on the next "
                    "launch."
                ),
            },
            {
                "heading": "Bring audio to headset",
                "body": (
                    "If an app suddenly has no sound, it is usually because it is playing to the "
                    "wrong output device — a TV over HDMI, an S/PDIF port, or another sound card — "
                    "instead of your headset. This happens when the system (WirePlumber) remembers "
                    "an old output for that app.\n\n"
                    "The system tray menu → \"Bring audio to headset\" fixes this in one click. It "
                    "finds every application currently playing on a non-Arctis device and moves it "
                    "back to your default Arctis sink; a notification tells you how many apps were "
                    "moved. Applications already on a channel are left untouched.\n\n"
                    "It does not change a channel's own output: if you sent Media to another "
                    "device, that choice is kept. To bring a channel back, pick the headset on "
                    "that channel's card.\n\n"
                    "It is safe to click at any time: if nothing is misrouted, it does nothing."
                ),
            },
            {
                "heading": "Controlling channels from a key",
                "body": (
                    "A channel's volume can be bound to a key — including the unused function "
                    "keys a macropad dial sends.\n\n"
                    "ASM cannot claim a key combination for itself on Wayland: the compositor "
                    "owns that, and the mechanism that hands them out is not available on every "
                    "desktop, nor can it grant a *specific* key. So the action is a command "
                    "instead, and your desktop binds the key — which works the same everywhere.\n\n"
                    "Bind these in your desktop's keyboard settings:\n"
                    "• asm-cli volume game +5 — five points up\n"
                    "• asm-cli volume chat -5 — five points down\n"
                    "• asm-cli volume media 40 — set an exact level\n"
                    "• asm-cli volume game mute — also unmute, or toggle\n"
                    "• asm-cli volume game +5 chat -5 — several channels from one key\n"
                    "• asm-cli volume --list — what each channel is at right now\n\n"
                    "The change goes straight to PipeWire, so a bound key works whether or not "
                    "this window is open, and the new level is remembered exactly as the sliders' "
                    "are."
                ),
            },
            {
                "heading": "Streamer mode",
                "body": (
                    "Streamer mode adds a second, independent mix of Game, Chat, Media, Aux and "
                    "your processed Mic, with its own volume and mute for each channel. It is "
                    "delivered as a virtual stereo source named \"<device> Stream Mix\" that OBS "
                    "or any recorder can capture — like the Stream mode of SteelSeries Sonar in "
                    "GG.\n\n"
                    "── Turning it on ──\n"
                    "On the Channels page, switch on the \"Streamer mode\" toggle next to the Channels menu. "
                    "Each of the Game, Chat, Media, Aux and Mic cards then shows a compact "
                    "\"Stream\" slider (0-100) with a mute button under its fader. Moves are "
                    "applied live, with no dropout. Turning the mode on or off restarts the audio "
                    "chain once, so expect one short dropout, like switching a preset.\n\n"
                    "── Using it in OBS ──\n"
                    "Sources → + → Audio Input Capture → choose \"<device> Stream Mix\". Remove "
                    "any separate desktop-audio and microphone captures, otherwise viewers hear "
                    "everything twice.\n\n"
                    "── What viewers hear ──\n"
                    "The stream mix is taken before your personal volumes and before the "
                    "per-channel EQ and spatial audio, so viewers hear the clean game, not your "
                    "headphone EQ or HRTF. 7.1 game audio is folded to stereo with a proper "
                    "downmix that keeps the centre channel (dialogue). Your headset volumes and "
                    "the ChatMix dial do not affect the stream mix, and it does not affect them."
                ),
            },
            {
                "heading": "Profiles",
                "body": (
                    "Profiles let you save and restore your complete audio configuration in one click.\n\n"
                    "── What a profile contains ──\n"
                    "• EQ mode (Sonar or Custom)\n"
                    "• Active EQ preset for each channel (Game / Chat / Micro)\n"
                    "• Macro slider values (Basses / Voix / Aigus) per channel\n"
                    "• Spatial Audio state (enabled, immersion, distance)\n"
                    "• Channel volumes (Game / Chat / Media)\n"
                    "• Which channel cards are shown on the Home page\n\n"
                    "── Creating a profile ──\n"
                    "1. Configure your audio settings as desired.\n"
                    "2. Click \"＋ Save current settings\" in the Home page profile bar.\n"
                    "3. Give the profile a name and choose what to include.\n"
                    "4. Click Save — the profile chip appears immediately.\n\n"
                    "── Applying a profile ──\n"
                    "Click any profile chip in the Home page bar, or select it from the "
                    "system tray menu. Settings are applied instantly: volumes are set "
                    "immediately, EQ and PipeWire configs are regenerated and filter-chain "
                    "is restarted in the background.\n\n"
                    "── Deleting a profile ──\n"
                    "Right-click a profile chip → Delete.\n\n"
                    "── Typical use ──\n"
                    "Create a \"Gaming\" profile (Sonar EQ, Spatial Audio on, high Game volume) "
                    "and a \"Work\" profile (Custom EQ, Spatial off, balanced volumes). "
                    "Switch between them in one click from the tray or the Home page."
                ),
            },
            {
                "heading": "Equalizer",
                "body": (
                    "The Equalizer page lets you adjust the frequency response of your headset. "
                    "Two modes are available: Custom and Sonar.\n\n"
                    "── Custom mode ──\n"
                    "A 10-band equalizer (31 Hz → 16 kHz). Drag a slider up to boost, down to cut. "
                    "Changes are sent to the device immediately. You can save and load presets.\n\n"
                    "── Sonar mode ──\n"
                    "Switches to the full Sonar EQ system powered by PipeWire filter-chain "
                    "(see the Sonar EQ section below)."
                ),
            },
            {
                "heading": "Sonar EQ",
                "body": (
                    "The Sonar EQ page provides a full SteelSeries Sonar-style parametric EQ "
                    "system with a tab per channel: Game, Chat, Media, Aux (when turned on), "
                    "Output and Micro.\n\n"
                    "── EQ curve ──\n"
                    "Each channel has an interactive parametric EQ with up to 10 bands. "
                    "Click the curve to add a band, drag to adjust frequency and gain, "
                    "scroll to change Q (bandwidth).\n\n"
                    "── Presets ──\n"
                    "353 Game presets, 9 Chat and 14 Mic presets imported from SteelSeries Sonar. "
                    "Use the search bar to filter, and mark up to 9 favorites for quick access. "
                    "A Mic preset also sets the mic processing (ClearCast, noise reduction, "
                    "noise gate, compressor), not just its EQ.\n\n"
                    "── Automatic preset per game ──\n"
                    "When \"Automatic preset per game\" is on, ASM applies the Game preset "
                    "matching a game as soon as it starts — found through Steam, Lutris, Heroic "
                    "or the program's name — like GG's QuickSet. A preset you pick by hand while "
                    "the game runs is remembered for that game, and a notification says so. The tray "
                    "menu → \"Preset for <game>\" lets you go back to \"Automatic\", force a "
                    "preset or choose \"Do not change\". Off by "
                    "default; switching preset restarts the filter-chain, which cuts the sound "
                    "for a moment, as in GG.\n\n"
                    "── Output device ──\n"
                    "Each channel has a device picker next to its EQ: the headset, or another "
                    "output (HDMI, speakers…). It is the same choice as on the Home page card.\n\n"
                    "── Macro sliders ──\n"
                    "Three quick-adjust sliders below the curve: Basses, Voix (Mids) and Aigus "
                    "(Treble), each ±12 dB.\n\n"
                    "── Spatial Audio (Game / Media / Aux) ──\n"
                    "Routes the channel through HeSuVi virtual 7.1 surround. A game set to 5.1 "
                    "or 7.1 sends its real centre and rear channels, which are placed around "
                    "you. Immersion (0–12 dB gain) and Distance (plate reverb) sliders let you "
                    "fine-tune the effect.\n"
                    "\"Only for surround sources (5.1 / 7.1)\" keeps stereo audio — music, video, "
                    "games in their own headphone mode — plain stereo, and turns the virtual "
                    "surround on by itself while something plays 5.1 or 7.1.\n\n"
                    "── Volume Boost ──\n"
                    "Adds up to +12 dB gain at the end of the filter chain.\n\n"
                    "── Smart Volume ──\n"
                    "Evens out volume differences between sources, using Sonar's loudness "
                    "targets, with three profiles: Quiet / Balanced / Loud.\n\n"
                    "All changes are applied live via PipeWire filter-chain (biquad nodes)."
                ),
            },
            {
                "heading": "EQ Preset Import / Export",
                "body": (
                    "ASM lets you export and import EQ presets to share them with others or "
                    "back them up.\n\n"
                    "── Exporting a preset ──\n"
                    "In the Sonar EQ page, select a preset and click the export button (↑ or "
                    "\"Export\" in the preset menu). The Export dialog opens with three options:\n"
                    "• Share Link — a compact arctis-asm://import?data=… URL. Copy it and send "
                    "it to anyone running ASM; they can paste it in the Import dialog to load "
                    "your exact EQ settings instantly.\n"
                    "• Copy JSON — copies the raw preset data to clipboard.\n"
                    "• Save to File — saves the preset as a .json file on your computer.\n"
                    "• Share to Community — publishes the preset to the ASM Presets community "
                    "site (GitHub login required). A confirmation popup appears before publishing.\n\n"
                    "── Importing a preset ──\n"
                    "Click the import button (↓ or \"Import\" in the preset menu). "
                    "The Import dialog accepts:\n"
                    "• An arctis-asm:// deep link (paste from a share link)\n"
                    "• A SteelSeries GG deep link (https://www.steelseries.com/deeplink/…)\n"
                    "• A .json file — click \"Browse file\" to pick one from your disk\n"
                    "• Click \"Browse ASM Presets ↗\" to open the community site and import "
                    "presets shared by other users.\n\n"
                    "Imported presets are saved in ~/.config/arctis_manager/sonar_presets/ "
                    "and appear immediately in the preset list."
                ),
            },
            {
                "heading": "ASM Presets Community",
                "body": (
                    "The ASM Presets community site lets you browse, share and vote for EQ "
                    "presets created by other ASM users.\n\n"
                    "Site: https://loteran.github.io/asm-presets/\n\n"
                    "── What you can do ──\n"
                    "• Browse all published presets — filter by game, channel or device\n"
                    "• Vote for presets you like (GitHub login required)\n"
                    "• Publish your own presets from the ASM Export dialog\n"
                    "• Delete your own presets (visible only to the author)\n"
                    "• Copy the share link for any preset\n"
                    "• Import a preset directly by clicking \"Import in ASM\" — it opens "
                    "the deep link in ASM automatically\n\n"
                    "── Publishing ──\n"
                    "Click \"Share to Community\" in the ASM Export dialog. "
                    "If you are not logged in, you will be redirected to GitHub for "
                    "authentication and then returned to the submit page automatically.\n\n"
                    "No moderation — presets are published immediately. The community relies "
                    "on votes to surface the best ones."
                ),
            },
            {
                "heading": "Micro Processing",
                "body": (
                    "The Micro tab in Sonar EQ includes audio processing features for your "
                    "microphone, applied in real time via PipeWire filter-chain.\n\n"
                    "── ClearCast AI Noise Cancellation ──\n"
                    "Uses rnnoise (neural network) to isolate your voice and remove "
                    "background noise in real time. The slider adjusts the VAD (Voice Activity "
                    "Detection) threshold sensitivity.\n"
                    "Requires the noise-suppression-for-voice LADSPA plugin "
                    "(Arch: pacman, Debian/Ubuntu: apt, Fedora/Nobara: COPR — "
                    "see the tooltip on the toggle for install commands).\n\n"
                    "── Noise Reduction ──\n"
                    "• Background — high-pass filter that cuts low-frequency rumble "
                    "(fans, air conditioning). Slider adjusts the cutoff frequency.\n"
                    "• Impact — high-shelf filter that softens transient noises "
                    "(keyboard clicks, impacts). Slider adjusts attenuation intensity.\n\n"
                    "── Noise Gate ──\n"
                    "Cuts audio below a dB threshold — silences the mic when you are not "
                    "speaking. Adjustable threshold from -60 to -10 dB. "
                    "Auto mode lets the system calculate the optimal threshold.\n"
                    "Requires: swh-plugins package.\n\n"
                    "── Compressor ──\n"
                    "Volume stabilizer that evens out loud and quiet passages. "
                    "The slider controls compression intensity (threshold, ratio and makeup gain). "
                    "Useful for keeping a consistent mic level in voice chat.\n"
                    "Requires: swh-plugins package.\n\n"
                    "── DeepFilterNet ──\n"
                    "A second noise-cancellation engine, offered alongside ClearCast. It is "
                    "heavier on the CPU and better on steady background noise; ClearCast "
                    "reacts faster. Only one runs at a time — pick whichever sounds better "
                    "on your voice and your room.\n\n"
                    "── Which microphone ──\n"
                    "By default the chain follows your headset's microphone. You can point "
                    "it at a desk microphone instead, and have ASM switch between the two on "
                    "its own — when the headset is disconnected, when its mic is muted, or "
                    "both.\n\n"
                    "Processing chain order: EQ → Boost → Noise Reduction → Noise Gate → "
                    "ClearCast → Compressor."
                ),
            },
            {
                "heading": "ANC / Transparent mode",
                "body": (
                    "The ANC widget on the Headset page lets you control Active Noise Cancelling "
                    "directly from the GUI.\n\n"
                    "Three modes are available:\n"
                    "• Off — noise cancelling disabled\n"
                    "• Transparent — lets outside sound through (adjustable level 10–100%)\n"
                    "• ANC — full active noise cancelling\n\n"
                    "Changes made from the headset buttons are reflected in real time in the GUI, "
                    "and vice-versa."
                ),
            },
            {
                "heading": "Headset / DAC Infos",
                "body": (
                    "This page displays technical information about the connected device:\n\n"
                    "• Device name and model\n"
                    "• USB Vendor ID and Product ID\n"
                    "• Battery level and charging status\n"
                    "• ANC / Transparent mode state and controls\n"
                    "• The headset's own settings, one card per group, depending on\n"
                    "  the model: sidetone, mic volume, volume limiter, sleep timer,\n"
                    "  LED brightness, wireless transmitter power… Each one is applied\n"
                    "  to the headset as soon as you change it.\n"
                    "• Nova Elite and Nova Pro Omni: the base station's microphone and\n"
                    "  Bluetooth EQ presets. At start, ASM reads the base's settings\n"
                    "  and keeps the ones you never changed in ASM.\n"
                    "• Arctis Pro GameDAC: DAC gain, line out and its stream mix,\n"
                    "  screen brightness (in testing).\n"
                    "• Nova Pro Wired: line out and its stream mix, screen dimming\n"
                    "  (in testing).\n"
                    "• Arctis 7 and Arctis 1 Wireless families: microphone volume\n"
                    "  (in testing).\n"
                    "• Headset access (Settings): hidraw, the default, lets the kernel\n"
                    "  keep the headset's interfaces; libusb is ASM's former way. ASM\n"
                    "  falls back to libusb on its own if hidraw cannot open.\n"
                    "• Arctis GameBuds: audio source, 2.4 GHz dongle or Bluetooth\n"
                    "  (in testing).\n"
                    "• Arctis Pro Wireless: volume limiter, muted-mic LED, screensaver\n"
                    "  and its delay, Bluetooth options (in testing).\n"
                    "• Arctis GameBuds: while the charging case is plugged into this\n"
                    "  computer over USB, its battery, charging state, lid and which\n"
                    "  earbuds are seated in it\n\n"
                    "This information is useful for troubleshooting or reporting issues."
                ),
            },
            {
                "heading": "DAC — OLED Display",
                "body": (
                    "The DAC page is available on devices with an OLED screen: Arctis Nova Pro "
                    "Wireless / X, Nova Elite, Nova Pro Omni, Arctis Pro GameDAC and the Arctis "
                    "Pro Wireless base station. It only appears in the sidebar when the device "
                    "is detected.\n\n"
                    "── Custom Display ──\n"
                    "The toggle at the top switches between the original DAC display and the "
                    "custom mode. When custom mode is active, all elements below become "
                    "configurable.\n\n"
                    "── DAC Settings ──\n"
                    "• Brightness — controls the OLED screen brightness\n"
                    "• Screen timeout — turns off the screen after inactivity\n"
                    "• Scroll speed — controls how fast text scrolls on the display\n\n"
                    "── Display Elements ──\n"
                    "Choose which information is shown on the OLED screen and in what order:\n"
                    "• Time — always shown; adjust font size (7–30 pt)\n"
                    "• Battery — always shown; adjust font size\n"
                    "• Profile — shows the active audio profile name\n"
                    "• EQ Preset — shows the EQ preset of the channel you are listening to "
                    "(\"Media: Bass Boost\"). It follows the channel actually playing; a "
                    "Discord call does not take it over from a running game\n"
                    "• Now playing — \"Artist - Title\" of the music or video player, scrolling\n"
                    "• Weather — shows the current temperature\n\n"
                    "The elements can be reordered using the ▲/▼ buttons. "
                    "Each element has a checkbox to enable/disable it and a font size control.\n\n"
                    "── Screen off while you are away ──\n"
                    "With \"Turn screen off while the session is locked or idle\" ticked, the "
                    "screen goes dark while you are away, and lights back "
                    "up when you return or turn the volume or ChatMix wheel. This protects the "
                    "OLED panel from burn-in.\n\n"
                    "── Weather ──\n"
                    "Enter a city name and click Search to link your location. "
                    "Choose °C or °F. The temperature is fetched automatically and displayed "
                    "on the OLED screen."
                ),
            },
            {
                "heading": "Clips",
                "body": (
                    "Clips keeps the last thirty seconds of your screen in memory and writes them "
                    "to a file when you ask for them — nothing is recorded to disk until you press "
                    "Save, so the moment worth keeping is already captured by the time you decide "
                    "it was.\n\n"
                    "Clips ships switched off. Turn it on from the Clips page; the packages it "
                    "needs are installed from that same switch.\n\n"
                    "── Saving a clip ──\n"
                    "• Start arms the rolling buffer. Save writes what it holds.\n"
                    "• A global shortcut saves without switching windows. Your desktop owns that "
                    "binding, so the combination shown is the one it actually gave out — change it "
                    "with the Change… button, which opens your system's own shortcut settings.\n"
                    "• Capture automatically while a game is running arms the buffer when a game "
                    "starts playing audio and releases it when the game is gone. A buffer that has "
                    "to be armed by hand is armed after the moment worth keeping.\n\n"
                    "── What is in the file ──\n"
                    "Each Sonar channel is written as its own audio track: game, chat, media and "
                    "the microphone stay separate, so a clip can be re-mixed afterwards instead of "
                    "being stuck with what you happened to be hearing.\n\n"
                    "── The editor ──\n"
                    "Double-click a clip to open it. The preview is a mixer: every track plays at "
                    "once with its own level and mute, and what you set is what gets exported. "
                    "Drag on the timeline to scrub, drag the lit block to move the selection, its "
                    "edges to resize it. Trim and levels are saved per clip, so closing the editor "
                    "no longer throws that work away.\n\n"
                    "── Where clips are saved ──\n"
                    "In your video folder, under \"ASM Clips\". Change it from the gear on the "
                    "Clips page — Folder: Change…. Clips already recorded stay where they are; "
                    "only new ones follow the new folder. Default returns to the video folder."
                ),
            },
            {
                "heading": "Stream Guard",
                "body": (
                    "When you share your screen, the sound that goes with it is normally everything "
                    "your machine is playing. Stream Guard keeps the channels you have not allowed "
                    "out of that stream, so a private call or a video in another window does not "
                    "reach your audience.\n\n"
                    "It runs as its own background service and only acts while something is "
                    "actually capturing your screen."
                ),
            },
            {
                "heading": "Settings",
                "body": (
                    "The Settings page holds ASM's own options, in cards. The headset's own "
                    "settings (sidetone, sleep timer, LEDs…) are on the Headset page.\n\n"
                    "── Audio ──\n"
                    "• Redirect audio on disconnect (choose fallback output device)\n"
                    "• Redirect audio on connect, and the channel the system default goes to "
                    "(Media, Game or Chat)\n\n"
                    "── General settings ──\n"
                    "• Launch at startup — enables/disables the system tray autostart via systemd. "
                    "When enabled, the tray icon appears automatically after login.\n"
                    "• Language — switch the interface language (EN / FR / ES).\n"
                    "• Chat/Mix Overlay — turning the ChatMix dial (or the Home page's bar) shows "
                    "the ChatMix bar at the bottom of the screen for a moment, above the taskbar "
                    "and over fullscreen games.\n"
                    "• DAC Wheel Overlay — the same for the volume wheel of a base station or "
                    "GameDAC.\n\n"
                    "── Several headsets ──\n"
                    "When more than one Arctis is plugged in, choose the one ASM drives with "
                    "\"Preferred Arctis Device\" on the Headset page.\n\n"
                    "── Check for updates ──\n"
                    "The \"Check for updates\" button at the bottom of the page forces an immediate "
                    "check against the latest GitHub release, bypassing the normal 24-hour cache. "
                    "If a newer version is available, click the result label to open the update "
                    "dialog: for package manager installs (pacman, dnf, apt) it shows the command "
                    "and can open a terminal automatically; for pipx/pip installs it downloads and "
                    "installs the wheel in-app."
                ),
            },
            {
                "heading": "Using ASM without a SteelSeries headset",
                "body": (
                    "The channels, per-channel volume, the Sonar equaliser, spatial audio, the "
                    "application router and Clips never talk to USB — they work on PipeWire "
                    "devices. Only the conversation with the headset needs an Arctis: battery, "
                    "ANC, sidetone, ChatMix, the OLED screen.\n\n"
                    "So ASM can run on any output. In Settings, turn on \"Use ASM without a "
                    "SteelSeries headset\" and pick the device your headphones are plugged into. "
                    "An input device is optional — leave it empty if you only want playback, and "
                    "no microphone chain is set up.\n\n"
                    "The Headset and DAC pages come up empty in this mode rather than showing "
                    "controls that would do nothing.\n\n"
                    "Nothing changes for a SteelSeries headset: the mode is off by default, it is "
                    "never turned on for you, and a connected Arctis always takes precedence."
                ),
            },
            {
                "heading": "System tray",
                "body": (
                    "The tray icon provides quick access without opening the full window.\n\n"
                    "• Left-click or right-click the icon to open the context menu.\n"
                    "• The menu shows the current headset status (battery, connection state).\n"
                    "• Open App — brings the main window to the foreground.\n"
                    "• Profiles and EQ presets — apply one without opening the window.\n"
                    "• Output Routing — the output device of each channel.\n"
                    "• Preset for <game> — while a game runs, with Automatic preset per game on.\n"
                    "• Bring audio to headset — see the section above.\n"
                    "• Exit — fully quits the application (tray and daemon).\n\n"
                    "── Plasma widget and status bars ──\n"
                    "On KDE Plasma 6, a widget shows the headset's power state and battery, the "
                    "spare battery, noise cancelling and the microphone, and opens ASM.\n"
                    "Elsewhere, asm-cli status --json gives the same information to Waybar, "
                    "Polybar or any script.\n\n"
                    "If you close the main window, the tray stays active. The app only stops "
                    "when you choose Exit from the tray menu."
                ),
            },
            {
                "heading": "Surround sound",
                "body": (
                    "Arctis Sound Manager supports virtual 7.1 surround sound through PipeWire "
                    "and HeSuVi-compatible HRTF convolution filters.\n\n"
                    "── How it works ──\n"
                    "A virtual 7.1 sink is created in PipeWire. Audio routed to it is processed "
                    "by HRTF convolution (HeSuVi) and folded down to stereo for your headset.\n\n"
                    "── Setup ──\n"
                    "Surround is configured automatically during the initial setup (asm-setup). "
                    "A 'Virtual Surround Sink' will appear in your audio settings once setup "
                    "is complete.\n\n"
                    "── Spatial Audio (Sonar) ──\n"
                    "When using Sonar EQ mode, the Game, Media and Aux channels can route through "
                    "HeSuVi via the Spatial Audio toggle. These channels accept 5.1 / 7.1, so a "
                    "game set to surround sends its real centre and rear channels. Immersion and "
                    "Distance controls are included — no manual setup needed.\n\n"
                    "── Speakers and receivers ──\n"
                    "A channel sent to a 5.1 / 7.1 receiver or TV gets its channels as they are, "
                    "with no headphone rendering. With Spatial Audio off on a stereo device, "
                    "surround is folded into stereo (centre and surrounds at -3 dB) so a 7.1 "
                    "game's dialogue is not lost.\n\n"
                    "── HRIR Profile ──\n"
                    "The HRIR Profile drop-down is located in the Settings page. "
                    "It lets you choose from 57 built-in profiles grouped by brand: "
                    "Dolby Atmos, CMSS-3D, SBX Pro Studio, Sennheiser GSX, DTS Headphone:X, "
                    "Windows Sonic, Razer Surround, Out Of Your Head, Waves NX, Flux HEar, "
                    "OpenAL / DirectSound3D, Nahimic, Spatial Sound Card, and more.\n"
                    "Selecting a profile copies the WAV and restarts filter-chain automatically — "
                    "no manual steps needed. Your choice is saved across sessions.\n\n"
                    "Advanced: you can still manually replace "
                    "~/.local/share/pipewire/hrir_hesuvi/hrir.wav with any 14-channel "
                    "HeSuVi-compatible WAV, then restart filter-chain."
                ),
            },
            {
                "heading": "Autostart at login",
                "body": (
                    "To start Arctis Sound Manager automatically when you log in, use the "
                    "\"Launch at startup\" toggle in the Settings page.\n\n"
                    "When enabled, the system tray icon (asm-gui --systray) will start "
                    "automatically at login via systemd (arctis-gui.service). The daemon "
                    "(arctis-manager.service) is always started regardless of this toggle.\n\n"
                    "To disable autostart, switch the toggle off."
                ),
            },
            {
                "heading": "Troubleshooting",
                "body": (
                    "Device not detected:\n"
                    "• Make sure udev rules are installed: run asm-cli udev write-rules --reload\n"
                    "• Reconnect the USB cable.\n"
                    "• Check that the daemon is running:\n"
                    "  systemd: systemctl --user status arctis-manager.service\n"
                    "  dinit:   dinitctl status arctis-manager\n\n"
                    "Settings not applied:\n"
                    "• Restart the daemon:\n"
                    "  systemd: systemctl --user restart arctis-manager.service\n"
                    "  dinit:   dinitctl restart arctis-manager\n\n"
                    "GUI does not open:\n"
                    "• Run asm-gui -vvvv in a terminal to see detailed logs.\n"
                    "• \"libxcb-cursor0 is needed to load the Qt xcb platform plugin\":\n"
                    "  sudo apt install libxcb-cursor0 (Fedora/Arch: xcb-util-cursor).\n"
                    "• Check that the daemon is running (see above).\n\n"
                    "System tray not appearing at login:\n"
                    "• Enable \"Launch at startup\" in Settings, or check:\n"
                    "  systemd: systemctl --user status arctis-gui.service\n"
                    "  dinit:   check ~/.config/autostart/arctis-gui-autostart.desktop\n\n"
                    "No sound / flat audio and a \"Safe mode is on\" banner on the Sonar page:\n"
                    "• Safe mode is a safety net: if the PipeWire audio filter-chain crashes\n"
                    "  in a loop, ASM disables the EQ so you keep stable (flat) audio instead\n"
                    "  of constant cuts. The 7.1 surround and Sonar EQ are turned off while it\n"
                    "  is active.\n"
                    "• Once the cause is gone (usually an ASM or PipeWire update), click\n"
                    "  \"Re-enable EQ\" on the banner: ASM restores the EQ configs and restarts\n"
                    "  the filter-chain. If it still crashes, safe mode re-arms automatically.\n"
                    "• ASM also clears safe mode on its own after an ASM/PipeWire version\n"
                    "  change, so most users never need the button."
                ),
            },
        ],
    },
    "fr": {
        "title": "Aide",
        "subtitle": "Arctis Sound Manager — Manuel d'utilisation",
        "sections": [
            {
                "heading": "Présentation",
                "body": (
                    "Arctis Sound Manager est une application Linux permettant de configurer les casques "
                    "SteelSeries Arctis. Elle communique avec l'appareil via USB HID et expose les "
                    "réglages dans une interface graphique et une icône dans la barre système.\n\n"
                    "L'application fonctionne comme un service en arrière-plan (asm-daemon) géré "
                    "par systemd, et une interface graphique (asm-gui) qui s'y connecte via D-Bus."
                ),
            },
            {
                "heading": "Démarrage rapide",
                "body": (
                    "1. Branchez votre casque ou DAC SteelSeries en USB.\n"
                    "2. Lancez l'application depuis le menu ou exécutez asm-gui dans un terminal.\n"
                    "3. L'icône de la barre système apparaît automatiquement. Cliquez dessus pour "
                    "ouvrir le menu.\n"
                    "4. La fenêtre principale s'ouvre au premier lancement. La fermer ne quitte "
                    "pas l'application — elle reste active en arrière-plan.\n"
                    "5. Pour rouvrir la fenêtre, cliquez sur l'icône de tray → Ouvrir, ou "
                    "relancez asm-gui depuis le lanceur."
                ),
            },
            {
                "heading": "Accueil",
                "body": (
                    "La page Accueil affiche le statut du casque (En ligne / Hors ligne / En "
                    "charge, niveau de batterie du casque et de la batterie de rechange de la "
                    "base) ainsi que le mixeur des canaux.\n\n"
                    "── Barre de profils ──\n"
                    "Cliquez sur un chip de profil pour restaurer instantanément tous vos réglages. "
                    "Utilisez \"＋ Save current settings\" pour créer un nouveau profil. "
                    "(Voir la section Profils ci-dessous pour les détails.)\n\n"
                    "── Cartes des canaux ──\n"
                    "• Mic — le volume de ce que vos applications enregistrent, le micro qui "
                    "l'alimente, et les applications qui enregistrent\n"
                    "• Master — la sortie du casque lui-même. Sur une base ou un GameDAC, une "
                    "jauge « DAC wheel » montre la position de sa molette de volume\n"
                    "• Game — les jeux et l'audio général (Arctis_Game)\n"
                    "• Chat — les applications vocales : Discord, TeamSpeak… (Arctis_Chat)\n"
                    "• Media — navigateurs et lecteurs vidéo, déplacés automatiquement (Arctis_Media)\n"
                    "• Aux — un canal en plus, désactivé sauf si vous l'activez\n"
                    "• Output — un appareil externe : HDMI, enceintes, autre carte son. Le 5.1 / 7.1 "
                    "passe tel quel\n\n"
                    "Chaque carte affiche le nom de l'appareil sur lequel elle joue ; cliquez "
                    "dessus pour en choisir un autre. Sous le curseur, un sélecteur de preset EQ "
                    "liste les favoris enregistrés pour ce canal dans la page Égaliseur et en "
                    "applique un immédiatement.\n\n"
                    "── Afficher et masquer des cartes ──\n"
                    "Le menu « Canaux ▾ » liste toutes les cartes. Décochez celles dont vous ne "
                    "vous servez pas : masquer une carte la retire seulement de la page, le canal "
                    "continue de fonctionner. Activer ou désactiver Aux dans ce menu crée ou "
                    "supprime le canal lui-même.\n\n"
                    "── Barre ChatMix ──\n"
                    "Équilibre Chat face aux autres canaux, comme la molette ChatMix du casque, "
                    "qu'elle suit en temps réel. Les cases en dessous choisissent lesquels de Game, "
                    "Media et Aux y participent. Le côté le plus faible s'atténue comme dans "
                    "SteelSeries Sonar.\n\n"
                    "── Applications ──\n"
                    "Chaque carte affiche sous forme d'icônes les applications qui jouent à "
                    "travers elle. Survolez une icône pour voir le nom de l'application. "
                    "Glissez-la sur une autre carte pour la déplacer, ou cliquez dessus et "
                    "choisissez un canal. Les applications qui ne sont sur aucun canal sont "
                    "listées sous « Other applications ». Vos choix sont mémorisés et restaurés "
                    "au prochain lancement."
                ),
            },
            {
                "heading": "Ramener l'audio vers le casque",
                "body": (
                    "Si une application n'a soudainement plus de son, c'est généralement parce "
                    "qu'elle joue sur le mauvais périphérique de sortie — un téléviseur en HDMI, un "
                    "port S/PDIF ou une autre carte son — au lieu de votre casque. Cela arrive "
                    "quand le système (WirePlumber) a mémorisé une ancienne sortie pour cette "
                    "application.\n\n"
                    "Le menu du systray → « Bring audio to headset » corrige ça en un "
                    "clic. Il repère toutes les applications qui jouent sur un périphérique "
                    "non-Arctis et les redirige vers votre sink Arctis par défaut ; une "
                    "notification indique combien d'applications ont été déplacées. Les "
                    "applications déjà sur un canal ne sont pas touchées.\n\n"
                    "Il ne change pas la sortie d'un canal : si vous avez envoyé Media vers un "
                    "autre appareil, ce choix est conservé. Pour ramener un canal, choisissez le "
                    "casque sur la carte de ce canal.\n\n"
                    "Vous pouvez cliquer à tout moment sans risque : s'il n'y a rien de mal routé, "
                    "il ne se passe rien."
                ),
            },
            {
                "heading": "Piloter les canaux au clavier",
                "body": (
                    "Le volume d'un canal peut être associé à une touche — y compris les touches "
                    "de fonction inutilisées qu'envoie la molette d'un macropad.\n\n"
                    "Sous Wayland, ASM ne peut pas s'attribuer une combinaison de touches : c'est "
                    "le compositeur qui en décide, le mécanisme qui les distribue n'existe pas sur "
                    "tous les bureaux, et il ne permet pas de réclamer une touche *précise*. "
                    "L'action est donc une commande, et c'est votre bureau qui associe la touche — "
                    "ce qui fonctionne partout de la même façon.\n\n"
                    "À associer dans les réglages de clavier de votre bureau :\n"
                    "• asm-cli volume game +5 — cinq points de plus\n"
                    "• asm-cli volume chat -5 — cinq points de moins\n"
                    "• asm-cli volume media 40 — régler une valeur exacte\n"
                    "• asm-cli volume game mute — également unmute, ou toggle\n"
                    "• asm-cli volume game +5 chat -5 — plusieurs canaux d'une seule touche\n"
                    "• asm-cli volume --list — le niveau actuel de chaque canal\n\n"
                    "Le changement va directement à PipeWire : une touche associée fonctionne que "
                    "cette fenêtre soit ouverte ou non, et le nouveau niveau est mémorisé "
                    "exactement comme celui des curseurs."
                ),
            },
            {
                "heading": "Mode streamer",
                "body": (
                    "Le mode streamer ajoute un second mix indépendant de Game, Chat, Media, Aux "
                    "et de votre micro traité, avec un volume et un muet propres à chaque canal. "
                    "Il est fourni sous la forme d'une source stéréo virtuelle nommée "
                    "\"<appareil> Stream Mix\" que OBS ou n'importe quel enregistreur peut "
                    "capturer — comme le mode Stream de SteelSeries Sonar dans GG.\n\n"
                    "── Activation ──\n"
                    "Sur la page Canaux, activez l'interrupteur \"Mode streamer\" à côté du menu Canaux. "
                    "Les cartes Game, Chat, Media, Aux et Mic affichent alors un petit curseur "
                    "\"Stream\" (0-100) avec un bouton muet sous leur fader. Les changements "
                    "sont appliqués en direct, sans coupure. Activer ou désactiver le mode "
                    "redémarre une fois la chaîne audio : attendez-vous à une courte coupure, "
                    "comme lors d'un changement de preset.\n\n"
                    "── Utilisation dans OBS ──\n"
                    "Sources → + → Capture d'entrée audio → choisissez \"<appareil> Stream "
                    "Mix\". Supprimez les captures séparées de l'audio du bureau et du micro, "
                    "sinon les spectateurs entendent tout en double.\n\n"
                    "── Ce qu'entendent les spectateurs ──\n"
                    "Le mix stream est pris avant vos volumes personnels et avant l'EQ et "
                    "l'audio spatial de chaque canal : les spectateurs entendent le jeu propre, "
                    "pas l'EQ ni le HRTF de votre casque. L'audio de jeu 7.1 est ramené en "
                    "stéréo par un vrai downmix qui conserve le canal central (dialogues). Les "
                    "volumes de votre casque et le cadran ChatMix n'affectent pas le mix stream, "
                    "et inversement."
                ),
            },
            {
                "heading": "Profils",
                "body": (
                    "Les profils permettent de sauvegarder et restaurer toute votre configuration "
                    "audio en un seul clic.\n\n"
                    "── Contenu d'un profil ──\n"
                    "• Mode EQ (Sonar ou Personnalisé)\n"
                    "• Preset EQ actif par canal (Game / Chat / Micro)\n"
                    "• Valeurs des macro sliders (Basses / Voix / Aigus) par canal\n"
                    "• État du Spatial Audio (activé, immersion, distance)\n"
                    "• Volumes des canaux (Game / Chat / Media)\n"
                    "• Les cartes de canaux affichées sur la page Accueil\n\n"
                    "── Créer un profil ──\n"
                    "1. Configurez vos réglages audio comme souhaité.\n"
                    "2. Cliquez sur \"＋ Save current settings\" dans la barre de profils (page Accueil).\n"
                    "3. Donnez un nom au profil et choisissez ce à inclure.\n"
                    "4. Cliquez Enregistrer — le chip de profil apparaît immédiatement.\n\n"
                    "── Appliquer un profil ──\n"
                    "Cliquez sur un chip dans la barre de profils ou sélectionnez-le depuis le "
                    "menu du tray. Les réglages sont appliqués instantanément : les volumes sont "
                    "mis à jour immédiatement, l'EQ et les configs PipeWire sont régénérés et "
                    "le filter-chain est redémarré en arrière-plan.\n\n"
                    "── Supprimer un profil ──\n"
                    "Clic droit sur un chip → Supprimer.\n\n"
                    "── Exemple d'utilisation ──\n"
                    "Créez un profil \"Gaming\" (Sonar EQ, Spatial Audio activé, volume Game élevé) "
                    "et un profil \"Travail\" (EQ Personnalisé, Spatial off, volumes équilibrés). "
                    "Passez de l'un à l'autre en un clic depuis le tray ou la page Accueil."
                ),
            },
            {
                "heading": "Égaliseur",
                "body": (
                    "La page Égaliseur permet d'ajuster la réponse en fréquence du casque. "
                    "Deux modes sont disponibles : Personnalisé et Sonar.\n\n"
                    "── Mode Personnalisé ──\n"
                    "Égaliseur 10 bandes (31 Hz → 16 kHz). Montez un slider pour amplifier, "
                    "descendez pour atténuer. Sauvegarde et chargement de presets disponibles.\n\n"
                    "── Mode Sonar ──\n"
                    "Bascule vers le système Sonar EQ complet via PipeWire filter-chain "
                    "(voir la section Sonar EQ ci-dessous)."
                ),
            },
            {
                "heading": "Sonar EQ",
                "body": (
                    "La page Sonar EQ offre un système d'égalisation paramétrique complet de type "
                    "SteelSeries Sonar avec un onglet par canal : Game, Chat, Media, Aux (s'il est "
                    "activé), Output et Micro.\n\n"
                    "── Courbe EQ ──\n"
                    "Chaque canal dispose d'un EQ paramétrique interactif avec jusqu'à 10 bandes. "
                    "Cliquez sur la courbe pour ajouter une bande, glissez pour ajuster fréquence "
                    "et gain, scrollez pour modifier le Q (largeur de bande).\n\n"
                    "── Presets ──\n"
                    "353 presets Game, 9 Chat et 14 Micro importés de SteelSeries Sonar. "
                    "Barre de recherche et jusqu'à 9 favoris en accès rapide. Un preset Micro "
                    "règle aussi le traitement du micro (ClearCast, réduction de bruit, noise "
                    "gate, compresseur), pas seulement son EQ.\n\n"
                    "── Preset automatique par jeu ──\n"
                    "Quand « Automatic preset per game » est activé, ASM applique le preset Game "
                    "correspondant à un jeu dès son lancement — reconnu via Steam, Lutris, Heroic "
                    "ou le nom du programme — comme le QuickSet de GG. Un preset choisi à la main "
                    "pendant la partie est retenu pour ce jeu, et une notification le signale. Le "
                    "menu du tray → « Preset for <jeu> » permet de revenir à « Automatic », "
                    "d'imposer un preset ou de choisir « Do not change ». "
                    "Désactivé par défaut ; changer de preset redémarre le filter-chain, ce qui "
                    "coupe le son un instant, comme dans GG.\n\n"
                    "── Périphérique de sortie ──\n"
                    "Chaque canal a un sélecteur d'appareil à côté de son EQ : le casque, ou une "
                    "autre sortie (HDMI, enceintes…). C'est le même choix que sur la carte de la "
                    "page Accueil.\n\n"
                    "── Macro sliders ──\n"
                    "Trois curseurs rapides sous la courbe : Basses, Voix et Aigus, "
                    "chacun ±12 dB.\n\n"
                    "── Spatial Audio (Game / Media / Aux) ──\n"
                    "Route le canal via le surround virtuel 7.1 HeSuVi. Un jeu réglé en 5.1 ou "
                    "7.1 envoie ses vraies voies centrale et arrière, placées autour de vous. "
                    "Les curseurs Immersion (0–12 dB) et Distance (réverbération) permettent "
                    "d'affiner l'effet spatial.\n"
                    "« Only for surround sources (5.1 / 7.1) » laisse l'audio stéréo — musique, "
                    "vidéo, jeux dans leur propre mode casque — en stéréo simple, et active le "
                    "surround virtuel tout seul quand quelque chose joue en 5.1 ou 7.1.\n\n"
                    "── Boost de Volume ──\n"
                    "Ajoute jusqu'à +12 dB de gain en fin de chaîne.\n\n"
                    "── Smart Volume ──\n"
                    "Uniformise les différences de volume entre les sources, avec les cibles de "
                    "loudness de Sonar et trois profils : Calme / Équilibré / Fort.\n\n"
                    "Toutes les modifications sont appliquées en direct via PipeWire."
                ),
            },
            {
                "heading": "Import / Export de presets EQ",
                "body": (
                    "ASM vous permet d'exporter et d'importer des presets EQ pour les partager "
                    "ou les sauvegarder.\n\n"
                    "── Exporter un preset ──\n"
                    "Dans la page Sonar EQ, sélectionnez un preset et cliquez sur le bouton "
                    "d'export (↑ ou \"Exporter\" dans le menu du preset). La fenêtre d'export "
                    "propose trois options :\n"
                    "• Lien de partage — une URL compacte arctis-asm://import?data=… à copier "
                    "et envoyer à n'importe qui utilisant ASM ; ils peuvent la coller dans la "
                    "fenêtre d'import pour charger vos réglages EQ instantanément.\n"
                    "• Copier JSON — copie les données brutes du preset dans le presse-papier.\n"
                    "• Enregistrer en fichier — sauvegarde le preset en .json sur votre disque.\n"
                    "• Partager sur la communauté — publie le preset sur le site ASM Presets "
                    "(connexion GitHub requise). Une popup de confirmation apparaît avant la "
                    "publication.\n\n"
                    "── Importer un preset ──\n"
                    "Cliquez sur le bouton d'import (↓ ou \"Importer\"). "
                    "La fenêtre d'import accepte :\n"
                    "• Un lien deep link arctis-asm:// (collez un lien de partage)\n"
                    "• Un lien SteelSeries GG (https://www.steelseries.com/deeplink/…)\n"
                    "• Un fichier .json — cliquez sur \"Rechercher un fichier\" pour en "
                    "sélectionner un depuis votre disque\n"
                    "• Cliquez sur \"Parcourir ASM Presets ↗\" pour ouvrir le site communautaire "
                    "et importer des presets partagés par d'autres utilisateurs.\n\n"
                    "Les presets importés sont enregistrés dans "
                    "~/.config/arctis_manager/sonar_presets/ et apparaissent immédiatement "
                    "dans la liste."
                ),
            },
            {
                "heading": "Communauté ASM Presets",
                "body": (
                    "Le site communautaire ASM Presets vous permet de parcourir, partager et "
                    "voter pour des presets EQ créés par d'autres utilisateurs.\n\n"
                    "Site : https://loteran.github.io/asm-presets/\n\n"
                    "── Ce que vous pouvez faire ──\n"
                    "• Parcourir tous les presets publiés — filtrez par jeu, canal ou appareil\n"
                    "• Voter pour les presets que vous aimez (connexion GitHub requise)\n"
                    "• Publier vos propres presets depuis la fenêtre d'export d'ASM\n"
                    "• Supprimer vos presets (bouton visible uniquement pour l'auteur)\n"
                    "• Copier le lien de partage de n'importe quel preset\n"
                    "• Importer un preset directement en cliquant sur \"Importer dans ASM\" — "
                    "le lien deep link s'ouvre automatiquement dans ASM\n\n"
                    "── Publication ──\n"
                    "Cliquez sur \"Partager sur la communauté\" dans la fenêtre d'export. "
                    "Si vous n'êtes pas connecté, vous serez redirigé vers GitHub pour "
                    "l'authentification, puis ramené automatiquement à la page de soumission.\n\n"
                    "Aucune modération — les presets sont publiés immédiatement. "
                    "La communauté s'appuie sur les votes pour mettre en avant les meilleurs."
                ),
            },
            {
                "heading": "Traitement Micro",
                "body": (
                    "L'onglet Micro dans Sonar EQ inclut des fonctionnalités de traitement "
                    "audio pour votre microphone, appliquées en temps réel via PipeWire.\n\n"
                    "── ClearCast AI Noise Cancellation ──\n"
                    "Utilise rnnoise (réseau neuronal) pour isoler votre voix et supprimer "
                    "le bruit de fond en temps réel. Le curseur ajuste la sensibilité du "
                    "seuil de détection vocale (VAD).\n"
                    "Nécessite le plugin LADSPA noise-suppression-for-voice "
                    "(Arch : pacman, Debian/Ubuntu : apt, Fedora/Nobara : COPR — "
                    "voir le tooltip sur le bouton pour les commandes).\n\n"
                    "── Noise Reduction ──\n"
                    "• Background — filtre passe-haut qui coupe les bruits basse fréquence "
                    "(ventilateurs, climatisation). Le curseur ajuste la fréquence de coupure.\n"
                    "• Impact — filtre high-shelf qui atténue les bruits transitoires "
                    "(clics de clavier, impacts). Le curseur ajuste l'intensité.\n\n"
                    "── Noise Gate ──\n"
                    "Coupe l'audio sous un seuil en dB — rend le micro silencieux quand vous ne "
                    "parlez pas. Seuil réglable de -60 à -10 dB. "
                    "Le mode Auto calcule le seuil optimal.\n"
                    "Nécessite : paquet swh-plugins.\n\n"
                    "── Compresseur ──\n"
                    "Stabilisateur de volume qui uniformise les passages forts et faibles. "
                    "Le curseur contrôle l'intensité de la compression. "
                    "Utile pour garder un niveau micro constant en chat vocal.\n"
                    "Nécessite : paquet swh-plugins.\n\n"
                    "── DeepFilterNet ──\n"
                    "Un second moteur de réduction de bruit, proposé à côté de ClearCast. Il "
                    "coûte plus de CPU et se débrouille mieux sur un bruit de fond constant ; "
                    "ClearCast réagit plus vite. Un seul fonctionne à la fois : choisissez "
                    "celui qui rend le mieux sur votre voix et dans votre pièce.\n\n"
                    "── Quel microphone ──\n"
                    "Par défaut, la chaîne suit le micro de votre casque. Vous pouvez la "
                    "diriger vers un micro de bureau, et laisser ASM basculer tout seul "
                    "entre les deux : quand le casque est déconnecté, quand son micro est "
                    "coupé, ou les deux.\n\n"
                    "Ordre de la chaîne : EQ → Boost → Noise Reduction → Noise Gate → "
                    "ClearCast → Compresseur."
                ),
            },
            {
                "heading": "ANC / Mode Transparent",
                "body": (
                    "Le widget ANC sur la page Casque permet de contrôler la réduction de bruit "
                    "active directement depuis l'interface.\n\n"
                    "Trois modes sont disponibles :\n"
                    "• Off — réduction de bruit désactivée\n"
                    "• Transparent — laisse passer le son extérieur (niveau réglable 10–100%)\n"
                    "• ANC — réduction de bruit active complète\n\n"
                    "Les changements faits depuis les boutons du casque sont reflétés en temps "
                    "réel dans l'interface, et vice-versa."
                ),
            },
            {
                "heading": "Casque / DAC — Infos",
                "body": (
                    "Cette page affiche des informations techniques sur l'appareil connecté :\n\n"
                    "• Nom et modèle de l'appareil\n"
                    "• Identifiant USB (Vendor ID et Product ID)\n"
                    "• Niveau de batterie et état de charge\n"
                    "• Contrôles ANC / Mode Transparent\n"
                    "• Les réglages du casque, une carte par groupe, selon le modèle :\n"
                    "  sidetone, volume micro, limiteur de volume, mise en veille,\n"
                    "  luminosité des LED, puissance du transmetteur… Chacun est appliqué\n"
                    "  au casque dès que vous le modifiez.\n"
                    "• Nova Elite et Nova Pro Omni : les presets EQ micro et Bluetooth\n"
                    "  de la base. Au démarrage, ASM lit les réglages de la base et\n"
                    "  garde ceux que vous n'avez jamais modifiés dans ASM.\n"
                    "• Arctis Pro GameDAC : gain du DAC, line out et son mix stream,\n"
                    "  luminosité de l'écran (en test).\n"
                    "• Nova Pro Wired : line out et son mix stream, mise en veille de\n"
                    "  l'écran (en test).\n"
                    "• Familles Arctis 7 et Arctis 1 Wireless : volume du micro\n"
                    "  (en test).\n"
                    "• Accès au casque (Paramètres) : hidraw, par défaut, laisse le noyau\n"
                    "  garder les interfaces du casque ; libusb est l'ancienne méthode.\n"
                    "  ASM revient seul à libusb si hidraw ne s'ouvre pas.\n"
                    "• Arctis GameBuds : source audio, dongle 2,4 GHz ou Bluetooth\n"
                    "  (en test).\n"
                    "• Arctis Pro Wireless : limiteur de volume, LED du micro coupé,\n"
                    "  économiseur d'écran et son délai, options Bluetooth (en test).\n"
                    "• Arctis GameBuds : quand le boîtier de charge est branché en USB\n"
                    "  à cet ordinateur, sa batterie, son état de charge, le couvercle\n"
                    "  et les écouteurs posés dedans\n\n"
                    "Ces informations sont utiles pour le dépannage ou pour signaler un problème."
                ),
            },
            {
                "heading": "DAC — Écran OLED",
                "body": (
                    "La page DAC est disponible sur les appareils équipés d'un écran OLED : "
                    "Arctis Nova Pro Wireless / X, Nova Elite, Nova Pro Omni, Arctis Pro GameDAC "
                    "et la base de l'Arctis Pro Wireless. Elle n'apparaît dans la barre latérale "
                    "que lorsque l'appareil est détecté.\n\n"
                    "── Affichage personnalisé ──\n"
                    "Le bouton en haut bascule entre l'affichage d'origine du DAC et le mode "
                    "personnalisé. En mode personnalisé, tous les éléments ci-dessous deviennent "
                    "configurables.\n\n"
                    "── Réglages DAC ──\n"
                    "• Luminosité — contrôle la luminosité de l'écran OLED\n"
                    "• Délai d'extinction — éteint l'écran après une période d'inactivité\n"
                    "• Vitesse de défilement — contrôle la vitesse du texte défilant\n\n"
                    "── Éléments affichés ──\n"
                    "Choisissez quelles informations apparaissent sur l'écran OLED et dans quel ordre :\n"
                    "• Heure — toujours affichée ; ajustez la taille de police (7–30 pt)\n"
                    "• Batterie — toujours affichée ; ajustez la taille de police\n"
                    "• Profil — affiche le nom du profil audio actif\n"
                    "• Preset EQ — affiche le preset EQ du canal que vous écoutez "
                    "(« Media: Bass Boost »). Il suit le canal qui joue réellement ; un appel "
                    "Discord ne le prend pas à un jeu en cours\n"
                    "• Lecture en cours — « Artiste - Titre » du lecteur de musique ou de vidéo, "
                    "en défilement\n"
                    "• Météo — affiche la température actuelle\n\n"
                    "Les éléments peuvent être réordonnés avec les boutons ▲/▼. "
                    "Chaque élément dispose d'une case à cocher pour l'activer/désactiver "
                    "et d'un contrôle de taille de police.\n\n"
                    "── Écran éteint en votre absence ──\n"
                    "Avec « Turn screen off while the session is locked or idle » coché, l'écran "
                    "s'éteint quand la session est verrouillée ou inactive, et se rallume à votre "
                    "retour ou quand vous tournez la molette de volume ou de ChatMix. Cela protège "
                    "la dalle OLED du marquage.\n\n"
                    "── Météo ──\n"
                    "Saisissez un nom de ville et cliquez sur Rechercher pour lier votre "
                    "localisation. Choisissez °C ou °F. La température est récupérée "
                    "automatiquement et affichée sur l'écran OLED."
                ),
            },
            {
                "heading": "Clips",
                "body": (
                    "Clips garde les trente dernières secondes de votre écran en mémoire et ne les "
                    "écrit dans un fichier que lorsque vous le demandez — rien n'est enregistré sur "
                    "le disque avant que vous n'appuyiez sur Enregistrer, si bien que le moment qui "
                    "méritait d'être gardé l'est déjà quand vous vous en apercevez.\n\n"
                    "Clips est désactivé par défaut. Activez-le depuis la page Clips ; les paquets "
                    "nécessaires s'installent depuis ce même interrupteur.\n\n"
                    "── Enregistrer un clip ──\n"
                    "• Démarrer arme le tampon glissant. Enregistrer écrit ce qu'il contient.\n"
                    "• Un raccourci global enregistre sans changer de fenêtre. C'est votre bureau "
                    "qui décide de ce raccourci : la combinaison affichée est celle qu'il a "
                    "réellement attribuée. Le bouton Changer… ouvre les réglages de raccourcis de "
                    "votre système.\n"
                    "• Capturer automatiquement pendant qu'un jeu tourne arme le tampon dès qu'un "
                    "jeu émet du son et le relâche quand il disparaît. Un tampon qu'il faut armer "
                    "à la main est armé après le moment qu'on voulait garder.\n\n"
                    "── Ce que contient le fichier ──\n"
                    "Chaque canal Sonar occupe sa propre piste audio : jeu, chat, média et micro "
                    "restent séparés. Un clip peut donc être remixé après coup au lieu de rester "
                    "figé sur ce que vous entendiez à cet instant.\n\n"
                    "── L'éditeur ──\n"
                    "Double-cliquez sur un clip pour l'ouvrir. L'aperçu est une table de mixage : "
                    "toutes les pistes jouent ensemble, chacune avec son niveau et sa coupure, et "
                    "ce que vous réglez est ce qui sera exporté. Faites glisser sur la frise pour "
                    "vous déplacer, le bloc éclairé pour déplacer la sélection, ses bords pour la "
                    "redimensionner. Découpe et niveaux sont enregistrés par clip : fermer "
                    "l'éditeur ne jette plus ce travail.\n\n"
                    "── Où sont enregistrés les clips ──\n"
                    "Dans votre dossier vidéo, sous « ASM Clips ». Changez-le depuis l'engrenage de "
                    "la page Clips — Dossier : Changer…. Les clips déjà enregistrés restent où ils "
                    "sont ; seuls les nouveaux suivent le nouveau dossier. Défaut revient au "
                    "dossier vidéo."
                ),
            },
            {
                "heading": "Stream Guard",
                "body": (
                    "Quand vous partagez votre écran, le son qui l'accompagne est normalement tout "
                    "ce que votre machine joue. Stream Guard tient les canaux que vous n'avez pas "
                    "autorisés à l'écart de ce flux : un appel privé ou une vidéo dans une autre "
                    "fenêtre n'atteint pas votre audience.\n\n"
                    "Il tourne comme son propre service en arrière-plan et n'agit que pendant "
                    "qu'une capture d'écran est réellement en cours."
                ),
            },
            {
                "heading": "Paramètres",
                "body": (
                    "La page Paramètres regroupe les options d'ASM, en cartes. Les réglages du "
                    "casque lui-même (sidetone, mise en veille, LED…) sont sur la page Casque.\n\n"
                    "── Audio ──\n"
                    "• Redirection audio en cas de déconnexion (choix du périphérique de secours)\n"
                    "• Redirection audio à la connexion, et le canal vers lequel va la sortie "
                    "par défaut du système (Media, Game ou Chat)\n\n"
                    "── Paramètres généraux ──\n"
                    "• Lancer au démarrage — active/désactive le démarrage automatique du tray "
                    "via systemd. Quand activé, l'icône apparaît automatiquement après connexion.\n"
                    "• Langue — change la langue de l'interface (EN / FR / ES).\n"
                    "• Chat/Mix Overlay — tourner la molette ChatMix (ou la barre de la page "
                    "Accueil) affiche un instant la barre ChatMix en bas de l'écran, au-dessus de "
                    "la barre des tâches et des jeux en plein écran.\n"
                    "• DAC Wheel Overlay — la même chose pour la molette de volume d'une base ou "
                    "d'un GameDAC.\n\n"
                    "── Plusieurs casques ──\n"
                    "Quand plusieurs Arctis sont branchés, choisissez celui que pilote ASM avec "
                    "« Preferred Arctis Device » sur la page Casque.\n\n"
                    "── Rechercher une mise à jour ──\n"
                    "Le bouton en bas de page force une vérification immédiate auprès de GitHub, "
                    "en ignorant le cache habituel de 24h. Si une nouvelle version est disponible, "
                    "cliquez sur le label pour ouvrir le dialog de mise à jour : pour les installs "
                    "via gestionnaire de paquets (pacman, dnf, apt) il affiche la commande et peut "
                    "ouvrir un terminal automatiquement ; pour pipx/pip il télécharge et installe "
                    "le wheel directement dans l'application."
                ),
            },
            {
                "heading": "Utiliser ASM sans casque SteelSeries",
                "body": (
                    "Les canaux, le volume par canal, l'égaliseur Sonar, le son spatial, le "
                    "routeur d'applications et Clips ne parlent jamais à l'USB : ils travaillent "
                    "sur des périphériques PipeWire. Seul le dialogue avec le casque exige un "
                    "Arctis : batterie, ANC, retour micro, ChatMix, écran OLED.\n\n"
                    "ASM peut donc fonctionner sur n'importe quelle sortie. Dans les Paramètres, "
                    "activez « Utiliser ASM sans casque SteelSeries » et choisissez le "
                    "périphérique sur lequel votre casque est branché. Le périphérique d'entrée "
                    "est facultatif : laissez-le vide si vous ne voulez que la lecture, aucune "
                    "chaîne micro ne sera alors créée.\n\n"
                    "Dans ce mode, les pages Casque et DAC s'affichent vides plutôt que de "
                    "proposer des réglages sans effet.\n\n"
                    "Rien ne change pour un casque SteelSeries : le mode est désactivé par "
                    "défaut, il ne s'active jamais tout seul, et un Arctis connecté a toujours "
                    "la priorité."
                ),
            },
            {
                "heading": "Barre système (tray)",
                "body": (
                    "L'icône de la barre système permet un accès rapide sans ouvrir la fenêtre.\n\n"
                    "• Clic gauche ou droit sur l'icône pour ouvrir le menu contextuel.\n"
                    "• Le menu affiche le statut actuel du casque (batterie, connexion).\n"
                    "• Ouvrir — amène la fenêtre principale au premier plan.\n"
                    "• Profils et presets EQ — en appliquer un sans ouvrir la fenêtre.\n"
                    "• Output Routing — l'appareil de sortie de chaque canal.\n"
                    "• Preset for <jeu> — pendant une partie, si le preset automatique par jeu "
                    "est activé.\n"
                    "• Bring audio to headset — voir la section plus haut.\n"
                    "• Quitter — ferme complètement l'application.\n\n"
                    "── Widget Plasma et barres d'état ──\n"
                    "Sous KDE Plasma 6, un widget affiche l'état et la batterie du casque, la "
                    "batterie de rechange, la réduction de bruit et le micro, et ouvre ASM.\n"
                    "Ailleurs, asm-cli status --json donne les mêmes informations à Waybar, "
                    "Polybar ou n'importe quel script.\n\n"
                    "Fermer la fenêtre principale laisse le tray actif. L'application ne s'arrête "
                    "que si vous choisissez Quitter depuis le menu du tray."
                ),
            },
            {
                "heading": "Son surround",
                "body": (
                    "Arctis Sound Manager prend en charge le son surround virtuel 7.1 via PipeWire "
                    "et des filtres de convolution HRTF compatibles HeSuVi.\n\n"
                    "── Fonctionnement ──\n"
                    "Un sink 7.1 virtuel est créé dans PipeWire. Le son est traité par convolution "
                    "HRTF (HeSuVi) puis réduit en stéréo pour le casque.\n\n"
                    "── Mise en place ──\n"
                    "Le surround est configuré automatiquement lors de l'installation initiale "
                    "(asm-setup). Un sink 'Virtual Surround Sink' apparaît dans vos paramètres "
                    "audio une fois le setup terminé.\n\n"
                    "── Spatial Audio (Sonar) ──\n"
                    "En mode Sonar EQ, les canaux Game, Media et Aux peuvent passer par HeSuVi "
                    "grâce au toggle Spatial Audio. Ces canaux acceptent le 5.1 / 7.1 : un jeu "
                    "réglé en surround envoie ses vraies voies centrale et arrière. Les contrôles "
                    "Immersion et Distance sont inclus — sans configuration manuelle.\n\n"
                    "── Enceintes et amplis ──\n"
                    "Un canal envoyé vers un ampli ou une TV 5.1 / 7.1 reçoit ses voies telles "
                    "quelles, sans rendu casque. Avec Spatial Audio désactivé sur un appareil "
                    "stéréo, le surround est replié en stéréo (centre et surrounds à -3 dB) pour "
                    "ne pas perdre les dialogues d'un jeu en 7.1.\n\n"
                    "── Profil HRIR ──\n"
                    "Le menu déroulant Profil HRIR se trouve dans la page Paramètres. "
                    "Il permet de choisir parmi 57 profils intégrés, groupés par marque : "
                    "Dolby Atmos, CMSS-3D, SBX Pro Studio, Sennheiser GSX, DTS Headphone:X, "
                    "Windows Sonic, Razer Surround, Out Of Your Head, Waves NX, Flux HEar, "
                    "OpenAL / DirectSound3D, Nahimic, Spatial Sound Card, et autres.\n"
                    "La sélection copie le WAV et redémarre filter-chain automatiquement — "
                    "aucune manipulation manuelle nécessaire. Le choix est sauvegardé.\n\n"
                    "Avancé : vous pouvez toujours remplacer manuellement "
                    "~/.local/share/pipewire/hrir_hesuvi/hrir.wav par tout WAV HeSuVi "
                    "14 canaux, puis redémarrer filter-chain."
                ),
            },
            {
                "heading": "Démarrage automatique",
                "body": (
                    "Pour démarrer Arctis Sound Manager automatiquement à la connexion, "
                    "activez le toggle \"Lancer au démarrage\" dans la page Paramètres.\n\n"
                    "Une fois activé, l'icône du tray (asm-gui --systray) démarre "
                    "automatiquement via systemd (arctis-gui.service). Le daemon "
                    "(arctis-manager.service) démarre toujours indépendamment de ce réglage.\n\n"
                    "Pour désactiver le démarrage automatique, désactivez le toggle."
                ),
            },
            {
                "heading": "Dépannage",
                "body": (
                    "Appareil non détecté :\n"
                    "• Vérifiez que les règles udev sont installées : "
                    "asm-cli udev write-rules --reload\n"
                    "• Rebranchez le câble USB.\n"
                    "• Vérifiez que le daemon fonctionne :\n"
                    "  systemd : systemctl --user status arctis-manager.service\n"
                    "  dinit   : dinitctl status arctis-manager\n\n"
                    "Paramètres non appliqués :\n"
                    "• Redémarrez le daemon :\n"
                    "  systemd : systemctl --user restart arctis-manager.service\n"
                    "  dinit   : dinitctl restart arctis-manager\n\n"
                    "Interface qui ne s'ouvre pas :\n"
                    "• Lancez asm-gui -vvvv dans un terminal pour voir les logs détaillés.\n"
                    "• « libxcb-cursor0 is needed to load the Qt xcb platform plugin » :\n"
                    "  sudo apt install libxcb-cursor0 (Fedora/Arch : xcb-util-cursor).\n"
                    "• Vérifiez que le daemon fonctionne (voir ci-dessus).\n\n"
                    "Tray absent au démarrage :\n"
                    "• Activez \"Lancer au démarrage\" dans Paramètres, ou vérifiez :\n"
                    "  systemd : systemctl --user status arctis-gui.service\n"
                    "  dinit   : vérifiez ~/.config/autostart/arctis-gui-autostart.desktop\n\n"
                    "Pas de son / son plat et une bannière « Mode sans échec actif » sur la page Sonar :\n"
                    "• Le mode sans échec est un filet de sécurité : si la chaîne de filtres\n"
                    "  audio PipeWire crashe en boucle, ASM désactive l'EQ pour garder un son\n"
                    "  stable (plat) au lieu de coupures constantes. Le surround 7.1 et l'EQ\n"
                    "  Sonar sont désactivés tant qu'il est actif.\n"
                    "• Une fois la cause disparue (en général une mise à jour d'ASM ou de\n"
                    "  PipeWire), cliquez sur « Réactiver l'EQ » dans la bannière : ASM restaure\n"
                    "  les configs EQ et redémarre la chaîne de filtres. Si ça crashe encore,\n"
                    "  le mode sans échec se réarme automatiquement.\n"
                    "• ASM désarme aussi le mode sans échec tout seul après un changement de\n"
                    "  version ASM/PipeWire, donc la plupart des utilisateurs n'ont jamais\n"
                    "  besoin du bouton."
                ),
            },
        ],
    },
    "es": {
        "title": "Ayuda",
        "subtitle": "Arctis Sound Manager — Manual de usuario",
        "sections": [
            {
                "heading": "Descripción general",
                "body": (
                    "Arctis Sound Manager es una aplicación de Linux para configurar auriculares "
                    "SteelSeries Arctis. Se comunica con el dispositivo a través de USB HID y "
                    "expone los ajustes en una interfaz gráfica y un icono en la bandeja del "
                    "sistema.\n\n"
                    "La aplicación se ejecuta como un servicio en segundo plano (asm-daemon) "
                    "administrado por systemd, y una interfaz gráfica (asm-gui) que se conecta "
                    "a él a través de D-Bus."
                ),
            },
            {
                "heading": "Inicio rápido",
                "body": (
                    "1. Conecta tus auriculares o DAC SteelSeries por USB.\n"
                    "2. Lanza la aplicación desde el menú o ejecuta asm-gui en una terminal.\n"
                    "3. El icono de bandeja aparece automáticamente. Haz clic para abrir el menú.\n"
                    "4. La ventana principal se abre al primer lanzamiento. Cerrarla no cierra la "
                    "aplicación — continúa ejecutándose en segundo plano.\n"
                    "5. Para volver a abrir la ventana, haz clic en el icono de bandeja → Abrir, "
                    "o vuelve a ejecutar asm-gui desde el lanzador."
                ),
            },
            {
                "heading": "Inicio",
                "body": (
                    "La página de Inicio muestra el estado del auricular (En línea / Sin conexión "
                    "/ Cargando, nivel de batería del auricular y de la batería de repuesto de la "
                    "base) y el mezclador de canales.\n\n"
                    "── Barra de perfiles ──\n"
                    "Haz clic en un chip de perfil para restaurar al instante toda tu configuración. "
                    "Usa \"＋ Save current settings\" para crear un nuevo perfil. "
                    "(Ver la sección Perfiles para más detalles.)\n\n"
                    "── Tarjetas de canal ──\n"
                    "• Mic — el volumen de lo que graban tus aplicaciones, el micrófono que lo "
                    "alimenta y las aplicaciones que están grabando\n"
                    "• Master — la salida del propio auricular. En una base o un GameDAC, un "
                    "indicador «DAC wheel» muestra la posición de su rueda de volumen\n"
                    "• Game — juegos y audio general (Arctis_Game)\n"
                    "• Chat — aplicaciones de voz: Discord, TeamSpeak… (Arctis_Chat)\n"
                    "• Media — navegadores y reproductores de vídeo, movidos automáticamente "
                    "(Arctis_Media)\n"
                    "• Aux — un canal extra, desactivado salvo que lo actives\n"
                    "• Output — un dispositivo externo: HDMI, altavoces, otra tarjeta de sonido. "
                    "El 5.1 / 7.1 pasa tal cual\n\n"
                    "Cada tarjeta muestra el nombre del dispositivo en el que suena; haz clic en "
                    "él para elegir otro. Bajo el deslizador, un selector de preset EQ lista los "
                    "favoritos guardados para ese canal en la página Ecualizador y aplica uno al "
                    "instante.\n\n"
                    "── Mostrar y ocultar tarjetas ──\n"
                    "El menú «Canales ▾» lista todas las tarjetas. Desmarca las que no uses: "
                    "ocultar una tarjeta solo la quita de la página, el canal sigue funcionando. "
                    "Activar o desactivar Aux en ese menú crea o elimina el propio canal.\n\n"
                    "── Barra ChatMix ──\n"
                    "Equilibra Chat frente a los demás canales, como la rueda ChatMix del "
                    "auricular, a la que sigue en tiempo real. Las casillas de debajo eligen "
                    "cuáles de Game, Media y Aux participan. El lado más bajo se atenúa como en "
                    "SteelSeries Sonar.\n\n"
                    "── Aplicaciones ──\n"
                    "Cada tarjeta muestra como iconos las aplicaciones que suenan a través de "
                    "ella. Pasa el ratón por un icono para ver el nombre de la aplicación. "
                    "Arrástralo a otra tarjeta para moverla, o haz clic y elige un canal. Las "
                    "aplicaciones que no están en ningún canal aparecen en «Other applications». "
                    "Tus elecciones se recuerdan y se restauran en el siguiente inicio."
                ),
            },
            {
                "heading": "Devolver el audio a los auriculares",
                "body": (
                    "Si una aplicación se queda de repente sin sonido, normalmente se debe a que "
                    "está reproduciendo en el dispositivo de salida equivocado —un televisor por "
                    "HDMI, un puerto S/PDIF u otra tarjeta de sonido— en lugar de tus auriculares. "
                    "Esto ocurre cuando el sistema (WirePlumber) recuerda una salida antigua para "
                    "esa aplicación.\n\n"
                    "El menú de la bandeja del sistema → «Bring audio to headset» lo soluciona con "
                    "un clic. Detecta todas las aplicaciones que están reproduciendo en un "
                    "dispositivo que no es Arctis y las devuelve a tu sink Arctis predeterminado; "
                    "una notificación indica cuántas aplicaciones se han movido. Las aplicaciones "
                    "que ya están en un canal no se modifican.\n\n"
                    "No cambia la salida de un canal: si enviaste Media a otro dispositivo, esa "
                    "elección se mantiene. Para devolver un canal, elige los auriculares en la "
                    "tarjeta de ese canal.\n\n"
                    "Puedes pulsarlo en cualquier momento sin riesgo: si no hay nada mal enrutado, "
                    "no hace nada."
                ),
            },
            {
                "heading": "Controlar los canales con una tecla",
                "body": (
                    "El volumen de un canal puede asignarse a una tecla, incluidas las teclas de "
                    "función sin usar que envía la rueda de un macropad.\n\n"
                    "En Wayland, ASM no puede reservarse una combinación de teclas: eso lo decide "
                    "el compositor, el mecanismo que las reparte no existe en todos los "
                    "escritorios y tampoco permite pedir una tecla *concreta*. Por eso la acción "
                    "es una orden y es su escritorio quien asigna la tecla, lo que funciona igual "
                    "en todas partes.\n\n"
                    "Asígnelas en los ajustes de teclado de su escritorio:\n"
                    "• asm-cli volume game +5 — cinco puntos más\n"
                    "• asm-cli volume chat -5 — cinco puntos menos\n"
                    "• asm-cli volume media 40 — fijar un nivel exacto\n"
                    "• asm-cli volume game mute — también unmute o toggle\n"
                    "• asm-cli volume game +5 chat -5 — varios canales con una sola tecla\n"
                    "• asm-cli volume --list — el nivel actual de cada canal\n\n"
                    "El cambio va directamente a PipeWire: una tecla asignada funciona esté o no "
                    "abierta esta ventana, y el nuevo nivel se recuerda igual que el de los "
                    "deslizadores."
                ),
            },
            {
                "heading": "Modo streamer",
                "body": (
                    "El modo streamer añade una segunda mezcla independiente de Game, Chat, "
                    "Media, Aux y tu micrófono procesado, con volumen y silencio propios para "
                    "cada canal. Se entrega como una fuente estéreo virtual llamada "
                    "\"<dispositivo> Stream Mix\" que OBS o cualquier grabador puede capturar, "
                    "como el modo Stream de SteelSeries Sonar en GG.\n\n"
                    "── Activación ──\n"
                    "En la página Canales, activa el interruptor \"Modo streamer\" junto al menú Canales. "
                    "Las tarjetas Game, Chat, Media, Aux y Mic muestran entonces un pequeño "
                    "control \"Stream\" (0-100) con un botón de silencio bajo su fader. Los "
                    "cambios se aplican en directo, sin cortes. Activar o desactivar el modo "
                    "reinicia una vez la cadena de audio, así que habrá un breve corte, como al "
                    "cambiar de preset.\n\n"
                    "── Uso en OBS ──\n"
                    "Fuentes → + → Captura de entrada de audio → elige \"<dispositivo> Stream "
                    "Mix\". Elimina las capturas separadas del audio del escritorio y del "
                    "micrófono; de lo contrario los espectadores oyen todo por duplicado.\n\n"
                    "── Lo que oyen los espectadores ──\n"
                    "La mezcla de stream se toma antes de tus volúmenes personales y antes del "
                    "EQ y del audio espacial de cada canal, así que los espectadores oyen el "
                    "juego limpio, no el EQ ni el HRTF de tus auriculares. El audio de juego 7.1 "
                    "se pasa a estéreo con un downmix correcto que conserva el canal central "
                    "(diálogos). Los volúmenes de tu casco y el dial ChatMix no afectan a la "
                    "mezcla de stream, ni al revés."
                ),
            },
            {
                "heading": "Perfiles",
                "body": (
                    "Los perfiles permiten guardar y restaurar toda tu configuración de audio "
                    "con un solo clic.\n\n"
                    "── Contenido de un perfil ──\n"
                    "• Modo EQ (Sonar o Personalizado)\n"
                    "• Preset EQ activo por canal (Game / Chat / Micro)\n"
                    "• Valores de macro sliders (Graves / Voces / Agudos) por canal\n"
                    "• Estado del Spatial Audio (activado, inmersión, distancia)\n"
                    "• Volúmenes de canales (Game / Chat / Media)\n"
                    "• Las tarjetas de canal visibles en la página Inicio\n\n"
                    "── Crear un perfil ──\n"
                    "1. Configura tus ajustes de audio como desees.\n"
                    "2. Haz clic en \"＋ Save current settings\" en la barra de perfiles (página Inicio).\n"
                    "3. Asigna un nombre al perfil y elige qué incluir.\n"
                    "4. Haz clic en Guardar — el chip de perfil aparece de inmediato.\n\n"
                    "── Aplicar un perfil ──\n"
                    "Haz clic en un chip en la barra de perfiles o selecciónalo desde el menú "
                    "de la bandeja. Los ajustes se aplican al instante: los volúmenes se actualizan "
                    "inmediatamente, el EQ y las configuraciones de PipeWire se regeneran y el "
                    "filter-chain se reinicia en segundo plano.\n\n"
                    "── Eliminar un perfil ──\n"
                    "Clic derecho en un chip → Eliminar.\n\n"
                    "── Ejemplo de uso ──\n"
                    "Crea un perfil \"Gaming\" (Sonar EQ, Spatial Audio activado, volumen Game alto) "
                    "y un perfil \"Trabajo\" (EQ Personalizado, Spatial off, volúmenes equilibrados). "
                    "Cambia entre ellos con un clic desde la bandeja o la página de Inicio."
                ),
            },
            {
                "heading": "Ecualizador",
                "body": (
                    "La página del Ecualizador permite ajustar la respuesta en frecuencia de los "
                    "auriculares. Dos modos disponibles: Personalizado y Sonar.\n\n"
                    "── Modo Personalizado ──\n"
                    "Ecualizador de 10 bandas (31 Hz → 16 kHz). Sube para realzar, baja para "
                    "atenuar. Permite guardar y cargar presets.\n\n"
                    "── Modo Sonar ──\n"
                    "Cambia al sistema completo Sonar EQ mediante PipeWire filter-chain "
                    "(ver la sección Sonar EQ a continuación)."
                ),
            },
            {
                "heading": "Sonar EQ",
                "body": (
                    "La página Sonar EQ ofrece un sistema de ecualización paramétrica completo "
                    "de tipo SteelSeries Sonar con una pestaña por canal: Game, Chat, Media, Aux "
                    "(si está activado), Output y Micro.\n\n"
                    "── Curva EQ ──\n"
                    "Cada canal dispone de un EQ paramétrico interactivo con hasta 10 bandas. "
                    "Haz clic en la curva para añadir una banda, arrastra para ajustar frecuencia "
                    "y ganancia, desplaza para cambiar el Q (ancho de banda).\n\n"
                    "── Presets ──\n"
                    "353 presets Game, 9 Chat y 14 Micro importados de SteelSeries Sonar. "
                    "Barra de búsqueda y hasta 9 favoritos de acceso rápido. Un preset Micro "
                    "también ajusta el procesamiento del micrófono (ClearCast, reducción de "
                    "ruido, noise gate, compresor), no solo su EQ.\n\n"
                    "── Preset automático por juego ──\n"
                    "Con «Automatic preset per game» activado, ASM aplica el preset Game que "
                    "corresponde a un juego en cuanto se inicia —reconocido a través de Steam, "
                    "Lutris, Heroic o el nombre del programa—, como el QuickSet de GG. Un preset "
                    "elegido a mano durante la partida se recuerda para ese juego, y una "
                    "notificación lo indica. El menú de la bandeja → «Preset for <juego>» "
                    "permite volver a «Automatic», forzar un preset o elegir «Do not "
                    "change». Desactivado por defecto; cambiar de preset reinicia el "
                    "filter-chain, lo que corta el sonido un instante, como en GG.\n\n"
                    "── Dispositivo de salida ──\n"
                    "Cada canal tiene un selector de dispositivo junto a su EQ: los auriculares u "
                    "otra salida (HDMI, altavoces…). Es la misma elección que en la tarjeta de la "
                    "página Inicio.\n\n"
                    "── Macro sliders ──\n"
                    "Tres deslizadores rápidos bajo la curva: Graves, Voces y Agudos, "
                    "cada uno ±12 dB.\n\n"
                    "── Spatial Audio (Game / Media / Aux) ──\n"
                    "Enruta el canal a través del surround virtual 7.1 HeSuVi. Un juego en 5.1 o "
                    "7.1 envía sus canales central y traseros reales, que se colocan a tu "
                    "alrededor. Los deslizadores Inmersión (0–12 dB) y Distancia (reverberación) "
                    "permiten ajustar el efecto espacial.\n"
                    "«Only for surround sources (5.1 / 7.1)» deja el audio estéreo —música, "
                    "vídeo, juegos en su propio modo auriculares— en estéreo normal, y activa el "
                    "surround virtual por sí solo cuando algo suena en 5.1 o 7.1.\n\n"
                    "── Boost de Volumen ──\n"
                    "Añade hasta +12 dB de ganancia al final de la cadena.\n\n"
                    "── Smart Volume ──\n"
                    "Iguala las diferencias de volumen entre fuentes, con los objetivos de "
                    "sonoridad de Sonar y tres perfiles: Silencioso / Equilibrado / Alto.\n\n"
                    "Todos los cambios se aplican en directo mediante PipeWire."
                ),
            },
            {
                "heading": "Importar / Exportar presets EQ",
                "body": (
                    "ASM te permite exportar e importar presets EQ para compartirlos con "
                    "otros usuarios o guardarlos como copia de seguridad.\n\n"
                    "── Exportar un preset ──\n"
                    "En la página Sonar EQ, selecciona un preset y haz clic en el botón "
                    "de exportar (↑ o \"Exportar\" en el menú del preset). El diálogo de "
                    "exportación ofrece tres opciones:\n"
                    "• Enlace de compartición — una URL compacta arctis-asm://import?data=… "
                    "para copiar y enviar a cualquier usuario de ASM; puede pegarla en el "
                    "diálogo de importación para cargar tu EQ al instante.\n"
                    "• Copiar JSON — copia los datos brutos del preset al portapapeles.\n"
                    "• Guardar archivo — guarda el preset como .json en tu disco.\n"
                    "• Compartir en la comunidad — publica el preset en el sitio ASM Presets "
                    "(requiere cuenta GitHub). Aparece un popup de confirmación antes de publicar.\n\n"
                    "── Importar un preset ──\n"
                    "Haz clic en el botón de importar (↓ o \"Importar\"). "
                    "El diálogo de importación acepta:\n"
                    "• Un enlace deep link arctis-asm:// (pega un enlace de compartición)\n"
                    "• Un enlace SteelSeries GG (https://www.steelseries.com/deeplink/…)\n"
                    "• Un archivo .json — haz clic en \"Buscar archivo\" para seleccionarlo\n"
                    "• Haz clic en \"Explorar ASM Presets ↗\" para abrir el sitio comunitario "
                    "e importar presets compartidos por otros usuarios.\n\n"
                    "Los presets importados se guardan en "
                    "~/.config/arctis_manager/sonar_presets/ y aparecen inmediatamente "
                    "en la lista."
                ),
            },
            {
                "heading": "Comunidad ASM Presets",
                "body": (
                    "El sitio comunitario ASM Presets permite explorar, compartir y votar "
                    "presets EQ creados por otros usuarios de ASM.\n\n"
                    "Sitio: https://loteran.github.io/asm-presets/\n\n"
                    "── Qué puedes hacer ──\n"
                    "• Explorar todos los presets publicados — filtra por juego, canal o dispositivo\n"
                    "• Votar por los presets que te gusten (requiere cuenta GitHub)\n"
                    "• Publicar tus propios presets desde el diálogo de exportación de ASM\n"
                    "• Eliminar tus presets (botón visible solo para el autor)\n"
                    "• Copiar el enlace de compartición de cualquier preset\n"
                    "• Importar un preset directamente haciendo clic en \"Importar en ASM\" — "
                    "el deep link se abre automáticamente en ASM\n\n"
                    "── Publicación ──\n"
                    "Haz clic en \"Compartir en la comunidad\" en el diálogo de exportación. "
                    "Si no has iniciado sesión, serás redirigido a GitHub para autenticarte "
                    "y volver automáticamente a la página de envío.\n\n"
                    "Sin moderación — los presets se publican inmediatamente. "
                    "La comunidad usa los votos para destacar los mejores."
                ),
            },
            {
                "heading": "Procesamiento de Micrófono",
                "body": (
                    "La pestaña Micro en Sonar EQ incluye funciones de procesamiento de audio "
                    "para tu micrófono, aplicadas en tiempo real mediante PipeWire.\n\n"
                    "── ClearCast AI Noise Cancellation ──\n"
                    "Usa rnnoise (red neuronal) para aislar tu voz y eliminar el ruido de "
                    "fondo en tiempo real. El deslizador ajusta la sensibilidad del umbral "
                    "de detección vocal (VAD).\n"
                    "Requiere el plugin LADSPA noise-suppression-for-voice "
                    "(Arch: pacman, Debian/Ubuntu: apt, Fedora/Nobara: COPR — "
                    "ver el tooltip del botón para los comandos de instalación).\n\n"
                    "── Noise Reduction ──\n"
                    "• Background — filtro pasa-altos que corta ruidos de baja frecuencia "
                    "(ventiladores, aire acondicionado). El deslizador ajusta la frecuencia.\n"
                    "• Impact — filtro high-shelf que atenúa ruidos transitorios "
                    "(clics de teclado, impactos). El deslizador ajusta la intensidad.\n\n"
                    "── Noise Gate ──\n"
                    "Corta el audio por debajo de un umbral en dB — silencia el micro cuando "
                    "no hablas. Umbral ajustable de -60 a -10 dB. "
                    "El modo Auto calcula el umbral óptimo.\n"
                    "Requiere: paquete swh-plugins.\n\n"
                    "── Compresor ──\n"
                    "Estabilizador de volumen que uniformiza pasajes fuertes y suaves. "
                    "El deslizador controla la intensidad de la compresión. "
                    "Útil para mantener un nivel de micro constante en chat de voz.\n"
                    "Requiere: paquete swh-plugins.\n\n"
                    "── DeepFilterNet ──\n"
                    "Un segundo motor de cancelación de ruido, ofrecido junto a ClearCast. "
                    "Consume más CPU y se defiende mejor con un ruido de fondo constante; "
                    "ClearCast reacciona más rápido. Solo funciona uno a la vez: elija el "
                    "que suene mejor con su voz y en su sala.\n\n"
                    "── Qué micrófono ──\n"
                    "De forma predeterminada, la cadena sigue el micrófono de sus "
                    "auriculares. Puede apuntarla a un micrófono de escritorio y dejar que "
                    "ASM cambie solo entre ambos: cuando los auriculares se desconectan, "
                    "cuando su micrófono está silenciado, o ambas cosas.\n\n"
                    "Orden de la cadena: EQ → Boost → Noise Reduction → Noise Gate → "
                    "ClearCast → Compresor."
                ),
            },
            {
                "heading": "ANC / Modo Transparente",
                "body": (
                    "El widget ANC en la página de Auriculares permite controlar la cancelación "
                    "activa de ruido directamente desde la interfaz.\n\n"
                    "Tres modos disponibles:\n"
                    "• Off — cancelación de ruido desactivada\n"
                    "• Transparente — deja pasar el sonido exterior (nivel ajustable 10–100%)\n"
                    "• ANC — cancelación de ruido activa completa\n\n"
                    "Los cambios realizados desde los botones del auricular se reflejan en tiempo "
                    "real en la interfaz, y viceversa."
                ),
            },
            {
                "heading": "Auriculares / DAC — Información",
                "body": (
                    "Esta página muestra información técnica sobre el dispositivo conectado:\n\n"
                    "• Nombre y modelo del dispositivo\n"
                    "• Identificador USB (Vendor ID y Product ID)\n"
                    "• Nivel de batería y estado de carga\n"
                    "• Controles ANC / Modo Transparente\n"
                    "• Los ajustes de los propios auriculares, una tarjeta por grupo,\n"
                    "  según el modelo: sidetone, volumen del micrófono, limitador de\n"
                    "  volumen, suspensión, brillo de LEDs, potencia del transmisor…\n"
                    "  Cada uno se aplica a los auriculares en cuanto lo modificas.\n"
                    "• Nova Elite y Nova Pro Omni: los presets de EQ del micrófono y de\n"
                    "  Bluetooth de la base. Al arrancar, ASM lee los ajustes de la base\n"
                    "  y conserva los que nunca cambiaste en ASM.\n"
                    "• Arctis Pro GameDAC: ganancia del DAC, line out y su mezcla de\n"
                    "  stream, brillo de la pantalla (en pruebas).\n"
                    "• Nova Pro Wired: line out y su mezcla de stream, atenuación de la\n"
                    "  pantalla (en pruebas).\n"
                    "• Familias Arctis 7 y Arctis 1 Wireless: volumen del micrófono\n"
                    "  (en pruebas).\n"
                    "• Acceso a los auriculares (Ajustes): hidraw, por defecto, deja que el\n"
                    "  kernel conserve las interfaces; libusb es el método anterior. ASM\n"
                    "  vuelve solo a libusb si hidraw no se puede abrir.\n"
                    "• Arctis GameBuds: fuente de audio, dongle de 2,4 GHz o Bluetooth\n"
                    "  (en pruebas).\n"
                    "• Arctis Pro Wireless: limitador de volumen, LED del micro silenciado,\n"
                    "  salvapantallas y su retardo, opciones Bluetooth (en pruebas).\n"
                    "• Arctis GameBuds: mientras el estuche de carga esté conectado por\n"
                    "  USB a este ordenador, su batería, estado de carga, tapa y qué\n"
                    "  auriculares están dentro\n\n"
                    "Esta información es útil para solucionar problemas o reportar incidencias."
                ),
            },
            {
                "heading": "DAC — Pantalla OLED",
                "body": (
                    "La página DAC está disponible en dispositivos con pantalla OLED: Arctis Nova "
                    "Pro Wireless / X, Nova Elite, Nova Pro Omni, Arctis Pro GameDAC y la base "
                    "del Arctis Pro Wireless. Solo aparece en la barra lateral cuando el "
                    "dispositivo está detectado.\n\n"
                    "── Pantalla personalizada ──\n"
                    "El botón superior alterna entre la pantalla original del DAC y el modo "
                    "personalizado. En modo personalizado, todos los elementos siguientes "
                    "se vuelven configurables.\n\n"
                    "── Ajustes DAC ──\n"
                    "• Brillo — controla el brillo de la pantalla OLED\n"
                    "• Tiempo de espera — apaga la pantalla tras un período de inactividad\n"
                    "• Velocidad de desplazamiento — controla la velocidad del texto desplazado\n\n"
                    "── Elementos mostrados ──\n"
                    "Elige qué información aparece en la pantalla OLED y en qué orden:\n"
                    "• Hora — siempre visible; ajusta el tamaño de fuente (7–30 pt)\n"
                    "• Batería — siempre visible; ajusta el tamaño de fuente\n"
                    "• Perfil — muestra el nombre del perfil de audio activo\n"
                    "• Preset EQ — muestra el preset EQ del canal que estás escuchando "
                    "(«Media: Bass Boost»). Sigue al canal que suena de verdad; una llamada de "
                    "Discord no se lo quita a un juego en curso\n"
                    "• Reproduciendo — «Artista - Título» del reproductor de música o vídeo, con "
                    "desplazamiento\n"
                    "• Tiempo meteorológico — muestra la temperatura actual\n\n"
                    "Los elementos pueden reordenarse con los botones ▲/▼. "
                    "Cada elemento tiene una casilla para activarlo/desactivarlo y un "
                    "control de tamaño de fuente.\n\n"
                    "── Pantalla apagada en tu ausencia ──\n"
                    "Con «Turn screen off while the session is locked or idle» marcado, la "
                    "pantalla se apaga mientras la sesión está bloqueada o inactiva, y se vuelve "
                    "a encender cuando vuelves o giras la rueda de volumen o de ChatMix. Así se "
                    "protege el panel OLED del quemado.\n\n"
                    "── Tiempo meteorológico ──\n"
                    "Introduce el nombre de una ciudad y haz clic en Buscar para vincular "
                    "tu ubicación. Elige °C o °F. La temperatura se obtiene automáticamente "
                    "y se muestra en la pantalla OLED."
                ),
            },
            {
                "heading": "Clips",
                "body": (
                    "Clips mantiene en memoria los últimos treinta segundos de su pantalla y solo "
                    "los escribe en un archivo cuando usted lo pide: no se graba nada en el disco "
                    "hasta que pulsa Guardar, de modo que el momento que merecía conservarse ya "
                    "está capturado cuando se da cuenta de ello.\n\n"
                    "Clips viene desactivado. Actívelo desde la página Clips; los paquetes que "
                    "necesita se instalan desde ese mismo interruptor.\n\n"
                    "── Guardar un clip ──\n"
                    "• Iniciar arma el búfer continuo. Guardar escribe lo que contiene.\n"
                    "• Un atajo global guarda sin cambiar de ventana. Es su escritorio quien "
                    "decide ese atajo: la combinación mostrada es la que él ha asignado "
                    "realmente. El botón Cambiar… abre los ajustes de atajos de su sistema.\n"
                    "• Capturar automáticamente mientras se ejecuta un juego arma el búfer cuando "
                    "un juego empieza a emitir sonido y lo libera cuando el juego desaparece. Un "
                    "búfer que hay que armar a mano se arma después del momento que se quería.\n\n"
                    "── Qué contiene el archivo ──\n"
                    "Cada canal de Sonar ocupa su propia pista de audio: juego, chat, multimedia y "
                    "micrófono quedan separados. Así, un clip puede remezclarse después en lugar "
                    "de quedar fijado a lo que usted oía en ese instante.\n\n"
                    "── El editor ──\n"
                    "Haga doble clic en un clip para abrirlo. La vista previa es una mesa de "
                    "mezclas: todas las pistas suenan a la vez, cada una con su nivel y su "
                    "silencio, y lo que ajuste es lo que se exportará. Arrastre sobre la línea de "
                    "tiempo para desplazarse, el bloque iluminado para mover la selección y sus "
                    "bordes para redimensionarla. El recorte y los niveles se guardan por clip: "
                    "cerrar el editor ya no descarta ese trabajo.\n\n"
                    "── Dónde se guardan los clips ──\n"
                    "En su carpeta de vídeos, dentro de «ASM Clips». Cámbiela desde el engranaje "
                    "de la página Clips — Carpeta: Cambiar…. Los clips ya grabados se quedan donde "
                    "están; solo los nuevos siguen la nueva carpeta. Predeterminada vuelve a la "
                    "carpeta de vídeos."
                ),
            },
            {
                "heading": "Stream Guard",
                "body": (
                    "Cuando comparte su pantalla, el sonido que la acompaña es normalmente todo lo "
                    "que su equipo está reproduciendo. Stream Guard mantiene fuera de esa "
                    "transmisión los canales que usted no ha autorizado: una llamada privada o un "
                    "vídeo en otra ventana no llega a su audiencia.\n\n"
                    "Funciona como su propio servicio en segundo plano y solo actúa mientras algo "
                    "está capturando realmente su pantalla."
                ),
            },
            {
                "heading": "Ajustes",
                "body": (
                    "La página de Ajustes agrupa las opciones de ASM, en tarjetas. Los ajustes "
                    "de los propios auriculares (sidetone, suspensión, LEDs…) están en la "
                    "página Auriculares.\n\n"
                    "── Audio ──\n"
                    "• Redirección de audio al desconectar (dispositivo de respaldo)\n"
                    "• Redirección de audio al conectar, y el canal al que va la salida "
                    "predeterminada del sistema (Media, Game o Chat)\n\n"
                    "── Ajustes generales ──\n"
                    "• Iniciar al arranque — activa/desactiva el inicio automático del tray "
                    "mediante systemd. Cuando está activado, el icono aparece tras iniciar sesión.\n"
                    "• Idioma — cambia el idioma de la interfaz (EN / FR / ES).\n"
                    "• Chat/Mix Overlay — al girar la rueda ChatMix (o la barra de la página "
                    "Inicio) se muestra un momento la barra ChatMix en la parte inferior de la "
                    "pantalla, sobre la barra de tareas y los juegos a pantalla completa.\n"
                    "• DAC Wheel Overlay — lo mismo para la rueda de volumen de una base o un "
                    "GameDAC.\n\n"
                    "── Varios auriculares ──\n"
                    "Si hay más de un Arctis conectado, elige cuál controla ASM con «Preferred "
                    "Arctis Device» en la página Auriculares.\n\n"
                    "── Buscar actualización ──\n"
                    "El botón al final de la página fuerza una comprobación inmediata en GitHub, "
                    "ignorando la caché habitual de 24h. Si hay una versión más reciente, "
                    "haz clic en el resultado para abrir el diálogo de actualización: para installs "
                    "con gestor de paquetes (pacman, dnf, apt) muestra el comando y puede abrir un "
                    "terminal automáticamente; para pipx/pip descarga e instala el wheel directamente."
                ),
            },
            {
                "heading": "Usar ASM sin auriculares SteelSeries",
                "body": (
                    "Los canales, el volumen por canal, el ecualizador Sonar, el audio espacial, "
                    "el enrutador de aplicaciones y Clips nunca hablan con el USB: trabajan sobre "
                    "dispositivos de PipeWire. Solo la conversación con los auriculares necesita "
                    "un Arctis: batería, ANC, retorno de micrófono, ChatMix, pantalla OLED.\n\n"
                    "Por eso ASM puede funcionar con cualquier salida. En Ajustes, active «Usar "
                    "ASM sin auriculares SteelSeries» y elija el dispositivo donde están "
                    "conectados sus auriculares. El dispositivo de entrada es opcional: déjelo "
                    "vacío si solo quiere reproducción y no se creará ninguna cadena de "
                    "micrófono.\n\n"
                    "En este modo, las páginas Auriculares y DAC aparecen vacías en lugar de "
                    "mostrar controles que no harían nada.\n\n"
                    "Nada cambia para unos auriculares SteelSeries: el modo viene desactivado, "
                    "nunca se activa solo y unos Arctis conectados siempre tienen prioridad."
                ),
            },
            {
                "heading": "Bandeja del sistema",
                "body": (
                    "El icono de bandeja proporciona acceso rápido sin abrir la ventana "
                    "completa.\n\n"
                    "• Clic izquierdo o derecho en el icono para abrir el menú contextual.\n"
                    "• El menú muestra el estado actual del auricular (batería, conexión).\n"
                    "• Abrir — lleva la ventana principal al primer plano.\n"
                    "• Perfiles y presets EQ — aplica uno sin abrir la ventana.\n"
                    "• Ruta de salida — el dispositivo de salida de cada canal.\n"
                    "• Preset for <juego> — durante una partida, con el preset automático por "
                    "juego activado.\n"
                    "• Bring audio to headset — ver la sección de arriba.\n"
                    "• Salir — cierra completamente la aplicación.\n\n"
                    "── Widget de Plasma y barras de estado ──\n"
                    "En KDE Plasma 6, un widget muestra el estado y la batería del auricular, la "
                    "batería de repuesto, la cancelación de ruido y el micrófono, y abre ASM.\n"
                    "En otros entornos, asm-cli status --json da la misma información a Waybar, "
                    "Polybar o cualquier script.\n\n"
                    "Cerrar la ventana principal deja activa la bandeja. La aplicación solo se "
                    "detiene si eliges Salir desde el menú de la bandeja."
                ),
            },
            {
                "heading": "Sonido envolvente",
                "body": (
                    "Arctis Sound Manager admite sonido envolvente virtual 7.1 mediante PipeWire "
                    "y filtros de convolución HRTF compatibles con HeSuVi.\n\n"
                    "── Cómo funciona ──\n"
                    "Se crea un sink 7.1 virtual en PipeWire. El audio es procesado por "
                    "convolución HRTF (HeSuVi) y reducido a estéreo para los auriculares.\n\n"
                    "── Configuración ──\n"
                    "El sonido envolvente se configura automáticamente durante la instalación "
                    "inicial (asm-setup). Un sink 'Virtual Surround Sink' aparecerá en tus "
                    "ajustes de audio una vez completado el setup.\n\n"
                    "── Spatial Audio (Sonar) ──\n"
                    "En modo Sonar EQ, los canales Game, Media y Aux pueden pasar por HeSuVi "
                    "mediante el toggle Spatial Audio. Estos canales aceptan 5.1 / 7.1: un juego "
                    "en surround envía sus canales central y traseros reales. Incluye controles "
                    "de Inmersión y Distancia — sin configuración manual.\n\n"
                    "── Altavoces y receptores ──\n"
                    "Un canal enviado a un receptor o TV 5.1 / 7.1 recibe sus canales tal cual, "
                    "sin procesamiento para auriculares. Con Spatial Audio desactivado en un "
                    "dispositivo estéreo, el surround se mezcla en estéreo (centro y surrounds a "
                    "-3 dB) para no perder los diálogos de un juego en 7.1.\n\n"
                    "── Perfil HRIR ──\n"
                    "El menú desplegable Perfil HRIR se encuentra en la página de Ajustes. "
                    "Permite elegir entre 57 perfiles integrados, agrupados por marca: "
                    "Dolby Atmos, CMSS-3D, SBX Pro Studio, Sennheiser GSX, DTS Headphone:X, "
                    "Windows Sonic, Razer Surround, Out Of Your Head, Waves NX, Flux HEar, "
                    "OpenAL / DirectSound3D, Nahimic, Spatial Sound Card, y más.\n"
                    "Al seleccionar un perfil se copia el WAV y se reinicia filter-chain "
                    "automáticamente — sin pasos manuales. Tu elección se guarda entre sesiones.\n\n"
                    "Avanzado: aún puedes reemplazar manualmente "
                    "~/.local/share/pipewire/hrir_hesuvi/hrir.wav con cualquier WAV "
                    "HeSuVi de 14 canales, luego reinicia filter-chain."
                ),
            },
            {
                "heading": "Inicio automático",
                "body": (
                    "Para iniciar Arctis Sound Manager automáticamente al iniciar sesión, "
                    "activa el interruptor \"Iniciar al arranque\" en la página de Ajustes.\n\n"
                    "Una vez activado, el icono de bandeja (asm-gui --systray) se inicia "
                    "automáticamente mediante systemd (arctis-gui.service). El daemon "
                    "(arctis-manager.service) siempre se inicia independientemente de este ajuste.\n\n"
                    "Para desactivar el inicio automático, desactiva el interruptor."
                ),
            },
            {
                "heading": "Solución de problemas",
                "body": (
                    "Dispositivo no detectado:\n"
                    "• Verifica que las reglas udev estén instaladas: "
                    "asm-cli udev write-rules --reload\n"
                    "• Reconecta el cable USB.\n"
                    "• Comprueba que el daemon esté activo:\n"
                    "  systemd: systemctl --user status arctis-manager.service\n"
                    "  dinit:   dinitctl status arctis-manager\n\n"
                    "Ajustes no aplicados:\n"
                    "• Reinicia el daemon:\n"
                    "  systemd: systemctl --user restart arctis-manager.service\n"
                    "  dinit:   dinitctl restart arctis-manager\n\n"
                    "La interfaz no se abre:\n"
                    "• Ejecuta asm-gui -vvvv en una terminal para ver los registros detallados.\n"
                    "• \"libxcb-cursor0 is needed to load the Qt xcb platform plugin\":\n"
                    "  sudo apt install libxcb-cursor0 (Fedora/Arch: xcb-util-cursor).\n"
                    "• Comprueba que el daemon esté activo (ver arriba).\n\n"
                    "El icono de bandeja no aparece al inicio:\n"
                    "• Activa \"Iniciar al arranque\" en Ajustes, o comprueba:\n"
                    "  systemd: systemctl --user status arctis-gui.service\n"
                    "  dinit:   comprueba ~/.config/autostart/arctis-gui-autostart.desktop"
                ),
            },
        ],
    },
}

# ── Styles ─────────────────────────────────────────────────────────────────────


_HEADING_STYLE = (
    f"color: {TEXT_PRIMARY}; font-size: 14pt; font-weight: bold; "
    "background: transparent; padding: 0;"
)

_BODY_STYLE = (
    f"color: {TEXT_SECONDARY}; font-size: 11pt; background: transparent; "
    "padding: 0; line-height: 1.5;"
)


# ── HelpPage ───────────────────────────────────────────────────────────────────

class HelpPage(QWidget):
    _current_lang: str = "en"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {BG_MAIN};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header (title + lang selector) — fixed, outside scroll ──────────
        header_widget = QWidget()
        header_widget.setStyleSheet(f"background-color: {BG_MAIN};")
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(36, 28, 36, 0)
        header_layout.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setSpacing(16)

        self._title_label = QLabel("Help")
        self._title_label.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 28pt; font-weight: bold; background: transparent;"
        )
        title_row.addWidget(self._title_label, stretch=1)

        # Report a bug button
        _BTN = (
            "QPushButton {{ background-color: {bg}; color: {fg}; border: none; "
            "border-radius: 6px; padding: 6px 14px; font-size: 9pt; }}"
            "QPushButton:hover {{ background-color: {hover}; }}"
        )
        report_btn = QPushButton(I18n.translate('ui', 'report_bug'))
        report_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        report_btn.setStyleSheet(_BTN.format(bg=BG_BUTTON, fg=TEXT_PRIMARY, hover=BG_BUTTON_HOVER))
        report_btn.clicked.connect(lambda: ReportBugDialog(parent=self).exec())
        title_row.addWidget(report_btn)
        header_layout.addLayout(title_row)

        self._subtitle_label = QLabel("Arctis Sound Manager — User Manual")
        self._subtitle_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11pt; background: transparent;"
        )
        header_layout.addWidget(self._subtitle_label)
        header_layout.addSpacing(16)

        outer.addWidget(header_widget)

        divider = DividerLine()
        outer.addWidget(divider)

        # ── Scrollable content area ──────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {BG_MAIN}; border: none; }}"
        )

        self._content_widget = QWidget()
        self._content_widget.setStyleSheet(f"background-color: {BG_MAIN};")
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._content_layout.setContentsMargins(36, 24, 36, 36)
        self._content_layout.setSpacing(0)

        scroll.setWidget(self._content_widget)
        outer.addWidget(scroll, stretch=1)

        self._set_language(I18n.current_lang())
        I18n.on_language_changed(lambda: self._set_language(I18n.current_lang()))

        # Apply the currently-active theme on first paint.
        self.apply_theme()

    # ── Theme propagation ─────────────────────────────────────────────────────

    def apply_theme(self, t=None) -> None:
        """Restyle the help page for the current active theme."""
        self.setStyleSheet(f"background-color: {_theme.c('BG_MAIN')};")

        self._title_label.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 28pt; font-weight: bold; background: transparent;"
        )
        self._subtitle_label.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 11pt; background: transparent;"
        )
        self._content_widget.setStyleSheet(f"background-color: {_theme.c('BG_MAIN')};")

        # Rebuild the content section cards with the new theme colors
        self._set_language(self._current_lang)

    # ── Language switching ─────────────────────────────────────────────────

    def _set_language(self, lang: str) -> None:
        lang = lang if lang in HELP_CONTENT else 'en'
        self._current_lang = lang
        data = HELP_CONTENT[lang]

        # Update header labels
        self._title_label.setText(data["title"])
        self._subtitle_label.setText(data["subtitle"])

        # Rebuild content
        self._clear_content()
        for section in data["sections"]:
            self._add_section(section["heading"], section["body"])

    def _clear_content(self) -> None:
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _add_section(self, heading: str, body: str) -> None:
        # Section card — uses active-theme colors so apply_theme rebuilds correctly
        accent_link = _theme.c("ACCENT")
        card = QWidget()
        card.setStyleSheet(
            f"background-color: {_theme.c('BG_CARD')}; border-radius: 10px; "
            f"border: 1px solid {_theme.c('BORDER')};"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 16, 20, 16)
        card_layout.setSpacing(8)

        h_label = QLabel(heading)
        h_label.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 14pt; font-weight: bold; "
            "background: transparent; padding: 0;"
        )
        card_layout.addWidget(h_label)

        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {_theme.c('BORDER')};")
        card_layout.addWidget(sep)

        import re as _re
        html_body = _re.sub(
            r'(https?://[^\s]+)',
            rf'<a href="\1" style="color: {accent_link};">\1</a>',
            body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>"),
        )
        b_label = QLabel(html_body)
        b_label.setWordWrap(True)
        b_label.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 11pt; background: transparent; "
            "padding: 0; line-height: 1.5;"
        )
        b_label.setTextFormat(Qt.TextFormat.RichText)
        b_label.setOpenExternalLinks(True)
        b_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        b_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        card_layout.addWidget(b_label)

        self._content_layout.addWidget(card)
        self._content_layout.addSpacing(12)
