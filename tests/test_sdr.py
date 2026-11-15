import numpy as np
import pytest

from eggtimer_decoder.contracts import SampleSource
from eggtimer_decoder.sdr import DeviceInfo, discover, open_source, pick_sample_rate

RATES = (250e3, 960e3, 1.024e6, 2.048e6)


def info(i=0):
    return DeviceInfo(f"fake={i}", f"fake:{i}", f"fake={i}", (24e6, 1766e6), RATES)


class FakeHandle:
    def __init__(self):
        self.calls = []
        self.closed = False

    def set_sample_rate(self, r):
        self.calls.append(("rate", r))

    def set_center_freq(self, f):
        self.calls.append(("freq", f))

    def set_freq_corr(self, p):
        self.calls.append(("ppm", p))

    def set_gain(self, g):
        self.calls.append(("gain", g))

    def read(self, n):
        return np.ones(0 if self.closed else n, dtype=np.complex64)

    def close(self):
        self.closed = True


class FakeBackend:
    def __init__(self, n=2):
        self.devs = [info(i) for i in range(n)]
        self.handles = []

    def enumerate(self):
        return self.devs

    def open(self, i):
        h = FakeHandle()
        self.handles.append((i, h))
        return h


class Broken:
    def enumerate(self):
        raise RuntimeError("no lib")


def test_discover_empty_and_failure():
    assert discover(FakeBackend(0)) == []
    assert discover(Broken()) == []


def test_discover_reports_ranges():
    d = discover(FakeBackend(2))
    assert [x.id for x in d] == ["fake=0", "fake=1"]
    assert d[0].freq_range == (24e6, 1766e6) and d[0].sample_rates == RATES


def test_pick_rate_nearest_and_tolerance():
    i = info()
    assert pick_sample_rate(i, 1e6) == 1.024e6
    assert pick_sample_rate(i, 900e3) == 960e3
    with pytest.raises(ValueError):
        pick_sample_rate(i, 5e6, tolerance=0.01)
    with pytest.raises(ValueError):
        pick_sample_rate(DeviceInfo("x", "x", "x", (0, 1), ()), 1e6)


def test_open_selects_and_configures():
    be = FakeBackend(2)
    s = open_source("fake=1", 921e6, 1e6, gain_db=20, ppm=-3, backend=be)
    assert isinstance(s, SampleSource)
    assert be.handles[0][0].id == "fake=1"
    assert s.sample_rate == 1.024e6 and s.center_freq == 921e6
    calls = be.handles[0][1].calls
    assert ("gain", 20) in calls and ("ppm", -3) in calls and ("rate", 1.024e6) in calls
    assert len(s.read(100)) == 100
    s.tune(920e6)
    assert s.center_freq == 920e6
    with pytest.raises(ValueError):
        s.tune(1e3)
    assert s.center_freq == 920e6
    s.close()
    s.close()
    assert len(s.read(10)) == 0


def test_default_index_and_errors():
    be = FakeBackend(2)
    open_source(None, backend=be)
    assert be.handles[0][0].id == "fake=0"
    open_source("1", backend=be)
    assert be.handles[1][0].id == "fake=1"
    with pytest.raises(ValueError):
        open_source("nope", backend=be)
    with pytest.raises(ValueError):
        open_source(None, backend=FakeBackend(0))
    with pytest.raises(ValueError):
        open_source(None, center_freq=1e3, backend=be)
    assert len(be.handles) == 2  # bad freq rejected before opening


def test_real_backend_without_hardware_is_a_list():
    assert isinstance(discover(), list)
