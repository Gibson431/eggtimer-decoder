"""Continuous signal quality measurement over IQ chunks (numpy only).

``SignalMetrics`` keeps the most recent ``window`` samples (chunk size does
not matter) and, on each ``update``, measures:

* ``strength_db``: mean power of the window in dB relative to full scale
  (a complex tone of amplitude 1.0 is 0 dB). This is total power including
  noise, so it reads high by ``10*log10(1 + 1/SNR)`` (0.4 dB at 10 dB SNR).
* ``carrier_offset_hz``: carrier frequency relative to the expected channel
  centre. A 2-FSK/GFSK spectrum has two lobes (at carrier +/- deviation), so
  a single FFT peak would be off by the deviation. Instead the carrier is the
  power centroid (noise subtracted) of the occupied bins, which for balanced
  data is the midpoint of the lobes; a pure tone is handled identically. It
  is 0.0 while no signal is present.
* ``present``: peak of the smoothed power spectrum over its median (the noise
  floor), with hysteresis: on above ``on_db`` (10 dB), off below ``off_db``
  (6 dB). Noise-only input peaks about 1-3 dB above the median.

Stated tolerances (sample rate 192 kHz, window 4096, SNR >= 10 dB, both FSK tones
in the window):

* strength: +/- 1 dB (bias from noise above, bin-power estimation noise
  <0.1 dB over 4096 samples).
* carrier offset: +/- 300 Hz for 2-FSK +/-35 kHz at 9.6 kBd with random data
  (measured: 32 Hz rms, 90 Hz max over 120 trials; the limit leaves margin
  for noise and lobe asymmetry); +/- 100 Hz for a pure tone (bin width 47 Hz).
  The midpoint-of-lobes method is used because the plain centroid wanders
  by ~2.4 kHz rms with data imbalance in a 4096-sample window.
* detection: signals >= 10 dB SNR (per-bin in 192 kHz) present; noise-only
  absent.

Limitations: other strong signals in the passband bias the offset; a frame
burst shorter than the window is diluted, so S8 may want to debounce.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .contracts import SignalEvent

__all__ = ["SignalMetrics", "SignalMeasurement"]

_MIN_SAMPLES = 1024


@dataclass(frozen=True)
class SignalMeasurement:
    strength_db: float
    carrier_offset_hz: float
    present: bool

    def to_event(self, timestamp: float = 0.0) -> SignalEvent:
        return SignalEvent(self.strength_db, self.carrier_offset_hz, self.present, timestamp)


class SignalMetrics:
    def __init__(
        self,
        sample_rate: float,
        channel_offset_hz: float = 0.0,
        window: int = 4096,
        on_db: float = 10.0,
        off_db: float = 6.0,
        smooth_hz: float = 2000.0,
    ):
        if off_db > on_db:
            raise ValueError("off_db must not exceed on_db")
        self.sample_rate = float(sample_rate)
        self.channel_offset_hz = float(channel_offset_hz)
        self.on_db, self.off_db = on_db, off_db
        self._window = int(window)
        self._smooth = max(1, int(round(smooth_hz * window / sample_rate)) | 1)
        self._hann = np.hanning(self._window)
        self._buf = np.zeros(0, dtype=np.complex64)
        self._present = False
        self._last = SignalMeasurement(-np.inf, 0.0, False)

    def reset(self) -> None:
        """Drop buffered samples and state (call on retune / channel change)."""
        self._buf = np.zeros(0, dtype=np.complex64)
        self._present = False
        self._last = SignalMeasurement(-np.inf, 0.0, False)

    def set_channel_offset(self, offset_hz: float) -> None:
        self.channel_offset_hz = float(offset_hz)
        self.reset()

    def update(self, iq_chunk: np.ndarray) -> SignalMeasurement:
        """Feed samples; returns (and stores) the new measurement."""
        chunk = np.asarray(iq_chunk, dtype=np.complex64)
        if chunk.size:
            self._buf = np.concatenate([self._buf, chunk])[-self._window :]
        if self._buf.size < _MIN_SAMPLES:
            return self._last
        self._last = self._measure(self._buf)
        return self._last

    def snapshot(self) -> SignalMeasurement:
        """The latest measurement (no new data)."""
        return self._last

    def _measure(self, x: np.ndarray) -> SignalMeasurement:
        n = x.size
        power = float(np.mean(np.abs(x) ** 2))
        strength = 10 * np.log10(power) if power > 0 else -np.inf
        w = self._hann[:n] if n == self._window else np.hanning(n)
        psd = np.fft.fftshift(np.abs(np.fft.fft(x * w)) ** 2)
        freqs = np.fft.fftshift(np.fft.fftfreq(n, 1 / self.sample_rate))
        floor = float(np.median(psd))
        if floor <= 0:
            self._present = False
            return SignalMeasurement(strength, 0.0, False)
        k = max(1, min(self._smooth, n // 8) | 1)
        smooth = np.convolve(psd, np.ones(k) / k, mode="same")
        peak_db = 10 * np.log10(smooth.max() / floor)
        thr = self.off_db if self._present else self.on_db
        self._present = bool(peak_db >= thr)
        offset = 0.0
        if self._present:
            # median of an exponential PSD is ln2 * mean, so scale to the mean
            excess = np.where(smooth > 4 * floor, np.maximum(psd - floor / np.log(2), 0), 0)
            total = excess.sum()
            if total > 0:
                centre = float(np.sum(freqs * excess) / total)
                offset = _carrier(freqs, excess, centre) - self.channel_offset_hz
        return SignalMeasurement(float(strength), offset, self._present)


def _carrier(freqs: np.ndarray, excess: np.ndarray, centroid: float) -> float:
    """Midpoint of the two FSK lobes, or the centroid if only one lobe is there.

    The overall centroid shifts with data imbalance (~2.4 kHz rms over 4096
    samples of random 2-FSK); the lobe positions do not.
    """
    lo, hi = freqs < centroid, freqs >= centroid
    p_lo, p_hi = excess[lo].sum(), excess[hi].sum()
    if min(p_lo, p_hi) < 0.15 * (p_lo + p_hi):
        return centroid
    c_lo = np.sum(freqs[lo] * excess[lo]) / p_lo
    c_hi = np.sum(freqs[hi] * excess[hi]) / p_hi
    if c_hi - c_lo < 20_000.0:  # one broad/ tone-like lobe, not two
        return centroid
    return float((c_lo + c_hi) / 2)
