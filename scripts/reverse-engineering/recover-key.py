#!/usr/bin/env python3
"""Recover the OpenPGP descriptor passphrase from SteelSeriesEngine.exe.

This handles the GG layout used by version 114.0.0. It finds the named Go callback
used by DecryptDeviceFile and extracts the two static strings concatenated there.
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path


PCLNTAB_MAGIC = b"\xf1\xff\xff\xff\x00\x00\x01\x08"
CALLBACK_NAME = (
    b"github.com/steelseries/engine/pkg/utils/fileencryption.DecryptDeviceFile.func1"
)
CONCAT_NAME = "runtime.concatbyte2"
CONCAT_PATTERN = re.compile(
    rb"\x48\x8d\x05(?P<first_displacement>.{4})"
    rb"\xbb(?P<first_length>.{4})"
    rb"\x48\x8d\x0d(?P<second_displacement>.{4})"
    rb"(?:\xbf(?P<second_length>.{4})|\x48\x89\xdf)"
    rb"\xe8(?P<call_displacement>.{4})",
    re.DOTALL,
)


def parse_pe_sections(binary: bytes) -> tuple[int, list[tuple[int, int, int, int]]]:
    if binary[:2] != b"MZ":
        raise ValueError("not a Windows PE file")
    pe_offset = struct.unpack_from("<I", binary, 0x3C)[0]
    if binary[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise ValueError("invalid PE signature")

    section_count = struct.unpack_from("<H", binary, pe_offset + 6)[0]
    optional_size = struct.unpack_from("<H", binary, pe_offset + 20)[0]
    optional_offset = pe_offset + 24
    magic = struct.unpack_from("<H", binary, optional_offset)[0]
    if magic != 0x20B:
        raise ValueError("only PE32+ executables are supported")
    image_base = struct.unpack_from("<Q", binary, optional_offset + 24)[0]

    section_offset = optional_offset + optional_size
    sections = []
    for index in range(section_count):
        offset = section_offset + index * 40
        virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from(
            "<IIII", binary, offset + 8
        )
        sections.append((virtual_address, virtual_size, raw_offset, raw_size))
    return image_base, sections


def va_to_file_offset(
    address: int, image_base: int, sections: list[tuple[int, int, int, int]]
) -> int:
    relative_address = address - image_base
    for virtual_address, virtual_size, raw_offset, raw_size in sections:
        section_size = max(virtual_size, raw_size)
        if virtual_address <= relative_address < virtual_address + section_size:
            return raw_offset + relative_address - virtual_address
    raise ValueError(f"virtual address 0x{address:x} is outside PE sections")


def read_c_string(binary: bytes, offset: int) -> str:
    end = binary.find(b"\0", offset)
    if end < 0:
        raise ValueError("unterminated Go function name")
    return binary[offset:end].decode("utf-8")


def function_table(binary: bytes, pclntab_offset: int) -> tuple[int, dict[int, str], dict[str, tuple[int, int]]]:
    function_count = struct.unpack_from("<Q", binary, pclntab_offset + 8)[0]
    text_start = struct.unpack_from("<Q", binary, pclntab_offset + 0x18)[0]
    function_name_offset = struct.unpack_from("<Q", binary, pclntab_offset + 0x20)[0]
    pcln_offset = struct.unpack_from("<Q", binary, pclntab_offset + 0x40)[0]
    if not 0 < function_count < 1_000_000:
        raise ValueError("invalid Go function count")

    names = pclntab_offset + function_name_offset
    table = pclntab_offset + pcln_offset
    names_by_address: dict[int, str] = {}
    records_by_name: dict[str, tuple[int, int]] = {}
    for index in range(function_count):
        entry_offset, record_offset = struct.unpack_from("<II", binary, table + index * 8)
        record = table + record_offset
        name_offset = struct.unpack_from("<i", binary, record + 4)[0]
        if name_offset == 0:
            continue
        name = read_c_string(binary, names + name_offset)
        address = text_start + entry_offset
        names_by_address[address] = name
        records_by_name[name] = (index, address)
    return table, names_by_address, records_by_name


def locate_callback(binary: bytes) -> tuple[int, dict[int, str], tuple[int, int]]:
    for pclntab_offset in range(len(binary)):
        pclntab_offset = binary.find(PCLNTAB_MAGIC, pclntab_offset)
        if pclntab_offset < 0:
            break
        try:
            table, names_by_address, records_by_name = function_table(binary, pclntab_offset)
        except (IndexError, UnicodeDecodeError, ValueError, struct.error):
            continue
        callback = records_by_name.get(CALLBACK_NAME.decode())
        if callback:
            return table, names_by_address, callback
    raise ValueError("could not find the Go DecryptDeviceFile password callback")


def recover_key(binary: bytes, image_base: int, sections: list[tuple[int, int, int, int]]) -> str:
    table, names_by_address, callback = locate_callback(binary)
    callback_index, callback_address = callback
    _, next_record_offset = struct.unpack_from("<II", binary, table + (callback_index + 1) * 8)
    next_record = table + next_record_offset
    next_entry_offset = struct.unpack_from("<I", binary, next_record)[0]

    text_start = min(names_by_address)
    next_address = text_start + next_entry_offset
    callback_offset = va_to_file_offset(callback_address, image_base, sections)
    callback_end = va_to_file_offset(next_address, image_base, sections)
    callback_code = binary[callback_offset:callback_end]

    for match in CONCAT_PATTERN.finditer(callback_code):
        call_offset = match.start() + 22
        call_address = callback_address + call_offset
        call_displacement = struct.unpack("<i", match["call_displacement"])[0]
        if names_by_address.get(call_address + 5 + call_displacement) != CONCAT_NAME:
            continue

        first_length = struct.unpack("<I", match["first_length"])[0]
        second_length = (
            struct.unpack("<I", match["second_length"])[0]
            if match["second_length"] is not None
            else first_length
        )
        first_address = callback_address + match.start() + 7 + struct.unpack(
            "<i", match["first_displacement"]
        )[0]
        second_address = callback_address + match.start() + 19 + struct.unpack(
            "<i", match["second_displacement"]
        )[0]
        first_offset = va_to_file_offset(first_address, image_base, sections)
        second_offset = va_to_file_offset(second_address, image_base, sections)
        key = binary[first_offset : first_offset + first_length] + binary[
            second_offset : second_offset + second_length
        ]
        if not key or not key.isascii():
            raise ValueError("recovered key is not ASCII")
        decoded = key.decode()
        if not decoded.isprintable():
            raise ValueError("recovered key is not printable ASCII")
        return decoded

    raise ValueError("could not find the runtime.concatbyte2 key construction")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("engine", type=Path, help="path to SteelSeriesEngine.exe")
    arguments = parser.parse_args()

    binary = arguments.engine.read_bytes()
    image_base, sections = parse_pe_sections(binary)
    print(recover_key(binary, image_base, sections))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, struct.error) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)