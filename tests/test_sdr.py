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


class FakeMetaRange:
    """Mimics gr-osmosdr meta_range_t: values() is broken, step() is a bogus summary."""

    def __init__(self, pp, start, stop, step):
        self._pp, self._t = pp, (start, stop, step)

    def values(self):
        raise TypeError("Unregistered type : std::vector<double>")

    def to_pp_string(self):
        return self._pp

    def start(self):
        return self._t[0]

    def stop(self):
        return self._t[1]

    def step(self):
        return self._t[2]


def test_osmo_range_parsing_matches_rtl_sdr_blog_v4():
    from eggtimer_decoder import sdr

    freq = FakeMetaRange("(0, 1.766e+09)", 0.0, 1766e6, 0.0)
    rates = FakeMetaRange(
        "(250000)\n(1e+06)\n(1.024e+06)\n(1.8e+06)\n(1.92e+06)\n(2e+06)\n(2.048e+06)\n(2.4e+06)\n(2.56e+06)\n",
        250e3, 2.56e6, 24e3,
    )
    assert sdr._range_bounds(freq) == (0.0, 1766e6)
    got = sdr._rates(rates)
    assert got == (250e3, 1e6, 1.024e6, 1.8e6, 1.92e6, 2e6, 2.048e6, 2.4e6, 2.56e6)  # not 250k + n*24k
    # no parsable text -> falls back to start/stop/step
    assert sdr._rates(FakeMetaRange("", 1.0, 3.0, 1.0)) == (1.0, 2.0, 3.0)


def test_osmo_enumerate_with_stand_in_library(monkeypatch):
    import sys
    import types

    from eggtimer_decoder import sdr

    made = []

    class Src:
        def __init__(self, args):
            if "rtl=0" not in args:
                raise RuntimeError("Wrong rtlsdr device index given.")
            made.append(args)

        def get_freq_range(self):
            return FakeMetaRange("(0, 1.766e+09)", 0.0, 1766e6, 0.0)

        def get_sample_rates(self):
            return FakeMetaRange("(250000)\n(1.92e+06)\n", 250e3, 1.92e6, 1.67e6)

    mod = types.SimpleNamespace(source=lambda args: Src(args))
    monkeypatch.setitem(sys.modules, "osmosdr", mod)
    devs = discover()
    assert [d.id for d in devs] == ["rtl=0"]
    assert devs[0].freq_range == (0.0, 1766e6) and devs[0].sample_rates == (250e3, 1.92e6)
    monkeypatch.setitem(sys.modules, "osmosdr", types.SimpleNamespace(source=lambda args: Src("rtl=9")))
    assert discover() == []


def test_pick_rate_integer_multiple_of_channel_rate():
    # RTL-SDR Blog V4 rates: 960 kHz is not offered, 1 MHz is nearest but not a multiple of 192 kHz
    v4 = DeviceInfo("rtl=0", "rtl:0", "rtl=0", (0, 1766e6),
                    (250e3, 1e6, 1.024e6, 1.8e6, 1.92e6, 2e6, 2.048e6, 2.4e6, 2.56e6))
    assert pick_sample_rate(v4, 960e3) == 1e6
    assert pick_sample_rate(v4, 960e3, multiple_of=192e3) == 1.92e6
    assert pick_sample_rate(info(), 1e6, multiple_of=192e3) == 960e3  # fake rates include 5x192k
    assert pick_sample_rate(info(), 1e6, multiple_of=100e3) == 1.024e6  # no multiple: plain nearest
    s = open_source(None, 921e6, 960e3, backend=FakeBackend(1), rate_multiple_of=192e3)
    assert s.sample_rate == 960e3
