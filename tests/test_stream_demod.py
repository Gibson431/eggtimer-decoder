from dataclasses import replace

import numpy as np
import pytest

from eggtimer_decoder.channels import CHANNELS, NUM_CHANNELS
from eggtimer_decoder.simulate import make_capture
from eggtimer_decoder.stream_demod import RESUME_FRAMES_LOST, ChannelDecoder, StreamDemod

FS = 192_000.0
PAYLOADS = [bytes([65 + i]) * (5 + 3 * i) for i in range(10)]


def _iq(ch, payloads=PAYLOADS, snr=20.0, fs=FS, seed=1):
    """Capture with the signal placed at the channel's offset from the centre."""
    cfg = replace(ch.radio_config(), samp_rate=fs)
    return make_capture(payloads, cfg, snr_db=snr, freq_offset=ch.offset_hz, seed=seed)


def _run(dec, iq, size):
    out = []
    for i in range(0, len(iq), size):
        out += dec.process(iq[i : i + size])
    return out


def _alt_table():
    """Temporary channel 8 differing in offset, baud and deviation (PLAN rule 6)."""
    alt = replace(CHANNELS[0], number=8, offset_hz=+25_000.0, baud=4_800.0, deviation=20_000.0)
    return {**CHANNELS, 8: alt}


@pytest.mark.parametrize("n", range(NUM_CHANNELS))
def test_each_channel_end_to_end(n):
    dec = ChannelDecoder(FS, 921e6, n)
    assert _run(dec, _iq(CHANNELS[n]), 4096) == PAYLOADS


def test_higher_input_rate_decimates():
    iq = _iq(CHANNELS[0], fs=768_000.0)
    assert _run(ChannelDecoder(768_000.0, 921e6, 0), iq, 10_000) == PAYLOADS


@pytest.mark.parametrize("size", [1, 7, 333, 5000, 10**7])
def test_chunk_size_independence(size):
    iq = _iq(CHANNELS[0])[:20_000]
    ref = StreamDemod(FS, 921e6, 0).process(iq)
    d = StreamDemod(FS, 921e6, 0)
    got = np.concatenate([d.process(iq[i : i + size]) for i in range(0, len(iq), size)])
    # Float rounding can flip decisions on pure-noise samples between bursts, so allow a
    # few stray bits there; frames (below) must be identical.
    assert len(ref) == len(got)
    assert np.mean(ref != got) < 0.03


def test_chunk_size_independence_frames():
    iq = _iq(CHANNELS[3])
    assert _run(ChannelDecoder(FS, 921e6, 3), iq, 1000) == _run(ChannelDecoder(FS, 921e6, 3), iq, 77_777)


def test_extra_channel_decodes_only_when_selected():
    table = _alt_table()
    iq = _iq(table[8])
    assert _run(ChannelDecoder(FS, 921e6, 8, table), iq, 4096) == PAYLOADS
    # wrong channel selected: nothing decodes
    assert _run(ChannelDecoder(FS, 921e6, 0, table), iq, 4096) == []
    assert _run(ChannelDecoder(FS, 921e6, 7, table), iq, 4096) == []
    # and the altered channel does not decode a stock-channel signal
    assert _run(ChannelDecoder(FS, 921e6, 8, table), _iq(CHANNELS[0]), 4096) == []


def test_switch_channel_mid_stream_resumes():
    table = _alt_table()
    a, b = _iq(CHANNELS[0], seed=2), _iq(table[8], seed=3)
    dec = ChannelDecoder(FS, 921e6, 0, table)
    assert _run(dec, a, 4096) == PAYLOADS
    dec.set_channel(8)
    assert _run(dec, b, 4096) == PAYLOADS  # switched at a frame boundary: nothing lost
    dec.set_channel(0)
    assert _run(dec, a, 4096) == PAYLOADS


def test_switch_resume_bound_at_many_cut_points():
    """Switching at any sample loses at most RESUME_FRAMES_LOST frame; later ones decode."""
    table = _alt_table()
    b = _iq(table[8], seed=4)
    for cut in range(1000, len(b) - 5000, 9973):
        dec = ChannelDecoder(FS, 921e6, 0, table)
        dec.process(b[:cut])
        dec.set_channel(8)
        got = _run(dec, b[cut:], 3000)
        assert got == PAYLOADS[len(PAYLOADS) - len(got) :]  # a correct, in-order suffix
        # frames complete before the cut are not expected; at most one more is lost
        before = len(_run(ChannelDecoder(FS, 921e6, 8, table), b[:cut], 3000))
        assert len(got) >= len(PAYLOADS) - before - RESUME_FRAMES_LOST


def test_invalid_channel_rejected_and_state_kept():
    dec = ChannelDecoder(FS, 921e6, 2)
    with pytest.raises(ValueError):
        dec.set_channel(9)
    assert dec.demod.channel.number == 2
