import io
import json
from contextlib import redirect_stdout

from eggtimer_decoder.contracts import (
    FrameEvent,
    NmeaEvent,
    PositionEvent,
    ReceiverState,
    SignalEvent,
    StatusEvent,
)
from eggtimer_decoder.packet import Position, parse_gga
from eggtimer_decoder.simulate import SAMPLE_NMEA
from eggtimer_decoder.sinks import ConsoleSink, JsonLogSink, LastPositionStore

GGA = [s for s in SAMPLE_NMEA.splitlines() if parse_gga(s.strip())][0].strip()
POS = parse_gga(GGA)
OTHER = "$GNRMC,014048.084,V,,,,,0.00,0.00,151126,,,N,V*00"


class Unknown:
    pass


def events():
    return [
        StatusEvent(ReceiverState.RUNNING, "go", 1.0),
        FrameEvent(b"\x01ab\r\n", 2.0),
        NmeaEvent(GGA, 3.0),
        PositionEvent(POS, 3.0),
        NmeaEvent(OTHER, 4.0),
        SignalEvent(-30.5, 120.0, True, 5.0),
        Unknown(),
    ]


def test_console_matches_legacy_prints():
    buf = io.StringIO()
    sink = ConsoleSink(buf)
    for e in events():
        sink(e)
    f = b"\x01ab\r\n"
    # Legacy: print(f"frame len={len(f):2d} {f!r}"); print(line, f"=> {pos}" if pos else "")
    legacy = io.StringIO()
    with redirect_stdout(legacy):
        print(f"frame len={len(f):2d} {f!r}")
        print(GGA, f"=> {POS}")
        print(OTHER)
    assert buf.getvalue() == "[running] go\n" + legacy.getvalue()


def test_json_log_golden(tmp_path):
    p = tmp_path / "log.jsonl"
    sink = JsonLogSink(p)
    for e in events():
        sink(e)
    sink.close()
    sink(StatusEvent(ReceiverState.STOPPED))  # after close: ignored
    compact = {"separators": (",", ":")}
    assert p.read_text().splitlines() == [
        '{"type":"status","timestamp":1.0,"state":"running","message":"go"}',
        '{"type":"frame","timestamp":2.0,"payload":"0161620d0a"}',
        json.dumps({"type": "nmea", "timestamp": 3.0, "sentence": GGA}, **compact),
        json.dumps(
            {
                "type": "position",
                "timestamp": 3.0,
                "lat": POS.lat,
                "lon": POS.lon,
                "alt_m": POS.alt_m,
                "satellites": POS.satellites,
            },
            **compact,
        ),
        json.dumps({"type": "nmea", "timestamp": 4.0, "sentence": OTHER}, **compact),
        '{"type":"signal","timestamp":5.0,"strength_db":-30.5,"carrier_offset_hz":120.0,"present":true}',
    ]


def test_last_position_store(tmp_path):
    p = tmp_path / "sub" / "pos.json"
    store = LastPositionStore(p)
    assert store.position is None
    for e in events():
        store(e)
    assert store.position == POS and store.timestamp == 3.0
    newer = Position(1.5, -2.5, 10.0, 7)
    store(PositionEvent(newer, 9.0))
    assert json.loads(p.read_text()) == {
        "lat": 1.5,
        "lon": -2.5,
        "alt_m": 10.0,
        "satellites": 7,
        "timestamp": 9.0,
    }
    assert [x.name for x in p.parent.iterdir()] == ["pos.json"]
    again = LastPositionStore(p)
    assert again.position == newer and again.timestamp == 9.0


def test_last_position_store_corrupt_file(tmp_path):
    p = tmp_path / "pos.json"
    p.write_text("{nope")
    assert LastPositionStore(p).position is None
    assert LastPositionStore().position is None
