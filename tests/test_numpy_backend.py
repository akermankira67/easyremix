"""The numpy-only DSP backend (used on Android) must behave like the pedalboard one."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from easyremix import dsp, fx_numpy as N  # noqa: E402

SR = 44100


def tone(freq, seconds=4.0, amp=0.3):
    t = np.arange(int(SR * seconds)) / SR
    return np.stack([amp * np.sin(2 * np.pi * freq * t)] * 2).astype(np.float32)


def peak_freq(x):
    spec = np.abs(np.fft.rfft(x[0] * np.hanning(x.shape[1])))
    return np.fft.rfftfreq(x.shape[1], 1 / SR)[spec.argmax()]


def rms(x):
    return float(np.sqrt(np.mean(np.square(x))))


@pytest.mark.parametrize("band", range(1, 9))
def test_eq_boosts_the_right_band(band):
    gains = [0.0] * 10
    gains[band] = 6.0
    f = dsp.EQ_BANDS[band]
    x = tone(f)
    y = N.eq(x, SR, dsp.EQ_BANDS, gains)
    assert 20 * np.log10(rms(y[:, SR:]) / rms(x[:, SR:])) == pytest.approx(6.0, abs=0.3)


@pytest.mark.parametrize("speed", [0.75, 0.891, 1.26])
def test_vinyl_speed_changes_pitch(speed):
    y = N.vinyl_speed(tone(1000), speed)
    assert y.shape[1] == round(4 * SR / speed)
    assert peak_freq(y) == pytest.approx(1000 * speed, rel=2e-3)


@pytest.mark.parametrize("speed,semitones", [(1.25, 0), (0.8, 0), (1.0, 3), (0.84, -2)])
def test_time_stretch_keeps_pitch(speed, semitones):
    y = N.time_stretch(tone(440), SR, speed, semitones)
    assert y.shape[1] == round(4 * SR / speed)
    assert peak_freq(y) == pytest.approx(440 * 2 ** (semitones / 12), rel=3e-3)
    assert rms(y) == pytest.approx(rms(tone(440)), rel=0.1)


def test_filters():
    rng = np.random.default_rng(0)
    noise = rng.standard_normal(SR * 2)
    low = N.filt(noise, SR, "lowpass", 500)
    high = N.filt(noise, SR, "highpass", 5000)
    f = np.fft.rfftfreq(len(noise), 1 / SR)
    spec = lambda x: np.abs(np.fft.rfft(x))  # noqa: E731
    assert spec(low)[f > 4000].mean() < 0.05 * spec(low)[f < 300].mean()
    assert spec(high)[f < 1000].mean() < 0.05 * spec(high)[(f > 8000) & (f < 15000)].mean()


def test_limiter_never_exceeds_threshold():
    y = N.limiter(tone(220) * 8, SR, threshold_db=-1.0)
    assert np.abs(y).max() <= 10 ** (-1 / 20) + 1e-3


def test_reverb_and_echo_leave_a_tail():
    x = np.pad(tone(440, 1.0), ((0, 0), (0, SR)))
    for kw in (dict(reverb_mix=0.5), dict(echo_mix=0.5, echo_delay=0.3)):
        y = N.effects(x, SR, dsp.RemixSettings(**kw))
        assert rms(y[:, -SR // 2:]) > 1e-3
