"""Contracts between the receiver core, its sample sources and its consumers.

Contracts only: no behavior lives here. Other parts import these names and
implement or consume them.

Events
------
The core emits ``Event`` values (a union of frozen dataclasses) to subscribers.
Consumers should dispatch on type and ignore event types they do not know, so
new event types can be added without breaking them.

Threading and delivery
----------------------
* Events are delivered on a thread owned by the receiver, never on the
  caller's thread, and in the order they were produced. Subscriber callbacks
  must return quickly and must not call ``stop()`` synchronously from within
  a callback (it may deadlock); hand off to another thread instead.
* A subscriber that raises is isolated: the exception is caught and logged,
  and other subscribers still get the event. It never stops reception.
* All ``Receiver`` methods are thread-safe and may be called from any thread.

Live changes
------------
* ``set_frequency`` and ``set_channel`` apply while running, without reopening
  the device. Decoding may glitch briefly; frames in flight may be lost.
* ``set_device`` restarts reception: the old source is closed and a new one is
  opened. Status moves through ``RESTARTING`` -> ``RUNNING`` (or ``ERROR``).
* When not running, all setters just record the value for the next ``start``.
* Invalid values raise ``ValueError`` from the setter and change nothing.
* Every accepted change is reported with a ``StatusEvent`` carrying the new
  ``ReceiverState`` and a message.

Shutdown
--------
* ``start()`` on a running receiver is a no-op. A stopped receiver may be
  started again.
* ``stop()`` is idempotent and blocks until reception has ended and the source
  is closed. After it returns no further events are delivered (a final
  ``StatusEvent`` with state ``STOPPED`` is delivered before it returns).
* A source failure is reported as ``StatusEvent`` with state ``ERROR`` and the
  receiver stops itself; it does not raise into the caller.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

from .packet import Position

__all__ = [
    "Event",
    "FrameEvent",
    "NmeaEvent",
    "Position",
    "PositionEvent",
    "Receiver",
    "ReceiverState",
    "SampleSource",
    "SignalEvent",
    "StatusEvent",
    "Subscriber",
    "Unsubscribe",
]


class ReceiverState(enum.Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    RESTARTING = "restarting"  # device change in progress
    ERROR = "error"  # failed; receiver has stopped itself


@dataclass(frozen=True)
class FrameEvent:
    """A CRC-valid radio frame; ``payload`` is the frame body (NMEA fragment)."""

    payload: bytes
    timestamp: float  # time.time() seconds


@dataclass(frozen=True)
class NmeaEvent:
    """A complete NMEA sentence reassembled from frames (no trailing newline)."""

    sentence: str
    timestamp: float


@dataclass(frozen=True)
class PositionEvent:
    """A position from a GGA sentence with a fix."""

    position: Position
    timestamp: float


@dataclass(frozen=True)
class StatusEvent:
    """Lifecycle and health. ``message`` is human readable."""

    state: ReceiverState
    message: str = ""
    timestamp: float = 0.0


@dataclass(frozen=True)
class SignalEvent:
    """Periodic signal quality. ``strength_db`` is power in dB relative to full
    scale, ``carrier_offset_hz`` is measured relative to the channel centre and
    ``present`` says whether a signal is detected above the noise floor."""

    strength_db: float
    carrier_offset_hz: float
    present: bool
    timestamp: float = 0.0


Event = FrameEvent | NmeaEvent | PositionEvent | StatusEvent | SignalEvent
Subscriber = Callable[[Event], None]
Unsubscribe = Callable[[], None]


@runtime_checkable
class SampleSource(Protocol):
    """A stream of complex baseband IQ samples from a device or simulation."""

    @property
    def sample_rate(self) -> float:
        """Samples per second of the stream returned by ``read``."""
        ...

    @property
    def center_freq(self) -> float:
        """Current tuned centre frequency in Hz."""
        ...

    def tune(self, center_freq: float) -> None:
        """Retune while open, without reopening. Raises ValueError if out of range."""
        ...

    def read(self, num_samples: int) -> np.ndarray:
        """Block for up to ``num_samples`` complex64 samples.

        Returns a possibly shorter array; an empty array means the source has
        ended (e.g. a finite simulation). Raises on device failure. Called from
        a single thread; ``tune`` and ``close`` may be called from another, and
        ``close`` must unblock a pending ``read``.
        """
        ...

    def close(self) -> None:
        """Release the device. Idempotent; ``read`` after close returns empty."""
        ...


@runtime_checkable
class Receiver(Protocol):
    """Controller for a running decode pipeline. See module docs for semantics."""

    @property
    def state(self) -> ReceiverState: ...

    def start(self) -> None:
        """Begin reception and return promptly. No-op if already running."""
        ...

    def stop(self) -> None:
        """Stop and close the source. Idempotent; no events after it returns."""
        ...

    def set_frequency(self, center_freq: float) -> None:
        """Change centre frequency in Hz. Applies live."""
        ...

    def set_channel(self, channel: int) -> None:
        """Select channel (0 to 8). Applies live."""
        ...

    def set_device(self, device: str | None) -> None:
        """Select the device (None = default). Restarts reception if running."""
        ...

    def subscribe(self, callback: Subscriber) -> Unsubscribe:
        """Register ``callback`` for all events; returns a function to remove it."""
        ...
