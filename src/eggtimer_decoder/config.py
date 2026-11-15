"""Radio parameters, measured from a live capture of an Eggfinder TX."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RadioConfig:
    samp_rate: float = 192_000.0  # complex baseband sample rate (Hz)
    baud: float = 9_600.0
    deviation: float = 35_000.0  # 2-FSK peak deviation (Hz), measured ~±34.5 kHz

    @property
    def sps(self) -> float:
        return self.samp_rate / self.baud
