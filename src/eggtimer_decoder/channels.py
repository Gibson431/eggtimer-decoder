"""Channel table: channel number -> GFSK parameters plus offset from the centre frequency.

Adding or editing a channel is a data-only change to CHANNELS (see docs/CHANNELS.md).
"""

from dataclasses import dataclass, replace
from typing import Mapping

from .config import RadioConfig

NUM_CHANNELS = 9  # channels 0..8
CENTER_FREQ_HZ = 921_000_000.0  # default centre frequency


@dataclass(frozen=True)
class Channel:
    number: int
    offset_hz: float  # signal frequency minus centre frequency
    samp_rate: float = 192_000.0
    baud: float = 9_600.0
    deviation: float = 35_000.0  # 2-FSK peak deviation (Hz)
    verified: bool = False  # True once confirmed against a live capture

    def radio_config(self) -> RadioConfig:
        """The receiver's GFSK parameters for this channel."""
        return RadioConfig(samp_rate=self.samp_rate, baud=self.baud, deviation=self.deviation)


# Channel 0: measured live, signal at 920.99 MHz with a 921 MHz centre.
_CH0 = Channel(number=0, offset_hz=-10_000.0, verified=True)

# Channels 1-8 are unverified copies of channel 0 until measured.
CHANNELS: dict[int, Channel] = {0: _CH0, **{n: replace(_CH0, number=n, verified=False) for n in range(1, NUM_CHANNELS)}}


def get_channel(number: int, table: Mapping[int, Channel] = CHANNELS) -> Channel:
    """Look up a channel; raises ValueError if it is not in 0..8 (or not in `table`)."""
    if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number < NUM_CHANNELS:
        raise ValueError(f"channel must be an integer 0..{NUM_CHANNELS - 1}, got {number!r}")
    if number not in table:
        raise ValueError(f"channel {number} is not defined")
    return table[number]


def channel_params(number: int, table: Mapping[int, Channel] = CHANNELS) -> tuple[RadioConfig, float]:
    """(GFSK RadioConfig, offset_hz) the receiver should use for a channel."""
    ch = get_channel(number, table)
    return ch.radio_config(), ch.offset_hz
