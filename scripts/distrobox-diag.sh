#!/usr/bin/env bash
# Arctis Sound Manager — Distrobox diagnostic script
# Run this after installing ASM via distrobox-install.sh to verify everything works.
#
# Usage:
#   bash scripts/distrobox-diag.sh
#
# Output:
#   - Real-time check results on stdout (colored ✓/✗)
#   - Full log report at ~/asm-distrobox-diag-YYYYMMDD-HHMMSS.txt

set -uo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CONTAINER_NAME="arctis-sound-manager"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
REPORT_FILE="$HOME/asm-distrobox-diag-${TIMESTAMP}.txt"

# ---------------------------------------------------------------------------
# Color helpers (tput with fallback if not a tty)
# ---------------------------------------------------------------------------
if [ -t 1 ] && command -v tput &>/dev/null && tput setaf 1 &>/dev/null; then
    GREEN="$(tput setaf 2)"
    RED="$(tput setaf 1)"
    BOLD="$(tput bold)"
    RESET="$(tput sgr0)"
else
    GREEN=""
    RED=""
    BOLD=""
    RESET=""
fi

PASS="${GREEN}✓${RESET}"
FAIL="${RED}✗${RESET}"

# ---------------------------------------------------------------------------
# State tracking
# ---------------------------------------------------------------------------
CHECKS_TOTAL=0
CHECKS_FAILED=0
declare -a CHECK_SUMMARY=()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
check() {
    local label="$1"
    local result="$2"   # "pass" or "fail"
    local detail="${3:-}"

    CHECKS_TOTAL=$(( CHECKS_TOTAL + 1 ))

    if [ "$result" = "pass" ]; then
        printf "  %s %s\n" "$PASS" "$label"
        CHECK_SUMMARY+=("PASS: $label${detail:+  ($detail)}")
    else
        printf "  %s %s%s%s\n" "$FAIL" "$BOLD" "$label" "$RESET"
        [ -n "$detail" ] && printf "      %s\n" "$detail"
        CHECKS_FAILED=$(( CHECKS_FAILED + 1 ))
        CHECK_SUMMARY+=("FAIL: $label${detail:+  ($detail)}")
    fi
}

section() {
    printf "\n%s=== %s ===%s\n" "$BOLD" "$1" "$RESET"
}

# Write a line to both stdout and the report file
log_report() {
    printf "%s\n" "$1" >> "$REPORT_FILE"
}

# Run a command and append its output to the report file with a header
report_cmd() {
    local header="$1"
    shift
    {
        printf "\n--- %s ---\n" "$header"
        "$@" 2>&1 || true
        printf "\n"
    } >> "$REPORT_FILE"
}

# ---------------------------------------------------------------------------
# Begin report file
# ---------------------------------------------------------------------------
{
    printf "ASM Distrobox Diagnostic Report\n"
    printf "Generated: %s\n" "$(date)"
    printf "Container: %s\n" "$CONTAINER_NAME"
    printf "Host: %s\n" "$(uname -n)"
    printf "\n"
} > "$REPORT_FILE"

# ---------------------------------------------------------------------------
# Section 1 — Distrobox infrastructure
# ---------------------------------------------------------------------------
section "Distrobox infrastructure"

# 1. Distrobox binary present
if command -v distrobox &>/dev/null; then
    check "distrobox installed" pass "$(distrobox --version 2>/dev/null | head -1)"
else
    check "distrobox installed" fail "distrobox not found in PATH"
fi

# 2. Container exists
if distrobox list 2>/dev/null | grep -q "$CONTAINER_NAME"; then
    check "container '$CONTAINER_NAME' exists" pass
else
    check "container '$CONTAINER_NAME' exists" fail "run distrobox-install.sh first"
fi

# 3. Container is running (not stopped)
CONTAINER_STATUS="$(distrobox list 2>/dev/null | grep "$CONTAINER_NAME" | awk '{print $NF}' || true)"
if distrobox list 2>/dev/null | grep "$CONTAINER_NAME" | grep -qi "running\|up"; then
    check "container is running" pass
else
    # Try to enter briefly to start it; some distrobox versions auto-start on enter
    if distrobox enter "$CONTAINER_NAME" -- true &>/dev/null 2>&1; then
        check "container is running" pass "was stopped, started successfully"
    else
        check "container is running" fail "status: ${CONTAINER_STATUS:-unknown}"
    fi
fi

# ---------------------------------------------------------------------------
# Section 2 — Exported binaries
# ---------------------------------------------------------------------------
section "Exported binaries"

for bin in asm-gui asm-cli asm-setup asm-router; do
    if [ -f "$HOME/.local/bin/$bin" ] && [ -x "$HOME/.local/bin/$bin" ]; then
        check "~/.local/bin/$bin" pass
    else
        check "~/.local/bin/$bin" fail "not found or not executable"
    fi
done

# ---------------------------------------------------------------------------
# Section 3 — Desktop entry
# ---------------------------------------------------------------------------
section "Desktop entry"

DESKTOP_FILE="$(find "$HOME/.local/share/applications/" -iname "*arctis*" -o -iname "*asm*" 2>/dev/null | head -1)"
if [ -n "$DESKTOP_FILE" ]; then
    check "desktop entry exported" pass "$(basename "$DESKTOP_FILE")"
else
    check "desktop entry exported" fail "no arctis/asm entry in ~/.local/share/applications/"
fi

# ---------------------------------------------------------------------------
# Section 4 — systemd services
# ---------------------------------------------------------------------------
section "systemd services"

for svc in arctis-manager.service arctis-gui.service; do
    SVC_STATE="$(systemctl --user is-active "$svc" 2>/dev/null || true)"
    if [ "$SVC_STATE" = "active" ]; then
        check "$svc is active" pass
    elif systemctl --user cat "$svc" &>/dev/null 2>&1; then
        check "$svc is active" fail "unit exists but state: $SVC_STATE"
    else
        check "$svc is active" fail "unit file not found"
    fi
done

# ---------------------------------------------------------------------------
# Section 5 — PipeWire sinks
# ---------------------------------------------------------------------------
section "PipeWire sinks"

SINKS_RAW="$(pactl list short sinks 2>/dev/null || true)"
for sink_label in Game Chat Media Micro; do
    if printf "%s\n" "$SINKS_RAW" | grep -qi "$sink_label"; then
        check "PipeWire sink: $sink_label" pass
    else
        check "PipeWire sink: $sink_label" fail "not found in pactl list"
    fi
done

# ---------------------------------------------------------------------------
# Section 6 — udev rules on host
# ---------------------------------------------------------------------------
section "udev rules"

UDEV_RULES="/etc/udev/rules.d/91-steelseries-arctis.rules"
if [ -f "$UDEV_RULES" ]; then
    check "udev rules present ($UDEV_RULES)" pass
else
    check "udev rules present ($UDEV_RULES)" fail "file not found — udev rules were not written to host"
fi

# ---------------------------------------------------------------------------
# Section 7 — HID devices inside container
# ---------------------------------------------------------------------------
section "HID device access"

HID_INSIDE="$(distrobox enter "$CONTAINER_NAME" -- bash -c 'ls /dev/hidraw* 2>/dev/null' 2>/dev/null || true)"
if [ -n "$HID_INSIDE" ]; then
    check "/dev/hidraw* visible inside container" pass "$(printf "%s" "$HID_INSIDE" | tr '\n' ' ')"
else
    check "/dev/hidraw* visible inside container" fail "no hidraw devices — check distrobox-install.sh --additional-flags or udev rules"
fi

# ---------------------------------------------------------------------------
# Section 8 — D-Bus / ASM daemon reachable
# ---------------------------------------------------------------------------
section "ASM daemon"

ASM_STATUS="$(distrobox enter "$CONTAINER_NAME" -- bash -c 'asm-cli status 2>/dev/null' 2>/dev/null || true)"
if [ -n "$ASM_STATUS" ]; then
    check "asm-cli status (D-Bus reachable)" pass "$(printf "%s" "$ASM_STATUS" | head -1)"
else
    check "asm-cli status (D-Bus reachable)" fail "no response — daemon may not be running inside container"
fi

# ---------------------------------------------------------------------------
# Write full log report
# ---------------------------------------------------------------------------
{
    printf "=== CHECK SUMMARY ===\n"
    for line in "${CHECK_SUMMARY[@]}"; do
        printf "  %s\n" "$line"
    done
    printf "\n"
} >> "$REPORT_FILE"

report_cmd "systemctl --user status arctis-manager.service (last 50 lines)" \
    systemctl --user status arctis-manager.service --no-pager -l --lines=50

report_cmd "systemctl --user status arctis-gui.service (last 50 lines)" \
    systemctl --user status arctis-gui.service --no-pager -l --lines=50

report_cmd "journalctl --user arctis-manager.service (last 1 hour)" \
    journalctl --user -u arctis-manager.service --since "1 hour ago" --no-pager

report_cmd "journalctl --user arctis-gui.service (last 1 hour)" \
    journalctl --user -u arctis-gui.service --since "1 hour ago" --no-pager

report_cmd "distrobox list" \
    distrobox list

report_cmd "pactl list short sinks" \
    pactl list short sinks

report_cmd "ls -la /dev/hidraw*" \
    bash -c 'ls -la /dev/hidraw* 2>/dev/null || echo "(no hidraw devices)"'

report_cmd "udev rules (first 20 lines)" \
    bash -c "head -20 \"$UDEV_RULES\" 2>/dev/null || echo '(file not found)'"

report_cmd "asm-cli version (inside container)" \
    bash -c "distrobox enter \"$CONTAINER_NAME\" -- bash -c 'asm-cli version 2>/dev/null' 2>/dev/null || echo '(unavailable)'"

report_cmd "journalctl inside container — arctis-manager (last 1 hour)" \
    bash -c "distrobox enter \"$CONTAINER_NAME\" -- bash -c 'journalctl --user -u arctis-manager --since \"1 hour ago\" --no-pager 2>/dev/null' 2>/dev/null || echo '(unavailable)'"

report_cmd "uname -a" \
    uname -a

report_cmd "OS release" \
    bash -c 'cat /etc/os-release 2>/dev/null || echo "(no /etc/os-release)"'

# ---------------------------------------------------------------------------
# Final verdict
# ---------------------------------------------------------------------------
printf "\n"
if [ "$CHECKS_FAILED" -eq 0 ]; then
    printf "%s%s✓ All checks passed. ASM Distrobox install is working correctly.%s\n" \
        "$GREEN" "$BOLD" "$RESET"
    log_report ""
    log_report "VERDICT: All $CHECKS_TOTAL checks passed."
else
    printf "%s%s✗ %d check(s) failed.%s Report saved to %s\n" \
        "$RED" "$BOLD" "$CHECKS_FAILED" "$RESET" "$REPORT_FILE"
    printf "   Please attach it to your GitHub issue:\n"
    printf "   https://github.com/loteran/Arctis-Sound-Manager/issues/new\n"
    log_report ""
    log_report "VERDICT: $CHECKS_FAILED of $CHECKS_TOTAL check(s) failed."
fi

printf "\n%sReport file:%s %s\n" "$BOLD" "$RESET" "$REPORT_FILE"
