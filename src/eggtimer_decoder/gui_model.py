"""Display-free logic for the GUI: form <-> Settings, event -> display, and the controller.

Nothing here imports tkinter, so all of it is testable without a display.

* ``FormValues`` are the raw strings in the widgets; ``parse_form`` validates them through
  ``settings.Settings`` and returns per-field error messages.
* ``StatusModel`` folds events into what the status panel shows; ``event_to_row`` turns an
  event into a line for the packet list.
* ``Controller`` owns the receiver and applies a changed ``Settings`` to it using only the
  ``Receiver`` contract (``set_frequency``/``set_channel``/``set_device``). gain and ppm are
  source configuration fixed when the source is opened (D10, D18), so changing them stops the
  receiver and starts a fresh one with the new settings (``receiver_factory``). That is the
  only "restart" the GUI does itself.
"""

from __future__ import annotations

import dataclasses
import queue
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .channels import CHANNEL_NUMBERS
from .contracts import (
    Event,
    FrameEvent,
    NmeaEvent,
    PositionEvent,
    Receiver,
    ReceiverState,
    SignalEvent,
    StatusEvent,
)
from .packet import Position, parse_gga
from .settings import Settings, SettingsError, save_settings

__all__ = [
    "Controller",
    "DEFAULT_DEVICE_LABEL",
    "DisplayRow",
    "FormResult",
    "FormValues",
    "StatusModel",
    "device_choices",
    "device_to_label",
    "event_to_row",
    "format_position",
    "parse_form",
    "settings_to_form",
]

DEFAULT_DEVICE_LABEL = "default (first available)"
SIM_LABEL = "sim (simulated signal)"


# -- form <-> settings -------------------------------------------------------
@dataclass(frozen=True)
class FormValues:
    device: str = ""  # label or raw text
    freq_mhz: str = "921"
    channel: str = "0"
    gain: str = "auto"
    ppm: str = "0"


@dataclass(frozen=True)
class FormResult:
    settings: Settings | None
    errors: dict[str, str]  # field name -> message; empty when settings is set


def device_choices(infos: Iterable = ()) -> list[tuple[str, str]]:
    """(label, value) pairs for the device picker: default, sim, then each discovered device."""
    out = [(DEFAULT_DEVICE_LABEL, ""), (SIM_LABEL, "sim")]
    for d in infos:
        out.append((f"{d.id} - {d.name}", d.id))
    return out


def device_to_label(value: str, choices: list[tuple[str, str]]) -> str:
    for label, v in choices:
        if v == value:
            return label
    return value  # unknown/free-form device string shown as is


def _label_to_device(text: str, choices: list[tuple[str, str]]) -> str:
    text = text.strip()
    for label, v in choices:
        if text == label:
            return v
    return text


def _fmt_mhz(hz: float) -> str:
    return f"{hz / 1e6:.6f}".rstrip("0").rstrip(".")


def settings_to_form(
    s: Settings, choices: list[tuple[str, str]] | None = None
) -> FormValues:
    return FormValues(
        device=device_to_label(s.device, choices or device_choices()),
        freq_mhz=_fmt_mhz(s.center_freq_hz),
        channel=str(s.channel),
        gain="auto" if s.gain is None else f"{s.gain:g}",
        ppm=f"{s.ppm:g}",
    )


def _parse_freq_mhz(text: str) -> float:
    """MHz by default; an explicit k/M/G/Hz suffix is honoured (same rules as the CLI)."""
    from .cli import parse_freq

    t = text.strip()
    if not t:
        raise ValueError("enter a frequency, e.g. 921")
    try:
        float(t)
    except ValueError:
        return parse_freq(t)  # has a suffix (or is garbage -> ValueError with a hint)
    return float(t) * 1e6


def parse_form(
    form: FormValues, base: Settings, choices: list[tuple[str, str]] | None = None
) -> FormResult:
    """Validate ``form`` on top of ``base`` (which supplies the non-form fields, e.g. log_file)."""
    choices = choices or device_choices()
    errors: dict[str, str] = {}
    kw: dict = {"device": _label_to_device(form.device, choices)}

    try:
        kw["center_freq_hz"] = _parse_freq_mhz(form.freq_mhz)
    except ValueError as e:
        errors["freq_mhz"] = str(e)
    try:
        kw["channel"] = int(form.channel.strip())
        if kw["channel"] not in CHANNEL_NUMBERS:
            raise ValueError
    except ValueError:
        kw.pop("channel", None)
        errors["channel"] = f"channel must be one of {list(CHANNEL_NUMBERS)}"
    g = form.gain.strip().lower()
    if g in ("", "auto"):
        kw["gain"] = None
    else:
        try:
            kw["gain"] = float(g)
        except ValueError:
            errors["gain"] = "gain must be a number in dB or 'auto'"
    try:
        kw["ppm"] = float(form.ppm.strip() or "0")
    except ValueError:
        errors["ppm"] = "ppm must be a number"

    # Per-field range checks through Settings itself: validate each field alone on top of base.
    for field_name, form_key in (
        ("center_freq_hz", "freq_mhz"),
        ("channel", "channel"),
        ("gain", "gain"),
        ("ppm", "ppm"),
    ):
        if form_key in errors or field_name not in kw:
            continue
        try:
            dataclasses.replace(base, **{field_name: kw[field_name]})
        except SettingsError as e:
            errors[form_key] = str(e)
    if errors:
        return FormResult(None, errors)
    try:
        return FormResult(dataclasses.replace(base, **kw), {})
    except SettingsError as e:
        return FormResult(None, {"device": str(e)})


# -- events -> display -------------------------------------------------------
@dataclass(frozen=True)
class DisplayRow:
    time: str  # HH:MM:SS
    kind: str  # "frame" | "nmea" | "status"
    text: str


def _clock(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts or time.time()))


def format_position(p: Position | None, timestamp: float | None = None) -> str:
    if p is None:
        return "no position yet"
    s = f"{p.lat:.6f}, {p.lon:.6f}  alt {p.alt_m:.1f} m  sats {p.satellites}"
    return s + (f"  (at {_clock(timestamp)})" if timestamp else "")


def event_to_row(event: Event) -> DisplayRow | None:
    """Row for the packet list, or None for events that are not listed (signal, position)."""
    if isinstance(event, FrameEvent):
        return DisplayRow(
            _clock(event.timestamp),
            "frame",
            f"len={len(event.payload):2d} {event.payload!r}",
        )
    if isinstance(event, NmeaEvent):
        try:
            pos = parse_gga(event.sentence)
        except (ValueError, IndexError):
            pos = None
        return DisplayRow(
            _clock(event.timestamp),
            "nmea",
            f"{event.sentence} => {pos}" if pos else event.sentence,
        )
    if isinstance(event, StatusEvent):
        return DisplayRow(
            _clock(event.timestamp),
            "status",
            f"[{event.state.value}] {event.message}".rstrip(),
        )
    return None


@dataclass
class StatusModel:
    """Latest status shown in the panel. Feed it every event with ``apply``."""

    state: ReceiverState = ReceiverState.STOPPED
    message: str = ""
    strength_db: float | None = None
    present: bool = False
    carrier_offset_hz: float | None = None
    position: Position | None = None
    position_time: float | None = None
    frames: int = 0
    sentences: int = 0
    _history: deque = field(default_factory=lambda: deque(maxlen=5), repr=False)

    def apply(self, event: Event) -> None:
        if isinstance(event, StatusEvent):
            self.state, self.message = event.state, event.message
            if event.state != ReceiverState.RUNNING:
                self.strength_db = self.carrier_offset_hz = None
                self.present = False
                self._history.clear()
        elif isinstance(event, SignalEvent):
            self.strength_db, self.present = event.strength_db, event.present
            self._history.append(event.carrier_offset_hz)
            self.carrier_offset_hz = event.carrier_offset_hz
        elif isinstance(event, PositionEvent):
            self.position, self.position_time = event.position, event.timestamp
        elif isinstance(event, FrameEvent):
            self.frames += 1
        elif isinstance(event, NmeaEvent):
            self.sentences += 1

    # Text for the labels
    def state_text(self) -> str:
        return self.state.value.upper() + (f" - {self.message}" if self.message else "")

    def signal_text(self) -> str:
        if self.strength_db is None:
            return "no signal data"
        return f"{self.strength_db:.1f} dBFS, {'signal present' if self.present else 'no signal'}"

    def offset_text(self) -> str:
        """Carrier offset is noisy (D27): show the median of the last few values, rounded to 0.1 kHz."""
        if not self._history:
            return "n/a"
        vals = sorted(self._history)
        med = vals[len(vals) // 2]
        return f"~{med / 1e3:+.1f} kHz (noisy)"

    def position_text(self) -> str:
        return format_position(self.position, self.position_time)

    def counts_text(self) -> str:
        return f"{self.frames} frames, {self.sentences} sentences"


# -- controller ------------------------------------------------------------
ReceiverFactory = Callable[[Settings], Receiver]


class Controller:
    """Owns the receiver; applies settings changes; hands events to the UI thread via a queue.

    Events arrive on the receiver's delivery thread and are only put in ``self.events``;
    the UI drains it with ``drain()`` from its own thread.
    """

    def __init__(
        self,
        settings: Settings,
        receiver_factory: ReceiverFactory,
        path: Path | None = None,
        extra_subscribers: Iterable[Callable[[Event], None]] = (),
    ) -> None:
        self.settings = settings
        self.path = path
        self.events: queue.Queue[Event] = queue.Queue()
        self._factory = receiver_factory
        self._extra = list(extra_subscribers)
        self._unsub: Callable[[], None] | None = None
        self.receiver: Receiver = self._new_receiver(settings)

    def _new_receiver(self, settings: Settings) -> Receiver:
        r = self._factory(settings)
        subs = [self.events.put, *self._extra]

        def fan_out(event: Event) -> None:
            for cb in subs:
                cb(event)

        self._unsub = r.subscribe(fan_out)
        return r

    @property
    def running(self) -> bool:
        return self.receiver.state not in (ReceiverState.STOPPED, ReceiverState.ERROR)

    def drain(self, limit: int = 500) -> list[Event]:
        out: list[Event] = []
        while len(out) < limit:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                break
        return out

    def start(self) -> None:
        self.receiver.start()

    def stop(self) -> None:
        """Stop reception (blocks until the source is closed). Call from the UI thread, not a subscriber."""
        self.receiver.stop()

    def close(self) -> None:
        self.stop()
        if self._unsub:
            self._unsub()
            self._unsub = None

    def apply(self, new: Settings) -> list[str]:
        """Apply ``new`` live, persist it, and return human-readable notes (or raise ValueError).

        Frequency, channel and device go through the Receiver setters while running. gain/ppm
        need a new source, so the receiver is stopped and replaced by a fresh one with the new
        settings (restarted if it was running). Nothing is saved when a change is rejected.
        """
        old, notes = self.settings, []
        if new == old:
            return ["no changes"]
        was_running = self.running
        if (new.gain, new.ppm) != (old.gain, old.ppm):
            self.close()
            self.receiver = self._new_receiver(new)
            if was_running:
                self.receiver.start()
            notes.append(
                "gain/ppm changed: receiver restarted"
                if was_running
                else "gain/ppm recorded"
            )
        else:
            try:
                if new.center_freq_hz != old.center_freq_hz:
                    self.receiver.set_frequency(new.center_freq_hz)
                    notes.append("frequency applied")
                if new.channel != old.channel:
                    self.receiver.set_channel(new.channel)
                    notes.append(f"channel {new.channel} applied")
                if new.device != old.device:
                    self.receiver.set_device(new.device or None)
                    notes.append(
                        "device changed: restarting reception"
                        if was_running
                        else "device recorded"
                    )
            except ValueError:
                # Part of the change may have been accepted; keep what the receiver now holds.
                self.settings = dataclasses.replace(
                    new, **_receiver_view(self.receiver)
                )
                raise
        self.settings = new
        if self.path is not None:
            save_settings(new, self.path)
            notes.append("saved")
        return notes


def _receiver_view(receiver: Receiver) -> dict:
    """Settings fields the receiver reports (LiveReceiver exposes ``settings``); else none."""
    s = getattr(receiver, "settings", None)
    if isinstance(s, Settings):
        return {
            "center_freq_hz": s.center_freq_hz,
            "channel": s.channel,
            "device": s.device,
        }
    return {}
