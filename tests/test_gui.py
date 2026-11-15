import os
import time

import pytest

from eggtimer_decoder.contracts import (
    FrameEvent,
    NmeaEvent,
    PositionEvent,
    ReceiverState,
    SignalEvent,
    StatusEvent,
)
from eggtimer_decoder.gui_model import (
    Controller,
    FormValues,
    StatusModel,
    device_choices,
    event_to_row,
    parse_form,
    settings_to_form,
)
from eggtimer_decoder.packet import Position
from eggtimer_decoder.receiver import LiveReceiver
from eggtimer_decoder.settings import Settings, load_settings_file
from eggtimer_decoder.sdr import DeviceInfo


# -- pure view-model ---------------------------------------------------------
def test_form_roundtrip():
    s = Settings(device="sim", center_freq_hz=920.5e6, channel=2, gain=22.5, ppm=-4)
    assert parse_form(settings_to_form(s), Settings()).settings == s


def test_form_defaults_and_auto_gain():
    r = parse_form(FormValues(), Settings(gain=10))
    assert r.settings == Settings()  # 921 MHz, ch 0, auto gain


def test_form_keeps_non_form_fields():
    base = Settings(log_file="x.jsonl", verbose=True)
    assert parse_form(FormValues(channel="2"), base).settings.log_file == "x.jsonl"


@pytest.mark.parametrize(
    "form, field",
    [
        (FormValues(freq_mhz="abc"), "freq_mhz"),
        (FormValues(freq_mhz=""), "freq_mhz"),
        (FormValues(freq_mhz="99999"), "freq_mhz"),  # 99.999 GHz out of range
        (FormValues(channel="9"), "channel"),
        (FormValues(channel="x"), "channel"),
        (FormValues(gain="loud"), "gain"),
        (FormValues(gain="500"), "gain"),
        (FormValues(ppm="5000"), "ppm"),
        (FormValues(ppm="nan"), "ppm"),
    ],
)
def test_form_errors_are_per_field(form, field):
    r = parse_form(form, Settings())
    assert r.settings is None and field in r.errors and r.errors[field]


def test_form_reports_all_errors_at_once():
    r = parse_form(FormValues(freq_mhz="x", channel="12", ppm="q"), Settings())
    assert set(r.errors) == {"freq_mhz", "channel", "ppm"}


def test_form_freq_suffix_and_device_labels():
    infos = [DeviceInfo("rtl=0", "RTL", "rtl=0", (1e6, 2e9), (1e6,))]
    ch = device_choices(infos)
    assert (
        parse_form(
            FormValues(freq_mhz="920.99M"), Settings(), ch
        ).settings.center_freq_hz
        == 920.99e6
    )
    assert (
        parse_form(FormValues(device=ch[-1][0]), Settings(), ch).settings.device
        == "rtl=0"
    )
    assert (
        parse_form(FormValues(device=ch[1][0]), Settings(), ch).settings.device == "sim"
    )
    assert (
        parse_form(FormValues(device="custom=1"), Settings(), ch).settings.device
        == "custom=1"
    )
    assert settings_to_form(Settings(device="rtl=0"), ch).device == ch[-1][0]


def test_event_rows():
    f = event_to_row(FrameEvent(b"abc", 1.0))
    assert f.kind == "frame" and "abc" in f.text
    gga = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
    n = event_to_row(NmeaEvent(gga, 1.0))
    assert n.kind == "nmea" and "Position(" in n.text
    assert event_to_row(NmeaEvent("$GPRMC,junk", 1.0)).text == "$GPRMC,junk"
    s = event_to_row(StatusEvent(ReceiverState.ERROR, "boom", 1.0))
    assert s.text == "[error] boom"
    assert event_to_row(SignalEvent(-30.0, 0.0, True)) is None
    assert event_to_row(PositionEvent(Position(1, 2, 3, 4), 1.0)) is None


def test_status_model():
    m = StatusModel()
    m.apply(StatusEvent(ReceiverState.RUNNING, "receiving"))
    assert m.state_text() == "RUNNING - receiving" and m.offset_text() == "n/a"
    for off in (-10e3, -45e3, -10.2e3):  # one outlier (D27)
        m.apply(SignalEvent(-30.0, off, True))
    assert "-10.2 kHz" in m.offset_text() and "noisy" in m.offset_text()
    assert m.signal_text() == "-30.0 dBFS, signal present"
    m.apply(PositionEvent(Position(10.0, 20.0, 5.0, 7), 0.0))
    assert "10.000000, 20.000000" in m.position_text()
    m.apply(FrameEvent(b"x", 0))
    m.apply(NmeaEvent("$x", 0))
    assert m.counts_text() == "1 frames, 1 sentences"
    m.apply(StatusEvent(ReceiverState.STOPPED, "stopped"))
    assert m.signal_text() == "no signal data" and m.offset_text() == "n/a"


# -- controller with a fake receiver ------------------------------------------
class FakeReceiver:
    def __init__(self, settings):
        self.settings = settings
        self.calls = []
        self.state = ReceiverState.STOPPED
        self.subs = []
        self.reject = False

    def subscribe(self, cb):
        self.subs.append(cb)
        return lambda: self.subs.remove(cb)

    def start(self):
        self.calls.append("start")
        self.state = ReceiverState.RUNNING

    def stop(self):
        self.calls.append("stop")
        self.state = ReceiverState.STOPPED

    def set_frequency(self, f):
        self.calls.append(("freq", f))

    def set_channel(self, c):
        if self.reject:
            raise ValueError("nope")
        self.calls.append(("channel", c))

    def set_device(self, d):
        self.calls.append(("device", d))


def make_controller(tmp_path, settings=None):
    made = []

    def factory(s):
        r = FakeReceiver(s)
        made.append(r)
        return r

    c = Controller(settings or Settings(), factory, tmp_path / "s.json")
    return c, made


def test_controller_live_changes_use_setters_and_persist(tmp_path):
    c, made = make_controller(tmp_path)
    c.start()
    new = Settings(center_freq_hz=920e6, channel=1, device="sim")
    c.apply(new)
    assert made[0].calls == [
        "start",
        ("freq", 920e6),
        ("channel", 1),
        ("device", "sim"),
    ]
    assert len(made) == 1  # no restart
    assert load_settings_file(tmp_path / "s.json")[0]["channel"] == 1


def test_controller_gain_ppm_restarts_receiver(tmp_path):
    c, made = make_controller(tmp_path)
    c.start()
    c.apply(Settings(gain=20.0, ppm=3.0))
    assert len(made) == 2 and made[0].calls == ["start", "stop"]
    assert made[1].settings.gain == 20.0 and made[1].calls == ["start"]
    assert made[0].subs == [] and len(made[1].subs) == 1
    made[1].subs[0](FrameEvent(b"a", 0))  # events from the new receiver reach the queue
    assert len(c.drain()) == 1


def test_controller_gain_while_stopped_does_not_start(tmp_path):
    c, made = make_controller(tmp_path)
    c.apply(Settings(gain=5.0))
    assert made[-1].calls == [] and made[-1].settings.gain == 5.0


def test_controller_rejected_change_not_saved(tmp_path):
    c, made = make_controller(tmp_path)
    c.start()
    made[0].reject = True
    with pytest.raises(ValueError):
        c.apply(Settings(channel=1))
    assert not (tmp_path / "s.json").exists()


# -- real LiveReceiver + sim, no display ---------------------------------------
def test_controller_with_live_sim_receiver(tmp_path):
    c = Controller(Settings(device="sim"), LiveReceiver, tmp_path / "s.json")
    try:
        c.start()
        deadline = time.time() + 20
        seen = []
        while time.time() < deadline and not any(
            isinstance(e, FrameEvent) for e in seen
        ):
            seen += c.drain()
            time.sleep(0.05)
        assert any(
            isinstance(e, StatusEvent) and e.state == ReceiverState.RUNNING
            for e in seen
        )
        assert any(isinstance(e, FrameEvent) for e in seen)
        c.apply(Settings(device="sim", channel=2))  # live channel change
        c.apply(Settings(device="sim", channel=2, gain=10.0))  # restart path
        assert c.running
    finally:
        c.close()
    assert c.receiver.state == ReceiverState.STOPPED


# -- widget smoke tests (Qt, offscreen: no display needed) ---------------------
@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_gui_main_autoclose_sim(qapp, tmp_path, monkeypatch):
    from eggtimer_decoder import gui
    from eggtimer_decoder.settings import save_settings

    cfg = tmp_path / "settings.json"
    save_settings(Settings(device="sim", channel=1), cfg)
    monkeypatch.setenv("EGGTIMER_CONFIG", str(cfg))
    for k in list(os.environ):
        if k.startswith("EGGTIMER_") and k not in ("EGGTIMER_CONFIG",):
            monkeypatch.delenv(k)
    assert gui.main(autoclose_ms=1500) == 0


def test_gui_apply_validates_and_persists(qapp, tmp_path):
    from eggtimer_decoder.gui import App

    cfg = tmp_path / "s.json"
    app = App(
        Settings(device="sim"),
        receiver_factory=FakeReceiver,
        path=cfg,
        discover=lambda: [],
        autostart=True,
    )
    try:
        app.freq_edit.setText("bogus")
        app.apply()
        assert (
            "frequency" in app.error_label.text().lower()
            or "parse" in app.error_label.text().lower()
        )
        assert not cfg.exists()
        app.freq_edit.setText("920.5")
        app.channel_box.setCurrentText("1")
        app.apply()
        assert app.error_label.text() == ""
        assert ("channel", 1) in app.controller.receiver.calls
        assert load_settings_file(cfg)[0]["center_freq_hz"] == 920.5e6
        # events from "another thread" show up after a poll
        app.controller.receiver.subs[0](FrameEvent(b"hi", 1.0))
        deadline = time.time() + 2
        while time.time() < deadline and not app.tree.topLevelItemCount():
            qapp.processEvents()
            time.sleep(0.02)
        assert app.tree.topLevelItemCount() >= 1
    finally:
        app.close_app()


def test_gui_kind_filter(qapp, tmp_path):
    from eggtimer_decoder.gui import App

    app = App(
        Settings(device="sim"),
        receiver_factory=FakeReceiver,
        path=tmp_path / "s.json",
        discover=lambda: [],
        autostart=True,
    )
    try:
        app._handle(FrameEvent(b"hi", 1.0))
        app._handle(StatusEvent(ReceiverState.RUNNING, "x"))
        kinds = [
            app.tree.topLevelItem(i).text(1)
            for i in range(app.tree.topLevelItemCount())
        ]
        assert "frame" in kinds and "status" in kinds

        app.set_kind_filter("frame")
        assert app.tree.headerItem().text(1) == "Kind: frame"
        hidden = {
            app.tree.topLevelItem(i).text(1): app.tree.topLevelItem(i).isHidden()
            for i in range(app.tree.topLevelItemCount())
        }
        assert hidden["frame"] is False and hidden["status"] is True

        app._handle(StatusEvent(ReceiverState.RUNNING, "y"))  # new rows obey the filter
        assert app.tree.topLevelItem(app.tree.topLevelItemCount() - 1).isHidden()

        app.set_kind_filter(None)
        assert not any(
            app.tree.topLevelItem(i).isHidden()
            for i in range(app.tree.topLevelItemCount())
        )
        assert app.tree.headerItem().text(1) == "Kind"
    finally:
        app.close_app()
