from eggtimer_decoder.config import RadioConfig
from eggtimer_decoder.packet import (
    NmeaAssembler,
    build_frame,
    bytes_to_bits,
    crc16,
    find_frames,
    parse_gga,
)
from eggtimer_decoder.simulate import SAMPLE_NMEA, make_capture, nmea_payloads

CFG = RadioConfig()


def test_crc_matches_captured_frame():
    # Real frame from a live capture: len=32 payload, crc b81a
    body = bytes([32]) + b"$GPTXT,01,01,02,ANTSTATUS=OPEN*2"
    assert crc16(body) == 0xB81A


def test_frame_roundtrip_both_polarities():
    bits = bytes_to_bits(build_frame(b"hello"))
    assert find_frames(bits) == [b"hello"]
    assert find_frames(bits ^ 1) == [b"hello"]


def test_bad_crc_rejected():
    frame = bytearray(build_frame(b"hello world"))
    frame[10] ^= 0xFF
    assert find_frames(bytes_to_bits(bytes(frame))) == []


def test_nmea_reassembly_across_frames():
    asm = NmeaAssembler()
    lines = [l for p in nmea_payloads() for l in asm.feed(p)]
    assert "".join(l + "\r\n" for l in lines) == SAMPLE_NMEA


def test_parse_gga():
    pos = parse_gga("$GNGGA,014049.000,3520.8200,N,11748.4800,W,1,08,1.1,1500.5,M,-33.0,M,,*00")
    assert pos is not None
    assert (round(pos.lat, 4), round(pos.lon, 4), pos.alt_m, pos.satellites) == (35.347, -117.808, 1500.5, 8)
    assert parse_gga("$GNGGA,014048.084,,,,,0,0,,,M,,M,,*58") is None


def test_gnuradio_demod_with_offset_and_noise():
    from eggtimer_decoder.demod_gnuradio import decode

    payloads = nmea_payloads()
    iq = make_capture(payloads, CFG, snr_db=15, freq_offset=1500)
    assert decode(iq, CFG) == payloads


def test_gnuradio_end_to_end_nmea():
    from eggtimer_decoder.demod_gnuradio import decode

    asm = NmeaAssembler()
    lines = [l for f in decode(make_capture(nmea_payloads(), CFG), CFG) for l in asm.feed(f)]
    assert len(lines) == 3
