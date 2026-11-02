#!/usr/bin/env bash
# Arctis Sound Manager — try a fix before it is released
#
# A maintainer posts the exact command in your issue. It looks like:
#   curl -fsSL -o /tmp/asm-hotfix.sh https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/<commit>/scripts/asm-hotfix.sh
#   bash /tmp/asm-hotfix.sh \
#       <commit> --for 1.5.1 src/arctis_sound_manager/core.py …
#
# It downloads those files into ~/.local/state/asm-hotfix and makes your
# installed ASM run with them, then restarts ASM. Same package, same version,
# and nothing written outside your home but one file the package owns. When the
# release containing the fix comes out, update ASM the usual way (pacman/paru,
# dnf, apt…): it installs over the test fix with no conflict, nothing to undo
# first.
#
# Go back to the files your package installed:
#   curl -fsSL -o /tmp/asm-hotfix.sh https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/scripts/asm-hotfix.sh
#   bash /tmp/asm-hotfix.sh --revert
# (Two commands rather than bash <(curl …): fish rejects process substitution.)
#
# Copyright (C) 2026 loteran — SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

REPO="loteran/Arctis-Sound-Manager"
RAW="https://raw.githubusercontent.com/$REPO"
CONTAINER="${ASM_CONTAINER_NAME:-arctis-sound-manager}"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/asm-hotfix"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n'  "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n'  "$*" >&2; exit 1; }

main() {
    local revert=0 commit="" for_versions="" files=()
    while [ $# -gt 0 ]; do
        case "$1" in
            --revert) revert=1 ;;
            --for)    shift; for_versions="${1:-}" ;;
            -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
            *) if [ -z "$commit" ] && [ "$revert" -eq 0 ]; then commit="$1"; else files+=("$1"); fi ;;
        esac
        shift
    done

    # SteamOS / Bazzite / Silverblue: ASM lives in a Distrobox container, so
    # its files are patched in there. The script is fetched again inside: the
    # copy this shell is reading (bash <(curl …)) is not visible from it.
    if ! find_python >/dev/null && [ ! -f /run/.containerenv ] && [ ! -f /.dockerenv ] \
       && command -v distrobox >/dev/null 2>&1 \
       && distrobox list 2>/dev/null | grep -qw "$CONTAINER"; then
        say "ASM is installed in the '$CONTAINER' container — applying there."
        local ref args
        if [ "$revert" -eq 1 ]; then
            ref=main; args="--revert"
        else
            ref="$commit"; args=$(printf '%q ' "$commit" --for "$for_versions" "${files[@]}")
        fi
        distrobox enter "$CONTAINER" -- bash -c \
            "bash <(curl -fsSL '$RAW/$ref/scripts/asm-hotfix.sh') $args" </dev/null
        exit $?
    fi

    local py pkgdir version
    py=$(find_python) || die "Arctis Sound Manager does not seem to be installed here."
    # __spec__.origin, not __file__: the redirect rewrites __file__.
    pkgdir=$("$py" -c 'import arctis_sound_manager as a,os;print(os.path.dirname(a.__spec__.origin))')
    version=$("$py" -c 'from importlib.metadata import version;print(version("arctis-sound-manager"))')
    case "$pkgdir" in
        /nix/store/*) die "Nix installs are read-only — a test build is not possible here, sorry. The fix will come with the next release." ;;
    esac

    forget_if_superseded "$version"

    if [ "$revert" -eq 1 ]; then
        do_revert "$pkgdir"
        restart_asm
        return
    fi

    [[ "$commit" =~ ^[0-9a-f]{7,40}$ ]] || die "Missing or invalid commit id (try --help)."
    [ "${#files[@]}" -gt 0 ] || die "No file to update given (try --help)."
    if [ -n "$for_versions" ] && [[ ",$for_versions," != *",$version,"* ]]; then
        die "This fix was prepared for ASM $for_versions, but $version is installed. Please mention it in the issue."
    fi
    do_apply "$commit" "$pkgdir" "$version" "${files[@]}"
    restart_asm
    say "Done. Please test, then report back in the issue."
    # Two commands, no <(…): fish (CachyOS's default shell) rejects process
    # substitution, and this line is copied into whatever shell the user has.
    say "To undo:  curl -fsSL -o /tmp/asm-hotfix.sh $RAW/main/scripts/asm-hotfix.sh && bash /tmp/asm-hotfix.sh --revert"
}

# The interpreter ASM really runs with: the one in asm-daemon's shebang.
find_python() {
    local bin py
    bin=$(command -v asm-daemon 2>/dev/null) || return 1
    py=$(head -n1 "$bin" | sed -E 's/^#! *//; s/^\/usr\/bin\/env +//')
    "$py" -c 'import arctis_sound_manager' 2>/dev/null || return 1
    echo "$py"
}

# Writing into a package-owned directory needs root; a pipx/user install does not.
as_owner() {
    local dir="$1"; shift
    if [ -w "$dir" ]; then "$@"; return; fi
    if command -v sudo >/dev/null 2>&1; then sudo "$@"; return; fi
    command -v pkexec >/dev/null 2>&1 || die "Neither sudo nor pkexec is available to write to $dir."
    pkexec "$@"
}

# After a package update the backups describe a version that is gone: the
# package has already put its own files back. Drop them silently.
forget_if_superseded() {
    [ -f "$STATE/version" ] || return 0
    [ "$(cat "$STATE/version")" = "$1" ] && return 0
    rm -rf "$STATE"
}

# How a test fix is laid out, so that the next package update installs over
# it without a single conflict — pacman refuses to write over any file it does
# not own, and checks that before running any hook:
#   - every fixed or new file goes into $STATE/overlay, in the home directory,
#     which no package manager looks at;
#   - the overlay mirrors the installed package with symlinks, the fixed and
#     new files being real copies, so data files found next to a module
#     (icons, device profiles, translations) resolve there too;
#   - the only file of the package that changes is its __init__.py, which the
#     package owns: it points Python at the overlay. The next update puts the
#     package's own __init__.py back, and the overlay is simply no longer read.
MARKER="# asm-hotfix redirect"

# The packaged __init__.py is kept in $STATE; if that copy is gone (home
# cleaned, state deleted), the redirect itself still carries it: it is the
# source its fallback branch runs.
recover_orig() {
    local init="$1" py
    [ -f "$STATE/__init__.py.orig" ] && return 0
    py=$(find_python) || return 1
    mkdir -p "$STATE"
    "$py" - "$init" > "$STATE/__init__.py.orig" <<'EOF' || { rm -f "$STATE/__init__.py.orig"; return 1; }
import ast, sys
tree = ast.parse(open(sys.argv[1], encoding="utf-8").read())
for node in ast.walk(tree):
    if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "compile"
            and isinstance(node.args[0], ast.Constant)):
        sys.stdout.write(node.args[0].value)
        break
else:
    sys.exit(1)
EOF
}

do_apply() {
    local commit="$1" pkgdir="$2" version="$3"; shift 3
    local rel sub py overlay="$STATE/overlay/arctis_sound_manager" init="$pkgdir/__init__.py"
    TMP=$(mktemp -d)
    trap 'rm -rf "$TMP"' EXIT

    # Download everything first: a failed download must not leave half a fix.
    for rel in "$@"; do
        case "$rel" in
            src/arctis_sound_manager/*) ;;
            *) die "Refusing '$rel': only files of the arctis_sound_manager package can be tested this way." ;;
        esac
        [[ "$rel" != *..* ]] || die "Refusing '$rel'."
        sub=${rel#src/arctis_sound_manager/}
        mkdir -p "$TMP/$(dirname "$sub")"
        curl -fsSL "$RAW/$commit/$rel" -o "$TMP/$sub" || die "Could not download $rel at $commit."
    done

    # The packaged __init__.py, unless this file already is our redirect.
    mkdir -p "$STATE"
    if grep -qF "$MARKER" "$init"; then recover_orig "$init"; else cp -p "$init" "$STATE/__init__.py.orig"; fi
    [ -f "$STATE/__init__.py.orig" ] || die "The original __init__.py is lost — reinstall ASM with your package manager, then try again."

    # A fresh overlay each time: a second test fix replaces the first.
    rm -rf "$STATE/overlay"
    mkdir -p "$STATE/overlay"
    cp -rs "$pkgdir" "$overlay"
    find "$overlay" -name __pycache__ -prune -exec rm -rf {} +
    # Its __init__.py must be the real one, not a link to the redirect.
    rm -f "$overlay/__init__.py"
    cp "$STATE/__init__.py.orig" "$overlay/__init__.py"
    say "Test fix ($commit) — $# file(s):"
    for rel in "$@"; do
        sub=${rel#src/arctis_sound_manager/}
        mkdir -p "$overlay/$(dirname "$sub")"
        rm -f "$overlay/$sub"
        cp "$TMP/$sub" "$overlay/$sub"
        echo "    $sub"
    done
    echo "$version" > "$STATE/version"
    echo "$commit" > "$STATE/commit"

    py=$(find_python)
    "$py" - "$STATE/__init__.py.orig" "$overlay" "$MARKER" > "$TMP/__init__.py" <<'EOF'
import sys
orig, overlay, marker = sys.argv[1:4]
print(f"""{marker} — written by scripts/asm-hotfix.sh.
# This ASM runs a test fix kept in {overlay}.
# The next package update replaces this file, and the test fix goes with it.
import os as _os
_hotfix = {overlay!r}
if _os.path.isfile(_os.path.join(_hotfix, "__init__.py")):
    __path__[:] = [_hotfix]
    __file__ = _os.path.join(_hotfix, "__init__.py")
    with open(__file__, encoding="utf-8") as _f:
        exec(compile(_f.read(), __file__, "exec"))
else:  # overlay deleted by hand: run as packaged
    exec(compile({open(orig, encoding="utf-8").read()!r}, __file__, "exec"))
""")
EOF
    say "Pointing ASM at the test fix (one password prompt)"
    as_owner "$pkgdir" install -m 644 "$TMP/__init__.py" "$init"
    recompile "$pkgdir" "$init"
}

# Refresh the cached bytecode only where the package shipped one: creating a
# new .pyc would leave a file no package owns.
recompile() {
    local pkgdir="$1" src="$2" py pyc
    py=$(find_python) || return 0
    pyc=$("$py" -c 'import importlib.util,sys;print(importlib.util.cache_from_source(sys.argv[1]))' "$src")
    [ -f "$pyc" ] || return 0
    as_owner "$pkgdir" "$py" -c 'import py_compile,sys;py_compile.compile(sys.argv[1],cfile=sys.argv[2],doraise=True)' "$src" "$pyc" \
        || warn "Could not refresh $(basename "$pyc") (harmless: Python recompiles it in memory)."
}

do_revert() {
    local pkgdir="$1" init="$1/__init__.py"
    if ! grep -qF "$MARKER" "$init"; then
        say "No test fix active — nothing to undo."
        rm -rf "$STATE"
        return 0
    fi
    recover_orig "$init" || die "The original __init__.py is lost — reinstall ASM with your package manager."
    say "Back to the ASM your package installed"
    as_owner "$pkgdir" install -p -m 644 "$STATE/__init__.py.orig" "$init"
    recompile "$pkgdir" "$init"
    rm -rf "$STATE"
}

restart_asm() {
    say "Restarting ASM"
    systemctl --user restart arctis-manager.service 2>/dev/null \
        || warn "Could not restart the daemon — log out and back in, or reboot."
    if pgrep -f 'bin/asm-gui' >/dev/null 2>&1; then
        asm-gui --restart >/dev/null 2>&1 || warn "Close and reopen the ASM window to finish."
    fi
}

main "$@"
