"""Simulated live SampleSource: repeating Eggfinder frames placed at a channel's offset.

Behaves like a device: blocking ``read``, live ``tune``, ``close`` unblocks a
pending ``read``. Memory is bounded: one pre-built cycle of noiseless frames is
replayed, and each read adds fresh noise to a chunk-sized array only.

Frequency model: the simulated transmitter sits at an absolute frequency fixed
when the channel is selected (``center_freq + channel.offset_hz`` at that
moment, or an explicit ``tx_freq``). ``tune()`` then moves the signal within
the passband (offset = tx_freq - centre); if it leaves the passband only noise
is produced. Selecting a channel re-anchors the transmitter on the current centre.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping

import numpy as np

from .channels import CENTER_FREQ_HZ, CHANNELS, Channel, get_channel
from .simulate import make_capture, nmea_payloads

__all__ = ["SimSource"]


class SimSource:
    def __init__(
        self,
        channel: int = 0,
        center_freq: float = CENTER_FREQ_HZ,
        snr_db: float = 20.0,
        carrier_error_hz: float = 0.0,
        realtime: bool = True,
        table: Mapping[int, Channel] = CHANNELS,
        payloads: list[bytes] | None = None,
        seed: int = 0,
        tx_freq: float | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._table = table
        self._payloads = payloads if payloads is not None else nmea_payloads()
        self._rng = np.random.default_rng(seed)
        self._seed = seed
        self.snr_db = snr_db
        self.carrier_error_hz = carrier_error_hz
        self.realtime = realtime
        self._center = self._check_freq(center_freq)
        self._phase = 0.0  # carrier phase accumulator (radians)
        self._pos = 0  # index into the cycle
        self._emitted = 0  # samples emitted since (re)start of pacing
        self._t0: float | None = None
        self._set_channel(channel, tx_freq)

    # -- SampleSource -------------------------------------------------
    @property
    def sample_rate(self) -> float:
        return self._chan.samp_rate

    @property
    def center_freq(self) -> float:
        return self._center

    @property
    def channel(self) -> int:
        return self._chan.number

    @property
    def signal_offset_hz(self) -> float:
        """Where the signal currently sits relative to the centre (before carrier error)."""
        return self._tx_freq - self._center

    def tune(self, center_freq: float) -> None:
        with self._lock:
            self._center = self._check_freq(center_freq)

    def set_channel(self, channel: int, tx_freq: float | None = None) -> None:
        """Switch the transmitted channel live (re-anchors on the current centre)."""
        with self._lock:
            self._set_channel(channel, tx_freq)

    def read(self, num_samples: int) -> np.ndarray:
        if num_samples <= 0:
            raise ValueError("num_samples must be positive")
        if self._closed.is_set():
            return np.empty(0, np.complex64)
        with self._lock:
            fs = self.sample_rate
            idx = (self._pos + np.arange(num_samples)) % len(self._cycle)
            sig = self._cycle[idx]
            self._pos = int((self._pos + num_samples) % len(self._cycle))
            offset = self._tx_freq - self._center + self.carrier_error_hz
            if abs(offset) >= fs / 2:
                sig = np.zeros(num_samples, np.complex64)  # signal outside the passband
            else:
                step = 2 * np.pi * offset / fs
                ph = self._phase + step * np.arange(num_samples)
                sig = sig * np.exp(1j * ph).astype(np.complex64)
                self._phase = float((self._phase + step * num_samples) % (2 * np.pi))
            sigma = (
                10 ** (-self.snr_db / 20) / np.sqrt(2)
                if np.isfinite(self.snr_db)
                else 0.0
            )
            noise = sigma * (
                self._rng.standard_normal(num_samples)
                + 1j * self._rng.standard_normal(num_samples)
            )
            out = (sig + noise).astype(np.complex64)
            self._emitted += num_samples
            emitted = self._emitted
        if self.realtime:
            if self._t0 is None:
                self._t0 = time.monotonic()
            delay = self._t0 + emitted / fs - time.monotonic()
            if delay > 0 and self._closed.wait(delay):
                return np.empty(0, np.complex64)  # closed while waiting
        return out

    def close(self) -> None:
        self._closed.set()

    # -- internals ----------------------------------------------------
    @staticmethod
    def _check_freq(f: float) -> float:
        if not np.isfinite(f) or f <= 0:
            raise ValueError(
                f"centre frequency must be a positive finite number, got {f!r}"
            )
        return float(f)

    def _set_channel(self, channel: int, tx_freq: float | None) -> None:
        ch = get_channel(channel, self._table)
        cycle = make_capture(
            self._payloads, ch.radio_config(), snr_db=float("inf"), seed=self._seed
        )
        self._chan = ch
        self._cycle = cycle
        self._tx_freq = (
            float(tx_freq) if tx_freq is not None else self._center + ch.offset_hz
        )
        self._pos = 0
        self._emitted = 0
        self._t0 = None
