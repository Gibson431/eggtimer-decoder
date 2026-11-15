"""Channel table: channel number -> GFSK parameters plus offset from the centre frequency.

Adding or editing a channel is a data-only change to CHANNELS (see docs/CHANNELS.md).
"""

from dataclasses import dataclass
from typing import Iterable, Mapping

from .config import RadioConfig

CENTER_FREQ_HZ = 921_000_000.0  # default centre frequency


@dataclass(frozen=True)
class Channel:
    number: int
    offset_hz: float  # added to the centre frequency to get the SDR tune frequency; no other effect
    samp_rate: float = 192_000.0
    baud: float = 9_600.0
    deviation: float = 35_000.0  # 2-FSK peak deviation (Hz)

    def radio_config(self) -> RadioConfig:
        """The receiver's GFSK parameters for this channel."""
        return RadioConfig(
            samp_rate=self.samp_rate, baud=self.baud, deviation=self.deviation
        )


def _build_table(channels: Iterable[Channel]) -> dict[int, Channel]:
    table: dict[int, Channel] = {}
    for ch in channels:
        if (
            isinstance(ch.number, bool)
            or not isinstance(ch.number, int)
            or ch.number < 0
        ):
            raise ValueError(f"invalid channel number {ch.number!r}")
        if ch.number in table:
            raise ValueError(f"duplicate channel number {ch.number}")
        table[ch.number] = ch
    return dict(sorted(table.items()))


# To add a channel, append one Channel(...) line here. Everything else (CLI, GUI, validation,
# tests) derives from this list.
_DEFINED = [
    # These all seem to increment 250khz, this list is here anyway to help populate other application details
    Channel(number=0, offset_hz=-15_000.0),
    Channel(number=1, offset_hz=235_000.0),
    Channel(number=2, offset_hz=485_000.0),
    Channel(number=3, offset_hz=735_000.0),
    Channel(number=4, offset_hz=985_000.0),
    Channel(number=5, offset_hz=1_235_000.0),
    Channel(number=6, offset_hz=1_485_000.0),
    Channel(number=7, offset_hz=1_735_000.0),
]

CHANNELS: dict[int, Channel] = _build_table(_DEFINED)
CHANNEL_NUMBERS: tuple[int, ...] = tuple(CHANNELS)
NUM_CHANNELS = len(CHANNELS)  # kept for convenience; numbers are CHANNEL_NUMBERS


def channel_choices(table: Mapping[int, Channel] = CHANNELS) -> list[int]:
    """Valid channel numbers, ascending."""
    return sorted(table)


def get_channel(number: int, table: Mapping[int, Channel] = CHANNELS) -> Channel:
    """Look up a channel; raises ValueError if it is not defined in `table`."""
    if isinstance(number, bool) or not isinstance(number, int) or number not in table:
        raise ValueError(
            f"channel must be one of {channel_choices(table)}, got {number!r}"
        )
    return table[number]


def channel_params(
    number: int, table: Mapping[int, Channel] = CHANNELS
) -> tuple[RadioConfig, float]:
    """(GFSK RadioConfig, offset_hz) the receiver should use for a channel."""
    ch = get_channel(number, table)
    return ch.radio_config(), ch.offset_hz
