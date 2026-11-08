"""
Phone audio <-> Gemini Live audio.

Twilio Media Streams carry G.711 μ-law, 8 kHz, mono (base64 in JSON frames).
Gemini Live takes 16-bit PCM at 16 kHz and answers with 16-bit PCM at 24 kHz.

Pure Python on purpose: `audioop` is deprecated and gone in Python 3.13, and a
call moves only ~8 000 samples/s each way, which plain loops handle easily.
"""
from __future__ import annotations

from array import array
import sys

_BIAS = 0x84
_CLIP = 32635


def _ulaw_to_linear(u: int) -> int:
    u = ~u & 0xFF
    t = ((u & 0x0F) << 3) + _BIAS
    t <<= (u & 0x70) >> 4
    return (_BIAS - t) if (u & 0x80) else (t - _BIAS)


def _linear_to_ulaw(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0
    if sample < 0:
        sample = -sample
    sample = min(sample, _CLIP) + _BIAS
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not (sample & mask):
        exponent -= 1
        mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


_DECODE = [_ulaw_to_linear(u) for u in range(256)]
# Indexed by the int16 sample as an unsigned 16-bit value.
_ENCODE = bytes(_linear_to_ulaw(s - 65536 if s >= 32768 else s) for s in range(65536))
_LITTLE = sys.byteorder == "little"


def _pcm_array(pcm: bytes) -> array:
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not _LITTLE:
        samples.byteswap()
    return samples


def _pcm_bytes(samples: array) -> bytes:
    if not _LITTLE:
        samples = array("h", samples)
        samples.byteswap()
    return samples.tobytes()


def ulaw8k_to_pcm16k(ulaw: bytes) -> bytes:
    """Twilio → Gemini: μ-law 8 kHz → PCM16 LE 16 kHz (linear interpolation)."""
    out = array("h")
    prev = None
    for byte in ulaw:
        cur = _DECODE[byte]
        out.append(cur if prev is None else (prev + cur) // 2)
        out.append(cur)
        prev = cur
    return _pcm_bytes(out)


class Downsampler24kTo8k:
    """Gemini → Twilio: PCM16 LE 24 kHz → μ-law 8 kHz.

    Averages each group of 3 samples (a cheap low-pass, fine for a phone line).
    Chunks don't arrive in multiples of 3 samples, so the remainder carries over;
    reset() drops it when the model is interrupted.
    """

    def __init__(self) -> None:
        self._carry = array("h")
        self._odd_byte = b""

    def reset(self) -> None:
        self._carry = array("h")
        self._odd_byte = b""

    def convert(self, pcm24k: bytes) -> bytes:
        data = self._odd_byte + pcm24k
        self._odd_byte = data[-1:] if len(data) % 2 else b""
        samples = self._carry + _pcm_array(data)
        usable = len(samples) - (len(samples) % 3)
        self._carry = samples[usable:]
        return bytes(
            _ENCODE[((samples[i] + samples[i + 1] + samples[i + 2]) // 3) & 0xFFFF]
            for i in range(0, usable, 3)
        )


def pcm16_to_ulaw(pcm: bytes) -> bytes:
    """PCM16 LE (any rate) → μ-law, sample by sample. Used to feed recorded audio in tests."""
    return bytes(_ENCODE[s & 0xFFFF] for s in _pcm_array(pcm))


def ulaw_energy(ulaw: bytes) -> float:
    """Mean absolute amplitude of a μ-law frame (0 = silence, ~32 000 = max).
    Phone-line silence sits well under 200; speech is usually over 1 000."""
    if not ulaw:
        return 0.0
    return sum(abs(_DECODE[b]) for b in ulaw) / len(ulaw)


class LineLevel:
    """Inbound line level, learned during the call.

    A real landline call (2026-11-07) came in so quiet that a fixed 900 threshold
    never saw the customer speak: no barge-in, and the silence nudge fired while
    they were answering. The threshold now follows the line: a few times its own
    noise floor, between MIN_SPEECH and the configured ceiling. Also keeps the
    level distribution for the call summary (numbers only — no audio, no PII)."""

    MIN_SPEECH = 250.0

    def __init__(self, ceiling: float = 900.0):
        self.ceiling = ceiling
        self.floor = 100.0          # running estimate of the line's background level
        self._levels = array("f")

    @property
    def threshold(self) -> float:
        return max(self.MIN_SPEECH, min(self.ceiling, self.floor * 4))

    def is_speech(self, energy: float) -> bool:
        self._levels.append(energy)
        speech = energy >= self.threshold
        if not speech:              # only background frames move the floor
            self.floor += (energy - self.floor) * 0.02
        return speech

    def stats(self) -> dict:
        if not self._levels:
            return {}
        ordered = sorted(self._levels)
        pick = lambda q: round(ordered[min(len(ordered) - 1, int(q * len(ordered)))])  # noqa: E731
        return {"frames": len(ordered), "p10": pick(0.10), "p50": pick(0.50), "p90": pick(0.90),
                "p99": pick(0.99), "floor": round(self.floor), "threshold": round(self.threshold),
                "speechFrames": sum(1 for e in ordered if e >= self.threshold)}
