# Eggtimer Decoder

Live decoder for Eggtimer / Eggfinder TX packets (Si1000, 2-FSK, 9600 baud) from an RTL-SDR
or a built-in simulated source. It has a CLI and a small tkinter GUI. You choose the device,
centre frequency (default 921 MHz) and channel 0-8; each channel is a row of parameters plus
a frequency offset from the centre (see `docs/CHANNELS.md`).

## Install

Needs [pixi](https://pixi.sh) (or [mise](https://mise.jdx.dev), which installs it). GNU Radio,
gr-osmosdr and rtl-sdr come from conda-forge. Linux x86-64 only.

    git clone <repo> && cd eggtimer-decoder
    pixi install

pixi may warn that post-link scripts were skipped; that is harmless.

## Use

No SDR is needed to try it:

    pixi run eggtimer-decoder run --device sim --channel 3 --duration 5   # simulated source
    pixi run eggtimer-decoder channels                                    # channel table
    pixi run eggtimer-decoder devices                                     # attached SDRs ("none found" if empty)
    pixi run eggtimer-decoder                                             # no arguments: open the GUI (needs a display)

Real SDR (receive only):

    pixi run eggtimer-decoder run --device rtl=0 --center-freq 921M --channel 0 --gain 30

`run` is the default when only options are given. Useful options: `--ppm`, `--log-file PATH`
(JSON lines), `--position-file PATH` (last position), `-v`, `-q`; Ctrl-C stops. Frequencies accept
`k`/`M`/`G` suffixes. Exit codes: 0 ok, 1 receiver error, 2 settings error. `--help` works on every command.

### Settings

Settings are saved between runs (the GUI uses the same file). Precedence: command-line options >
`EGGTIMER_*` environment variables (e.g. `EGGTIMER_CHANNEL=3`) > saved file > defaults.
The file path can be moved with `EGGTIMER_CONFIG`.

    pixi run eggtimer-decoder settings show
    pixi run eggtimer-decoder settings set channel=3 center_freq_hz=921M gain=auto
    pixi run eggtimer-decoder settings reset

### Tasks

Shortcuts defined in `pyproject.toml` (also available as `mise run <task>`; extra CLI args go after `--`):

| Task | Does |
|---|---|
| `pixi run decode [args]` | the CLI |
| `pixi run gui` | open the GUI |
| `pixi run sim` | simulated source, channel 0, 10 s |
| `pixi run test` | test suite (hermetic, no SDR; GUI widget tests skip without a display) |
| `pixi run hardware-test` | opt-in real RTL-SDR test (sets `EGGTIMER_HARDWARE=1`); see `docs/HARDWARE_TEST.md` |

## Architecture

All code is in `src/eggtimer_decoder/`. The core knows nothing about the interface; CLI and GUI are thin layers.

    source (sdr / sim_source) -> stream_demod -> packet framing -> NMEA reassembly -> events -> sinks / GUI
                                      signal_metrics (strength, carrier offset, present) -> events

- `contracts.py`: event types (Frame, Nmea, Position, Status, Signal), `Receiver` and `SampleSource` protocols.
- `channels.py`: the channel table `CHANNELS` (data only).
- `settings.py`: validated `Settings`, save/load, precedence merge.
- `sdr.py`: device discovery and `open_source` (gr-osmosdr); `sim_source.py`: simulated live source.
- `stream_demod.py`: streaming channel selection and 2-FSK demod (numpy); `packet.py`: frame detection, CRC, NMEA.
- `signal_metrics.py`: signal strength, carrier offset, presence.
- `receiver.py`: `LiveReceiver`, which ties it together; frequency/channel change apply live, device change restarts.
- `sinks.py`: `ConsoleSink`, `JsonLogSink`, `LastPositionStore` (plain `Event -> None` callables).
- `cli.py`: argparse entry point; `gui.py` (tkinter widgets) and `gui_model.py` (testable view model).
- `config.py`, `demod_gnuradio.py`, `rtlsdr.py`, `simulate.py`: legacy offline/capture helpers.

### Extending

- **Add or fix a channel:** edit the `CHANNELS` table only; see `docs/CHANNELS.md`.
- **Add a sink:** write a callable taking an event, dispatch on its type, ignore unknown types, and subscribe it
  to the receiver (see `sinks.py` and `cli._cmd_run`).
- **Add a source:** implement `SampleSource` (`read(n)` returns complex64, empty array = end; expose
  `sample_rate`), and give `LiveReceiver` a `source_factory` that returns it (see `sim_source.py`).
  Keep the source rate an integer multiple of the channel rate (192 kS/s).

## Known limitations

Details in `docs/DECISIONS.md` (D18, D27, D36).

- Only RTL-SDR is tested; other gr-osmosdr devices are untested. Gain is manual dB (no auto); gain/ppm are fixed
  at open time (the GUI restarts the receiver to change them). Queue overruns drop chunks silently.
- Reported carrier offset is noisy (occasionally about -35 kHz off live); signal strength includes the RTL DC spike
  and the V4's gain behaviour looks odd.
- Discovery probes `rtl=0..7` and stops at the first failure; it finds nothing if another program holds the dongle.
- Channels 1-8 are unverified copies of channel 0. No GPS fix was seen on the test module, so position parsing is
  only exercised by simulated data.

## Signal (measured at ~920.99 MHz)

Frame: `55 55 55 55 55 | 2D D4 | len | payload | CRC-16/XMODEM(len+payload)`. Payloads are <=32-byte chunks of the
module's NMEA text stream, so one sentence spans several frames; checksum-valid sentences are reassembled.
Also seen: `!0>@0>=QUASAR>\r\n` and bare `\r\n` frames (passed through).
