<div align="center">

# 🥚 Eggtimer Decoder

**Live decoder for Eggtimer / Eggfinder TX packets**
_Si1000 · 2-FSK · 9600 baud — from an RTL-SDR or a built-in simulated source_

<img src="assets/demo.png" alt="Eggtimer Decoder GUI showing receiver status and a live list of decoded NMEA packets" width="720">

</div>

---

## ✨ Overview

Eggtimer Decoder comes with a CLI and a small GUI.

Works with any SDR that [GNU Radio OsmoSDR](https://github.com/osmocom/gr-osmosdr) supports (recommended: [RTL-SDR](https://www.rtl-sdr.com/buy-rtl-sdr-dvb-t-dongles/)).

## 📦 Install

Needs [mise](https://mise.jdx.dev). This will handle all the dependency management for you (hopefully).
**Linux x86-64 only.**

```sh
git clone <repo> && cd eggtimer-decoder
mise trust
mise install
```

> [!NOTE]
> The first `mise run` below sets up the rest (a one-off download, so it takes a while).

## 🚀 Use

No SDR is needed to try it. Arguments for the decoder go after `--`:

```sh
mise run decode -- run --device sim --channel 3 --duration 5   # simulated source
mise run decode -- channels                                    # channel table
mise run decode -- devices                                     # attached SDRs ("none found" if empty)
mise run gui                                                   # open the GUI (needs a display)
```

Real SDR (receive only):

```sh
mise run decode -- run --device rtl=0 --center-freq 921M --channel 0 --gain 30
```

`run` is the default when only options are given. Useful options:

| Option                 | Purpose                 |
| ---------------------- | ----------------------- |
| `--ppm`                | frequency correction    |
| `--log-file PATH`      | write JSON lines        |
| `--position-file PATH` | write the last position |
| `-v`, `-q`             | more / less output      |

Ctrl-C stops. Frequencies accept `k`/`M`/`G` suffixes. `--help` works on every command.

**Exit codes:** `0` ok · `1` receiver error · `2` settings error

### ⚙️ Settings

Settings are saved between runs (the GUI uses the same file). Precedence, highest first:

1. command-line options
2. `EGGTIMER_*` environment variables (e.g. `EGGTIMER_CHANNEL=3`)
3. saved file
4. defaults

The file path can be moved with `EGGTIMER_CONFIG`.

```sh
mise run decode -- settings show
mise run decode -- settings set channel=3 center_freq_hz=921M gain=auto
mise run decode -- settings reset
```

### 🧰 Tasks

Run `mise tasks` to list them.

| Task                        | Does                                                                           |
| --------------------------- | ------------------------------------------------------------------------------ |
| `mise run decode -- [args]` | the CLI                                                                        |
| `mise run gui`              | open the GUI                                                                   |
| `mise run sim`              | simulated source, channel 0, 10 s                                              |
| `mise run test`             | test suite (hermetic, no SDR; GUI widget tests run offscreen)                  |

## 🔧 Extending

- **Add or fix a channel:** edit the `_DEFINED` list only; see [`docs/CHANNELS.md`](docs/CHANNELS.md).
- **Add a sink:** write a callable taking an event, dispatch on its type, ignore unknown types, and subscribe it
  to the receiver (see `sinks.py` and `cli._cmd_run`).
- **Add a source:** implement `SampleSource` (`read(n)` returns complex64, empty array = end; expose
  `sample_rate`), and give `LiveReceiver` a `source_factory` that returns it (see `sim_source.py`).
  Keep the source rate an integer multiple of the channel rate (192 kS/s).
