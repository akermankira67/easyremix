"""Audio decode/encode through PyAV, the path the Android app uses (no ffmpeg program)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

av = pytest.importorskip("av")

from easyremix import audio_io  # noqa: E402

SR = 44100


@pytest.fixture(autouse=True)
def force_pyav(monkeypatch):
    monkeypatch.setenv("EASYREMIX_AUDIO", "pyav")


def tone(seconds=3.0):
    t = np.arange(int(SR * seconds)) / SR
    return np.stack([0.3 * np.sin(2 * np.pi * 440 * t), 0.3 * np.sin(2 * np.pi * 660 * t)]).astype(np.float32)


@pytest.mark.parametrize("fmt", list(audio_io.EXPORT_FORMATS))
def test_roundtrip(tmp_path, fmt):
    p = str(tmp_path / f"t.{fmt}")
    audio_io.save_audio(tone(), p)
    back = audio_io.load_audio(p)
    assert back.shape[0] == 2
    assert abs(back.shape[1] - 3 * SR) < SR * 0.1
    # channels stay separate and the content survives
    spec = np.abs(np.fft.rfft(back[0, SR // 2: SR * 2]))
    f = np.fft.rfftfreq(SR * 2 - SR // 2, 1 / SR)
    assert f[spec.argmax()] == pytest.approx(440, abs=3)


@pytest.mark.parametrize("codec", ["vorbis", "opus"])
def test_ogg_without_libvorbis(tmp_path, monkeypatch, codec):
    """Android's ffmpeg has no libvorbis; the built-in encoders must work instead."""
    if codec not in av.codecs_available:
        pytest.skip(f"{codec} not in this ffmpeg build")
    only = [c for c in audio_io.PYAV_CODECS["ogg"] if c[0] == codec]
    monkeypatch.setitem(audio_io.PYAV_CODECS, "ogg", only)
    p = str(tmp_path / "t.ogg")
    audio_io.save_audio(tone(), p)
    assert abs(audio_io.load_audio(p).shape[1] - 3 * SR) < SR * 0.1


def test_extract_audio_from_video(tmp_path):
    from fractions import Fraction

    video = str(tmp_path / "v.mp4")
    pcm = tone(2.0)
    with av.open(video, "w") as c:
        vs = c.add_stream("mpeg4", rate=25)
        vs.width, vs.height, vs.pix_fmt = 64, 64, "yuv420p"
        aud = c.add_stream("aac", rate=SR)
        aud.layout = "stereo"
        for i in range(50):
            frame = av.VideoFrame.from_ndarray(np.full((64, 64, 3), i * 4, np.uint8), format="rgb24")
            c.mux(vs.encode(frame))
        c.mux(vs.encode(None))
        rs = av.AudioResampler(format="fltp", layout="stereo", rate=SR, frame_size=1024)
        af = av.AudioFrame.from_ndarray(np.ascontiguousarray(pcm.T).reshape(1, -1), format="flt",
                                        layout="stereo")
        af.sample_rate, af.pts, af.time_base = SR, 0, Fraction(1, SR)
        for f in rs.resample(af) + rs.resample(None):
            c.mux(aud.encode(f))
        c.mux(aud.encode(None))
    out = str(tmp_path / "a.mp3")
    audio_io.extract_audio(video, out)
    assert abs(audio_io.load_audio(out).shape[1] - 2 * SR) < SR * 0.1


def test_file_without_audio_is_reported(tmp_path):
    video = str(tmp_path / "silent.mp4")
    with av.open(video, "w") as c:
        vs = c.add_stream("mpeg4", rate=25)
        vs.width, vs.height, vs.pix_fmt = 64, 64, "yuv420p"
        for i in range(10):
            c.mux(vs.encode(av.VideoFrame.from_ndarray(np.zeros((64, 64, 3), np.uint8), format="rgb24")))
        c.mux(vs.encode(None))
    with pytest.raises(RuntimeError, match="does not contain any audio"):
        audio_io.load_audio(video)
