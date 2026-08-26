#!/usr/bin/env bash
# Arctis Sound Manager: USB access diagnostic
#
# Read-only. Removes nothing, installs nothing, changes no setting. It answers
# one question: why can ASM not open the headset on this machine?
#
# Run it:
#   curl -fsSL https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/scripts/diagnose-usb-access.sh | bash
#
# It writes a report to ~/asm-usb-access.txt and prints it. Attach that file to
# the issue or discussion you are replying to.
#
# Self-contained on purpose: no BASH_SOURCE, no prompts, nothing read from
# stdin. stdin is the script itself when this is piped into bash, which is
# exactly what broke the uninstaller in discussion #190.
set -uo pipefail   # no -e: a probe that fails is a result, not a reason to stop

OUT="$HOME/asm-usb-access.txt"
: > "$OUT"

say()  { printf '%s\n' "$*" | tee -a "$OUT"; }
head_() { printf '\n===== %s =====\n' "$*" | tee -a "$OUT"; }
# Run a command into the report. Everything is teed: a section that shows on
# screen but not in the file, or the reverse, reads as "nothing found" to
# whoever is looking, and there is no way to tell that apart from a real empty.
run()  {
    if command -v "$1" >/dev/null 2>&1; then
        { "$@" 2>&1 || true; } | tee -a "$OUT"
    else
        say "($1 not installed)"
    fi
}

say "Arctis Sound Manager: USB access diagnostic"
say "date: $(date -Is)"

head_ "System"
say "kernel:  $(uname -r)"
say "distro:  $(. /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-unknown}")"
# bash 5.3 turned `cd ""` into an error, which is what made the old piped
# uninstaller die outright rather than merely misbehave.
say "bash:    ${BASH_VERSION:-unknown}"
say "session: ${XDG_SESSION_TYPE:-unset} / ${XDG_CURRENT_DESKTOP:-unset}"
say "user:    $(id -un) uid=$(id -u) groups=$(id -Gn | tr ' ' ',')"

head_ "Which ASM is installed"
# Both names matter: Fedora's Terra ships this project as
# python3-arctis-sound-manager, and looking only for our own name is how the
# uninstaller concluded "nothing installed" on Nobara.
for pkg in arctis-sound-manager python3-arctis-sound-manager; do
    if command -v rpm >/dev/null 2>&1; then
        v=$(rpm -q --qf '%{VERSION}-%{RELEASE} vendor=%{VENDOR}' "$pkg" 2>/dev/null)
        [ -n "$v" ] && say "rpm    $pkg: $v" || say "rpm    $pkg: not installed"
    fi
done
run dpkg-query -W -f='dpkg   arctis-sound-manager: ${Version}\n' arctis-sound-manager
run pacman -Q arctis-sound-manager

say ""
say "asm binaries in PATH:"
command -v -a asm-daemon asm-gui asm-cli 2>/dev/null | tee -a "$OUT" || say "  (none)"
say ""
say "pip --user copies (these override the packaged one: Python looks in ~/.local first):"
ls -d "$HOME"/.local/lib/python*/site-packages/arctis_sound_manager 2>/dev/null | tee -a "$OUT" \
    || say "  (none: good)"

head_ "udev rules"
FOUND_RULES=""
for p in /etc/udev/rules.d/91-steelseries-arctis.rules \
         /usr/lib/udev/rules.d/91-steelseries-arctis.rules \
         /lib/udev/rules.d/91-steelseries-arctis.rules \
         /run/udev/rules.d/91-steelseries-arctis.rules; do
    if [ -e "$p" ]; then
        FOUND_RULES="$FOUND_RULES $p"
        # A rules file that is not world-readable is silently ignored by udev.
        say "$(stat -c '%A %U:%G %n' "$p" 2>/dev/null)"
        say "  product ids covered: $(grep -o 'idProduct}=="[0-9a-fA-F|]*"' "$p" 2>/dev/null \
            | sed 's/.*"\(.*\)"/\1/' | tr '\n' ' ')"
    fi
done
[ -n "$FOUND_RULES" ] || say "NO rules file found at any known path."

head_ "SteelSeries devices the kernel sees"
# Straight from sysfs: this works with no permissions at all, which is the
# whole point: every other view of USB in ASM goes through pyusb and so goes
# blank exactly when permissions are the problem.
FOUND_DEV=0
for d in /sys/bus/usb/devices/*; do
    [ -r "$d/idVendor" ] || continue
    [ "$(cat "$d/idVendor" 2>/dev/null)" = "1038" ] || continue
    FOUND_DEV=1
    pid=$(cat "$d/idProduct" 2>/dev/null)
    say ""
    say "$(basename "$d"): 1038:$pid  $(cat "$d/product" 2>/dev/null)"
    # Which interface is this device's vendor control channel. ASM's device
    # profiles name one by number, and the number is the one value SteelSeries'
    # specifications never carry, so it was typed by hand for every profile.
    # class=03 is HID; the usage page tells the vendor control channel (0xff00
    # and up) apart from the consumer-control interface carrying media keys
    # (0x000c). Reading it here needs no permissions and disturbs no driver:
    # the kernel already parsed the descriptor and published it.
    for i in "$d":*; do
        [ -r "$i/bInterfaceNumber" ] || continue
        inum=$(cat "$i/bInterfaceNumber" 2>/dev/null)
        icls=$(cat "$i/bInterfaceClass" 2>/dev/null || echo "??")
        idrv="(none - unclaimed)"
        [ -L "$i/driver" ] && idrv=$(basename "$(readlink -f "$i/driver")")
        page="not readable"
        for rd in "$i"/*/report_descriptor; do
            [ -r "$rd" ] || continue
            # The descriptor opens with its usage page: 06 lo hi (long form,
            # little-endian) or 05 xx (short form).
            bytes=$(od -An -tx1 -N3 "$rd" 2>/dev/null | tr -s ' ' | sed 's/^ //')
            b0=$(echo "$bytes" | cut -d' ' -f1)
            b1=$(echo "$bytes" | cut -d' ' -f2)
            b2=$(echo "$bytes" | cut -d' ' -f3)
            if [ "$b0" = "06" ]; then
                page="0x$b2$b1"
            elif [ "$b0" = "05" ]; then
                page="0x00$b1"
            fi
            break
        done
        case "$page" in
            0xff*) page="$page (vendor control channel)" ;;
        esac
        say "  interface $inum: class=0x$icls usage_page=$page driver=$idrv"
    done
    bus=$(cat "$d/busnum" 2>/dev/null); dev=$(cat "$d/devnum" 2>/dev/null)
    if [ -z "$bus" ] || [ -z "$dev" ]; then
        say "  (no bus/dev number: cannot locate the device node)"
        continue
    fi
    node=$(printf '/dev/bus/usb/%03d/%03d' "$bus" "$dev")
    say "  node: $(stat -c '%A %U:%G %n' "$node" 2>/dev/null || echo "$node missing")"
    # TAG+="uaccess" grants access by adding an ACL entry for the seat's user.
    # A named entry present or absent separates "the rules never matched this
    # device" from "the rules matched, but after it was already enumerated".
    if command -v getfacl >/dev/null 2>&1; then
        acl=$(getfacl -p "$node" 2>/dev/null | grep '^user:[^:]' )
        if [ -n "$acl" ]; then
            say "  acl:  $acl"
        else
            say "  acl:  no named user entry  <-- uaccess did not tag this device"
        fi
    else
        say "  acl:  (getfacl not installed)"
    fi
    if [ -w "$node" ]; then
        say "  writable by me: yes"
    else
        say "  writable by me: NO  <-- this is what the permissions popup is about"
    fi
    if command -v udevadm >/dev/null 2>&1; then
        _props=$(udevadm info --query=property --path="$d" 2>/dev/null \
            | grep -E '^(TAGS|CURRENT_TAGS|ID_VENDOR_ID|ID_MODEL_ID)=' | sed 's/^/  /')
        [ -n "$_props" ] && say "$_props"
    fi
done
[ "$FOUND_DEV" -eq 1 ] || say "No SteelSeries device is plugged in / enumerated."

head_ "lsusb (SteelSeries lines only)"
if command -v lsusb >/dev/null 2>&1; then
    _lsusb=$(lsusb 2>/dev/null | grep -i '1038:')
    say "${_lsusb:-(no 1038: device listed)}"
else
    say "(lsusb not installed: usbutils)"
fi

head_ "Where audio is actually going"
# The second half of #216: applications that could not be moved off
# "Arctis 7 Game", to the point of uninstalling ASM to get sound back. ASM
# never moves an application's stream itself, so what matters is the default
# sink and whether the Arctis sinks still have a working path to hardware.
# An application that reopens its stream lands on the default sink again, so a
# default left pointing at an Arctis sink whose loopback is dead looks exactly
# like "the change would not take".
if command -v pactl >/dev/null 2>&1; then
    say "default sink: $(pactl get-default-sink 2>/dev/null || echo unknown)"
    say ""
    say "sinks:"
    pactl list short sinks 2>/dev/null | sed 's/^/  /' | tee -a "$OUT"
    say ""
    say "playback streams and the sink each is on:"
    # Which sink a stream sits on is the question; the app name says whose it is.
    pactl list sink-inputs 2>/dev/null         | grep -E 'Sink Input #|Sink:|application.name = '         | sed 's/^[[:space:]]*/  /' | tee -a "$OUT"
else
    say "(pactl not installed: pulseaudio-utils)"
fi

say ""
if command -v pw-link >/dev/null 2>&1; then
    say "links out of the Arctis sinks (empty means audio has nowhere to go):"
    _links=$(pw-link -l 2>/dev/null | grep -A2 -i 'arctis\|sonar' | head -40)
    say "${_links:-(none found)}"
else
    say "(pw-link not installed: pipewire-utils)"
fi

head_ "ASM processes still running"
# "Closing it leaves it in the system monitor": the daemon is a separate user
# service and stays up by design, so what matters is which of these is which.
_procs=$(ps -eo pid,etime,cmd 2>/dev/null \
    | grep -E 'asm-(daemon|gui|cli|router|clipd|stream-guard)' | grep -v grep)
say "${_procs:-(no ASM process running)}"

head_ "ASM user services"
run systemctl --user --no-pager --plain list-units 'arctis-*'
say ""
run systemctl --user is-enabled arctis-manager.service

head_ "Which interface the daemon settled on"
# 1.4.10 could move commands to another interface without saying much about
# it, and the profile it moved off is the one it kept claiming, so every write
# went to an interface usbhid still held. These lines say whether that
# happened here (issues #216 and #217).
if command -v journalctl >/dev/null 2>&1; then
    _iface=$(journalctl --user -u arctis-manager.service -n 4000 --no-pager 2>/dev/null         | grep -E 'Command interface resolved|does not match the hardware|cannot be this device|Detaching kernel driver|Kernel driver active on interface|holding the interface'         | tail -25)
    say "${_iface:-(nothing about interface selection in the journal)}"
else
    say "(journalctl not available)"
fi

head_ "Daemon log (last 60 lines)"
run journalctl --user -u arctis-manager.service -n 60 --no-pager

say ""
say "======================================================================"
say "Report written to: $OUT"
say "Attach that file to the discussion: drag and drop it into the reply box."
say "======================================================================"
