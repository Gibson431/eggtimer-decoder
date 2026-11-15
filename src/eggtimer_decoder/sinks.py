"""Consumers of receiver events. Each sink is a ``Subscriber`` (Event -> None).

Unknown event types are ignored, so new events never break a sink.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import IO

from .contracts import (
    Event,
    FrameEvent,
    NmeaEvent,
    PositionEvent,
    SignalEvent,
    StatusEvent,
)
from .packet import Position, parse_gga

__all__ = ["ConsoleSink", "JsonLogSink", "LastPositionStore", "event_to_dict"]


class ConsoleSink:
    """Human-readable lines, same format as the original decode script.

    Frames print as ``frame len=NN b'...'``; NMEA sentences print as the
    sentence, followed by `` => Position(...)`` when it is a GGA with a fix.
    PositionEvent and SignalEvent print nothing (the position is derived from
    the sentence); status prints ``[state] message``.
    """

    def __init__(self, stream: IO[str] | None = None) -> None:
        self._stream = stream

    def __call__(self, event: Event) -> None:
        if isinstance(event, FrameEvent):
            line = f"frame len={len(event.payload):2d} {event.payload!r}"
        elif isinstance(event, NmeaEvent):
            pos = parse_gga(event.sentence)
            line = f"{event.sentence} => {pos}" if pos else event.sentence
        elif isinstance(event, StatusEvent):
            line = f"[{event.state.value}]" + (
                f" {event.message}" if event.message else ""
            )
        else:
            return
        print(line, file=self._stream or sys.stdout, flush=True)


def event_to_dict(event: Event) -> dict | None:
    """JSON-ready dict with a ``type`` field, or None for unknown events."""
    if isinstance(event, FrameEvent):
        return {
            "type": "frame",
            "timestamp": event.timestamp,
            "payload": event.payload.hex(),
        }
    if isinstance(event, NmeaEvent):
        return {
            "type": "nmea",
            "timestamp": event.timestamp,
            "sentence": event.sentence,
        }
    if isinstance(event, PositionEvent):
        return {
            "type": "position",
            "timestamp": event.timestamp,
            **dataclasses.asdict(event.position),
        }
    if isinstance(event, StatusEvent):
        return {
            "type": "status",
            "timestamp": event.timestamp,
            "state": event.state.value,
            "message": event.message,
        }
    if isinstance(event, SignalEvent):
        return {
            "type": "signal",
            "timestamp": event.timestamp,
            "strength_db": event.strength_db,
            "carrier_offset_hz": event.carrier_offset_hz,
            "present": event.present,
        }
    return None


class JsonLogSink:
    """Appends one JSON object per event (JSON Lines) to ``path``."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._fh: IO[str] | None = open(path, "a", encoding="utf-8")
        self._lock = threading.Lock()

    def __call__(self, event: Event) -> None:
        d = event_to_dict(event)
        if d is None:
            return
        with self._lock:
            if self._fh is not None:
                self._fh.write(json.dumps(d, separators=(",", ":")) + "\n")
                self._fh.flush()

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None


class LastPositionStore:
    """Remembers the latest position; optionally persists it to a JSON file.

    With ``path`` set, an existing file is loaded at construction (a missing or
    corrupt file is ignored) and each update is written atomically.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self._path = Path(path) if path else None
        self._lock = threading.Lock()
        self._position: Position | None = None
        self._timestamp: float | None = None
        if self._path and self._path.exists():
            try:
                d = json.loads(self._path.read_text(encoding="utf-8"))
                self._position = Position(
                    float(d["lat"]),
                    float(d["lon"]),
                    float(d["alt_m"]),
                    int(d["satellites"]),
                )
                self._timestamp = float(d["timestamp"])
            except (OSError, ValueError, KeyError, TypeError):
                self._position = self._timestamp = None

    @property
    def position(self) -> Position | None:
        with self._lock:
            return self._position

    @property
    def timestamp(self) -> float | None:
        with self._lock:
            return self._timestamp

    def __call__(self, event: Event) -> None:
        if not isinstance(event, PositionEvent):
            return
        with self._lock:
            self._position, self._timestamp = event.position, event.timestamp
            if self._path:
                self._write(event)

    def _write(self, event: PositionEvent) -> None:
        assert self._path is not None
        data = json.dumps(
            {**dataclasses.asdict(event.position), "timestamp": event.timestamp}
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=self._path.name + ".")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(data)
            os.replace(tmp, self._path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
