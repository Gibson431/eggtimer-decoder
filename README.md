# Eggtimer Decoder

Decodes Eggtimer (Si1000, 2-FSK) packets from complex baseband IQ.
Input is a simulated capture by default; `--source rtlsdr` reads from an RTL-SDR (default 920.99 MHz).

    mise install          # installs pixi
    mise run decode       # decode a simulated capture
    mise run test    # pytest
    mise run decode --source rtlsdr --freq 433.0e6 --seconds 10   # extra args go to the app

The mise tasks call `pixi run` directly (tasks `decode` and `test` are defined in `pyproject.toml`).
GNU Radio comes from conda-forge via pixi (config in `pyproject.toml` under `[tool.pixi]`).

## Signal (measured from a live capture at ~920.99 MHz)
2-FSK, 9600 baud, ~±35 kHz deviation (`config.py`). Frames (`packet.py`):

    55 55 55 55 55 | 2D D4 | len | payload | CRC-16/XMODEM(len+payload)

Payloads are <=32-byte chunks of the module's NMEA text stream, so one sentence spans
several frames; `NmeaAssembler` reassembles them and keeps checksum-valid sentences.
Also seen: `!0>@0>=QUASAR>\r\n` and bare `\r\n` frames (passed through, not interpreted).
The module had no GPS fix during testing (empty GGA/RMC fields), so position parsing
(`parse_gga`) is only exercised by simulated data.
