"""DSP backend that needs nothing but numpy. Used on Android, where pedalboard and scipy
are not available. Same interface as ``fx_native``.

Everything is vectorised (FFT convolution, phase vocoder, delay lines) so it stays fast
in pure Python. Long signals are processed in blocks to keep memory use low on phones.
"""
from __future__ import annotations

import numpy as np

NAME = "numpy"


# ---------------------------------------------------------------- helpers
def _pow2(n: float) -> int:
    return 1 << int(np.ceil(np.log2(max(2.0, float(n)))))


def convolve(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    """Full linear convolution along the last axis (overlap-add, block by block).

    ``h`` is 1-D (same filter for every channel) or shaped like ``x`` without its length.
    Returns ``len(x) + len(h) - 1`` samples.
    """
    x = np.asarray(x, dtype=np.float32)
    h = np.asarray(h, dtype=np.float32)
    m, n = h.shape[-1], x.shape[-1]
    nfft = _pow2(max(4 * m, 1 << 15))
    step = nfft - m + 1
    spec_h = np.fft.rfft(h, nfft)
    out = np.zeros(x.shape[:-1] + (n + m - 1,), dtype=np.float32)
    for start in range(0, n, step):
        seg = x[..., start:start + step]
        y = np.fft.irfft(np.fft.rfft(seg, nfft) * spec_h, nfft)[..., : seg.shape[-1] + m - 1]
        out[..., start:start + y.shape[-1]] += y
    return out


def _butter_mag(f: np.ndarray, kind: str, freq, order: int) -> np.ndarray:
    """Magnitude response of a Butterworth filter."""
    def lp(fc):
        return 1.0 / np.sqrt(1.0 + (f / fc) ** (2 * order))

    def hp(fc):
        r = f / fc
        return r ** order / np.sqrt(1.0 + r ** (2 * order))

    if kind == "lowpass":
        return lp(freq)
    if kind == "highpass":
        return hp(freq)
    if kind == "bandpass":
        return hp(freq[0]) * lp(freq[1])
    raise ValueError(kind)


def filt(x: np.ndarray, sr: int, kind: str, freq, order: int = 2) -> np.ndarray:
    """Butterworth-shaped low/high/band-pass (linear phase) along the last axis."""
    x = np.asarray(x)
    lowest = float(np.min(np.atleast_1d(freq)))
    length = int(min(1 << 16, max(1 << 11, _pow2(8 * sr / lowest))))
    f = np.fft.rfftfreq(length, 1.0 / sr)
    h = np.fft.irfft(_butter_mag(f, kind, freq, order), length)
    h = np.roll(h, length // 2) * np.hanning(length)
    y = convolve(x, h)
    d = length // 2
    return y[..., d:d + x.shape[-1]].astype(x.dtype if x.dtype.kind == "f" else np.float64)


def _sinc_table(cutoff: float, width: int, phases: int) -> np.ndarray:
    """Windowed-sinc kernels for ``phases`` fractional positions, shape (phases + 1, 2 * width)."""
    offsets = np.arange(-width + 1, width + 1)
    frac = np.arange(phases + 1)[:, None] / phases
    dist = frac - offsets[None, :]
    arg = np.clip(dist / width, -1.0, 1.0)
    window = 0.42 + 0.5 * np.cos(np.pi * arg) + 0.08 * np.cos(2 * np.pi * arg)  # Blackman
    return (cutoff * np.sinc(cutoff * dist) * window).astype(np.float32)


def resample_to(x: np.ndarray, new_len: int, half_taps: int = 16, phases: int = 4096) -> np.ndarray:
    """Band-limited resampling of the last axis to exactly ``new_len`` samples.

    Polyphase windowed-sinc interpolation (Blackman window, ``half_taps`` zero crossings per
    side, kernels precomputed for ``phases`` fractional positions), done block by block so
    memory stays small even for long tracks.
    """
    x = np.asarray(x, dtype=np.float32)
    n = x.shape[-1]
    if n == new_len:
        return x.copy()
    lead = x.shape[:-1]
    flat = x.reshape(-1, n)
    ratio = new_len / n                      # output samples per input sample
    cutoff = min(1.0, ratio)                 # anti-alias when shrinking
    width = int(np.ceil(half_taps / cutoff))  # taps per side, in input samples
    table = _sinc_table(cutoff, width, phases)
    padded = np.pad(flat, ((0, 0), (width, width + 1)))
    offsets = np.arange(-width + 1, width + 1) + width
    out = np.empty((flat.shape[0], new_len), dtype=np.float32)
    block = max(1024, (1 << 20) // (2 * width))
    for start in range(0, new_len, block):
        stop = min(new_len, start + block)
        pos = np.arange(start, stop) / ratio
        base = np.floor(pos).astype(np.int64)
        kernel = table[np.rint((pos - base) * phases).astype(np.int64)]   # (block, taps)
        idx = base[:, None] + offsets[None, :]
        for c in range(flat.shape[0]):
            out[c, start:stop] = np.einsum("ij,ij->i", padded[c][idx], kernel)
    return out.reshape(lead + (new_len,))


def resample(x: np.ndarray, up: int, down: int) -> np.ndarray:
    n = np.asarray(x).shape[-1]
    return resample_to(x, int(np.ceil(n * up / down)))


def vinyl_speed(audio: np.ndarray, speed: float) -> np.ndarray:
    return resample_to(audio, max(1, int(round(audio.shape[-1] / speed))))


# ---------------------------------------------------------------- equalizer
def _biquad_response(w: np.ndarray, kind: str, f0: float, sr: int, gain_db: float, q: float) -> np.ndarray:
    """Complex response of an RBJ-cookbook shelf/peak biquad at angular frequencies ``w``."""
    a = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * f0 / sr
    cw, sw = np.cos(w0), np.sin(w0)
    alpha = sw / (2 * q)
    if kind == "peak":
        b = [1 + alpha * a, -2 * cw, 1 - alpha * a]
        den = [1 + alpha / a, -2 * cw, 1 - alpha / a]
    else:
        sq = 2 * np.sqrt(a) * alpha
        if kind == "lowshelf":
            b = [a * ((a + 1) - (a - 1) * cw + sq), 2 * a * ((a - 1) - (a + 1) * cw),
                 a * ((a + 1) - (a - 1) * cw - sq)]
            den = [(a + 1) + (a - 1) * cw + sq, -2 * ((a - 1) + (a + 1) * cw), (a + 1) + (a - 1) * cw - sq]
        else:  # highshelf
            b = [a * ((a + 1) + (a - 1) * cw + sq), -2 * a * ((a - 1) + (a + 1) * cw),
                 a * ((a + 1) + (a - 1) * cw - sq)]
            den = [(a + 1) - (a - 1) * cw + sq, 2 * ((a - 1) - (a + 1) * cw), (a + 1) - (a - 1) * cw - sq]
    z1 = np.exp(-1j * w)
    z2 = z1 * z1
    return (b[0] + b[1] * z1 + b[2] * z2) / (den[0] + den[1] * z1 + den[2] * z2)


def eq(audio: np.ndarray, sr: int, bands: list[float], gains: list[float]) -> np.ndarray:
    length = 1 << 15
    w = 2 * np.pi * np.fft.rfftfreq(length)
    resp = np.ones_like(w, dtype=np.complex128)
    for i, (freq, gain) in enumerate(zip(bands, gains)):
        if abs(gain) < 0.05:
            continue
        if i == 0:
            resp *= _biquad_response(w, "lowshelf", freq * 1.4, sr, gain, 0.707)
        elif i == len(bands) - 1:
            resp *= _biquad_response(w, "highshelf", min(freq * 0.7, sr * 0.45), sr, gain, 0.707)
        else:
            resp *= _biquad_response(w, "peak", freq, sr, gain, 1.2)
    h = np.fft.irfft(resp, length)  # causal impulse response of the IIR chain
    fade = length // 4
    h[-fade:] *= np.hanning(2 * fade)[fade:]
    return convolve(audio, h)[..., : audio.shape[-1]]


# ---------------------------------------------------------------- time-stretch
def _phase_vocoder(audio: np.ndarray, out_len: int, n_fft: int = 4096, hop: int = 1024) -> np.ndarray:
    """Change the length of ``audio`` (channels, n) to ``out_len`` without changing pitch.

    Phase vocoder with identity phase locking (Laroche & Dolson), which keeps the
    sound much less "phasey" than a plain phase vocoder.
    """
    chans, n = audio.shape
    rate = n / out_len  # analysis hop / synthesis hop
    win = np.hanning(n_fft).astype(np.float32)
    x = np.pad(audio.astype(np.float32), ((0, 0), (n_fft, n_fft + int(n_fft * rate) + hop)))
    frames = int(np.ceil(out_len / hop)) + 2
    out = np.zeros((chans, frames * hop + n_fft), dtype=np.float32)
    norm = np.zeros(frames * hop + n_fft, dtype=np.float32)
    bins = n_fft // 2 + 1
    idx = np.arange(bins)

    prev_pos = None
    prev_phase = None
    out_phase = None
    for k in range(frames):
        pos = int(round(k * hop * rate))
        spec = np.fft.rfft(x[:, pos:pos + n_fft] * win)
        mag = np.abs(spec)
        phase = np.angle(spec)
        if out_phase is None:
            out_phase = phase.copy()
        else:
            dpos = max(pos - prev_pos, 1)
            expected = 2 * np.pi * idx * dpos / n_fft
            dev = phase - prev_phase - expected
            dev -= 2 * np.pi * np.round(dev / (2 * np.pi))
            inst = (expected + dev) / dpos  # true frequency in radians per sample
            advanced = out_phase + inst * hop
            # identity phase locking: bins follow the phase of their nearest spectral peak
            m = mag.mean(axis=0)
            peaks = np.flatnonzero((m[1:-1] > m[:-2]) & (m[1:-1] >= m[2:])) + 1
            if len(peaks):
                mids = (peaks[:-1] + peaks[1:]) // 2
                owner = peaks[np.searchsorted(mids, idx, side="right")]
                out_phase = advanced[:, owner] + (phase - phase[:, owner])
            else:
                out_phase = advanced
        prev_pos, prev_phase = pos, phase
        frame = np.fft.irfft(mag * np.exp(1j * out_phase), n_fft).astype(np.float32) * win
        s = k * hop
        out[:, s:s + n_fft] += frame
        norm[s:s + n_fft] += win * win
    out /= np.maximum(norm, 1e-3)
    start = int(round(n_fft / rate))  # the leading padding, in output time
    return out[:, start:start + out_len]


def time_stretch(audio: np.ndarray, sr: int, speed: float, semitones: float) -> np.ndarray:
    """Tempo x ``speed`` (>1 = faster) and pitch + ``semitones``, independently."""
    n = audio.shape[-1]
    target = max(1, int(round(n / speed)))
    ratio = 2 ** (semitones / 12)
    if abs(ratio - 1) < 1e-4:
        return _phase_vocoder(audio, target)
    # stretch to the length that, once resampled by the pitch ratio, gives the target length
    stretched = _phase_vocoder(audio, max(1, int(round(target * ratio))))
    return resample_to(stretched, target)


# ---------------------------------------------------------------- effects
def _distortion(x: np.ndarray, drive_db: float) -> np.ndarray:
    return np.tanh(x * 10 ** (drive_db / 20)).astype(np.float32)


def _bitcrush(x: np.ndarray, bits: float) -> np.ndarray:
    step = 2.0 / (2 ** bits)
    return (np.round(x / step) * step).astype(np.float32)


def _chorus(x: np.ndarray, sr: int, mix: float, rate: float = 0.8, centre_ms: float = 8.0,
            depth_ms: float = 4.0) -> np.ndarray:
    n = x.shape[-1]
    t = np.arange(n, dtype=np.float64)
    out = np.empty_like(x)
    for ch in range(x.shape[0]):
        lfo = np.sin(2 * np.pi * rate * t / sr + ch * np.pi / 2)
        delay = (centre_ms + depth_ms * lfo) * sr / 1000.0
        wet = np.interp(t - delay, t, x[ch], left=0.0)
        out[ch] = (1 - mix) * x[ch] + mix * wet
    return out


def _delay(x: np.ndarray, sr: int, seconds: float, feedback: float, mix: float) -> np.ndarray:
    d = max(1, int(round(seconds * sr)))
    n = x.shape[-1]
    wet = np.zeros_like(x)
    gain, k = 1.0, 1
    while gain > 1e-3 and k * d < n:
        wet[:, k * d:] += gain * x[:, : n - k * d]
        gain *= feedback
        k += 1
    return ((1 - mix) * x + mix * wet).astype(np.float32)


def _reverb_ir(sr: int, room_size: float, damping: float, chans: int, seed: int = 7) -> np.ndarray:
    """Synthetic room impulse response: early reflections + exponentially decaying noise.

    High frequencies decay faster than low ones (``damping``), like real rooms.
    """
    rng = np.random.default_rng(seed)
    rt60 = 0.8 + 3.9 * room_size ** 3  # seconds; fitted to pedalboard's Reverb
    length = int(min(rt60 * 1.1, 4.0) * sr)
    t = np.arange(length) / sr
    predelay = int(0.012 * sr)
    ir = np.zeros((chans, length), dtype=np.float32)
    for ch in range(chans):
        noise = rng.standard_normal(length)
        low = filt(noise, sr, "lowpass", 2500)
        high = noise - low
        env_low = np.exp(-6.91 * t / rt60)
        env_high = np.exp(-6.91 * t / (rt60 * (1 - 0.7 * damping)))
        tail = low * env_low + high * env_high
        tail[:predelay] = 0
        # a few early reflections
        for _ in range(8):
            p = int(rng.uniform(0.005, 0.08) * sr)
            tail[p] += rng.uniform(-1, 1) * 3
        ir[ch] = tail
    ir /= np.sqrt(np.sum(ir ** 2, axis=1, keepdims=True)) + 1e-12  # unit energy
    return ir


def _reverb(x: np.ndarray, sr: int, room_size: float, damping: float, wet: float, dry: float) -> np.ndarray:
    ir = _reverb_ir(sr, room_size, damping, x.shape[0])
    tail = convolve(x, ir)[..., : x.shape[-1]]
    # unit-energy IR -> scale so the wet level matches pedalboard's Reverb
    return (dry * x + wet * (1.5 + 0.65 * room_size) * tail).astype(np.float32)


def effects(audio: np.ndarray, sr: int, s) -> np.ndarray:
    out = audio.astype(np.float32)
    if s.distortion_db > 0.1:
        out = _distortion(out, s.distortion_db)
    if s.lofi_amount > 0.01:
        a = s.lofi_amount
        out = _bitcrush(out, 16 - 9 * a)
        out = filt(out, sr, "lowpass", 16000 - 12500 * a, order=1).astype(np.float32)
    if s.chorus_mix > 0.01:
        out = _chorus(out, sr, s.chorus_mix)
    if s.echo_mix > 0.01:
        out = _delay(out, sr, s.echo_delay, s.echo_feedback, s.echo_mix)
    if s.reverb_mix > 0.01:
        out = _reverb(out, sr, s.reverb_size, 0.45, s.reverb_mix * 0.6, 1.0 - s.reverb_mix * 0.4)
    return out


# ---------------------------------------------------------------- limiter
def limiter(audio: np.ndarray, sr: int, threshold_db: float = -1.0, release_ms: float = 150) -> np.ndarray:
    """Look-ahead peak limiter: instant attack, exponential release."""
    thr = 10 ** (threshold_db / 20)
    n = audio.shape[-1]
    if n == 0:
        return audio
    block = 64
    nb = -(-n // block)
    peak = np.abs(audio).max(axis=0)
    peak = np.pad(peak, (0, nb * block - n)).reshape(nb, block).max(axis=1)
    peak = np.maximum(peak, np.append(peak[1:], 0.0))  # look one block ahead
    need = np.minimum(1.0, thr / np.maximum(peak, 1e-9))
    coef = float(np.exp(-block / (release_ms / 1000.0 * sr)))
    gain = np.empty(nb)
    cur = 1.0
    for i, g in enumerate(need.tolist()):
        cur = g if g < cur else g + (cur - g) * coef
        gain[i] = cur
    per_sample = np.interp(np.arange(n), np.arange(nb) * block, gain)
    # smooth between blocks, but never above what the block itself needs
    per_sample = np.minimum(per_sample, np.repeat(gain, block)[:n])
    return (audio * per_sample).astype(np.float32)
