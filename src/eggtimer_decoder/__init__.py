import argparse

import numpy as np

from .config import RadioConfig
from .packet import NmeaAssembler, parse_gga
from .simulate import SAMPLE_NMEA, make_capture, nmea_payloads


def main() -> None:
    ap = argparse.ArgumentParser(description="Eggfinder TX decoder")
    ap.add_argument("--source", choices=["sim", "rtlsdr"], default="sim")
    ap.add_argument("--snr", type=float, default=20.0, help="simulated SNR in dB")
    ap.add_argument("--freq-offset", type=float, default=0.0, help="simulated carrier offset in Hz")
    ap.add_argument("--freq", type=float, default=920.99e6, help="RTL-SDR centre freq (Hz)")
    ap.add_argument("--seconds", type=float, default=10.0, help="RTL-SDR capture length")
    ap.add_argument("--gain", type=float, default=30.0, help="RTL-SDR gain (dB)")
    args = ap.parse_args()

    from .demod_gnuradio import decode

    cfg = RadioConfig()
    if args.source == "rtlsdr":
        from .rtlsdr import capture

        iq = capture(args.freq, args.seconds, cfg, args.gain)
        print(f"[rtlsdr] {len(iq)} samples @ {args.freq / 1e6:.3f} MHz, mean power {np.mean(np.abs(iq) ** 2):.3e}")
    else:
        iq = make_capture(nmea_payloads(), cfg, snr_db=args.snr, freq_offset=args.freq_offset)
        print(f"[mock] {len(iq)} samples")

    frames = decode(iq, cfg)
    asm = NmeaAssembler()
    sentences: list[str] = []
    for f in frames:
        print(f"frame len={len(f):2d} {f!r}")
        sentences += asm.feed(f)
    print(f"-- {len(frames)} frames, {len(sentences)} NMEA sentences")
    for line in sentences:
        pos = parse_gga(line)
        print(line, f"=> {pos}" if pos else "")
