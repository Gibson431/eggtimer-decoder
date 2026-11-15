import threading
import time

import numpy as np
import pytest

from eggtimer_decoder.contracts import (
    FrameEvent,
    NmeaEvent,
    PositionEvent,
    Receiver,
    ReceiverState,
    SignalEvent,
    StatusEvent,
)
from eggtimer_decoder.receiver import LiveReceiver, default_source_factory
from eggtimer_decoder.settings import Settings
from eggtimer_decoder.sim_source import SimSource


class PacedSim(SimSource):
    """Unpaced simulation with a small sleep per read: about 10x real time, cheap on CPU."""

    def __init__(self, *a, **kw):
        super().__init__(*a, realtime=False, **kw)

    def read(self, n):
        out = super().read(n)
        time.sleep(0.004)
        return out


def sim_factory(s):
    return PacedSim(channel=s.channel, center_freq=s.center_freq_hz)


class Collector:
    def __init__(self):
        self.events = []
        self.threads = set()
        self._lock = threading.Lock()

    def __call__(self, e):
        with self._lock:
            self.events.append(e)
            self.threads.add(threading.current_thread())

    def of(self, kind):
        with self._lock:
            return [e for e in self.events if isinstance(e, kind)]

    def states(self):
        return [e.state for e in self.of(StatusEvent)]

    def wait(self, pred, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            time.sleep(0.01)
        return False

    def wait_frames(self, n_more, timeout=10.0):
        base = len(self.of(FrameEvent))
        return self.wait(lambda: len(self.of(FrameEvent)) >= base + n_more, timeout)


def make(factory=sim_factory, **kw):
    rx = LiveReceiver(Settings(device="sim"), factory, **kw)
    col = Collector()
    rx.subscribe(col)
    return rx, col


def receiver_threads():
    return [t for t in threading.enumerate() if t.name.startswith("eggtimer-")]


def test_is_receiver_protocol():
    assert isinstance(LiveReceiver(), Receiver)


def test_start_decodes_everything_then_stops():
    rx, col = make()
    assert rx.state is ReceiverState.STOPPED
    rx.start()
    assert col.wait(lambda: col.of(PositionEvent) and col.of(SignalEvent))
    rx.stop()
    assert rx.state is ReceiverState.STOPPED
    assert col.states()[:2] == [ReceiverState.STARTING, ReceiverState.RUNNING]
    assert col.states()[-1] is ReceiverState.STOPPED
    assert isinstance(col.events[-1], StatusEvent)
    sentences = [e.sentence for e in col.of(NmeaEvent)]
    assert any(s.startswith("$GNGGA") for s in sentences) and any(s.startswith("$GNRMC") for s in sentences)
    pos = col.of(PositionEvent)[0].position
    assert pos.lat == pytest.approx(35.347, abs=1e-3) and pos.satellites == 8
    assert all(e.timestamp > 0 for e in col.events)
    sig = col.of(SignalEvent)
    assert any(e.present for e in sig)
    assert not receiver_threads()
    # events come from a thread other than the caller's, and only one
    assert len(col.threads) == 1 and threading.current_thread() not in col.threads


def test_nothing_delivered_after_stop():
    rx, col = make()
    rx.start()
    assert col.wait_frames(2)
    rx.stop()
    n = len(col.events)
    time.sleep(0.2)
    assert len(col.events) == n
    rx.set_channel(3)  # recorded only, no event while stopped
    rx.stop()
    time.sleep(0.05)
    assert len(col.events) == n


def test_channel_change_live():
    rx, col = make()
    rx.start()
    assert col.wait_frames(3)
    rx.set_channel(4)
    assert rx.settings.channel == 4
    assert col.wait(lambda: any("channel 4" in e.message for e in col.of(StatusEvent)))
    msg = [e for e in col.of(StatusEvent) if "channel 4" in e.message]
    assert msg[0].state is ReceiverState.RUNNING
    assert col.wait_frames(5)
    rx.stop()
    assert not receiver_threads()


def test_channel_change_with_different_offset_and_rate():
    from dataclasses import replace

    from eggtimer_decoder.channels import CHANNELS

    table = dict(CHANNELS)
    table[2] = replace(CHANNELS[0], number=2, offset_hz=15_000.0, samp_rate=144_000.0)
    # a 144 kS/s channel is fine for a 192 kS/s source only if the source rate follows (D20)
    rx = LiveReceiver(Settings(device="sim"), lambda s: PacedSim(s.channel, s.center_freq_hz, table=table), table=table)
    col = Collector()
    rx.subscribe(col)
    rx.start()
    assert col.wait_frames(3)
    rx.set_channel(2)  # source rate changes 192k -> 144k: decoder is rebuilt
    assert col.wait_frames(5)
    rx.set_channel(0)
    assert col.wait_frames(5)
    rx.stop()


def test_frequency_change_live():
    rx, col = make()
    rx.start()
    assert col.wait_frames(3)
    rx.set_frequency(921_002_000.0)  # transmitter now 2 kHz further from the centre than expected
    assert rx.settings.center_freq_hz == 921_002_000.0
    assert col.wait(lambda: any("center frequency 921.002" in e.message for e in col.of(StatusEvent)))
    assert col.wait_frames(5)
    # a retune that moves the signal out of the passband stops frames; re-selecting the
    # channel re-anchors the simulated transmitter and decoding resumes
    rx.set_frequency(921_500_000.0)
    time.sleep(0.5)
    n = len(col.of(FrameEvent))
    time.sleep(0.5)
    assert len(col.of(FrameEvent)) == n
    rx.set_channel(0)
    assert col.wait_frames(3)
    rx.stop()


def test_invalid_values_rejected_without_change():
    rx, col = make()
    rx.start()
    assert col.wait_frames(2)
    for bad in (9, -1, "x", True):
        with pytest.raises(ValueError):
            rx.set_channel(bad)
    for bad in (float("nan"), -5.0, 0.0, 1e12):
        with pytest.raises(ValueError):
            rx.set_frequency(bad)
    assert rx.settings.channel == 0 and rx.settings.center_freq_hz == 921e6
    assert col.wait_frames(3)
    rx.stop()


def test_setters_record_when_stopped():
    rx, col = make()
    rx.set_channel(5)
    rx.set_frequency(920e6)
    rx.set_device("sim")
    with pytest.raises(ValueError):
        rx.set_channel(11)
    assert (rx.settings.channel, rx.settings.center_freq_hz) == (5, 920e6)
    rx.start()
    assert col.wait_frames(2)
    rx.stop()
    assert "channel 5" in col.of(StatusEvent)[0].message


def test_device_change_restarts():
    opened = []

    def factory(s):
        opened.append(s.device)
        return PacedSim(s.channel, s.center_freq_hz)

    rx, col = make(factory)
    rx.start()
    assert col.wait_frames(2)
    rx.set_device("other")
    assert col.wait(lambda: col.states().count(ReceiverState.RUNNING) == 2)
    assert col.states() == [
        ReceiverState.STARTING,
        ReceiverState.RUNNING,
        ReceiverState.RESTARTING,
        ReceiverState.RUNNING,
    ]
    assert opened == ["sim", "other"]
    assert col.wait_frames(3)
    rx.set_device("other")  # unchanged: no restart
    rx.stop()
    assert opened == ["sim", "other"]
    assert not receiver_threads()


def test_device_change_to_failing_device_is_error():
    def factory(s):
        if s.device == "bad":
            raise OSError("no such device")
        return PacedSim(s.channel, s.center_freq_hz)

    rx, col = make(factory)
    rx.start()
    assert col.wait_frames(2)
    rx.set_device("bad")
    assert col.wait(lambda: ReceiverState.ERROR in col.states())
    assert "no such device" in col.of(StatusEvent)[-1].message
    assert col.wait(lambda: not receiver_threads())
    assert rx.state is ReceiverState.ERROR
    rx.set_device("sim")  # recorded
    rx.stop()  # joins, delivers nothing more
    assert col.states()[-1] is ReceiverState.ERROR
    rx.start()  # may be started again
    assert col.wait(lambda: col.states()[-1] is ReceiverState.RUNNING)
    rx.stop()
    assert col.states()[-1] is ReceiverState.STOPPED


class FailingSource:
    sample_rate = 192_000.0
    center_freq = 921e6

    def __init__(self, fail_after=0):
        self.n = 0
        self.fail_after = fail_after
        self.closed = False

    def tune(self, f):
        pass

    def read(self, n):
        if self.n >= self.fail_after:
            raise RuntimeError("usb unplugged")
        self.n += 1
        return np.zeros(n, np.complex64)

    def close(self):
        self.closed = True


def test_open_failure_is_error_status():
    def factory(s):
        raise OSError("device busy")

    rx, col = make(factory)
    rx.start()
    assert col.wait(lambda: ReceiverState.ERROR in col.states())
    err = col.of(StatusEvent)[-1]
    assert err.state is ReceiverState.ERROR and "device busy" in err.message
    assert col.wait(lambda: not receiver_threads())
    rx.stop()
    assert not receiver_threads()


def test_read_failure_is_error_status_and_closes_source():
    src = FailingSource(fail_after=3)
    rx, col = make(lambda s: src)
    rx.start()
    assert col.wait(lambda: ReceiverState.ERROR in col.states())
    assert "usb unplugged" in col.of(StatusEvent)[-1].message
    assert col.wait(lambda: not receiver_threads())
    assert src.closed and rx.state is ReceiverState.ERROR
    rx.stop()


def test_source_that_ends_is_reported():
    class Ends(FailingSource):
        def read(self, n):
            return np.empty(0, np.complex64)

    rx, col = make(lambda s: Ends())
    rx.start()
    assert col.wait(lambda: ReceiverState.ERROR in col.states())
    rx.stop()


def test_bad_channel_for_source_rate_is_error():
    class Slow(FailingSource):
        sample_rate = 48_000.0  # below the channel's 192 kS/s

    rx, col = make(lambda s: Slow())
    rx.start()
    assert col.wait(lambda: ReceiverState.ERROR in col.states())
    rx.stop()


def test_repeated_start_stop_leaves_no_threads():
    sources = []

    def factory(s):
        src = PacedSim(s.channel, s.center_freq_hz)
        sources.append(src)
        return src

    rx, col = make(factory)
    for _ in range(5):
        rx.start()
        rx.start()  # no-op
        rx.stop()
        rx.stop()  # idempotent
        assert not receiver_threads()
    assert len(sources) <= 5 and all(s._closed.is_set() for s in sources)
    assert col.states().count(ReceiverState.STOPPED) == 5
    rx.start()
    assert col.wait_frames(2)
    rx.stop()
    assert not receiver_threads()


def test_stop_while_opening_blocks_until_closed():
    gate = threading.Event()
    srcs = []

    def factory(s):
        gate.wait(5)
        src = PacedSim(s.channel, s.center_freq_hz)
        srcs.append(src)
        return src

    rx, col = make(factory)
    rx.start()
    threading.Timer(0.2, gate.set).start()
    rx.stop()
    assert srcs and srcs[0]._closed.is_set()
    assert not receiver_threads()
    assert col.states()[-1] is ReceiverState.STOPPED


def test_subscriber_exception_isolated_and_unsubscribe():
    rx, col = make()
    good = Collector()

    def bad(e):
        raise RuntimeError("boom")

    rx.subscribe(bad)
    rx.subscribe(good)
    off = rx.subscribe(lambda e: (_ for _ in ()).throw(ValueError("x")))
    off()
    rx.start()
    assert col.wait_frames(2)
    rx.stop()
    assert len(good.events) == len(col.events) > 2
    assert col.of(FrameEvent)


def test_stop_from_subscriber_does_not_deadlock():
    rx, col = make()
    done = threading.Event()

    def cb(e):
        if isinstance(e, FrameEvent):
            rx.stop()
            done.set()

    rx.subscribe(cb)
    rx.start()
    assert done.wait(10)
    rx.stop()
    assert col.wait(lambda: not receiver_threads())


def test_signal_present_is_debounced_between_bursts():
    rx, col = make(signal_interval=0.02)
    rx.start()
    assert col.wait(lambda: len(col.of(SignalEvent)) >= 30, 15)
    rx.stop()
    sig = col.of(SignalEvent)[5:]
    assert all(e.present for e in sig)
    # a window holding only one FSK lobe reads ~35 kHz off (S6 limitation), so use the median
    assert abs(float(np.median([e.carrier_offset_hz for e in sig]))) < 500


def test_signal_absent_for_noise_only():
    class Noise(FailingSource):
        def __init__(self):
            super().__init__(fail_after=10**9)
            self.rng = np.random.default_rng(1)

        def read(self, n):
            time.sleep(0.002)
            return ((self.rng.standard_normal(n) + 1j * self.rng.standard_normal(n)) * 0.01).astype(np.complex64)

    rx, col = make(lambda s: Noise(), signal_interval=0.02)
    rx.start()
    assert col.wait(lambda: len(col.of(SignalEvent)) >= 5)
    rx.stop()
    assert not any(e.present for e in col.of(SignalEvent))
    assert not col.of(FrameEvent)


def test_default_factory_sim_and_gain_ppm_passed(monkeypatch):
    src = default_source_factory(Settings(device="sim", channel=3, center_freq_hz=922e6))
    assert isinstance(src, SimSource) and src.channel == 3 and src.center_freq == 922e6
    src.close()
    from eggtimer_decoder import sdr

    seen = {}

    def fake_open(device, center, sample_rate, ppm, **kw):
        seen.update(device=device, center=center, rate=sample_rate, ppm=ppm, **kw)
        return "src"

    monkeypatch.setattr(sdr, "open_source", fake_open)
    default_source_factory(Settings(device="", gain=12.5, ppm=3.0))
    assert seen == dict(device=None, center=921e6, rate=960_000.0, ppm=3.0, gain_db=12.5, rate_multiple_of=192_000.0)
