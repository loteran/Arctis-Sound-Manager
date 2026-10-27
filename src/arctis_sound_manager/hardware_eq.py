# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Encoders for the equaliser that lives inside the headset.

Some Arctis families take a flat run of gain bytes (Nova Pro Wireless: report
id, opcode, then one byte per band). Others take a full parametric description
— frequency, filter type, gain and Q per band, plus a preset name — split over
several HID frames. That cannot be expressed in a device profile's
`update_sequence`, so a profile names an encoder here instead and the daemon
calls it.

Everything below is transcribed from SteelSeries' own device specifications,
including the value encodings, which are unusual enough to be worth stating:
gain is a signed byte of decidecibels (0 dB → 0x00, +12 dB → 0x78, -12 dB →
0x88) and Q is a 16-bit value of thousandths.
"""
from __future__ import annotations

from dataclasses import dataclass

# Filter types, per the specs' eqBand.filter_type.
FILTER_NONE = 0x00
FILTER_PEAK = 0x01
FILTER_LOW_PASS = 0x02
FILTER_HIGH_PASS = 0x03
FILTER_LOW_SHELF = 0x04
FILTER_HIGH_SHELF = 0x05
FILTER_NOTCH = 0x06

# Connection the curve applies to (parametric_eq.connection_type, and its
# derived structs parametric_eq_bt / parametric_eq_mic).
CONNECTION_WIRELESS = 0x00
CONNECTION_BLUETOOTH = 0x01
CONNECTION_MIC = 0x02

PRESET_BUILTIN = 0x00
PRESET_CUSTOM = 0x01

# The ten fixed frequencies ASM's custom EQ sliders sit on.
DEFAULT_BAND_FREQUENCIES = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
# Q the specs use for a plain 10-band graphic curve.
DEFAULT_Q = 1.41


@dataclass(frozen=True)
class EqBand:
    """One parametric band, in the units a user thinks in."""
    frequency: int
    gain_db: float
    q: float = DEFAULT_Q
    filter_type: int = FILTER_PEAK


#: How a family's filter payload encodes a band's gain (`hardware_eq.gain_encoding`).
#: decibel_signed: tenths of a dB in a signed byte, 0 dB = 0x00 (Nova 7 Gen 2,
#: Nova 3 Wireless, GameBuds, Elite: `(* 10 gain)` then `int16->bytes`).
#: offset_binary: half dBs above -10 dB, 0-40, 0 dB = 20 (the Nova 5's
#: gain_float_to_int, `(* 2 (+ 10 gain))` capped to ±10 dB).
GAIN_DECIBEL_SIGNED = "decibel_signed"
GAIN_OFFSET_BINARY = "offset_binary"


def encode_band(band: EqBand, gain_encoding: str = GAIN_DECIBEL_SIGNED) -> bytes:
    """Six bytes: frequency, filter type, gain, Q.

    Gain is decidecibels in a signed byte on most families — the specs spell
    out 0 dB = 0x00, 12 dB = 0x78, -12 dB = 0x88 — but half dBs above -10 dB
    on the Nova 5 (see GAIN_OFFSET_BINARY). Q is thousandths in 16 bits, both
    little-endian like every other multi-byte field in these frames.
    """
    frequency = max(20, min(20000, int(round(band.frequency))))
    if gain_encoding == GAIN_OFFSET_BINARY:
        gain_byte = bytes([max(0, min(40, int(2 * (10 + band.gain_db))))])
    elif gain_encoding == GAIN_DECIBEL_SIGNED:
        gain = max(-128, min(127, int(round(band.gain_db * 10))))
        gain_byte = gain.to_bytes(1, "little", signed=True)
    else:
        raise ValueError(f"unknown gain encoding {gain_encoding!r}")
    q = max(200, min(10000, int(round(band.q * 1000))))
    return (
        frequency.to_bytes(2, "little")
        + bytes([band.filter_type & 0xFF])
        + gain_byte
        + q.to_bytes(2, "little")
    )


def bands_from_gains(gains_db: list[float],
                     frequencies: tuple[int, ...] = DEFAULT_BAND_FREQUENCIES,
                     q: float = DEFAULT_Q) -> list[EqBand]:
    """Turn ASM's ten graphic-EQ gains into parametric bands."""
    return [EqBand(frequency=f, gain_db=g, q=q)
            for f, g in zip(frequencies, gains_db)]


# GG's own ten-band layout for the Elite family, read off the 0x1B frames it
# sends a Nova Pro Omni (#327): 32 and 64 Hz rather than 31/62, Q 1.414, and
# the two end bands shelving — its Bass Boost, and every Custom curve GG
# derives from it, put a low shelf on band 1 and a high shelf on band 10.
# A peak at 31 Hz or 16 kHz is next to inaudible on music, which is how the
# Elite's Custom EQ seemed to do nothing on exactly the two sliders the test
# in #327 asked for (62 Hz and 8 kHz were heard plainly).
GG_SHELVING_FREQUENCIES = (32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
GG_SHELVING_Q = 1.414


def shelving_bands_from_gains(gains_db: list[float]) -> list[EqBand]:
    """ASM's ten gains on GG's layout: low shelf, eight peaks, high shelf."""
    bands = bands_from_gains(gains_db, GG_SHELVING_FREQUENCIES, GG_SHELVING_Q)
    last = len(bands) - 1
    return [EqBand(b.frequency, b.gain_db, b.q,
                   FILTER_LOW_SHELF if i == 0
                   else FILTER_HIGH_SHELF if i == last else b.filter_type)
            for i, b in enumerate(bands)]


#: How a profile's `hardware_eq.band_layout` turns the sliders into bands.
BAND_LAYOUTS = {
    "graphic": bands_from_gains,
    "shelving": shelving_bands_from_gains,
}


def encode_parametric_eq(bands: list[EqBand], name: str = "Custom",
                         connection: int = CONNECTION_WIRELESS,
                         preset_type: int = PRESET_CUSTOM,
                         name_command: int = 0xA7,
                         band_command: int = 0x33,
                         commit_command: int | None = None,
                         bands_carry_connection: bool = True,
                         gain_encoding: str = GAIN_DECIBEL_SIGNED) -> list[list[int]]:
    """Frames for the parametric families (Nova 7 Gen 2: 0xA7, Nova 5: 0xA5).

    Each frame starts with its **opcode**, not with the `report_id` the specs
    declare as their first field. That field is the HID report number, which
    belongs in the SET_REPORT `wValue` — `CoreEngine.send_command()` puts it
    there from the profile's `command_report_id`, and these families all
    declare it constant 0x00 (unnumbered reports, nothing on the wire). GG's
    own frames look like they carry it because hidapi takes a 65-byte buffer
    whose first byte is that report number and strips it before sending the 64
    bytes; ASM's control transfer carries only those 64.

    Emitting it as payload shifted every frame one byte right, so the headset
    read opcode 0x00 and discarded the lot without a word: the sliders moved
    and nothing happened, on every parametric family (#146). Hardware settled
    it — the readback queries in this module have never carried the byte, and
    a real Nova 7P Gen 2 answers them.

    GG sends the curve in order:

      1. `name_command` — connection, preset slot, then the preset name, which
         the headset reports back and shows on a DAC screen;
      2. `band_command` — the ten encoded bands;
      3. `commit_command` — apply. The Nova 7 Gen 2 needs this third write and
         changes nothing before it; the Nova 5 declares no such opcode and
         applies on the second. It therefore defaults to absent: a profile that
         needs the commit says so, and a profile that doesn't can't grow a
         stray frame by omission.

    The band frame differs between families, and getting it wrong shifts every
    byte of the curve: the Nova 7 Gen 2 repeats the connection there, the
    Nova 5 and the GameBuds do not. Hence `bands_carry_connection`. The Nova 5
    also encodes each gain its own way (`gain_encoding`, see encode_band).

    Returned as byte lists ready for CoreEngine.send_command(), which pads
    each one to the profile's frame length.
    """
    if len(bands) != 10:
        raise ValueError(f"expected 10 bands, got {len(bands)}")

    name_bytes = list(name.encode("ascii", errors="ignore")[:32])
    payload: list[int] = []
    for band in bands:
        payload += list(encode_band(band, gain_encoding))

    band_frame = [band_command]
    if bands_carry_connection:
        band_frame.append(connection)

    frames = [
        [name_command, connection, preset_type] + name_bytes,
        band_frame + payload,
    ]
    if commit_command is not None:
        frames.append([commit_command])
    return frames


# Builtin presets the Elite's parametric_eq picks its onboard slot from by
# name when preset_type is 0 (base_arctis_nova_elite_tx.device, api
# parametric_eq): [0] Flat [1] Bass Boost [2] Focus [3] Smiley [4] Custom.
_ELITE_BUILTIN_SLOTS = {"Flat": 0, "Bass Boost": 1, "Focus": 2, "Smiley": 3}
_ELITE_CUSTOM_SLOT = 4
_ELITE_ALIAS_LENGTH = 6
_ELITE_NAME_LENGTH = 61


def _fixed_ascii(text: str, length: int) -> list[int]:
    raw = list(text.encode("ascii", errors="ignore")[:length])
    return raw + [0] * (length - len(raw))


def encode_elite_parametric_eq(bands: list[EqBand], name: str = "Custom",
                               preset_type: int = PRESET_CUSTOM,
                               report_id: int = 0x01,
                               eq_command: int = 0x1B) -> list[list[int]]:
    """The one frame the Nova Elite takes for its 2.4 GHz curve.

    Transcribed from `parametric_eq` in base_arctis_nova_elite_tx.device (GG
    121). Unlike the 64-byte families above, the whole description fits in a
    single HIDFEATURE chunk of 1036 bytes:

      report_id, 0x1B, onboard slot, alias (6), name (61), 10 × eqBand (60)

    The slot is not the preset_type GG's engine carries but what the spec
    derives from it: 4 (Custom) for a custom curve, otherwise the builtin
    matching the name. Writing it is also what selects it — the spec has no
    separate select opcode for the Elite, unlike the Nova Pro's 0x2E.

    The report id stays in the frame: a numbered feature report carries it as
    its first data byte, and CoreEngine.send_feature_report() reads it from
    there for the SET_REPORT wValue.

    Each band is encode_band(): `create_filter_payload` in the same spec
    produces frequency, type, gain in decidecibels and Q in thousandths, the
    layout a real Nova 7P Gen 2 reads back (#146).
    """
    if len(bands) != 10:
        raise ValueError(f"expected 10 bands, got {len(bands)}")
    slot = (_ELITE_CUSTOM_SLOT if preset_type == PRESET_CUSTOM
            else _ELITE_BUILTIN_SLOTS.get(name, _ELITE_CUSTOM_SLOT))
    frame = [report_id, eq_command, slot]
    frame += _fixed_ascii(name, _ELITE_ALIAS_LENGTH)
    frame += _fixed_ascii(name, _ELITE_NAME_LENGTH)
    for band in bands:
        frame += list(encode_band(band))
    return [frame]


# ── Ten-band EQ (Nova Elite / Pro Omni microphone and Bluetooth) ────────────
#
# ten_band_eq_mic (0x1D) and ten_band_eq_bt (0x1F) in base_arctis_nova_elite_tx
# take one HIDFEATURE frame of 1036 bytes, like parametric_eq, but carry ten
# plain gains instead of filters:
#
#   report_id, command, onboard slot, alias (6), name (61), 10 gains
#
# each gain one signed byte in tenths of a dB (transform_gains_ui_to_fw:
# 0x88 = -12 dB ... 0x78 = +12 dB). The slot selects the preset; the spec has
# no other select command for these two on this family.
#
# The presets are GG's own (engine database, device_game_eq_presets, GG 95):
# what its UI lists for supported mode MICROPHONE (32) and BLUETOOTH (16), and
# whose gains it sends with the slot. Only the gains go on the wire; GG's
# frequencies are fixed by the firmware. One exception: the database's Bass
# Boost row is a copy of Smiley's. GG's UI hardcodes the real curve (its Nova
# Pro module), and an Elite base reads that one back for its Bass Boost slot
# (#331 probe), so it is the one used here.

TEN_BAND_GAIN_MIN_DB = -12.0
TEN_BAND_GAIN_MAX_DB = 12.0


@dataclass(frozen=True)
class OnboardPreset:
    slot: int
    name: str
    alias: str
    gains_db: tuple[float, ...]


_MIC_FLAT = (0.0,) * 10

#: The slots ten_band_eq_mic maps names to (spec api ten_band_eq_mic). The
#: Omni adds Walkie Talkie as slot 9; the Elite's spec stops at 8 (Custom).
MIC_PRESETS = (
    OnboardPreset(0, "Flat", "FLAT", _MIC_FLAT),
    OnboardPreset(1, "Balanced", "BAL", (-12.0, 0.0, 0.0, -3.0, -2.0, 0.0, 2.0, 3.0, 1.0, 0.0)),
    OnboardPreset(2, "Broadcast High Pitch", "BC HI", (-12.0, -12.0, -3.0, 6.0, 3.0, -2.5, 0.0, 3.0, 4.0, 4.0)),
    OnboardPreset(3, "Broadcast Low Pitch", "BC LO", (-12.0, 2.0, 5.0, 2.0, -3.0, -2.0, 0.0, 3.0, 4.0, 4.0)),
    OnboardPreset(4, "Clarity Low Pitch", "CT LO", (-12.0, -3.0, -2.5, 0.0, 0.0, 0.0, 3.0, 4.0, 4.0, 4.0)),
    OnboardPreset(5, "Clarity High Pitch", "CT HI", (-12.0, -12.0, -4.0, -4.0, 0.0, 0.0, 3.0, 4.0, 4.0, 4.0)),
    OnboardPreset(6, "Deep Voice", "DEEP", (-12.0, 4.0, 4.0, 3.0, 0.0, -3.0, -1.0, 0.0, 0.0, 0.0)),
    OnboardPreset(7, "Less Nasal", "NASAL-", (-12.0, 0.0, 0.0, 0.0, -2.0, -4.0, -2.0, 2.0, 4.0, 0.0)),
    OnboardPreset(9, "Walkie Talkie", "WKTK", (-12.0, -12.0, -12.0, -12.0, 5.0, 6.0, 6.0, 5.0, -12.0, -12.0)),
)

#: The slots ten_band_eq_bt maps names to (spec api ten_band_eq_bt).
BLUETOOTH_PRESETS = (
    OnboardPreset(0, "Flat", "FLAT", (0.0,) * 10),
    OnboardPreset(1, "Bass Boost", "BASS", (3.5, 5.5, 4.0, 1.0, -1.5, -1.5, -1.0, -1.0, -1.0, -1.0)),
    OnboardPreset(2, "Focus", "FOCUS", (-5.0, -3.5, -1.0, -3.5, -2.5, 4.0, 6.0, 3.5, -3.5, 0.0)),
    OnboardPreset(3, "Smiley", "SMILEY", (3.0, 3.5, 1.5, -1.5, -4.0, -4.0, -2.5, 1.5, 3.0, 4.0)),
)

ONBOARD_PRESETS = {"microphone": MIC_PRESETS, "bluetooth": BLUETOOTH_PRESETS}


def encode_ten_band_eq(command: int, preset: OnboardPreset,
                       report_id: int = 0x01) -> list[int]:
    """The frame ten_band_eq_mic / ten_band_eq_bt send for *preset*."""
    frame = [report_id, command, preset.slot]
    frame += _fixed_ascii(preset.alias, _ELITE_ALIAS_LENGTH)
    frame += _fixed_ascii(preset.name, _ELITE_NAME_LENGTH)
    for gain in preset.gains_db:
        gain = min(max(gain, TEN_BAND_GAIN_MIN_DB), TEN_BAND_GAIN_MAX_DB)
        frame.append(round(gain * 10) & 0xFF)
    return frame


#: Encoders a device profile can name in `hardware_eq.format`.
ENCODERS = {
    "parametric": encode_parametric_eq,
    "elite_parametric": encode_elite_parametric_eq,
}


# ── Reading the curve back (#146 diagnostics) ───────────────────────────────
#
# The write side above only proves ASM *sent* a conformant frame — it says
# nothing about what actually landed inside the headset. The Nova 7 Gen 2
# family's spec (base_arctis_nova_7_gen2_tx.device) declares two read-back
# commands: `get_eq_preset_data` (0x32) returns the ten stored bands,
# `read_eq_preset_name` (0xA6) returns the preset name and slot. Decoding
# their replies lets a user confirm whether the curve they set with the
# sliders is actually sitting in the headset.
#
# The offsets below are no longer inferred. A real Arctis Nova 7P Gen 2
# answered both queries (#146) and its replies are pinned as fixtures in
# tests/test_hardware_eq_readback.py:
#
#   32 00 | 20 00 01 00 86 05 | 40 00 01 00 86 05 | ... | 00 00
#   a6 00 00 | "Flat" + NUL padding
#
# 1. ASM's read buffers never carry the leading `report_id` byte the spec
#    declares as a constant field — response[0] is the opcode echo. This
#    holds for every response_mapping / SettingsReadback in this codebase
#    (see config.py's SettingsReadback docstring and the 0x20/0xA0/0xB0
#    replies these profiles already parse), and both replies above confirm it.
# 2. connection_type *is* echoed, right after the opcode, in both replies.
#    `get_eq_preset_data`'s incoming struct carries a
#    "; TODO: remove after FW fix missing byte" note on that very field, and
#    an earlier reading of this module took it to mean current firmware omits
#    the byte — so band 1 was decoded from offset 1. It does not: 32 00 20 00
#    decodes to the canonical 32 Hz first band at offset 2, and to a nonsense
#    8192 Hz at offset 1. Whatever that note is about, the field is present.
#
# This is the failure mode the raw hex in read_hardware_eq() exists for: at
# offset 1 the reply still decoded into ten plausible-looking bands.

def decode_band(data: bytes) -> EqBand:
    """Six bytes → EqBand, the inverse of :func:`encode_band`.

    The *incoming* eqBand struct uses narrower types than the outgoing one:
    gain is a plain signed byte (not the float32 encode_band's caller
    produces) and Q is a 16-bit unsigned value of thousandths (not float32).
    Decoding with the outgoing widths would misread every band.
    """
    if len(data) < 6:
        raise ValueError(f"eq band frame too short: {len(data)} bytes (need 6)")
    frequency = int.from_bytes(data[0:2], "little")
    filter_type = data[2]
    gain_raw = int.from_bytes(data[3:4], "little", signed=True)
    q_raw = int.from_bytes(data[4:6], "little")
    return EqBand(
        frequency=frequency,
        gain_db=gain_raw / 10,
        q=q_raw / 1000,
        filter_type=filter_type,
    )


# Offset of band 1 inside a decoded get_eq_preset_data (0x32) reply, and the
# size of each band: response[0] is the 0x32 echo, response[1] the
# connection_type echo, per the captured reply above. Ten bands of six bytes
# then fill offsets 2..61 of a 64-byte frame, leaving two padding bytes —
# which is also why an off-by-one here cannot be caught by length alone.
_EQ_BAND_START = 2
_EQ_BAND_SIZE = 6
_EQ_BAND_COUNT = 10


def decode_eq_preset_data(response: list[int]) -> list[EqBand]:
    """Decode a `get_eq_preset_data` (0x32) reply into its ten EqBand.

    Raises ValueError if the reply is shorter than expected — deliberately,
    rather than returning bands built from padding. See the module-level note
    above for the offset this assumes and why.
    """
    needed = _EQ_BAND_START + _EQ_BAND_SIZE * _EQ_BAND_COUNT
    if len(response) < needed:
        raise ValueError(
            f"get_eq_preset_data reply too short: got {len(response)} bytes, "
            f"need {needed}."
        )
    data = bytes(b & 0xFF for b in response)
    return [
        decode_band(data[_EQ_BAND_START + i * _EQ_BAND_SIZE:
                          _EQ_BAND_START + (i + 1) * _EQ_BAND_SIZE])
        for i in range(_EQ_BAND_COUNT)
    ]


# read_eq_preset_name's incoming struct carries no missing-byte note, so it
# mirrors its outgoing shape in full once report_id is stripped:
# response[0] = 0xA6 echo, [1] = connection_type, [2] = preset_type,
# [3:64] = 61-byte name.
_NAME_CONNECTION_OFFSET = 1
_NAME_PRESET_TYPE_OFFSET = 2
_NAME_START = 3
_NAME_LENGTH = 61


def decode_eq_preset_name(response: list[int]) -> tuple[str, int]:
    """Decode a `read_eq_preset_name` (0xA6) reply into (name, preset_type)."""
    needed = _NAME_START + _NAME_LENGTH
    if len(response) < needed:
        raise ValueError(
            f"read_eq_preset_name reply too short: got {len(response)} bytes, "
            f"need {needed}."
        )
    preset_type = response[_NAME_PRESET_TYPE_OFFSET]
    name_bytes = bytes(b & 0xFF for b in response[_NAME_START:_NAME_START + _NAME_LENGTH])
    name = name_bytes.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    return name, preset_type


# read_radio_eq_settings (0x1A) on the Nova Elite: incoming is the 0x1A echo,
# the selected onboard slot, a 6-byte alias, the 61-byte name, then ten
# eqBands (base_arctis_nova_elite_tx.device). It comes back as a feature
# report, whose first byte is the report id when the report is numbered —
# the spec's struct starts at the echo, so the decoder finds it rather than
# trusting one offset. Not yet seen from real hardware: the raw hex is kept
# by the caller for exactly that reason.
_ELITE_SETTINGS_OPCODE = 0x1A


def decode_elite_eq_settings(response: list[int]) -> dict:
    """Decode a read_radio_eq_settings (0x1A) reply.

    Returns {'slot', 'name', 'bands'}; raises ValueError when the echo is
    missing or the reply is too short to hold ten bands.
    """
    data = bytes(b & 0xFF for b in response)
    for start in (0, 1):
        if len(data) > start and data[start] == _ELITE_SETTINGS_OPCODE:
            break
    else:
        raise ValueError("no 0x1A echo at offset 0 or 1")
    name_at = start + 2 + _ELITE_ALIAS_LENGTH
    bands_at = name_at + _ELITE_NAME_LENGTH
    if len(data) < bands_at + _EQ_BAND_SIZE * _EQ_BAND_COUNT:
        raise ValueError(f"0x1A reply too short: {len(data)} bytes")
    name = data[name_at:bands_at].split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    return {
        "slot": data[start + 1],
        "name": name,
        "bands": [decode_band(data[bands_at + i * _EQ_BAND_SIZE:
                                   bands_at + (i + 1) * _EQ_BAND_SIZE])
                  for i in range(_EQ_BAND_COUNT)],
    }
