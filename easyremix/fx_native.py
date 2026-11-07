"""DSP backend built on pedalboard (JUCE) and scipy. Used by the desktop builds."""
from __future__ import annotations

from fractions import Fraction

import numpy as np
import pedalboard as pb
from scipy.signal import butter, resample_poly, sosfilt

NAME = "pedalboard"


def filt(x: np.ndarray, sr: int, kind: str, freq, order: int = 2) -> np.ndarray:
    """Butterworth low/high/band-pass along the last axis."""
    sos = butter(order, freq, btype=kind, fs=sr, output="sos")
    return sosfilt(sos, x)


def resample(x: np.ndarray, up: int, down: int) -> np.ndarray:
    """Change the number of samples by up/down (last axis)."""
    return resample_poly(x, up, down, axis=-1)


def eq(audio: np.ndarray, sr: int, bands: list[float], gains: list[float]) -> np.ndarray:
    plugins = []
    for i, (freq, gain) in enumerate(zip(bands, gains)):
        if abs(gain) < 0.05:
            continue
        if i == 0:
            plugins.append(pb.LowShelfFilter(cutoff_frequency_hz=freq * 1.4, gain_db=gain, q=0.707))
        elif i == len(bands) - 1:
            plugins.append(pb.HighShelfFilter(cutoff_frequency_hz=freq * 0.7, gain_db=gain, q=0.707))
        else:
            plugins.append(pb.PeakFilter(cutoff_frequency_hz=freq, gain_db=gain, q=1.2))
    return pb.Pedalboard(plugins)(audio, sr)


def time_stretch(audio: np.ndarray, sr: int, speed: float, semitones: float) -> np.ndarray:
    """Change tempo by ``speed`` (>1 = faster) and pitch by ``semitones`` independently."""
    return pb.time_stretch(audio, sr, stretch_factor=speed, pitch_shift_in_semitones=semitones)


def vinyl_speed(audio: np.ndarray, speed: float) -> np.ndarray:
    """Resample so tempo and pitch change together (record-player style)."""
    ratio = Fraction(1.0 / speed).limit_denominator(1000)
    return resample_poly(audio, ratio.numerator, ratio.denominator, axis=1).astype(np.float32)


def effects(audio: np.ndarray, sr: int, s) -> np.ndarray:
    """Distortion, lo-fi, chorus, echo and reverb from the RemixSettings ``s``."""
    fx = []
    if s.distortion_db > 0.1:
        fx.append(pb.Distortion(drive_db=s.distortion_db))
    if s.lofi_amount > 0.01:
        a = s.lofi_amount
        fx.append(pb.Bitcrush(bit_depth=16 - 9 * a))
        fx.append(pb.LowpassFilter(cutoff_frequency_hz=16000 - 12500 * a))
    if s.chorus_mix > 0.01:
        fx.append(pb.Chorus(rate_hz=0.8, depth=0.3, mix=s.chorus_mix, centre_delay_ms=8))
    if s.echo_mix > 0.01:
        fx.append(pb.Delay(delay_seconds=s.echo_delay, feedback=s.echo_feedback, mix=s.echo_mix))
    if s.reverb_mix > 0.01:
        fx.append(pb.Reverb(room_size=s.reverb_size, damping=0.45, wet_level=s.reverb_mix * 0.6,
                            dry_level=1.0 - s.reverb_mix * 0.4, width=1.0))
    if not fx:
        return audio
    return pb.Pedalboard(fx)(audio, sr)


def limiter(audio: np.ndarray, sr: int, threshold_db: float = -1.0, release_ms: float = 150) -> np.ndarray:
    return pb.Pedalboard([pb.Limiter(threshold_db=threshold_db, release_ms=release_ms)])(audio, sr)
