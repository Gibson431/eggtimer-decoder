import numpy as np
import pytest

from eggtimer_decoder.packet import (
    MAX_PAYLOAD,
    FrameDetector,
    build_frame,
    bytes_to_bits,
    find_frames,
)

PAYLOADS = [b"hello", b"$GPGGA,1*00\r\n", bytes(range(1, 33)), b"x", b"\r\n"]


def _stream(invert_mask=None, seed=0):
    rng = np.random.default_rng(seed)
    parts, expected = [], []
    for i, p in enumerate(PAYLOADS):
        noise = rng.integers(0, 2, int(rng.integers(0, 40)), dtype=np.uint8)
        fb = bytes_to_bits(build_frame(p))
        if invert_mask and invert_mask[i]:
            fb = fb ^ 1
        parts += [noise, fb]
        expected.append(p)
    return np.concatenate(parts), expected


def _feed_chunks(bits, sizes):
    det, out, i = FrameDetector(), [], 0
    for n in sizes:
        out += det.feed(bits[i : i + n])
        i += n
        if i >= len(bits):
            break
    return out


def _chunked(bits, size):
    return _feed_chunks(bits, [size] * (len(bits) // size + 1))


@pytest.mark.parametrize("mask", [None, [1] * 5, [0, 1, 0, 1, 1]])
def test_chunking_matches_batch(mask):
    bits, expected = _stream(mask)
    ref = find_frames(bits)
    assert ref == expected
    for size in (1, 7, 8, 13, 100, len(bits)):
        assert _chunked(bits, size) == ref
    rng = np.random.default_rng(1)
    for _ in range(5):
        sizes = rng.integers(1, 90, len(bits)).tolist()
        assert _feed_chunks(bits, sizes) == ref


def test_frame_split_at_every_boundary():
    bits = bytes_to_bits(build_frame(b"split me"))
    for cut in range(1, len(bits)):
        det = FrameDetector()
        assert det.feed(bits[:cut]) == []
        assert det.feed(bits[cut:]) == [b"split me"]


def test_state_is_bounded():
    det = FrameDetector()
    bits, _ = _stream()
    for _ in range(50):
        det.feed(bits)
    max_bits = 16 + 8 * (MAX_PAYLOAD + 3) + len(bits)
    assert len(det._buf) <= max_bits
    det.feed(np.zeros(10_000, dtype=np.uint8))
    assert len(det._buf) < 16


def test_reset_drops_partial_frame():
    bits = bytes_to_bits(build_frame(b"abc"))
    det = FrameDetector()
    det.feed(bits[:70])
    det.reset()
    assert det.feed(bits[70:]) == []
