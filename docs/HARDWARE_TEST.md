# Manual hardware test: real SDR, 921 MHz centre, channel 0

Receive-only: nothing here transmits. Needs an RTL-SDR (tested: RTL-SDR Blog V4), an antenna for ~921 MHz,
and ideally an Eggfinder TX powered on within range (channel 0 signal at 920.99 MHz, i.e. -10 kHz from the centre).

## Setup

1. `pixi install`. Plug in the dongle. Close anything else using it (rtl_tcp, SDR++, gqrx); a claimed device
   shows as `usb_claim_interface error -6` and discovery returns nothing.
2. If the kernel DVB driver grabs the dongle, blacklist `dvb_usb_rtl28xxu` or add the usual rtl-sdr udev rules.

## Steps

1. Discovery: `pixi run eggtimer-decoder devices`. Expect one line such as
   `rtl=0  rtl:0  0-1766 MHz`. "no SDR devices found" means the dongle is missing, busy, or lacks permissions.
2. Run: `pixi run eggtimer-decoder run --device rtl=0 --center-freq 921M --channel 0 --duration 60`
   (omit `--device` for the first SDR; add `--gain 30` or `--ppm N` to adjust; `-v` for debug logging).
3. Expected with the transmitter on: `[running]` status, then `frame len=...` lines, NMEA sentences
   (`$GNGGA...`, `$GNRMC...`) and `=> Position(...)`. With it off: only the status lines and no `ERROR`.
   Exit code 0 (`echo $?`); 1 means an `ERROR` status was shown.
4. Live changes (GUI, or a script using `LiveReceiver.set_channel/set_frequency`): moving the centre by a few
   kHz should keep decoding if the carrier stays inside the channel passband; other channels are copies of
   channel 0 until measured (see `docs/CHANNELS.md`).
5. Stop with Ctrl-C; the dongle must be released (rerun step 1 immediately and it should still be listed).

## Automated opt-in test

`EGGTIMER_HARDWARE=1 pixi run pytest -m hardware -s tests/test_hardware.py`

It checks discovery finds a device covering 921 MHz, then runs the receiver on channel 0 for 6 s, requiring
no `ERROR` status and at least one signal event. It does not require a frame. Without `EGGTIMER_HARDWARE=1`
both tests are skipped with the reason shown by `pytest -rs`.

## Troubleshooting

- `Segmentation fault` on open: historically the osmosdr sink block was garbage collected (fixed in S11).
- Saturated or weak signal: try `--gain` between 10 and 40; the RTL DC spike sits at the centre, 10 kHz from channel 0.
- The source rate is chosen as an integer multiple of the channel rate (192 kS/s); the RTL-SDR Blog V4
  offers 1.92 MS/s, so that is what gets used.
