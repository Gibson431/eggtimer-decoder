import threading
import time
import tracemalloc
from dataclasses import replace

import numpy as np
import pytest

from eggtimer_decoder.channels import (
    CENTER_FREQ_HZ,
    CHANNELS,
    CHANNEL_NUMBERS,
    channel_params,
)
from eggtimer_decoder.contracts import SampleSource
from eggtimer_decoder.sim_source import SimSource
from eggtimer_decoder.simulate import nmea_payloads


def _mix_down(iq, offset_hz, fs):
    """Stand-in for S5's channel selection: shift the channel to DC."""
    n = np.arange(len(iq))
    return (iq * np.exp(-2j * np.pi * offset_hz * n / fs)).astype(np.complex64)


def _capture(src, seconds=1.0):
    return src.read(
        int(src.sample_rate * seconds)
    )  # one full cycle is shorter than 1 s


def _mean_freq(iq, fs):
    return float(np.angle(np.sum(iq[1:] * np.conj(iq[:-1]))) * fs / (2 * np.pi))


def _offset_hz(make, n=65536):
    """Signal offset from the centre, measured against the same stream with the carrier at DC.

    The mean frequency of a 2-FSK stream is biased by its bit balance, so compare against
    an identical stream whose transmitter is exactly on the centre frequency."""
    src = make()
    ref = make()
    ref.set_channel(ref.channel, tx_freq=ref.center_freq)
    return _mean_freq(src.read(n), src.sample_rate) - _mean_freq(
        ref.read(n), ref.sample_rate
    )


def test_is_sample_source():
    assert isinstance(SimSource(realtime=False), SampleSource)


@pytest.mark.parametrize("ch", CHANNEL_NUMBERS)
def test_each_channel_decodes_at_its_offset(ch):
    from eggtimer_decoder.demod_gnuradio import decode

    cfg, offset = channel_params(ch)
    tuned = CENTER_FREQ_HZ + offset  # the SDR is tuned onto the channel
    src = SimSource(
        channel=ch,
        center_freq=tuned,
        tx_freq=tuned,
        realtime=False,
        snr_db=20,
        carrier_error_hz=500,
    )
    iq = _capture(src)
    assert src.signal_offset_hz == 0
    assert (
        decode(_mix_down(iq, 500, src.sample_rate), cfg)[: len(nmea_payloads())]
        == nmea_payloads()
    )


def test_tune_shifts_signal():
    def make(center=CENTER_FREQ_HZ):
        return SimSource(channel=0, realtime=False, snr_db=40, center_freq=center)

    assert _offset_hz(make) == pytest.approx(CHANNELS[0].offset_hz, abs=300)
    src = make()
    src.tune(CENTER_FREQ_HZ + 5_000)  # transmitter stays put, so it moves to -20 kHz
    ref = make()
    ref.set_channel(0, tx_freq=ref.center_freq)
    d = _mean_freq(src.read(65536), src.sample_rate) - _mean_freq(
        ref.read(65536), ref.sample_rate
    )
    assert d == pytest.approx(CHANNELS[0].offset_hz - 5_000, abs=300)
    assert src.center_freq == CENTER_FREQ_HZ + 5_000
    src.tune(CENTER_FREQ_HZ + 1e6)  # signal outside the passband: noise only
    assert np.abs(src.read(4096)).max() < 0.2
    with pytest.raises(ValueError):
        src.tune(-1)


def test_extra_channel_values_reach_signal():
    """Rule 6: a channel with a different offset and GFSK params must drive the signal."""
    from eggtimer_decoder.demod_gnuradio import decode

    odd = replace(
        CHANNELS[0], number=3, offset_hz=25_000.0, baud=4_800.0, deviation=20_000.0
    )
    table = {**CHANNELS, 3: odd}
    src = SimSource(channel=3, realtime=False, snr_db=25, table=table)
    iq = _capture(src)
    cfg, offset = channel_params(3, table)
    assert offset == 25_000.0 and src.sample_rate == odd.samp_rate
    assert _offset_hz(
        lambda: SimSource(channel=3, realtime=False, snr_db=40, table=table)
    ) == pytest.approx(25_000, abs=300)
    want = nmea_payloads()
    assert decode(_mix_down(iq, offset, src.sample_rate), cfg)[: len(want)] == want
    # Decoding with channel 0's offset or parameters must fail: values are not silently ch0's.
    cfg0, off0 = channel_params(0)
    assert decode(_mix_down(iq, off0, src.sample_rate), cfg0)[: len(want)] != want
    assert decode(_mix_down(iq, offset, src.sample_rate), cfg0)[: len(want)] != want


def _sw(odd):
    src = SimSource(channel=0, realtime=False, snr_db=40, table={**CHANNELS, 4: odd})
    src.set_channel(4)
    return src


def test_set_channel_live():
    odd = replace(CHANNELS[0], number=4, offset_hz=30_000.0)
    src = SimSource(channel=0, realtime=False, snr_db=40, table={**CHANNELS, 4: odd})
    src.set_channel(4)
    assert src.channel == 4 and src.signal_offset_hz == 30_000.0
    assert _offset_hz(lambda: _sw(odd)) == pytest.approx(30_000, abs=300)
    with pytest.raises(ValueError):
        src.set_channel(9)
    assert src.channel == 4


def test_close_unblocks_read():
    src = SimSource(realtime=True)
    src.read(int(src.sample_rate * 0.01))
    result = []
    t = threading.Thread(
        target=lambda: result.append(src.read(int(src.sample_rate * 30)))
    )
    t.start()
    time.sleep(0.1)
    src.close()
    t.join(2)
    assert not t.is_alive() and len(result[0]) == 0
    assert len(src.read(10)) == 0
    src.close()  # idempotent


def test_realtime_pacing():
    src = SimSource(realtime=True)
    t0 = time.monotonic()
    for _ in range(5):
        src.read(int(src.sample_rate * 0.04))
    assert 0.15 < time.monotonic() - t0 < 0.5


def test_no_memory_growth_over_10s_of_stream():
    src = SimSource(realtime=False)
    chunk = 4096
    total = int(src.sample_rate * 12)
    for _ in range(50):  # warm up
        src.read(chunk)
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    for _ in range(total // chunk):
        out = src.read(chunk)
    growth = tracemalloc.get_traced_memory()[0] - base
    tracemalloc.stop()
    assert len(out) == chunk
    assert growth < 200_000
