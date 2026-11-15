"""RTL-SDR capture via gr-osmosdr, returning IQ at ``cfg.samp_rate``."""

from __future__ import annotations

import numpy as np

from .config import RadioConfig

RTL_RATE = 960_000.0  # 5x the 192 kHz working rate; valid RTL2832 rate


def capture(freq_hz: float, seconds: float, cfg: RadioConfig, gain_db: float = 30.0) -> np.ndarray:
    import osmosdr
    from gnuradio import blocks, gr
    from gnuradio.filter import firdes, fir_filter_ccf

    decim = int(round(RTL_RATE / cfg.samp_rate))
    if abs(RTL_RATE / decim - cfg.samp_rate) > 1:
        raise ValueError("cfg.samp_rate must divide 960 kHz evenly")

    tb = gr.top_block("rtl-capture")
    src = osmosdr.source(args="numchan=1 rtl=0")
    src.set_sample_rate(RTL_RATE)
    src.set_center_freq(freq_hz)
    src.set_gain_mode(False)
    src.set_gain(gain_db)
    taps = firdes.low_pass(1.0, RTL_RATE, cfg.samp_rate * 0.4, cfg.samp_rate * 0.1)
    dec = fir_filter_ccf(decim, taps)
    head = blocks.head(gr.sizeof_gr_complex, int(seconds * cfg.samp_rate))
    sink = blocks.vector_sink_c()
    tb.connect(src, dec, head, sink)
    tb.run()
    return np.asarray(sink.data(), dtype=np.complex64)
