"""End-to-end tests: the whole LiveReceiver and the CLI over the simulated source.

Reuses the helpers from test_receiver (paced sim, event collector). Unit-level
behaviours (error paths, threading, debounce) are covered there; these check
that every channel and a realistic live session produce correct decoded output.
"""

import json
import os
import subprocess
import sys

import pytest

from eggtimer_decoder.channels import CHANNEL_NUMBERS
from eggtimer_decoder.contracts import (
    FrameEvent,
    NmeaEvent,
    PositionEvent,
    ReceiverState,
    SignalEvent,
    StatusEvent,
)

from test_receiver import make, receiver_threads

LAT, LON = 35.347, -117.808  # simulate.nmea_payloads()


def assert_decoded(col, start=0):
    """Frames, NMEA (GGA + RMC) and a position, from event index ``start``."""
    ev = col.events[start:]
    frames = [e for e in ev if isinstance(e, FrameEvent)]
    sentences = [e.sentence for e in ev if isinstance(e, NmeaEvent)]
    positions = [e.position for e in ev if isinstance(e, PositionEvent)]
    assert frames and positions
    assert any(
        s.startswith("$GNGGA,014049.000,3520.8200,N,11748.4800,W,1,08")
        for s in sentences
    )
    assert any(s.startswith("$GNRMC") for s in sentences)
    assert all(s.startswith("$") and "*" in s for s in sentences)
    assert positions[0].lat == pytest.approx(LAT, abs=1e-3)
    assert positions[0].lon == pytest.approx(LON, abs=1e-3)
    assert positions[0].satellites == 8


@pytest.mark.parametrize("channel", CHANNEL_NUMBERS)
def test_every_channel_end_to_end(channel):
    rx, col = make()
    rx.set_channel(channel)  # recorded while stopped, used by the first open
    rx.start()
    try:
        assert col.wait(
            lambda: col.of(PositionEvent)
            and any(e.present for e in col.of(SignalEvent))
        )
    finally:
        rx.stop()
    assert_decoded(col)
    assert col.states()[:2] == [ReceiverState.STARTING, ReceiverState.RUNNING]
    assert ReceiverState.ERROR not in col.states()
    assert not receiver_threads()


def test_live_reconfigure_channel_and_frequency():
    rx, col = make()
    rx.start()
    try:
        assert col.wait(lambda: col.of(PositionEvent))

        rx.set_channel(2)  # channel change while running
        assert col.wait(
            lambda: any("channel 2" in e.message for e in col.of(StatusEvent))
        )
        assert col.wait_frames(4)

        # retune so the transmitter is no longer where the channel expects it
        rx.set_frequency(920_800_000.0)
        assert rx.settings.center_freq_hz == 920_800_000.0
        assert col.wait(
            lambda: any("920.800000" in e.message for e in col.of(StatusEvent))
        )

        # picking a channel re-anchors the simulated transmitter on the new centre: decoding resumes
        mark = len(col.events)
        rx.set_channel(2)
        assert col.wait(
            lambda: any(isinstance(e, PositionEvent) for e in col.events[mark:])
        )
        assert col.wait_frames(3)
        assert_decoded(col, mark)
    finally:
        rx.stop()
    assert rx.state is ReceiverState.STOPPED
    assert ReceiverState.ERROR not in col.states()
    assert not receiver_threads()


def test_cli_subprocess_sim_run(tmp_path):
    """The real entry point in a fresh interpreter, with the real (real-time) SimSource."""
    log = tmp_path / "events.jsonl"
    env = {k: v for k, v in os.environ.items() if not k.startswith("EGGTIMER_")}
    env["EGGTIMER_CONFIG"] = str(tmp_path / "settings.json")
    code = "import sys; from eggtimer_decoder.cli import main; sys.exit(main())"
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            "run",
            "--device",
            "sim",
            "--channel",
            "1",
            "--center-freq",
            "921M",
            "--duration",
            "4",
            "--log-file",
            str(log),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "$GNGGA,014049.000,3520.8200,N,11748.4800,W,1,08" in proc.stdout
    assert "=> Position(" in proc.stdout
    kinds = {json.loads(line)["type"] for line in log.read_text().splitlines()}
    assert {"frame", "nmea", "position", "status"} <= kinds
