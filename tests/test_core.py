import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from easyremix import analysis, audio_io, dsp, theory  # noqa: E402

SR = 44100


def chord_song(chords, seconds_per_chord=0.5, repeats=8):
    def tone(f, d):
        t = np.arange(int(d * SR)) / SR
        return sum(np.sin(2 * np.pi * f * h * t) / h for h in (1, 2, 3)) * np.exp(-t * 2)

    x = np.concatenate([sum(tone(f, seconds_per_chord) for f in ch) + tone(ch[0] / 2, seconds_per_chord)
                        for _ in range(repeats) for ch in chords]) * 0.15
    return np.stack([x, x]).astype(np.float32)


def test_semitone_math():
    assert theory.speed_for_semitones(12) == pytest.approx(2.0)
    assert theory.semitones_for_speed(0.5) == pytest.approx(-12)
    assert theory.speed_for_semitones(-3) == pytest.approx(0.8409, abs=1e-4)
    assert theory.tuning_offset_cents(theory.semitones_for_speed(0.9)) == pytest.approx(17.6, abs=0.5)


def test_camelot():
    K = theory.Key
    assert K(0, False).camelot == "8B"    # C major
    assert K(9, True).camelot == "8A"     # A minor
    assert K(11, False).camelot == "1B"   # B major
    assert K(5, False).camelot == "7B"    # F major
    assert K(4, True).camelot == "9A"     # E minor
    assert K(0, True).camelot == "5A"     # C minor


def test_suggestions_are_in_tune():
    key = theory.Key(9, True)
    sugg = theory.speed_suggestions(key, 120)
    assert len(sugg) == 14
    for s in sugg:
        assert theory.tuning_offset_cents(theory.semitones_for_speed(s.speed)) == pytest.approx(0, abs=1e-6)
        assert s.new_key == key.transpose(s.semitones)
    best = max(sugg, key=lambda s: s.stars)
    assert best.stars == 5


def test_key_detection_minor():
    # Am - Dm - E - Am
    audio = chord_song([[220, 261.63, 329.63], [146.83, 174.61, 220], [164.81, 207.65, 246.94],
                        [220, 261.63, 329.63]])
    res = analysis.detect_key(audio, SR)
    assert res.key == theory.Key(9, True)


def test_key_detection_major():
    # G - C - D - G
    audio = chord_song([[196, 246.94, 293.66], [261.63, 329.63, 392], [293.66, 369.99, 440],
                        [196, 246.94, 293.66]])
    assert analysis.detect_key(audio, SR).key == theory.Key(7, False)


def test_key_shift_after_resample():
    audio = chord_song([[220, 261.63, 329.63], [146.83, 174.61, 220], [164.81, 207.65, 246.94],
                        [220, 261.63, 329.63]])
    slowed = dsp.change_speed(audio, SR, theory.speed_for_semitones(-3), keep_pitch=False)
    assert analysis.detect_key(slowed, SR).key == theory.Key(6, True)  # F#m


def test_bpm():
    clicks = np.zeros(SR * 20, dtype=np.float32)
    for i in range(0, len(clicks), SR // 2):  # 120 BPM
        clicks[i:i + 800] += np.random.default_rng(i).standard_normal(800).astype(np.float32) * np.exp(
            -np.arange(800) / 100)
    assert analysis.detect_bpm(np.stack([clicks, clicks]), SR) == pytest.approx(120, abs=2)


def test_speed_lengths():
    audio = np.random.default_rng(0).standard_normal((2, SR * 4)).astype(np.float32) * 0.1
    slow = dsp.change_speed(audio, SR, 0.8, keep_pitch=False)
    assert slow.shape[1] == pytest.approx(SR * 5, rel=0.01)
    stretched = dsp.change_speed(audio, SR, 1.25, keep_pitch=True)
    assert stretched.shape[1] == pytest.approx(SR * 3.2, rel=0.02)


def test_full_process_never_clips():
    audio = chord_song([[220, 261.63, 329.63]], repeats=10)
    s = dsp.RemixSettings(speed=0.84, reverb_mix=0.6, echo_mix=0.4, chorus_mix=0.4, lofi_amount=0.5,
                          distortion_db=10, rotate_8d=0.8, rain=1, vinyl=1, noise=1, ocean=1, wind=1, fire=1,
                          eq_gains=dsp.EQ_PRESETS["Super Bass"], volume_db=12, normalize=True,
                          fade_in=1, fade_out=1)
    out = dsp.process(audio, SR, s)
    assert out.shape[0] == 2
    assert np.isfinite(out).all()
    assert np.abs(out).max() <= 1.0


@pytest.mark.parametrize("name", list(dsp.REMIX_PRESETS))
def test_presets_run(name):
    audio = chord_song([[220, 261.63, 329.63]], repeats=4)
    s = dsp.RemixSettings(**dsp.REMIX_PRESETS[name])
    assert dsp.process(audio, SR, s).shape[0] == 2


@pytest.mark.parametrize("fmt", list(audio_io.EXPORT_FORMATS))
def test_audio_roundtrip(tmp_path, fmt):
    audio = chord_song([[220, 261.63, 329.63]], repeats=2)
    p = str(tmp_path / f"x.{fmt}")
    audio_io.save_audio(audio, p)
    back = audio_io.load_audio(p)
    assert back.shape[0] == 2
    assert abs(back.shape[1] - audio.shape[1]) < SR * 0.1


def test_extract_audio_from_video(tmp_path):
    from easyremix.tools import ffmpeg_path, run

    video = str(tmp_path / "v.mp4")
    run([ffmpeg_path(), "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-f", "lavfi",
         "-i", "sine=frequency=440", "-t", "2", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", video])
    out = str(tmp_path / "a.mp3")
    audio_io.extract_audio(video, out)
    assert audio_io.load_audio(out).shape[1] > SR
