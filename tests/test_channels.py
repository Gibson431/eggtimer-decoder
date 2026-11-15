from dataclasses import replace

import pytest

from eggtimer_decoder.channels import (
    CHANNELS,
    CHANNEL_NUMBERS,
    channel_params,
    get_channel,
)
from eggtimer_decoder.config import RadioConfig


@pytest.mark.parametrize("bad", [-1, 100, 1.5, "0", None, True])
def test_out_of_range_rejected(bad):
    with pytest.raises(ValueError):
        get_channel(bad)
    with pytest.raises(ValueError):
        channel_params(bad)


def test_table_is_consistent():
    assert CHANNEL_NUMBERS == tuple(sorted(CHANNELS))
    cfg0, off0 = channel_params(0)
    assert cfg0 == RadioConfig()
    assert off0 == -15_000.0
    for n in CHANNEL_NUMBERS:
        assert get_channel(n).number == n


def test_duplicate_channel_rejected():
    from eggtimer_decoder.channels import Channel, _build_table

    with pytest.raises(ValueError):
        _build_table(
            [Channel(number=0, offset_hz=0.0), Channel(number=0, offset_hz=1.0)]
        )


def test_editing_one_channel_affects_only_it():
    table = dict(CHANNELS)
    table[1] = replace(table[1], offset_hz=25_000.0, deviation=20_000.0)
    default = {n: channel_params(n) for n in CHANNEL_NUMBERS}
    for n in CHANNEL_NUMBERS:
        before = default[n]
        cfg, off = channel_params(n, table)
        if n == 1:
            assert off == 25_000.0 and cfg.deviation == 20_000.0
        else:
            assert (cfg, off) == before
    assert channel_params(1) == default[1]  # default table untouched
