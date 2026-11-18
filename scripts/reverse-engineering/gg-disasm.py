#!/usr/bin/env python3
"""Disassemble the parts of SteelSeries GG that the Arctis specs lean on.

The decoded .device specs name the bytes, but not always how GG builds them:
a spec can hand its data to an engine builtin — the OLED frames go through
convert-to-column-packed-byte-format* — and the builtin's code lives in
SteelSeriesEngine.exe, not in the spec. Guessing what such a builtin does
from its name cost #305 two rounds of hardware probes. So for every GG
version this writes, next to decoded-<version>/:

  go/<builtin>.asm      every Go builtin the Arctis specs call
  go/UNRESOLVED.txt     builtins called but not found in the binary
  hid-chunk-types.txt   the HIDIO / HIDFEATURE / ... constants' values
  ssedevice/*.asm       SSEdevice.dll code around the HID I/O calls
                        (WriteFile, DeviceIoControl, ReadFile, HidD_*)
  ssedevice/exports.txt the DLL's exported C++ names
  fingerprints.json     {name: hash}, addresses masked, to tell what changed

Usage:
    gg-disasm.py ENGINE_EXE SSEDEVICE_DLL DECODED_DIR OUT_DIR

Needs capstone and pefile (pip install capstone pefile). The output is
SteelSeries' code: it goes to the private research repo, never here.

Copyright (C) 2026 loteran — SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import bisect
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, Cs

HERE = Path(__file__).resolve().parent
HID_IO_IMPORTS = {"WriteFile", "ReadFile", "DeviceIoControl", "GetOverlappedResult",
                  "HidD_GetInputReport", "HidD_SetFeature", "HidD_GetFeature",
                  "HidD_SetOutputReport"}
HID_CONSTANTS_FN = "support.InitHIDCommandChunkTypeConstants"
# Any immediate this long is an address or a RIP displacement: it moves with
# every build and says nothing about what the code does.
_ADDRESS = re.compile(r"0x[0-9a-f]{5,}")
# Capstone prints small negative immediates as "-1" or "0xffffffffffffffff",
# and xchg's operands in either order, depending on its version: GG 124
# "changed" every hid-io function on that alone (#392). _canon folds both
# before the address mask eats the long form.
_NEG64 = re.compile(r"\b0x(f[0-9a-f]{15})\b")
_XCHG = re.compile(r"^xchg (\S+), (\S+)$")


def _canon(ins: str) -> str:
    ins = _NEG64.sub(lambda m: str(int(m.group(1), 16) - (1 << 64)), ins)
    m = _XCHG.match(ins)
    if m:
        ins = "xchg " + ", ".join(sorted(m.groups()))
    return ins


def _load_recover_key():
    spec = importlib.util.spec_from_file_location("recover_key", HERE / "recover-key.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rk = _load_recover_key()


def _fingerprint(lines: list[str]) -> str:
    masked = "\n".join(_ADDRESS.sub("A", _canon(line.split(" ", 1)[1])) for line in lines)
    return hashlib.sha256(masked.encode()).hexdigest()[:16]


def _normalise(name: str) -> str:
    name = name.replace("->", "to").lower()
    name = re.sub(r"[^a-z0-9]", "", name)
    return name.replace("with", "")


# ── the Go engine ─────────────────────────────────────────────────────────────

class GoBinary:
    def __init__(self, path: Path) -> None:
        self.data = path.read_bytes()
        self.image_base, self.sections = rk.parse_pe_sections(self.data)
        self.names_by_address, self.records = self._pclntab()
        self.starts = sorted(self.names_by_address)
        self.md = Cs(CS_ARCH_X86, CS_MODE_64)

    def _pclntab(self):
        offset = 0
        while True:
            offset = self.data.find(rk.PCLNTAB_MAGIC, offset)
            if offset < 0:
                raise SystemExit("no Go pclntab found in the engine")
            try:
                _, names, records = rk.function_table(self.data, offset)
                return names, records
            except (IndexError, UnicodeDecodeError, ValueError):
                offset += 1

    def disassemble(self, name: str) -> list[str]:
        address = self.records[name][1]
        end = self.starts[bisect.bisect_right(self.starts, address)]
        start = rk.va_to_file_offset(address, self.image_base, self.sections)
        lines = []
        for ins in self.md.disasm(self.data[start:start + end - address], address):
            target = ""
            if ins.mnemonic == "call" and ins.op_str.startswith("0x"):
                target = self.names_by_address.get(int(ins.op_str, 16), "")
            lines.append(f"{ins.address:x} {ins.mnemonic} {ins.op_str} {target}".rstrip())
        return lines

    def builtins(self) -> dict[str, str]:
        """{normalised builtin name: Go symbol}. GG's own builtins are
        `def<Name>`, GoLisp's core ones `<Name>Impl` or plain `<Name>`."""
        found: dict[str, str] = {}
        for pattern in (r"\.def([A-Z]\w*)$", r"golisp\.([A-Z]\w*?)Impl$",
                        r"golisp\.([A-Z]\w*)$"):
            for name in self.records:
                match = re.search(pattern, name)
                if match and "steelseries" in name:
                    found.setdefault(_normalise(match.group(1)), name)
        return found

    def chunk_type_constants(self) -> list[tuple[str, int]]:
        """Read the (symbol, value) pairs InitHIDCommandChunkTypeConstants binds."""
        name = next(n for n in self.records if n.endswith(HID_CONSTANTS_FN))
        address = self.records[name][1]
        end = self.starts[bisect.bisect_right(self.starts, address)]
        start = rk.va_to_file_offset(address, self.image_base, self.sections)
        pairs, symbol, length, last_lea, value = [], None, 0, 0, 0
        for ins in self.md.disasm(self.data[start:start + end - address], address):
            rip = re.match(r"rax, \[rip \+ (0x[0-9a-f]+)\]", ins.op_str)
            if ins.mnemonic == "lea" and rip:
                last_lea = ins.address + ins.size + int(rip.group(1), 16)
            size = re.match(r"(?:ebx|rbx), (0x[0-9a-f]+|\d+)$", ins.op_str)
            if ins.mnemonic == "mov" and size:
                length = int(size.group(1), 0)
            stored = re.match(r"qword ptr \[.*\], (0x[0-9a-f]+|\d+)$", ins.op_str)
            if ins.mnemonic == "mov" and stored and symbol:
                value = int(stored.group(1), 0)
            if ins.mnemonic == "call" and ins.op_str.startswith("0x"):
                callee = self.names_by_address.get(int(ins.op_str, 16), "")
                if callee.endswith("golisp.Intern"):
                    at = rk.va_to_file_offset(last_lea, self.image_base, self.sections)
                    symbol, value = self.data[at:at + length].decode(errors="replace"), 0
                elif "BindTo" in callee and symbol:
                    pairs.append((symbol, value))
                    symbol = None
        return pairs


def spec_builtins(decoded: Path) -> set[str]:
    """Names the specs call but never define themselves."""
    text = "\n".join(p.read_text(errors="replace") for p in decoded.glob("*.device"))
    defined = set(re.findall(r"\(define \(?([^\s()]+)", text))
    called = set(re.findall(r"\(([a-z][\w!?*<>=/+\-]*-[\w!?*<>=/+\-]*)", text))
    return called - defined


# ── SSEdevice.dll, where the HID reports are actually sent ──────────────────

def dll_io_functions(path: Path) -> tuple[list[str], dict[str, list[str]]]:
    pe = pefile.PE(str(path))
    base = pe.OPTIONAL_HEADER.ImageBase
    image = pe.get_memory_mapped_image()
    imports = {i.address: i.name.decode() for d in pe.DIRECTORY_ENTRY_IMPORT
               for i in d.imports if i.name}
    exports = sorted(e.name.decode() for e in pe.DIRECTORY_ENTRY_EXPORT.symbols if e.name)
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.skipdata = True

    functions: dict[str, list[str]] = {}
    for entry in pe.DIRECTORY_ENTRY_EXCEPTION:
        begin, end = entry.struct.BeginAddress, entry.struct.EndAddress
        lines, touches_io = [], False
        for ins in md.disasm(image[begin:end], base + begin):
            note = ""
            rip = re.search(r"\[rip \+ (0x[0-9a-f]+)\]", ins.op_str)
            if rip:
                note = imports.get(ins.address + ins.size + int(rip.group(1), 16), "")
                touches_io |= ins.mnemonic == "call" and note in HID_IO_IMPORTS
            if re.search(r"\b0xb01[0-9a-f]{2}\b", ins.op_str):
                note, touches_io = "IOCTL_HID_*", True
            lines.append(f"{ins.address:x} {ins.mnemonic} {ins.op_str} {note}".rstrip())
        if touches_io:
            functions[f"fn_{base + begin:x}"] = lines
    return exports, functions


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit(__doc__)
    engine, dll, decoded, out = (Path(a) for a in sys.argv[1:])
    (out / "go").mkdir(parents=True, exist_ok=True)
    fingerprints: dict[str, str] = {}

    go = GoBinary(engine)
    available = go.builtins()
    unresolved = []
    for builtin in sorted(spec_builtins(decoded)):
        symbol = available.get(_normalise(builtin))
        if not symbol:
            unresolved.append(builtin)
            continue
        lines = go.disassemble(symbol)
        (out / "go" / f"{builtin}.asm").write_text(f"; {symbol}\n" + "\n".join(lines) + "\n")
        fingerprints[f"go/{builtin}"] = _fingerprint(lines)
    (out / "go" / "UNRESOLVED.txt").write_text(
        "# Called by the specs, no matching Go symbol: local helpers bound with\n"
        "# let/lambda, GoLisp special forms, or a builtin renamed in this version.\n" + "\n".join(unresolved) + "\n")

    constants = go.chunk_type_constants()
    (out / "hid-chunk-types.txt").write_text(
        "".join(f"{name} = {value}\n" for name, value in constants))
    fingerprints["hid-chunk-types"] = hashlib.sha256(
        repr(constants).encode()).hexdigest()[:16]

    if dll.is_file():
        (out / "ssedevice").mkdir(exist_ok=True)
        exports, functions = dll_io_functions(dll)
        (out / "ssedevice" / "exports.txt").write_text("\n".join(exports) + "\n")
        for name, lines in functions.items():
            (out / "ssedevice" / f"{name}.asm").write_text("\n".join(lines) + "\n")
        # Function addresses move between builds: fingerprint the I/O code
        # as one set, so a change anywhere in it is seen, not where.
        fingerprints["ssedevice/hid-io"] = hashlib.sha256("".join(
            sorted(_fingerprint(lines) for lines in functions.values())).encode()
        ).hexdigest()[:16]

    (out / "fingerprints.json").write_text(json.dumps(fingerprints, indent=1, sort_keys=True) + "\n")
    print(f"{len(fingerprints)} fingerprints, {len(unresolved)} unresolved builtins → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
