"""Command-line entry point: ``eggtimer-decoder [command] [options]``.

No arguments opens the GUI (``eggtimer_decoder.gui.main``, when it exists).
Options without a command mean ``run``.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import logging
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Callable, Sequence

from .channels import CENTER_FREQ_HZ, CHANNEL_NUMBERS, CHANNELS
from .settings import (
    Settings,
    SettingsError,
    default_settings_path,
    load_settings,
    load_settings_file,
    resolve_settings,
    save_settings,
    settings_from_env,
)

__all__ = ["main", "parse_freq", "settings_path"]

CONFIG_ENV = "EGGTIMER_CONFIG"  # overrides the saved-settings path


def settings_path() -> Path:
    """Saved-settings file: $EGGTIMER_CONFIG if set, else settings.default_settings_path()."""
    override = os.environ.get(CONFIG_ENV)
    return Path(override) if override else default_settings_path()


def parse_freq(text: str) -> float:
    """Hz from '921e6', '921000000', '921M'/'921MHz', '920.99m', '433k', '0.921G'.

    A bare number below 10000 is taken as MHz ('921' -> 921 MHz).
    """
    s = text.strip().lower().replace("hz", "").replace(" ", "")
    mult = {"k": 1e3, "m": 1e6, "g": 1e9}.get(s[-1:])
    try:
        if mult is not None:
            return float(s[:-1]) * mult
        v = float(s)
    except ValueError:
        raise ValueError(
            f"cannot parse frequency {text!r} (try 921e6 or 921M)"
        ) from None
    return v * 1e6 if 0 < abs(v) < 1e4 else v


def _freq_arg(text: str) -> float:
    try:
        return parse_freq(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def _gain_arg(text: str) -> float | str:
    if text.strip().lower() == "auto":
        return "auto"
    try:
        return float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid gain {text!r} (a number in dB, or 'auto')"
        ) from None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eggtimer-decoder",
        description="Eggtimer / Eggfinder TX decoder. With no arguments, opens the GUI. "
        "Options given without a command mean 'run'.",
    )
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    r = sub.add_parser(
        "run",
        help="decode live and print packets until Ctrl-C",
        description="Decode live and print packets as they arrive, until Ctrl-C or SIGTERM. "
        "Unset options fall back to EGGTIMER_* environment, then saved settings, then defaults.",
    )
    r.add_argument(
        "--device",
        help="device id from 'devices' (e.g. rtl=0); 'sim' = simulated source; default first SDR",
    )
    r.add_argument(
        "--center-freq",
        type=_freq_arg,
        metavar="FREQ",
        help=f"centre frequency: Hz, or with suffix k/M/G (default {CENTER_FREQ_HZ / 1e6:g}M)",
    )
    r.add_argument(
        "--channel",
        type=int,
        choices=CHANNEL_NUMBERS,
        metavar="N",
        help=f"channel number, one of {list(CHANNEL_NUMBERS)} (see 'channels')",
    )
    r.add_argument(
        "--gain",
        type=_gain_arg,
        metavar="DB|auto",
        help="tuner gain in dB, or 'auto' (default)",
    )
    r.add_argument("--ppm", type=float, help="frequency correction in ppm")
    r.add_argument(
        "--log-file", metavar="PATH", help="append every event as JSON lines"
    )
    r.add_argument(
        "--position-file",
        metavar="PATH",
        help="keep the last position in this JSON file",
    )
    r.add_argument(
        "--duration",
        type=float,
        metavar="SECONDS",
        help="stop after this long (default: run until interrupted)",
    )
    g = r.add_mutually_exclusive_group()
    g.add_argument(
        "-v", "--verbose", action="store_true", help="also print debug logging"
    )
    g.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="print only NMEA sentences and errors",
    )

    sub.add_parser(
        "devices",
        help="list attached SDR devices",
        description="List attached SDR devices.",
    )
    sub.add_parser(
        "channels",
        help="show the channel table",
        description="Show channel parameters and offsets.",
    )

    s = sub.add_parser(
        "settings",
        help="view or change saved settings",
        description=f"View or change the saved settings file (path: ${CONFIG_ENV} or the default config location).",
    )
    ss = s.add_subparsers(dest="action", metavar="ACTION", required=True)
    ss.add_parser(
        "show", help="print saved settings (with defaults filled in) and the file path"
    )
    st = ss.add_parser(
        "set",
        help="set KEY=VALUE pairs",
        description="Set one or more settings, e.g. 'settings set channel=3 center_freq_hz=921M gain=auto'. "
        "Keys: "
        + ", ".join(f.name for f in dataclasses.fields(Settings))
        + ". Use 'none' to unset a path.",
    )
    st.add_argument("pairs", nargs="+", metavar="KEY=VALUE")
    ss.add_parser("reset", help="delete the saved settings file")
    return p


# -- commands -----------------------------------------------------------
def _cmd_devices(args, out) -> int:
    from . import sdr

    devices = sdr.discover()
    if not devices:
        print(
            "no SDR devices found (use '--device sim' for the simulated source)",
            file=out,
        )
        return 0
    for d in devices:
        lo, hi = d.freq_range
        print(f"{d.id}\t{d.name}\t{lo / 1e6:g}-{hi / 1e6:g} MHz", file=out)
    return 0


def _cmd_channels(args, out) -> int:
    print(
        f"{'ch':>2} {'offset_hz':>10} {'freq_mhz':>9} {'samp_rate':>9} {'baud':>6} {'dev_hz':>7}",
        file=out,
    )
    for n in sorted(CHANNELS):
        c = CHANNELS[n]
        print(
            f"{n:>2} {c.offset_hz:>10.0f} {(CENTER_FREQ_HZ + c.offset_hz) / 1e6:>9.3f} {c.samp_rate:>9.0f} "
            f"{c.baud:>6.0f} {c.deviation:>7.0f}",
            file=out,
        )
    print(
        f"(frequencies assume the default {CENTER_FREQ_HZ / 1e6:g} MHz centre; tuned frequency = centre + offset)",
        file=out,
    )
    return 0


def _cmd_settings(args, out) -> int:
    path = settings_path()
    if args.action == "reset":
        try:
            path.unlink()
            print(f"removed {path}", file=out)
        except FileNotFoundError:
            print(f"no saved settings at {path}", file=out)
        return 0
    values, warning = load_settings_file(path)
    if warning:
        print(f"warning: {warning}", file=sys.stderr)
    if args.action == "show":
        print(
            f"# {path}"
            + ("" if path.exists() else " (not saved yet; showing defaults)"),
            file=out,
        )
        print(
            json.dumps(
                dataclasses.asdict(resolve_settings(Settings(), values)), indent=2
            ),
            file=out,
        )
        return 0
    new = dict(values)
    for pair in args.pairs:
        key, eq, raw = pair.partition("=")
        key = key.strip().lower().replace("-", "_")
        if not eq or not key:
            raise SettingsError(f"expected KEY=VALUE, got {pair!r}")
        if key in ("center_freq", "center_freq_hz"):
            key = "center_freq_hz"
            try:
                new[key] = parse_freq(raw)
            except ValueError as e:
                raise SettingsError(str(e)) from None
        else:
            parsed = settings_from_env({"EGGTIMER_" + key.upper(): raw})
            if not parsed:
                raise SettingsError(f"unknown setting {key!r}")
            new[key] = parsed[key]
    save_settings(
        resolve_settings(Settings(), new), path
    )  # validates the whole result first
    print(f"saved {path}", file=out)
    return 0


def _cmd_run(args, out, source_factory=None) -> int:
    from .contracts import FrameEvent, ReceiverState, StatusEvent
    from .receiver import LiveReceiver
    from .sinks import ConsoleSink, JsonLogSink, LastPositionStore

    cli = {
        k: getattr(args, k)
        for k in ("device", "channel", "gain", "ppm", "log_file", "position_file")
    }
    cli["center_freq_hz"] = args.center_freq
    cli["verbose"] = True if args.verbose else None
    cli["quiet"] = True if args.quiet else None
    auto_gain = (
        cli["gain"] == "auto"
    )  # D12: None means "not given", so auto needs its own handling
    if auto_gain:
        cli["gain"] = None
    settings, warning = load_settings(settings_path(), os.environ, cli)
    if auto_gain:
        settings = dataclasses.replace(settings, gain=None)
    if warning:
        print(f"warning: {warning}", file=sys.stderr)
    if settings.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s: %(message)s")

    console = ConsoleSink(out)
    if settings.quiet:

        def printer(event):
            if isinstance(event, FrameEvent):
                return
            if (
                isinstance(event, StatusEvent)
                and event.state is not ReceiverState.ERROR
            ):
                return
            console(event)

    else:
        printer = console

    kwargs = {} if source_factory is None else {"source_factory": source_factory}
    receiver = LiveReceiver(settings, **kwargs)
    closers: list[Callable[[], None]] = []
    receiver.subscribe(printer)
    if settings.log_file:
        sink = JsonLogSink(settings.log_file)
        closers.append(sink.close)
        receiver.subscribe(sink)
    if settings.position_file:
        receiver.subscribe(LastPositionStore(settings.position_file))

    done = threading.Event()
    old = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            old[sig] = signal.signal(sig, lambda *_: done.set())
    failed = False
    try:
        receiver.start()
        remaining = args.duration
        while not done.is_set() and receiver.state is not ReceiverState.ERROR:
            if remaining is not None and remaining <= 0:
                break
            step = 0.1 if remaining is None else min(0.1, remaining)
            done.wait(step)
            if remaining is not None:
                remaining -= step
    except KeyboardInterrupt:
        pass
    finally:
        failed = receiver.state is ReceiverState.ERROR
        receiver.stop()
        for close in closers:
            close()
        for sig, handler in old.items():
            signal.signal(sig, handler)
    return 1 if failed else 0


def _cmd_gui() -> int:
    try:
        gui = importlib.import_module(f"{__package__}.gui")
    except ModuleNotFoundError as e:
        if e.name != f"{__package__}.gui":
            raise
        print(
            "GUI is not available yet. Run 'eggtimer-decoder --help' for the command-line interface."
        )
        return 0
    return int(gui.main() or 0)


def main(argv: Sequence[str] | None = None, *, source_factory=None, out=None) -> int:
    """Run the application; returns the process exit code.

    ``source_factory`` and ``out`` (stdout stream) exist for tests.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    out = out or sys.stdout
    if not argv:
        return _cmd_gui()
    if argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv.insert(0, "run")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run":
            return _cmd_run(args, out, source_factory)
        if args.command == "devices":
            return _cmd_devices(args, out)
        if args.command == "channels":
            return _cmd_channels(args, out)
        if args.command == "settings":
            return _cmd_settings(args, out)
        build_parser().print_help(out)
        return 0
    except SettingsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
