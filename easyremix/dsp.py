"""The remix engine: speed/pitch, equalizer, effects, ambience and volume.

All audio is float32 numpy arrays shaped (channels, samples).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .backend import fx as _fx
from .theory import speed_for_semitones

# ---------------------------------------------------------------- equalizer
EQ_BANDS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
EQ_LABELS = ["31", "62", "125", "250", "500", "1k", "2k", "4k", "8k", "16k"]

EQ_PRESETS: dict[str, list[float]] = {
    "Flat":          [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    "Bass Boost":    [7, 6, 4.5, 2, 0, 0, 0, 0, 0, 0],
    "Super Bass":    [10, 8, 6, 3, 0, -1, -1, 0, 0, 0],
    "Treble Boost":  [0, 0, 0, 0, 0, 1, 2.5, 4.5, 6, 7],
    "Vocal":         [-2, -2, -1, 1, 3.5, 4, 3.5, 2, 0, -1],
    "Pop":           [-1, 1, 3, 4, 3, 0, -1, -1, 1, 2],
    "Rock":          [5, 4, 2.5, 0, -1.5, -1, 1, 3, 4, 4.5],
    "Hip-Hop":       [6, 5, 2, 3, -1, -1, 1, 0, 2, 3],
    "Electronic":    [5, 4.5, 1.5, 0, -2, 2, 1, 1.5, 4.5, 5],
    "Jazz":          [3, 2, 1, 2, -1.5, -1.5, 0, 1.5, 3, 3.5],
    "Classical":     [4, 3, 2.5, 1.5, -1, -1, 0, 2, 3, 4],
    "Acoustic":      [4, 4, 3, 1, 2, 1.5, 3, 3.5, 3, 2],
    "Loudness (V)":  [6, 4, 0, -2, -3, -2, 0, 2, 4, 6],
    "Lo-Fi":         [2, 3, 2, 1, 0, -1, -3, -6, -10, -14],
    "Muffled / Party next door": [4, 5, 4, 2, -4, -10, -16, -20, -24, -24],
    "Telephone":     [-24, -24, -18, -6, 3, 6, 5, -6, -18, -24],
}


# ---------------------------------------------------------------- settings
@dataclass
class RemixSettings:
    # speed / pitch
    speed: float = 1.0
    keep_pitch: bool = False          # True = time-stretch (tempo only)
    pitch_semitones: float = 0.0      # extra pitch shift, tempo unchanged
    # equalizer
    eq_gains: list[float] = field(default_factory=lambda: [0.0] * len(EQ_BANDS))
    # effects (0 = off)
    reverb_mix: float = 0.0           # 0..1
    reverb_size: float = 0.7          # 0..1
    echo_mix: float = 0.0             # 0..1
    echo_delay: float = 0.35          # seconds
    echo_feedback: float = 0.35       # 0..0.9
    chorus_mix: float = 0.0           # 0..1
    lofi_amount: float = 0.0          # 0..1
    distortion_db: float = 0.0        # 0..30
    rotate_8d: float = 0.0            # 0..1 auto-pan depth
    rotate_speed: float = 0.12        # Hz
    # ambience layers, 0..1 loudness
    rain: float = 0.0
    vinyl: float = 0.0
    noise: float = 0.0                # soft pink "tape hiss"
    ocean: float = 0.0
    wind: float = 0.0
    fire: float = 0.0
    # output
    volume_db: float = 0.0
    normalize: bool = False
    fade_in: float = 0.0              # seconds
    fade_out: float = 0.0             # seconds


# ---------------------------------------------------------------- speed
def change_speed(audio: np.ndarray, sr: int, speed: float, keep_pitch: bool,
                 pitch_semitones: float = 0.0) -> np.ndarray:
    if abs(speed - 1.0) < 1e-4 and abs(pitch_semitones) < 1e-3:
        return audio
    if keep_pitch or abs(speed - 1.0) < 1e-4:
        return _fx.time_stretch(audio, sr, speed, pitch_semitones)
    # "Vinyl" style: resample, which changes tempo and pitch together
    out = _fx.vinyl_speed(audio, speed)
    if abs(pitch_semitones) >= 1e-3:
        out = _fx.time_stretch(out, sr, 1.0, pitch_semitones)
    return out


# ---------------------------------------------------------------- ambience
def _pink(n: int, rng: np.random.Generator) -> np.ndarray:
    """Pink (1/f) noise via spectral shaping, normalised to unit RMS."""
    size = 1 << int(np.ceil(np.log2(max(n, 2))))  # power of two: much faster FFT
    white = rng.standard_normal(size)
    spec = np.fft.rfft(white)
    f = np.arange(len(spec), dtype=np.float64)
    f[0] = 1.0
    spec /= np.sqrt(f)
    out = np.fft.irfft(spec, size)[:n]
    return out / (np.sqrt(np.mean(out ** 2)) + 1e-12)


def _brown(n: int, rng: np.random.Generator) -> np.ndarray:
    out = np.cumsum(rng.standard_normal(n))
    out = _filt(out, 44100, "highpass", 15)  # remove drift
    return out / (np.sqrt(np.mean(out ** 2)) + 1e-12)


def _filt(x: np.ndarray, sr: int, kind: str, freq) -> np.ndarray:
    return _fx.filt(x, sr, kind, freq)


def _smooth_lfo(n: int, sr: int, rng: np.random.Generator, rate_hz: float) -> np.ndarray:
    """Random slowly-varying curve in 0..1."""
    points = max(4, int(n / sr * rate_hz) + 4)
    knots = rng.random(points)
    return np.interp(np.linspace(0, points - 3, n), np.arange(points), knots)


def gen_rain(n: int, sr: int, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.zeros((2, n))
    # steady wash of rain: band-limited pink noise with gentle swells
    for ch in range(2):
        wash = _filt(_pink(n, rng), sr, "bandpass", [400, 9000])
        out[ch] = 0.35 * wash * (0.75 + 0.25 * _smooth_lfo(n, sr, rng, 0.2))
    # individual drops: tiny decaying "bloop" chirps (bubble resonance) + clicks
    dur = n / sr
    count = int(dur * 55)
    times = rng.integers(0, max(1, n - sr // 10), count)
    drop_len = int(0.03 * sr)
    t = np.arange(drop_len) / sr
    for start in times:
        f0 = rng.uniform(1200, 4500)
        decay = rng.uniform(80, 220)
        chirp = np.sin(2 * np.pi * f0 * t * (1 + 6 * t)) * np.exp(-decay * t)
        click = rng.standard_normal(drop_len) * np.exp(-900 * t) * 0.5
        drop = (chirp * 0.4 + click) * rng.uniform(0.15, 1.0) ** 2
        pan = rng.random()
        end = min(n, start + drop_len)
        out[0, start:end] += drop[: end - start] * (1 - pan)
        out[1, start:end] += drop[: end - start] * pan
    return out


def gen_vinyl(n: int, sr: int, seed: int = 2) -> np.ndarray:
    rng = np.random.default_rng(seed)
    crackle = np.zeros(n)
    count = int(n / sr * 25)
    pos = rng.integers(0, n, count)
    crackle[pos] = rng.standard_normal(count) * rng.random(count) ** 3 * 4
    # an occasional bigger pop
    pops = rng.integers(0, n, max(1, int(n / sr * 0.4)))
    crackle[pops] += rng.choice([-1, 1], len(pops)) * rng.uniform(1.5, 3, len(pops))
    crackle = _filt(crackle, sr, "highpass", 1500)
    hiss = 0.06 * _filt(_pink(n, rng), sr, "bandpass", [2000, 12000])
    rumble = 0.08 * _filt(rng.standard_normal(n), sr, "lowpass", 60)
    mono = crackle + hiss + rumble
    return np.stack([mono, np.roll(mono, 37)])


def gen_noise(n: int, sr: int, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.stack([0.25 * _filt(_pink(n, rng), sr, "lowpass", 10000) for _ in range(2)])


def gen_ocean(n: int, sr: int, seed: int = 4) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(n) / sr
    out = np.zeros((2, n))
    for ch in range(2):
        surf = _filt(_pink(n, rng), sr, "bandpass", [150, 5000])
        # waves roll in every ~7-11 seconds with a slow rise and quicker fall
        phase = 2 * np.pi * t / rng.uniform(7, 11) + rng.uniform(0, 6)
        env = (0.5 + 0.5 * np.sin(phase)) ** 2.5
        out[ch] = 0.6 * surf * (0.15 + env) + 0.2 * _brown(n, rng) * 0.3
    return out


def gen_wind(n: int, sr: int, seed: int = 5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.zeros((2, n))
    for ch in range(2):
        noise = _pink(n, rng)
        low = _filt(noise, sr, "bandpass", [150, 450])
        high = _filt(noise, sr, "bandpass", [450, 1400])
        whistle = _filt(noise, sr, "bandpass", [900, 1100])
        tone = _smooth_lfo(n, sr, rng, 0.15)   # how "high" the wind howls
        gust = 0.25 + 0.75 * _smooth_lfo(n, sr, rng, 0.25) ** 1.5
        out[ch] = (low * (1 - tone) + high * tone + 0.8 * whistle * tone ** 3) * gust
    return out


def gen_fire(n: int, sr: int, seed: int = 6) -> np.ndarray:
    rng = np.random.default_rng(seed)
    roar = 0.3 * _filt(_brown(n, rng), sr, "lowpass", 400) * (0.6 + 0.4 * _smooth_lfo(n, sr, rng, 0.5))
    crack = np.zeros(n)
    count = int(n / sr * 8)
    pos = rng.integers(0, n, count)
    crack[pos] = rng.standard_normal(count) * rng.uniform(0.5, 3, count)
    # crackles come in little bursts
    crack = np.convolve(crack, rng.random(64) * np.exp(-np.arange(64) / 10), mode="same")
    crack = _filt(crack, sr, "highpass", 900)
    mono = roar + crack
    return np.stack([mono, np.roll(mono, 53)])


AMBIENCE = {
    "rain": ("Rain", gen_rain),
    "vinyl": ("Vinyl crackle", gen_vinyl),
    "noise": ("Tape hiss / noise", gen_noise),
    "ocean": ("Ocean waves", gen_ocean),
    "wind": ("Wind", gen_wind),
    "fire": ("Fireplace", gen_fire),
}


# ---------------------------------------------------------------- effects
def _rotate_8d(audio: np.ndarray, sr: int, depth: float, rate: float) -> np.ndarray:
    n = audio.shape[1]
    t = np.arange(n) / sr
    pan = 0.5 + 0.5 * depth * np.sin(2 * np.pi * rate * t)  # 0 = left, 1 = right
    mono = audio.mean(axis=0)
    left = np.cos(pan * np.pi / 2) * np.sqrt(2)
    right = np.sin(pan * np.pi / 2) * np.sqrt(2)
    out = np.stack([mono * left, mono * right])
    return ((1 - depth) * audio + depth * out).astype(np.float32)


def _fade(audio: np.ndarray, sr: int, fade_in: float, fade_out: float) -> np.ndarray:
    n = audio.shape[1]
    if fade_in > 0:
        k = min(n, int(fade_in * sr))
        audio[:, :k] *= np.linspace(0, 1, k) ** 2
    if fade_out > 0:
        k = min(n, int(fade_out * sr))
        audio[:, n - k:] *= np.linspace(1, 0, k) ** 2
    return audio


def process(audio: np.ndarray, sr: int, s: RemixSettings, progress=None) -> np.ndarray:
    """Apply all remix settings and return the new audio."""
    def step(frac, msg):
        if progress:
            progress(frac, msg)

    step(0.05, "Changing speed / pitch...")
    out = change_speed(audio.astype(np.float32), sr, s.speed, s.keep_pitch, s.pitch_semitones)

    step(0.35, "Equalizer...")
    if any(abs(g) >= 0.05 for g in s.eq_gains):
        out = _fx.eq(out, sr, EQ_BANDS, s.eq_gains)

    step(0.45, "Effects...")
    if (s.distortion_db > 0.1 or s.lofi_amount > 0.01 or s.chorus_mix > 0.01
            or s.echo_mix > 0.01 or s.reverb_mix > 0.01):
        # pad with silence so reverb/echo tails are not cut off
        tail = int(sr * (2.5 if (s.reverb_mix > 0.01 or s.echo_mix > 0.01) else 0))
        out = np.pad(out, ((0, 0), (0, tail)))
        out = _fx.effects(out, sr, s)
        if tail:
            out = _trim_tail(out, audio_len=out.shape[1] - tail)
    if s.rotate_8d > 0.01:
        out = _rotate_8d(out, sr, s.rotate_8d, s.rotate_speed)

    step(0.65, "Adding ambience...")
    n = out.shape[1]
    music_rms = max(float(np.sqrt(np.mean(out ** 2))), 0.03)
    for attr, (_, gen) in AMBIENCE.items():
        level = getattr(s, attr)
        if level > 0.005:
            layer = gen(n, sr)
            layer /= float(np.sqrt(np.mean(layer ** 2))) + 1e-9  # unit loudness
            # level 1.0 = as loud as the music itself, 0.5 = about -9 dB
            out = out + (layer * music_rms * level ** 1.5).astype(np.float32)

    step(0.85, "Volume...")
    if s.normalize:
        rms = np.sqrt(np.mean(out ** 2))
        if rms > 1e-6:
            out = out * (10 ** (-14 / 20) / rms)  # loud, modern level
    if abs(s.volume_db) > 0.01:
        out = out * (10 ** (s.volume_db / 20))
    out = _fade(out.astype(np.float32), sr, s.fade_in, s.fade_out)
    # transparent limiter keeps everything below clipping
    out = _fx.limiter(out, sr, threshold_db=-1.0, release_ms=150)
    step(1.0, "Done")
    return np.clip(out, -1.0, 1.0).astype(np.float32)


def _trim_tail(out: np.ndarray, audio_len: int) -> np.ndarray:
    """Keep the effect tail until it decays to near-silence."""
    tail = out[:, audio_len:]
    if tail.shape[1] == 0:
        return out
    loud = np.where(np.abs(tail).max(axis=0) > 1e-3)[0]
    end = audio_len + (loud[-1] + 1 if len(loud) else 0)
    return out[:, :end]


# ---------------------------------------------------------------- one-click presets
REMIX_PRESETS: dict[str, dict] = {
    "Slowed + Reverb": dict(speed=speed_for_semitones(-3), reverb_mix=0.55, reverb_size=0.85,
                            eq_gains=[3, 2.5, 1.5, 0, 0, 0, -1, -2, -3, -4]),
    "Nightcore": dict(speed=speed_for_semitones(4), eq_gains=[2, 1.5, 0, 0, 0, 0, 1, 2, 2, 2]),
    "Sped Up": dict(speed=speed_for_semitones(2)),
    "Lo-Fi Rainy Night": dict(speed=speed_for_semitones(-2), lofi_amount=0.45, rain=0.5, vinyl=0.35,
                              reverb_mix=0.25, eq_gains=EQ_PRESETS["Lo-Fi"]),
    "8D Audio": dict(rotate_8d=0.9, rotate_speed=0.1, reverb_mix=0.3, reverb_size=0.6),
    "Bass Boosted": dict(eq_gains=EQ_PRESETS["Super Bass"], normalize=True),
    "Party Next Door": dict(eq_gains=EQ_PRESETS["Muffled / Party next door"], reverb_mix=0.35,
                            reverb_size=0.5),
    "Vintage Radio": dict(eq_gains=EQ_PRESETS["Telephone"], vinyl=0.5, lofi_amount=0.3),
    "Cosy Fireplace": dict(speed=speed_for_semitones(-1), fire=0.5, reverb_mix=0.2,
                           eq_gains=[2, 2, 1, 0, 0, 0, 0, -1, -2, -3]),
    "Beach Chill": dict(speed=speed_for_semitones(-2), ocean=0.5, reverb_mix=0.3),
}
