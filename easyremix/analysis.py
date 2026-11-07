"""Key and tempo (BPM) detection with plain numpy.

Key: a chromagram (energy of each of the 12 pitch classes) is correlated with
the Krumhansl-Kessler key profiles for all 24 major/minor keys.
BPM: autocorrelation of a spectral-flux onset envelope.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import resample_poly

from .theory import Key

# Krumhansl & Kessler (1982) probe-tone profiles
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

ANALYSIS_SR = 11025


@dataclass
class KeyResult:
    key: Key
    confidence: float  # correlation 0..1
    alternatives: list[tuple[Key, float]]


def _mono_downsampled(audio: np.ndarray, sr: int, max_seconds: float = 240) -> np.ndarray:
    mono = audio.mean(axis=0) if audio.ndim == 2 else audio
    mono = mono[: int(max_seconds * sr)]
    if sr == 44100:
        mono = resample_poly(mono, 1, 4)
    elif sr != ANALYSIS_SR:
        mono = resample_poly(mono, ANALYSIS_SR, sr)
    return mono.astype(np.float32)


def _stft_mag(x: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    frames = np.lib.stride_tricks.sliding_window_view(x, n_fft)[::hop]
    return np.abs(np.fft.rfft(frames * np.hanning(n_fft).astype(np.float32), axis=1))


def chromagram(audio: np.ndarray, sr: int) -> np.ndarray:
    x = _mono_downsampled(audio, sr)
    n_fft, hop = 8192, 2048
    mag = _stft_mag(x, n_fft, hop)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / ANALYSIS_SR)
    # 1) fold the spectrum into semitones (MIDI 33 = A1 ... 96 = C7)
    lo, hi = 33, 97
    band = (freqs >= 50) & (freqs <= 2200)
    midi = 69 + 12 * np.log2(freqs[band] / 440.0)
    nearest = np.round(midi).astype(int)
    w = np.cos(np.pi * (midi - nearest)) ** 2  # favour bins close to the exact pitch
    semis = np.zeros((mag.shape[0], hi - lo))
    for m in range(lo, hi):
        sel = nearest == m
        if sel.any():
            semis[:, m - lo] = (mag[:, band][:, sel] * w[sel]).sum(axis=1)
    # 2) remove overtones: every note also rings an octave (+12), an octave+fifth (+19)
    #    and two octaves (+24) higher. Without this the key drifts towards the dominant.
    for m in range(semis.shape[1]):
        for offset, amount in ((12, 0.5), (19, 0.35), (24, 0.25), (28, 0.15)):
            if m + offset < semis.shape[1]:
                semis[:, m + offset] = np.maximum(0.0, semis[:, m + offset] - amount * semis[:, m])
    # 3) compress dynamics and fold into 12 pitch classes
    semis = np.log1p(100.0 * semis / (semis.max() + 1e-12))
    chroma = np.zeros((semis.shape[0], 12))
    for m in range(lo, hi):
        chroma[:, m % 12] += semis[:, m - lo]
    return chroma


def detect_key(audio: np.ndarray, sr: int) -> KeyResult:
    chroma = chromagram(audio, sr)
    # ignore silent frames
    energy = chroma.sum(axis=1)
    if energy.max() <= 0:
        raise RuntimeError("The audio is silent, cannot detect the key.")
    profile = chroma[energy > 0.1 * energy.max()].sum(axis=0)
    scores = []
    for tonic in range(12):
        for minor, ref in ((False, MAJOR_PROFILE), (True, MINOR_PROFILE)):
            r = np.corrcoef(profile, np.roll(ref, tonic))[0, 1]
            scores.append((Key(tonic, minor), float(r)))
    scores.sort(key=lambda s: s[1], reverse=True)
    best, conf = scores[0]
    return KeyResult(best, max(0.0, conf), scores[1:4])


def detect_bpm(audio: np.ndarray, sr: int) -> float:
    x = _mono_downsampled(audio, sr, max_seconds=120)
    n_fft, hop = 1024, 256
    mag = _stft_mag(x, n_fft, hop)
    logmag = np.log1p(10 * mag)
    flux = np.maximum(0.0, np.diff(logmag, axis=0)).sum(axis=1)
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")  # remove slow trend
    flux = np.maximum(flux, 0)
    if flux.max() <= 0:
        return 0.0
    fps = ANALYSIS_SR / hop
    ac = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
    bpms = np.arange(60, 200.01, 0.25)
    lags = fps * 60.0 / bpms
    idx = np.arange(len(ac))
    # look at several beat periods at once: far lags give much finer tempo resolution
    strength = sum(np.interp(lags * k, idx, ac) for k in (1, 2, 3, 4))
    # add half-lag (double-time) evidence and a mild preference around 120 BPM
    strength = strength + 0.5 * np.interp(lags / 2, idx, ac)
    prior = np.exp(-0.5 * (np.log2(bpms / 120.0) / 0.9) ** 2)
    best = bpms[np.argmax(strength * prior)]
    return float(best)
