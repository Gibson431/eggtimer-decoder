"""SDR discovery and a ``SampleSource`` backed by a real (or stand-in) device.

The device library is isolated behind a small ``Backend`` seam so tests can
inject a stand-in. ``OsmoBackend`` is the real implementation (gr-osmosdr via
GNU Radio); only RTL-SDR is tested. Discovery never raises: with no device or
no library it returns an empty list.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Protocol

import numpy as np

__all__ = [
    "Backend",
    "DeviceHandle",
    "DeviceInfo",
    "OsmoBackend",
    "SdrSource",
    "discover",
    "open_source",
    "pick_sample_rate",
]


@dataclass(frozen=True)
class DeviceInfo:
    """An attached SDR. ``args`` is the backend's device string (e.g. ``rtl=0``)."""

    id: str  # stable selector, e.g. "rtl=0"
    name: str
    args: str
    freq_range: tuple[float, float]  # Hz, (min, max)
    sample_rates: tuple[float, ...]  # discrete rates in Hz, ascending


class DeviceHandle(Protocol):
    """An opened device (the part of the library the source needs)."""

    def set_sample_rate(self, rate: float) -> None: ...
    def set_center_freq(self, freq: float) -> None: ...
    def set_freq_corr(self, ppm: float) -> None: ...
    def set_gain(self, gain_db: float) -> None: ...
    def read(self, num_samples: int) -> np.ndarray: ...  # may block; empty after close
    def close(self) -> None: ...


class Backend(Protocol):
    def enumerate(self) -> list[DeviceInfo]: ...
    def open(self, info: DeviceInfo) -> DeviceHandle: ...


def _backend(backend: Backend | None) -> Backend:
    return backend if backend is not None else OsmoBackend()


def discover(backend: Backend | None = None) -> list[DeviceInfo]:
    """List attached SDRs; empty (never an error) if none or library missing."""
    try:
        return list(_backend(backend).enumerate())
    except Exception:
        return []


def pick_sample_rate(info: DeviceInfo, wanted: float, tolerance: float = 0.0) -> float:
    """Return the supported rate nearest ``wanted``.

    With ``tolerance`` > 0 (relative, e.g. 0.01) a ValueError is raised if even
    the nearest rate is further than that from ``wanted``.
    """
    if not info.sample_rates:
        raise ValueError(f"{info.id}: no sample rates reported")
    best = min(info.sample_rates, key=lambda r: abs(r - wanted))
    if tolerance and abs(best - wanted) > tolerance * wanted:
        raise ValueError(
            f"{info.id}: no supported rate within {tolerance:.1%} of {wanted:g} Hz "
            f"(nearest {best:g})"
        )
    return best


def _select(devices: list[DeviceInfo], device: str | None) -> DeviceInfo:
    if not devices:
        raise ValueError("no SDR devices found")
    if device is None:
        return devices[0]
    for d in devices:
        if device in (d.id, d.name):
            return d
    if device.isdigit() and int(device) < len(devices):
        return devices[int(device)]
    raise ValueError(f"unknown device {device!r}; available: {[d.id for d in devices]}")


def _check_freq(info: DeviceInfo, f: float) -> None:
    lo, hi = info.freq_range
    if not lo <= f <= hi:
        raise ValueError(f"{f:g} Hz outside device range {lo:g}..{hi:g} Hz")


class SdrSource:
    """``SampleSource`` over an opened device handle."""

    def __init__(self, info: DeviceInfo, handle: DeviceHandle, sample_rate: float,
                 center_freq: float):
        self.info = info
        self._h = handle
        self._rate = sample_rate
        self._freq = center_freq
        self._closed = False
        self._lock = threading.Lock()

    @property
    def sample_rate(self) -> float:
        return self._rate

    @property
    def center_freq(self) -> float:
        return self._freq

    def tune(self, center_freq: float) -> None:
        _check_freq(self.info, center_freq)
        with self._lock:
            if self._closed:
                return
            self._h.set_center_freq(center_freq)
            self._freq = center_freq

    def read(self, num_samples: int) -> np.ndarray:
        if self._closed:
            return np.empty(0, dtype=np.complex64)
        return np.asarray(self._h.read(num_samples), dtype=np.complex64)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._h.close()


def open_source(
    device: str | None = None,
    center_freq: float = 921e6,
    sample_rate: float = 960_000.0,
    gain_db: float = 30.0,
    ppm: float = 0.0,
    backend: Backend | None = None,
) -> SdrSource:
    """Open ``device`` (id, name, index or None for first) as a SampleSource.

    The sample rate snaps to the nearest supported rate (see ``SdrSource.sample_rate``).
    Raises ValueError for an unknown device or out-of-range frequency.
    """
    be = _backend(backend)
    info = _select(be.enumerate(), device)
    rate = pick_sample_rate(info, sample_rate)
    _check_freq(info, center_freq)
    h = be.open(info)
    try:
        h.set_sample_rate(rate)
        h.set_freq_corr(ppm)
        h.set_gain(gain_db)
        h.set_center_freq(center_freq)
    except Exception:
        h.close()
        raise
    return SdrSource(info, h, rate, center_freq)


# --- real backend -----------------------------------------------------------


def _meta_ranges(r) -> list:
    return list(r.values()) if hasattr(r, "values") else [r]


def _range_bounds(r) -> tuple[float, float]:
    rs = _meta_ranges(r)
    return min(x.start() for x in rs), max(x.stop() for x in rs)


def _rates(r) -> tuple[float, ...]:
    out: set[float] = set()
    for x in _meta_ranges(r):
        lo, hi, step = x.start(), x.stop(), x.step()
        if step and hi > lo:
            n = int(round((hi - lo) / step))
            out.update(lo + i * step for i in range(min(n, 10_000) + 1))
        else:
            out.update((lo, hi))
    return tuple(sorted(out))


class OsmoBackend:
    """gr-osmosdr backend. Probes ``rtl=0..`` style indices (RTL-SDR tested)."""

    DRIVERS = ("rtl",)
    MAX_PER_DRIVER = 8

    def enumerate(self) -> list[DeviceInfo]:
        try:
            import osmosdr
        except Exception:
            return []
        found: list[DeviceInfo] = []
        for drv in self.DRIVERS:
            for i in range(self.MAX_PER_DRIVER):
                args = f"{drv}={i}"
                try:
                    s = osmosdr.source(args=f"numchan=1 {args}")
                    info = DeviceInfo(
                        id=args,
                        name=f"{drv}:{i}",
                        args=args,
                        freq_range=_range_bounds(s.get_freq_range()),
                        sample_rates=_rates(s.get_sample_rates()),
                    )
                    del s
                except Exception:
                    break
                found.append(info)
        return found

    def open(self, info: DeviceInfo) -> DeviceHandle:
        return _OsmoHandle(info)


class _OsmoHandle:
    def __init__(self, info: DeviceInfo):
        import osmosdr
        from gnuradio import gr

        q: queue.Queue = queue.Queue(maxsize=64)
        self._q = q
        self._closed = threading.Event()
        self._buf = np.empty(0, dtype=np.complex64)

        class Sink(gr.sync_block):
            def __init__(self):
                gr.sync_block.__init__(self, "sdr_sink", [np.complex64], None)

            def work(self, input_items, output_items):
                chunk = input_items[0].copy()
                try:
                    q.put(chunk, timeout=0.2)  # drop on overrun rather than stall
                except queue.Full:
                    pass
                return len(chunk)

        self._src = osmosdr.source(args=f"numchan=1 {info.args}")
        self._tb = gr.top_block("sdr-source")
        self._tb.connect(self._src, Sink())
        self._tb.start()

    def set_sample_rate(self, rate):
        self._src.set_sample_rate(rate)

    def set_center_freq(self, freq):
        self._src.set_center_freq(freq)

    def set_freq_corr(self, ppm):
        self._src.set_freq_corr(ppm)

    def set_gain(self, gain_db):
        self._src.set_gain_mode(False)
        self._src.set_gain(gain_db)

    def read(self, num_samples):
        while len(self._buf) == 0 and not self._closed.is_set():
            try:
                self._buf = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
        out, self._buf = self._buf[:num_samples], self._buf[num_samples:]
        return out

    def close(self):
        self._closed.set()
        self._tb.stop()
        self._tb.wait()
