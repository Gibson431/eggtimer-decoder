from eggtimer_decoder import contracts
from eggtimer_decoder.contracts import (
    FrameEvent,
    NmeaEvent,
    PositionEvent,
    Receiver,
    ReceiverState,
    SampleSource,
    StatusEvent,
)


def test_contracts_import_and_construct():
    for name in contracts.__all__:
        assert hasattr(contracts, name)
    ev = StatusEvent(ReceiverState.RUNNING, "ok")
    assert ev.state is ReceiverState.RUNNING
    assert FrameEvent(b"x", 0.0).payload == b"x"
    assert NmeaEvent("$x", 0.0).sentence == "$x"
    assert "position" in PositionEvent.__dataclass_fields__
    assert SampleSource and Receiver
