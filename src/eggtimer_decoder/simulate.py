"""Generate a synthetic 2-FSK baseband capture (no radio required)."""

from __future__ import annotations

import numpy as np

from .config import RadioConfig
from .packet import build_frame, bytes_to_bits


def modulate(bits: np.ndarray, cfg: RadioConfig) -> np.ndarray:
    sps = int(round(cfg.sps))
    freq = np.repeat(np.where(bits == 1, cfg.deviation, -cfg.deviation), sps)
    phase = 2 * np.pi * np.cumsum(freq) / cfg.samp_rate
    return np.exp(1j * phase).astype(np.complex64)


def nmea(body: str) -> str:
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"${body}*{cs:02X}\r\n"


SAMPLE_NMEA = (
    nmea("GNGGA,014048.084,,,,,0,0,,,M,,M,,")
    + nmea("GNRMC,014048.084,V,,,,,0.00,0.00,151126,,,N,V")
    + nmea("GNGGA,014049.000,3520.8200,N,11748.4800,W,1,08,1.1,1500.5,M,-33.0,M,,")
)


def nmea_payloads(text: str = SAMPLE_NMEA, chunk: int = 32) -> list[bytes]:
    data = text.encode()
    return [data[i : i + chunk] for i in range(0, len(data), chunk)]


def make_capture(
    payloads: list[bytes],
    cfg: RadioConfig = RadioConfig(),
    snr_db: float = 20.0,
    freq_offset: float = 0.0,
    gap_bits: int = 200,
    seed: int = 0,
) -> np.ndarray:
    """Frames (one per payload) separated by idle noise, with AWGN and optional carrier offset."""
    rng = np.random.default_rng(seed)
    sps = int(round(cfg.sps))
    gap = np.zeros(gap_bits, dtype=np.uint8)
    bits, active = [gap], [gap]  # active=1 where the transmitter is on
    for p in payloads:
        frame = bytes_to_bits(build_frame(p))
        bits += [frame, gap]
        active += [np.ones(len(frame), dtype=np.uint8), gap]
    sig = modulate(np.concatenate(bits), cfg)
    sig = sig * np.repeat(np.concatenate(active), sps)  # silent between bursts
    n = np.arange(len(sig))
    sig = sig * np.exp(2j * np.pi * freq_offset * n / cfg.samp_rate)
    sigma = 10 ** (-snr_db / 20) / np.sqrt(2)
    noise = sigma * (rng.standard_normal(len(sig)) + 1j * rng.standard_normal(len(sig)))
    return (sig + noise).astype(np.complex64)
