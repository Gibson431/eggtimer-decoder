"""Streaming IQ -> bits demodulation for one selected channel (pure numpy).

Pipeline, all state carried across chunks::

    mix channel offset to 0 Hz -> channel low-pass + integer decimate
    -> FM discriminator -> symbol low-pass -> Mueller-Muller clock recovery -> bits

``StreamDemod.process(iq_chunk)`` returns the bits that became available;
``ChannelDecoder`` adds ``FrameDetector`` so ``process(iq)`` returns frames.
Decoded frames do not depend on how the input is split into chunks (float rounding
can flip a few bits in noise-only stretches between transmissions).

Switching channel (or centre frequency) with ``set_channel`` / ``set_center_freq``
keeps the device open; it only resets the demodulator and detector state.

Resume bound (tested): after a retarget, the frame being received at that
moment is lost, and decoding is correct from the NEXT frame on, provided frames
are separated by at least ``RESYNC_GAP_BITS`` idle bit-times (the simulator
uses 200; the real transmitter's gaps are much longer). So at most
``RESUME_FRAMES_LOST`` frame is lost per switch.
"""

from __future__ import annotations

import numpy as np

from .channels import CHANNELS, Channel, get_channel
from .packet import FrameDetector

RESYNC_GAP_BITS = 100
RESUME_FRAMES_LOST = 1


def _lowpass(cutoff: float, transition: float, fs: float) -> np.ndarray:
    """Hamming-windowed sinc FIR (odd length), unity DC gain."""
    n = int(np.ceil(3.3 * fs / transition)) | 1
    t = np.arange(n) - (n - 1) / 2
    h = np.sinc(2 * cutoff / fs * t) * np.hamming(n)
    return h / h.sum()


class StreamDemod:
    """Continuous GFSK demodulator for one channel of a wideband IQ stream.

    ``sample_rate`` and ``center_freq`` describe the incoming IQ; the channel
    is at ``center_freq + channel.offset_hz``. The input rate is decimated by
    ``floor(sample_rate / channel.samp_rate)``.
    """

    def __init__(
        self,
        sample_rate: float,
        center_freq: float | None = None,
        channel: int = 0,
        table: dict[int, Channel] = CHANNELS,
    ) -> None:
        self.sample_rate = float(sample_rate)
        self.center_freq = center_freq  # informational; offsets are relative to it
        self._table = table
        self.set_channel(channel)

    @property
    def channel(self) -> Channel:
        return self._ch

    def set_channel(self, channel: int) -> None:
        """Retarget to ``channel`` (ValueError if invalid, nothing changes) and reset state."""
        ch = get_channel(channel, self._table)
        if ch.samp_rate > self.sample_rate:
            raise ValueError(
                f"channel {channel} needs {ch.samp_rate} S/s, input is {self.sample_rate}"
            )
        if abs(ch.offset_hz) + ch.deviation >= self.sample_rate / 2:
            raise ValueError(
                f"channel {channel} offset {ch.offset_hz} Hz is outside the input bandwidth"
            )
        self._ch = ch
        self._configure()

    def set_center_freq(self, center_freq: float) -> None:
        """Offsets are relative to the centre, so this records it and resets state."""
        self.center_freq = center_freq
        self._configure()

    def reset(self) -> None:
        self._configure()

    def _configure(self) -> None:
        ch, fs = self._ch, self.sample_rate
        self._decim = int(fs // ch.samp_rate)
        self._fs2 = fs / self._decim
        self._w = -2 * np.pi * ch.offset_hz / fs
        self._phase = 0.0
        self._chan_taps = _lowpass(
            ch.deviation + 2 * ch.baud, 2 * ch.baud + fs / 20, fs
        )
        self._chan_tail = np.zeros(len(self._chan_taps) - 1, dtype=np.complex128)
        self._skip = 0
        self._last_iq = 0j
        self._have_last = False
        self._disc_gain = self._fs2 / (2 * np.pi * ch.deviation)
        self._sym_taps = _lowpass(
            ch.baud * 0.75, ch.baud * 0.5 + self._fs2 / 40, self._fs2
        )
        self._sym_tail = np.zeros(len(self._sym_taps) - 1)
        self._sps = self._fs2 / ch.baud
        self._omega = self._sps
        self._pos = 0.0
        self._cr_tail = np.zeros(0)
        self._last_s = 0.0

    def process(self, iq: np.ndarray) -> np.ndarray:
        """Consume a chunk of complex IQ (any length); return the new bits (uint8)."""
        x = np.asarray(iq, dtype=np.complex128).ravel()
        if len(x) == 0:
            return np.zeros(0, dtype=np.uint8)
        # mix to baseband
        ph = self._phase + self._w * np.arange(len(x))
        x = x * np.exp(1j * ph)
        self._phase = float((self._phase + self._w * len(x)) % (2 * np.pi))
        # channel filter + decimate
        buf = np.concatenate([self._chan_tail, x])
        self._chan_tail = buf[len(buf) - len(self._chan_tail) :]
        y = np.convolve(buf, self._chan_taps, mode="valid")[self._skip :: self._decim]
        self._skip = (self._skip - len(x)) % self._decim
        if len(y) == 0:
            return np.zeros(0, dtype=np.uint8)
        # FM discriminator
        prev = np.concatenate([[self._last_iq if self._have_last else y[0]], y[:-1]])
        self._last_iq, self._have_last = y[-1], True
        d = np.angle(y * np.conj(prev)) * self._disc_gain
        # symbol filter
        buf = np.concatenate([self._sym_tail, d])
        self._sym_tail = buf[len(buf) - len(self._sym_tail) :]
        s = np.convolve(buf, self._sym_taps, mode="valid")
        return self._clock_recover(s)

    def _clock_recover(self, s: np.ndarray) -> np.ndarray:
        gain_omega, gain_mu, rel = 0.25 * 0.175**2, 0.175, 0.005
        b = np.concatenate([self._cr_tail, s])
        pos, omega, last_s = self._pos, self._omega, self._last_s
        lo, hi = self._sps * (1 - rel), self._sps * (1 + rel)
        out = []
        n = len(b) - 1
        while pos < n:
            i = int(pos)
            f = pos - i
            cur = b[i] * (1 - f) + b[i + 1] * f
            out.append(cur > 0)
            err = (1.0 if last_s > 0 else -1.0) * cur - (
                1.0 if cur > 0 else -1.0
            ) * last_s
            err = max(-1.0, min(1.0, err))
            omega = min(hi, max(lo, omega + gain_omega * err))
            pos += omega + gain_mu * err
            last_s = cur
        shift = min(
            int(pos), len(b)
        )  # pos may point beyond this chunk; keep the offset
        self._cr_tail = b[shift:]
        self._pos, self._omega, self._last_s = pos - shift, omega, last_s
        return np.array(out, dtype=np.uint8)


class ChannelDecoder:
    """``StreamDemod`` + ``FrameDetector``: ``process(iq) -> list[bytes]`` frame payloads."""

    def __init__(
        self,
        sample_rate: float,
        center_freq: float | None = None,
        channel: int = 0,
        table: dict[int, Channel] = CHANNELS,
    ) -> None:
        self.demod = StreamDemod(sample_rate, center_freq, channel, table)
        self.detector = FrameDetector()

    def process(self, iq: np.ndarray) -> list[bytes]:
        return self.detector.feed(self.demod.process(iq))

    def set_channel(self, channel: int) -> None:
        """Retarget without reopening anything; see the module docstring for the resume bound."""
        self.demod.set_channel(channel)
        self.detector.reset()

    def set_center_freq(self, center_freq: float) -> None:
        self.demod.set_center_freq(center_freq)
        self.detector.reset()
