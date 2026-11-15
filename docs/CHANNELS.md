# Channels

Channels 0-8 live in the `CHANNELS` table in `src/eggtimer_decoder/channels.py`.
Each entry is a `Channel`: the GFSK parameters (`samp_rate`, `baud`, `deviation`) plus
`offset_hz`, the signal frequency minus the centre frequency (default centre 921 MHz).
Channel 0 is the measured setup (signal at 920.99 MHz, offset -10 kHz). Channels 1-8 are
unverified copies of channel 0.

Use `get_channel(n)` or `channel_params(n)` (returns `(RadioConfig, offset_hz)`); anything
outside 0..8 raises `ValueError`. Decoding code never needs to change when a channel does.

## Adding a verified channel

1. Capture the transmitter with an RTL-SDR at a known centre frequency (921 MHz).
2. Measure the carrier frequency (offset = carrier - centre), baud rate and peak deviation.
3. Check that frames decode with those values (valid CRC, checksum-valid NMEA).
4. Edit the channel's entry in `CHANNELS`: set `offset_hz`, any differing parameters, and
   `verified=True`. Only change the data; do not touch decoder code.
5. Run `pixi run test` and note the measurement (date, source, SNR) in your PR.
