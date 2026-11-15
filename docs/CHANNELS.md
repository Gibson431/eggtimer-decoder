# Channels

Channels live in the `_DEFINED` list in `src/eggtimer_decoder/channels.py`; `CHANNELS`,
`CHANNEL_NUMBERS`, the CLI choices, the GUI drop-down and validation all derive from it.
Each entry is a `Channel`: the GFSK parameters (`samp_rate`, `baud`, `deviation`) plus
`offset_hz`, added to the centre frequency (default 921 MHz) to give the frequency the SDR is
tuned to for that channel (the signal then sits at baseband 0 Hz). It changes nothing else.
Only channels listed there are selectable. Channel 0 is the measured setup (signal at
920.985 MHz, offset -15 kHz); channels 1 to 7 have not been measured, spaced 250 kHz apart.

Use `get_channel(n)` or `channel_params(n)` (returns `(RadioConfig, offset_hz)`); anything
any number not in the table raises `ValueError`. Decoding code never needs to change when a channel does.

## Adding a channel

Append `Channel(number=N, offset_hz=...)` to `_DEFINED` (numbers must be unique). Nothing
else needs to change.

## Measuring a channel

1. Capture the transmitter with an RTL-SDR at a known centre frequency (921 MHz).
2. Measure the carrier frequency (offset = carrier - centre), baud rate and peak deviation.
3. Check that frames decode with those values (valid CRC, checksum-valid NMEA).
4. Edit the channel's entry in `_DEFINED`: set `offset_hz` and any differing parameters.
   Only change the data; do not touch decoder code.
5. Run `pixi run test` and note the measurement (date, source, SNR) in your PR.
