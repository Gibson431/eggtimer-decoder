"""The running receiver: source -> demod -> frames -> NMEA -> positions, plus signal metrics.

``LiveReceiver`` implements ``contracts.Receiver`` (read that module for the
semantics). Threads, all owned by the receiver and gone after ``stop()``:

* a worker that opens the source (so open failures become status events, and
  ``start()`` returns promptly), reads it and feeds the pipeline;
* a delivery thread that calls subscribers in event order.

Behaviour worth knowing beyond the contract:

* Setters on a stopped (or errored) receiver only record the value and emit no
  event: after ``stop()`` nothing may be delivered, and there is no delivery
  thread to carry one.
* An ERROR (open/read failure, or a source that ends by itself) ends both
  threads after the ERROR event is delivered. State stays ERROR until
  ``start()`` is called again; ``stop()`` then only joins (no STOPPED event).
* On a channel change the source is told too when it has a ``set_channel``
  method (``SimSource`` moves its simulated transmitter; real devices have no
  such call), then the decoder is retargeted, or rebuilt if the source's
  ``sample_rate`` changed.
* ``signal_hold`` seconds of debounce keep ``SignalEvent.present`` true across the
  gaps between frame bursts; a decoded frame also counts as presence.
* ``stop()`` called from a subscriber callback does not join the delivery thread
  (it cannot join itself); it still stops reception.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import replace

import numpy as np

from .channels import CHANNELS, Channel, get_channel
from .contracts import (
    Event,
    FrameEvent,
    NmeaEvent,
    PositionEvent,
    ReceiverState,
    SampleSource,
    SignalEvent,
    StatusEvent,
    Subscriber,
    Unsubscribe,
)
from .packet import NmeaAssembler, parse_gga
from .settings import Settings
from .signal_metrics import SignalMeasurement, SignalMetrics
from .stream_demod import ChannelDecoder

__all__ = ["LiveReceiver", "SourceFactory", "default_source_factory"]

log = logging.getLogger(__name__)

SourceFactory = Callable[[Settings], SampleSource]
_SENTINEL = object()
_MIN_METRIC_SAMPLES = 4096  # samples needed after a reset before signal events are meaningful


def default_source_factory(settings: Settings) -> SampleSource:
    """``device == "sim"`` -> SimSource; otherwise ``sdr.open_source`` ("" = first device)."""
    if settings.device == "sim":
        from .sim_source import SimSource

        return SimSource(channel=settings.channel, center_freq=settings.center_freq_hz)
    from . import sdr

    kwargs = {} if settings.gain is None else {"gain_db": settings.gain}
    chan_rate = CHANNELS[settings.channel].samp_rate
    return sdr.open_source(  # source rate must be an integer multiple of the channel rate (D24)
        settings.device or None,
        settings.center_freq_hz,
        sample_rate=5 * chan_rate,
        ppm=settings.ppm,
        rate_multiple_of=chan_rate,
        **kwargs,
    )


class LiveReceiver:
    def __init__(
        self,
        settings: Settings | None = None,
        source_factory: SourceFactory = default_source_factory,
        table: dict[int, Channel] = CHANNELS,
        chunk_samples: int = 16384,
        signal_interval: float = 0.25,
        signal_hold: float = 1.0,
    ) -> None:
        self._settings = settings or Settings()
        self._factory = source_factory
        self._table = table
        self._chunk = chunk_samples
        self._sig_interval = signal_interval
        self._sig_hold = signal_hold
        self._lock = threading.RLock()  # state, pipeline, subscribers
        self._life = threading.Lock()  # serialises start/stop
        self._state = ReceiverState.STOPPED
        self._subs: list[Subscriber] = []
        self._queue: queue.Queue = queue.Queue()
        self._accepting = False
        self._stop = threading.Event()
        self._reopen = False
        self._source: SampleSource | None = None
        self._decoder: ChannelDecoder | None = None
        self._metrics: SignalMetrics | None = None
        self._asm = NmeaAssembler()
        self._reset_signal_state()
        self._worker: threading.Thread | None = None
        self._deliverer: threading.Thread | None = None

    # -- Receiver protocol --------------------------------------------
    @property
    def state(self) -> ReceiverState:
        with self._lock:
            return self._state

    @property
    def settings(self) -> Settings:
        """The current settings (what the next start, or the running source, uses)."""
        with self._lock:
            return self._settings

    def subscribe(self, callback: Subscriber) -> Unsubscribe:
        with self._lock:
            self._subs.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subs:
                    self._subs.remove(callback)

        return unsubscribe

    def start(self) -> None:
        with self._life:
            if self._worker is not None and self._worker.is_alive():
                return
            self._join_threads()  # leftovers of an earlier ERROR
            with self._lock:
                self._stop.clear()
                self._reopen = False
                self._queue = queue.Queue()
                self._accepting = True
                self._source = self._decoder = self._metrics = None
                self._state = ReceiverState.STARTING
                self._emit(StatusEvent(ReceiverState.STARTING, self._describe("starting")))
                self._deliverer = threading.Thread(target=self._deliver, args=(self._queue,), name="eggtimer-events", daemon=True)
                self._worker = threading.Thread(target=self._run, name="eggtimer-receiver", daemon=True)
                self._deliverer.start()
                self._worker.start()

    def stop(self) -> None:
        with self._life:
            if self._worker is None and self._deliverer is None:
                return
            self._stop.set()
            with self._lock:
                self._close_source()
            if self._worker is not None and self._worker is not threading.current_thread():
                self._worker.join()
            with self._lock:
                if self._accepting:  # not already ended by an ERROR
                    self._state = ReceiverState.STOPPED
                    self._emit(StatusEvent(ReceiverState.STOPPED, "stopped"))
                    self._accepting = False
                    self._queue.put(_SENTINEL)
            self._join_threads()

    def set_frequency(self, center_freq: float) -> None:
        with self._lock:
            new = replace(self._settings, center_freq_hz=center_freq)  # SettingsError is a ValueError
            if self._source is not None:
                assert self._decoder is not None and self._metrics is not None
                self._source.tune(new.center_freq_hz)
                self._decoder.set_center_freq(new.center_freq_hz)
                self._metrics.reset()
                self._reset_signal_state()
            self._settings = new
            self._asm = NmeaAssembler()
            self._emit_change(f"center frequency {new.center_freq_hz / 1e6:.6f} MHz")

    def set_channel(self, channel: int) -> None:
        with self._lock:
            new = replace(self._settings, channel=channel)  # validates the number
            ch = get_channel(channel, self._table)
            src = self._source
            if src is not None:
                old = self._settings.channel
                if hasattr(src, "set_channel"):
                    src.set_channel(channel)
                try:
                    self._retarget(src, ch)
                except ValueError:
                    if hasattr(src, "set_channel"):
                        src.set_channel(old)
                    raise
            self._settings = new
            self._asm = NmeaAssembler()
            self._emit_change(f"channel {channel}")

    def set_device(self, device: str | None) -> None:
        device = device or ""
        with self._lock:
            new = replace(self._settings, device=device)
            if new == self._settings:
                return
            self._settings = new
            if self._state in (ReceiverState.STOPPED, ReceiverState.ERROR):
                return
            self._state = ReceiverState.RESTARTING
            self._reopen = True
            self._emit(StatusEvent(ReceiverState.RESTARTING, f"switching to device {device or 'default'!r}"))
            self._close_source()  # unblocks the pending read; the worker reopens

    # -- helpers (call with self._lock held where they touch state) ---
    def _describe(self, what: str) -> str:
        s = self._settings
        return f"{what}: device {s.device or 'default'!r}, {s.center_freq_hz / 1e6:.6f} MHz, channel {s.channel}"

    def _emit(self, event: Event) -> None:
        if isinstance(event, StatusEvent) and not event.timestamp:
            event = replace(event, timestamp=time.time())
        if self._accepting:
            self._queue.put(event)

    def _emit_change(self, what: str) -> None:
        if self._state not in (ReceiverState.STOPPED, ReceiverState.ERROR):
            self._emit(StatusEvent(self._state, what))

    def _close_source(self) -> None:
        src, self._source = self._source, None
        if src is not None:
            self._close_one(src)

    @staticmethod
    def _close_one(src: SampleSource) -> None:
        try:
            src.close()
        except Exception:
            log.exception("error closing source")

    def _join_threads(self) -> None:
        me = threading.current_thread()
        for t in (self._worker, self._deliverer):
            if t is not None and t is not me:
                t.join()
        self._worker = self._deliverer = None

    def _reset_signal_state(self) -> None:
        self._metric_samples = 0
        self._last_signal_t = 0.0
        self._last_present_t = -1e9
        self._last_present: SignalMeasurement | None = None

    def _retarget(self, src: SampleSource, ch: Channel) -> None:
        """Point the pipeline at ``ch`` (lock held); a ValueError leaves the pipeline unchanged."""
        assert self._decoder is not None and self._metrics is not None
        if src.sample_rate != self._decoder.demod.sample_rate:
            self._decoder = ChannelDecoder(src.sample_rate, self._settings.center_freq_hz, ch.number, self._table)
            self._metrics = SignalMetrics(src.sample_rate, ch.offset_hz)
        else:
            self._decoder.set_channel(ch.number)
            self._metrics.set_channel_offset(ch.offset_hz)
        self._reset_signal_state()

    # -- delivery thread -----------------------------------------------
    def _deliver(self, q: queue.Queue) -> None:
        while True:
            event = q.get()
            if event is _SENTINEL:
                return
            with self._lock:
                subs = list(self._subs)
            for cb in subs:
                try:
                    cb(event)
                except Exception:
                    log.exception("subscriber %r raised on %s", cb, type(event).__name__)

    # -- worker thread -------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if not self._open():
                    return  # stopped while opening
            except Exception as e:
                self._fail(f"cannot open source: {e}")
                return
            try:
                self._pump()
            except Exception as e:
                with self._lock:
                    requested = self._stop.is_set() or self._reopen
                if not requested:
                    self._fail(f"source failed: {e}")
                    return
            with self._lock:
                self._close_source()
                if self._stop.is_set():
                    return
                reopen = self._reopen
            if not reopen:
                self._fail("source ended unexpectedly")
                return

    def _open(self) -> bool:
        """Open a source for the current settings and install the pipeline."""
        while True:
            with self._lock:
                snap = self._settings
            src = self._factory(snap)  # may be slow; no lock held
            with self._lock:
                if self._stop.is_set():
                    self._close_one(src)
                    return False
                if snap.device != self._settings.device:  # device changed while opening
                    self._close_one(src)
                    continue
                s = self._settings
                try:
                    if src.center_freq != s.center_freq_hz:
                        src.tune(s.center_freq_hz)
                    if hasattr(src, "set_channel") and getattr(src, "channel", s.channel) != s.channel:
                        src.set_channel(s.channel)
                    ch = get_channel(s.channel, self._table)
                    self._decoder = ChannelDecoder(src.sample_rate, s.center_freq_hz, s.channel, self._table)
                    self._metrics = SignalMetrics(src.sample_rate, ch.offset_hz)
                except Exception:
                    self._close_one(src)
                    raise
                self._asm = NmeaAssembler()
                self._reset_signal_state()
                self._source = src
                self._reopen = False
                self._state = ReceiverState.RUNNING
                self._emit(StatusEvent(ReceiverState.RUNNING, self._describe("receiving")))
                return True

    def _pump(self) -> None:
        with self._lock:
            src = self._source
        if src is None:
            return
        while not self._stop.is_set():
            iq = src.read(self._chunk)
            if len(iq) == 0:
                return
            with self._lock:
                if self._source is not src or self._stop.is_set():
                    return
                self._process(iq)

    def _process(self, iq: np.ndarray) -> None:
        """Feed one chunk through the pipeline and emit events (lock held)."""
        assert self._decoder is not None and self._metrics is not None
        now = time.time()
        for payload in self._decoder.process(iq):
            self._last_present_t = time.monotonic()
            self._emit(FrameEvent(payload, now))
            for line in self._asm.feed(payload):
                self._emit(NmeaEvent(line, now))
                try:
                    pos = parse_gga(line)
                except (ValueError, IndexError):
                    pos = None
                if pos is not None:
                    self._emit(PositionEvent(pos, now))
        m = self._metrics.update(iq)
        self._metric_samples += len(iq)
        mono = time.monotonic()
        if m.present:
            self._last_present_t, self._last_present = mono, m
        if mono - self._last_signal_t >= self._sig_interval and self._metric_samples >= _MIN_METRIC_SAMPLES:
            self._last_signal_t = mono
            present = mono - self._last_present_t <= self._sig_hold
            shown = self._last_present if present and not m.present and self._last_present else m
            strength = shown.strength_db if np.isfinite(shown.strength_db) else -200.0
            self._emit(SignalEvent(float(strength), float(shown.carrier_offset_hz), present, now))

    def _fail(self, message: str) -> None:
        """Report an ERROR, end reception and let the delivery thread finish."""
        with self._lock:
            if self._stop.is_set() or not self._accepting:
                return
            self._close_source()
            self._state = ReceiverState.ERROR
            self._emit(StatusEvent(ReceiverState.ERROR, message))
            self._accepting = False
            self._queue.put(_SENTINEL)
