#!/usr/bin/env bash
# Decode the Arctis headset device specifications shipped with SteelSeries GG,
# for interoperability work on Arctis Sound Manager (a Linux driver for these
# headsets). Method published at https://github.com/Cisien/arctis-things —
# the passphrase is recovered from SteelSeriesEngine.exe by recover-key.py.
#
# Scope: Arctis headsets only (plus the shared base/boilerplate files they
# include). Mice and keyboards are left alone — ASM does not drive them.
set -euo pipefail

readonly SPECS_DIR="${1:?usage: decode-arctis-specs.sh SPECS_DIR KEY_FILE OUT_DIR}"
readonly KEY_FILE="${2:?missing key file}"
readonly OUT_DIR="${3:?missing output dir}"

mkdir -p "$OUT_DIR"
GNUPGHOME="$(mktemp -d)"
export GNUPGHOME
trap 'rm -rf "$GNUPGHOME"' EXIT

passphrase="$(cat "$KEY_FILE")"

decode_one() {
    local source="$1" output="$2"
    # GG wraps the OpenPGP packet in a custom armor label + checksum line.
    awk '/^-----/{next} /^=/{next} NF {print}' "$source" \
        | base64 -d \
        | gpg --batch --yes --pinentry-mode loopback \
              --passphrase "$passphrase" --output "$output" --decrypt 2>/dev/null
}

decoded=0
failed=0
for encrypted in "$SPECS_DIR"/arctis*.edevice \
                 "$SPECS_DIR"/base_arctis*.edevice \
                 "$SPECS_DIR"/generic*.edevice \
                 "$SPECS_DIR"/standard*.edevice \
                 "$SPECS_DIR"/gamebuds*.edevice; do
    [[ -f "$encrypted" ]] || continue
    name="$(basename "$encrypted" .edevice)"
    if decode_one "$encrypted" "$OUT_DIR/$name.device"; then
        decoded=$((decoded + 1))
    else
        failed=$((failed + 1))
        printf 'failed: %s\n' "$name" >&2
    fi
done

printf 'decoded=%d failed=%d\n' "$decoded" "$failed"