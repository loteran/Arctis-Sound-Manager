#!/usr/bin/env bash
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Run oled_strip_probe.py and produce one file that can be pasted into the
# issue as-is.
#
# The probe itself only draws blocks and asks questions. Everything around it
# is what keeps getting lost between a reporter's terminal and the issue: the
# ASM daemon has to be stopped or it draws over the tests, the measured write
# times are printed between the questions and get trimmed out when someone
# summarises by hand, and the answers only mean something next to the model,
# the port and the udev rules they were produced on. So this collects all of
# it, and puts the services back afterwards even if the probe is interrupted.
#
#   ./collect_oled_probe.sh
#
# Needs pyusb and permission to talk to the DAC (ASM's udev rules are enough).
# No setting is written to the headset; the DAC's own UI comes back at the end.

set -uo pipefail

readonly HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROBE="${HERE}/oled_strip_probe.py"
readonly LOG="${PWD}/oled_probe_$(date +%Y%m%d_%H%M%S).log"
readonly SERVICES=(arctis-manager arctis-video-router arctis-stream-guard)

# Only the services that were actually running when we started, so a machine
# that never had the video router does not get it started as a parting gift.
STOPPED=()

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m!! %s\033[0m\n' "$*" >&2; }

# --- services ---------------------------------------------------------------

have_systemd() { command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; }
have_dinit() { command -v dinitctl >/dev/null 2>&1; }

service_active() {
  if have_systemd; then
    systemctl --user is-active --quiet "$1.service"
  elif have_dinit; then
    dinitctl --user status "$1" 2>/dev/null | grep -q STARTED
  else
    return 1
  fi
}

service_do() {
  local action=$1 name=$2
  if have_systemd; then
    systemctl --user "$action" "$name.service" >/dev/null 2>&1
  elif have_dinit; then
    dinitctl --user "$action" "$name" >/dev/null 2>&1
  fi
}

stop_services() {
  local s
  for s in "${SERVICES[@]}"; do
    if service_active "$s"; then
      service_do stop "$s" && STOPPED+=("$s")
    fi
  done
  if [ ${#STOPPED[@]} -gt 0 ]; then
    echo "Stopped for the duration: ${STOPPED[*]}"
  else
    echo "No ASM service was running — nothing to stop."
  fi
}

# Runs on every exit path, including Ctrl-C: leaving someone's headset control
# stopped because they interrupted a probe is not acceptable.
restore_services() {
  [ ${#STOPPED[@]} -gt 0 ] || return 0
  local s
  for s in "${STOPPED[@]}"; do
    service_do start "$s"
  done
  say "Restarted: ${STOPPED[*]}"
}
trap restore_services EXIT INT TERM

# --- context ----------------------------------------------------------------

collect_context() {
  echo "=== context ==="
  echo "date        : $(date -Is)"
  echo "kernel      : $(uname -r)"
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    echo "distro      : $(. /etc/os-release && echo "$PRETTY_NAME")"
  fi
  echo "desktop     : ${XDG_CURRENT_DESKTOP:-?} / ${XDG_SESSION_TYPE:-?}"
  echo "init        : $(have_systemd && echo systemd || { have_dinit && echo dinit || echo other; })"

  # The installed version matters more than the checkout: most reporters run
  # the packaged daemon and a git clone only for this probe.
  local version
  version=$(asm-cli --version 2>/dev/null) ||
    version=$(python3 -c \
      'import importlib.metadata as m; print(m.version("arctis-sound-manager"))' 2>/dev/null) ||
    version="not found"
  echo "asm         : $version"
  if command -v git >/dev/null 2>&1 && git -C "$HERE" rev-parse --git-dir >/dev/null 2>&1; then
    echo "git         : $(git -C "$HERE" rev-parse --short HEAD) on $(git -C "$HERE" rev-parse --abbrev-ref HEAD)"
  fi
  echo "pyusb       : $(python3 -c 'import usb; print(usb.__version__)' 2>/dev/null || echo MISSING)"

  echo
  echo "--- SteelSeries devices ---"
  lsusb -d 1038: 2>/dev/null || echo "lsusb unavailable"

  echo
  echo "--- where the DAC sits on the bus ---"
  # Which controller and hub the DAC hangs off, in full: a port that passes
  # audio while dropping interrupt frames has already been the answer once
  # (#198), and that only shows up next to its neighbours on the same hub.
  lsusb -t 2>/dev/null || echo "lsusb -t unavailable"

  echo
  echo "--- udev rules ---"
  local rules
  rules=$(ls -1 /etc/udev/rules.d/*{arctis,steelseries}* \
                /usr/lib/udev/rules.d/*{arctis,steelseries}* 2>/dev/null | sort -u)
  if [ -n "$rules" ]; then
    echo "$rules"
  else
    echo "none found — the probe will most likely fail to claim the interface"
  fi

  echo
  echo "--- device node permissions ---"
  local dev
  while read -r dev; do
    [ -n "$dev" ] && ls -l "$dev" 2>/dev/null
  done < <(lsusb -d 1038: 2>/dev/null |
           sed -nE 's#^Bus ([0-9]+) Device ([0-9]+).*#/dev/bus/usb/\1/\2#p')
  echo
}

# --- run --------------------------------------------------------------------

main() {
  if [ ! -f "$PROBE" ]; then
    warn "oled_strip_probe.py not found next to this script ($PROBE)."
    exit 1
  fi
  if ! python3 -c 'import usb.core' 2>/dev/null; then
    warn "pyusb is missing:  pip install --user pyusb"
    exit 1
  fi

  say "Collecting context..."
  collect_context >"$LOG" 2>&1
  stop_services | tee -a "$LOG"

  say "Starting the probe. Answer each question by looking at the panel."
  echo "Everything you see below is being saved to:"
  echo "  $LOG"
  echo

  echo "=== probe ===" >>"$LOG"
  local status=0
  if command -v script >/dev/null 2>&1; then
    # `script` keeps the probe interactive while capturing the questions too;
    # -u stops Python buffering its output out of order behind the prompts.
    script -q -a -e -c "python3 -u '$PROBE'" "$LOG" || status=$?
  else
    python3 -u "$PROBE" 2>&1 | tee -a "$LOG"
    status=${PIPESTATUS[0]}
  fi

  # `script` records the terminal, so the file carries colour escapes and bare
  # carriage returns. Strip them, or the paste is unreadable in a code block.
  if command -v sed >/dev/null 2>&1; then
    sed -i -e 's/\x1b\[[0-9;?]*[a-zA-Z]//g' -e 's/\r$//' "$LOG" 2>/dev/null || true
  fi

  say "Done. The complete session is in:"
  echo "  $LOG"
  echo
  echo "Please attach that file to the issue, or paste it between triple"
  echo "backticks. The lines that matter most are the '(writes: N, slowest"
  echo "X ms, ...)' notes between the questions, and any '!! USB error' —"
  echo "they are what tells a timing problem apart from an addressing one,"
  echo "and they are exactly what gets lost when the run is summarised."
  return "$status"
}

main "$@"
