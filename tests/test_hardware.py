"""Opt-in real-SDR test (RTL-SDR at 921 MHz centre, channel 0).

Skipped unless EGGTIMER_HARDWARE=1. Run: ``EGGTIMER_HARDWARE=1 pixi run pytest -m hardware -s``.
Does not require a frame (the transmitter may be off); see docs/HARDWARE_TEST.md for the manual procedure.
"""

import os
import time

import pytest

from eggtimer_decoder import sdr
from eggtimer_decoder.contracts import ReceiverState, SignalEvent, StatusEvent
from eggtimer_decoder.receiver import LiveReceiver
from eggtimer_decoder.settings import Settings

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(
        os.environ.get("EGGTIMER_HARDWARE") != "1",
        reason="real-SDR test: set EGGTIMER_HARDWARE=1 with an RTL-SDR attached to run it",
    ),
]


def test_discovery_finds_an_rtl_sdr():
    devices = sdr.discover()
    assert (
        devices
    ), "no SDR found (is the RTL-SDR attached and not in use by another program?)"
    d = devices[0]
    lo, hi = d.freq_range
    assert lo <= 921e6 <= hi
    assert d.sample_rates


def test_receiver_runs_on_channel_0_at_921mhz():
    rx = LiveReceiver(Settings(device="", center_freq_hz=921e6, channel=0))
    events = []
    rx.subscribe(events.append)
    rx.start()
    try:
        time.sleep(6)
        assert rx.state is ReceiverState.RUNNING, [
            e for e in events if isinstance(e, StatusEvent)
        ]
    finally:
        rx.stop()
    states = [e.state for e in events if isinstance(e, StatusEvent)]
    assert ReceiverState.ERROR not in states, [
        e.message for e in events if isinstance(e, StatusEvent)
    ]
    signals = [e for e in events if isinstance(e, SignalEvent)]
    assert signals, "no signal events reported"
    print(
        f"{len(signals)} signal events; present={any(e.present for e in signals)}; "
        f"max strength={max(e.strength_db for e in signals):.1f} dBFS"
    )
