"""IQ -> FM discriminator -> clock recovery -> bits (clock recovery runs in GNU Radio).

GNU Radio is not pip-installable; install it from your distro and create the
venv with ``uv venv --system-site-packages`` (see README). Imported lazily so
the rest of the package works without it.
"""

from __future__ import annotations

import math

import numpy as np

from .config import RadioConfig
from .packet import find_frames


def _fir(x: np.ndarray, taps: np.ndarray) -> np.ndarray:
    """Causal zero-history FIR, same alignment as gr's fir_filter_*, in float64."""
    t = np.asarray(taps, dtype=np.float64)
    if np.iscomplexobj(x):
        return np.convolve(x.real, t)[: len(x)] + 1j * np.convolve(x.imag, t)[: len(x)]
    return np.convolve(x, t)[: len(x)]


def demod_bits(iq: np.ndarray, cfg: RadioConfig) -> np.ndarray:
    """IQ -> bits.

    The linear stages (channel filter, FM discriminator, low-pass) run in numpy float64 on
    purpose: GNU Radio's volk FIR kernels round differently depending on buffer alignment and
    chunking (~1 ulp), and the Mueller-Muller loop amplifies that into different bit streams
    (seen as rare, load-dependent decode failures). Clock recovery on a fixed input vector is
    deterministic, so only that stage runs in GNU Radio.
    """
    from gnuradio import blocks, digital, gr
    from gnuradio.filter import firdes

    x = np.asarray(iq, dtype=np.complex128)
    x = _fir(x, firdes.low_pass(1.0, cfg.samp_rate, cfg.deviation + 2 * cfg.baud, cfg.baud * 2))
    d = np.zeros(len(x))
    d[1:] = np.angle(x[1:] * np.conj(x[:-1])) * (cfg.samp_rate / (2 * math.pi * cfg.deviation))
    d = _fir(d, firdes.low_pass(1.0, cfg.samp_rate, cfg.baud * 0.75, cfg.baud * 0.5)).astype(np.float32)

    class ClockRecovery(gr.top_block):
        def __init__(self) -> None:
            super().__init__("eggfinder-fsk")
            src = blocks.vector_source_f(d.tolist(), False)
            cr = digital.clock_recovery_mm_ff(cfg.sps, 0.25 * 0.175**2, 0.5, 0.175, 0.005)
            self.sink = blocks.vector_sink_b()
            self.connect(src, cr, digital.binary_slicer_fb(), self.sink)

    fg = ClockRecovery()
    fg.run()
    return np.asarray(fg.sink.data(), dtype=np.uint8)


def decode(iq: np.ndarray, cfg: RadioConfig) -> list[bytes]:
    return find_frames(demod_bits(iq, cfg))
