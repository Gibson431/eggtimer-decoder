"""GNU Radio flowgraph: IQ -> FM discriminator -> clock recovery -> bits.

GNU Radio is not pip-installable; install it from your distro and create the
venv with ``uv venv --system-site-packages`` (see README). Imported lazily so
the rest of the package works without it.
"""

from __future__ import annotations

import math

import numpy as np

from .config import RadioConfig
from .packet import find_frames


def demod_bits(iq: np.ndarray, cfg: RadioConfig) -> np.ndarray:
    from gnuradio import analog, blocks, digital, filter, gr
    from gnuradio.filter import firdes

    class FSKFlowgraph(gr.top_block):
        def __init__(self) -> None:
            super().__init__("eggfinder-fsk")
            src = blocks.vector_source_c(iq.tolist(), False)
            ch_taps = firdes.low_pass(1.0, cfg.samp_rate, cfg.deviation + 2 * cfg.baud, cfg.baud * 2)
            chan = filter.fir_filter_ccf(1, ch_taps)  # channel filter before the discriminator
            demod = analog.quadrature_demod_cf(cfg.samp_rate / (2 * math.pi * cfg.deviation))
            taps = firdes.low_pass(1.0, cfg.samp_rate, cfg.baud * 0.75, cfg.baud * 0.5)
            lpf = filter.fir_filter_fff(1, taps)
            mu = 0.5
            cr = digital.clock_recovery_mm_ff(cfg.sps, 0.25 * 0.175**2, mu, 0.175, 0.005)
            slicer = digital.binary_slicer_fb()
            self.sink = blocks.vector_sink_b()
            self.connect(src, chan, demod, lpf, cr, slicer, self.sink)

    fg = FSKFlowgraph()
    fg.run()
    return np.asarray(fg.sink.data(), dtype=np.uint8)


def decode(iq: np.ndarray, cfg: RadioConfig) -> list[bytes]:
    return find_frames(demod_bits(iq, cfg))
