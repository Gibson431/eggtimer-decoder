import numpy as np
import pytest

from eggtimer_decoder.config import RadioConfig
from eggtimer_decoder.contracts import SignalEvent
from eggtimer_decoder.signal_metrics import SignalMetrics
from eggtimer_decoder.simulate import modulate

CFG = RadioConfig()
FS = CFG.samp_rate


def _noise(n, power_db, seed=0):
    rng = np.random.default_rng(seed)
    s = 10 ** (power_db / 20) / np.sqrt(2)
    return s * (rng.standard_normal(n) + 1j * rng.standard_normal(n))


def _tone(n, offset, amp):
    return amp * np.exp(2j * np.pi * offset * np.arange(n) / FS)


def _gfsk(n_bits, offset, amp, seed=1):
    bits = np.random.default_rng(seed).integers(0, 2, n_bits, dtype=np.uint8)
    sig = modulate(bits, CFG)
    return amp * sig * np.exp(2j * np.pi * offset * np.arange(len(sig)) / FS)


def _run(sig, chunk=1000, **kw):
    m = SignalMetrics(FS, **kw)
    for i in range(0, len(sig), chunk):
        m.update(sig[i : i + chunk])
    return m.snapshot()


@pytest.mark.parametrize("amp_db", [-10, -30, -50])
@pytest.mark.parametrize("offset", [0.0, 12_345.0, -40_000.0])
def test_tone(amp_db, offset):
    amp = 10 ** (amp_db / 20)
    sig = _tone(8000, offset, amp) + _noise(8000, amp_db - 25)
    r = _run(sig)
    assert r.present
    assert r.strength_db == pytest.approx(amp_db, abs=1.0)
    assert r.carrier_offset_hz == pytest.approx(offset, abs=100)


@pytest.mark.parametrize("offset", [0.0, 5_000.0, -8_000.0])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_gfsk(offset, seed):
    amp = 10 ** (-20 / 20)
    sig = _gfsk(600, offset, amp, seed) + _noise(600 * 20, -20 - 15, seed)
    r = _run(sig)
    assert r.present
    assert r.strength_db == pytest.approx(-20, abs=1.0)
    assert r.carrier_offset_hz == pytest.approx(offset, abs=300)


def test_offset_relative_to_channel():
    sig = _gfsk(600, 25_000.0, 0.1) + _noise(12000, -35)
    r = _run(sig, channel_offset_hz=20_000.0)
    assert r.carrier_offset_hz == pytest.approx(5_000.0, abs=300)


@pytest.mark.parametrize("seed", range(10))
def test_noise_only_absent(seed):
    r = _run(_noise(20000, -30, seed))
    assert not r.present
    assert r.strength_db == pytest.approx(-30, abs=1.0)
    assert r.carrier_offset_hz == 0.0


def test_hysteresis_and_burst():
    m = SignalMetrics(FS)
    amp = 0.1
    for seed in range(5):
        m.update(_noise(4096, -45, seed))
    assert not m.snapshot().present
    m.update(_tone(4096, 3000, amp) + _noise(4096, -45))
    assert m.snapshot().present
    # marginal signal (~8.5 dB peak over floor): stays on once on, stays off if off
    marginal = _tone(4096, 3000, 10 ** (-45 / 20) * 0.2) + _noise(4096, -45, 9)
    m.update(marginal)
    assert m.snapshot().present
    m.reset()
    for seed in range(5):
        m.update(_noise(4096, -45, seed))
    m.update(marginal)
    assert not m.snapshot().present
    m.update(_noise(4096, -45, 11))
    assert not m.snapshot().present


def test_short_input_and_event():
    m = SignalMetrics(FS)
    assert not m.update(np.zeros(10, np.complex64)).present
    ev = _run(_tone(5000, 0, 0.1) + _noise(5000, -45)).to_event()
    assert isinstance(ev, SignalEvent) and ev.present and ev.timestamp == 0.0
