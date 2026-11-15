from dataclasses import replace

import pytest

from eggtimer_decoder.channels import CHANNELS, NUM_CHANNELS, channel_params, get_channel
from eggtimer_decoder.config import RadioConfig


@pytest.mark.parametrize("bad", [-1, 9, 100, 1.5, "0", None, True])
def test_out_of_range_rejected(bad):
    with pytest.raises(ValueError):
        get_channel(bad)
    with pytest.raises(ValueError):
        channel_params(bad)


def test_all_channels_match_channel_0():
    assert NUM_CHANNELS == 9
    cfg0, off0 = channel_params(0)
    assert cfg0 == RadioConfig()
    assert off0 == -10_000.0
    for n in range(9):
        assert channel_params(n) == (cfg0, off0)
        assert get_channel(n).number == n


def test_editing_one_channel_affects_only_it():
    table = dict(CHANNELS)
    table[3] = replace(table[3], offset_hz=25_000.0, deviation=20_000.0)
    cfg0, off0 = channel_params(0)
    for n in range(9):
        cfg, off = channel_params(n, table)
        if n == 3:
            assert off == 25_000.0 and cfg.deviation == 20_000.0
        else:
            assert (cfg, off) == (cfg0, off0)
    assert channel_params(3) == (cfg0, off0)  # default table untouched
