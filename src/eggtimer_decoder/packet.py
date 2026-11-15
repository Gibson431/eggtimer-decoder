"""Eggfinder TX framing, verified against a live capture at ~920.99 MHz.

    preamble (0x55 * 5) | sync (0x2D 0xD4) | len | payload[len] | crc16

CRC is CRC-16/XMODEM (poly 0x1021, init 0, big-endian) over len + payload.
The payload is a chunk (<= 32 bytes seen) of the module's NMEA text stream, so
sentences span several frames; ``NmeaAssembler`` stitches them back together.
Two non-NMEA payloads were also seen (``!0>@0>=QUASAR>\\r\\n`` and a bare
``\\r\\n``); they are passed through as raw frames.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

PREAMBLE = bytes([0x55] * 5)
SYNC = bytes([0x2D, 0xD4])
MAX_PAYLOAD = 64  # observed max is 32; anything larger is treated as noise


def crc16(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


def build_frame(payload: bytes) -> bytes:
    body = bytes([len(payload)]) + payload
    return PREAMBLE + SYNC + body + crc16(body).to_bytes(2, "big")


def bytes_to_bits(data: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(data, dtype=np.uint8))  # MSB first


def _bits_to_bytes(bits: np.ndarray) -> bytes:
    n = len(bits) // 8 * 8
    return np.packbits(bits[:n].astype(np.uint8)).tobytes()


def find_frames(bits: np.ndarray) -> list[bytes]:
    """Scan a hard-decision bit stream; return payloads of CRC-valid frames.

    Tries both polarities, since the demod sign depends on tuning/convention.
    """
    found: list[tuple[int, bytes]] = []
    sync_bits = bytes_to_bits(SYNC)
    for invert in (False, True):
        b = np.asarray(bits, dtype=np.uint8)
        if invert:
            b = b ^ 1
        if len(b) < len(sync_bits):
            continue
        windows = np.lib.stride_tricks.sliding_window_view(b, len(sync_bits))
        for start in np.flatnonzero((windows == sync_bits).all(axis=1)):
            pos = int(start) + len(sync_bits)
            hdr = _bits_to_bytes(b[pos : pos + 8])
            if not hdr or not 0 < hdr[0] <= MAX_PAYLOAD:
                continue
            n = hdr[0]
            raw = _bits_to_bytes(b[pos : pos + 8 * (n + 3)])
            if len(raw) < n + 3 or crc16(raw[: n + 1]) != int.from_bytes(raw[n + 1 : n + 3], "big"):
                continue
            found.append((int(start), raw[1 : n + 1]))
    return [p for _, p in sorted(found, key=lambda x: x[0])]


def nmea_checksum_ok(line: str) -> bool:
    if not line.startswith("$") or "*" not in line:
        return False
    body, _, cs = line[1:].partition("*")
    try:
        want = int(cs[:2], 16)
    except ValueError:
        return False
    got = 0
    for ch in body:
        got ^= ord(ch)
    return got == want


class NmeaAssembler:
    """Reassemble NMEA sentences from frame payload chunks.

    Lost frames corrupt a sentence; those fail the NMEA checksum and are
    dropped (returned lines are always checksum-valid).
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, payload: bytes) -> list[str]:
        self._buf += payload.decode("ascii", errors="replace")
        lines: list[str] = []
        while "\n" in self._buf:
            line, _, self._buf = self._buf.partition("\n")
            line = line.strip()
            if "$" in line:
                line = line[line.rindex("$") :]
            if nmea_checksum_ok(line):
                lines.append(line)
        return lines


@dataclass(frozen=True)
class Position:
    lat: float
    lon: float
    alt_m: float
    satellites: int


def parse_gga(line: str) -> Position | None:
    """Position from a GGA sentence, or None if there is no fix."""
    f = line[1:].split("*")[0].split(",")
    if not f[0].endswith("GGA") or len(f) < 10 or f[6] in ("", "0") or not f[2] or not f[4]:
        return None

    def deg(v: str, hemi: str, dlen: int) -> float:
        d = float(v[:dlen]) + float(v[dlen:]) / 60
        return -d if hemi in ("S", "W") else d

    return Position(deg(f[2], f[3], 2), deg(f[4], f[5], 3), float(f[9] or 0), int(f[7] or 0))
