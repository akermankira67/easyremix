"""Decode any audio/video file to numpy and encode numpy back to a file.

Uses the ffmpeg program when there is one (desktop builds), otherwise PyAV, the ffmpeg
libraries as a Python module (Android). ``EASYREMIX_AUDIO=pyav`` forces PyAV.
"""
from __future__ import annotations

import os
from fractions import Fraction

import numpy as np

from .tools import ffmpeg_available, ffmpeg_path, run

SAMPLE_RATE = 44100

# extension -> ffmpeg codec arguments
EXPORT_FORMATS: dict[str, list[str]] = {
    "mp3": ["-c:a", "libmp3lame", "-b:a", "320k"],
    "wav": ["-c:a", "pcm_s16le"],
    "flac": ["-c:a", "flac"],
    "ogg": ["-c:a", "libvorbis", "-q:a", "8"],
    "m4a": ["-c:a", "aac", "-b:a", "256k"],
}

# extension -> PyAV encoders to try in order: (codec, bit rate, options)
PYAV_CODECS: dict[str, list[tuple[str, int | None, dict]]] = {
    "mp3": [("libmp3lame", 320_000, {}), ("libshine", 320_000, {})],
    "wav": [("pcm_s16le", None, {})],
    "flac": [("flac", None, {})],
    "ogg": [("libvorbis", 256_000, {}), ("vorbis", 256_000, {"strict": "experimental"}),
            ("libopus", 192_000, {}), ("opus", 192_000, {"strict": "experimental"})],
    "m4a": [("aac", 256_000, {})],
}
_CONTAINERS = {"mp3": "mp3", "wav": "wav", "flac": "flac", "ogg": "ogg", "m4a": "ipod"}


def use_pyav() -> bool:
    forced = os.environ.get("EASYREMIX_AUDIO", "").lower()
    if forced in ("pyav", "ffmpeg"):
        return forced == "pyav"
    return not ffmpeg_available()


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower().lstrip(".")


def load_audio(path: str, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Return float32 array shaped (2, samples) at ``sr`` Hz (always stereo)."""
    if use_pyav():
        data = _pyav_decode(path, sr)
    else:
        raw = run([
            ffmpeg_path(), "-hide_banner", "-nostdin", "-i", path,
            "-vn", "-ac", "2", "-ar", str(sr), "-f", "f32le", "-",
        ])
        data = np.frombuffer(raw, dtype=np.float32)
    if data.size < 2:
        raise RuntimeError("The file does not contain any audio.")
    data = data[: data.size - data.size % 2]
    return np.ascontiguousarray(data.reshape(-1, 2).T)


def save_audio(audio: np.ndarray, path: str, sr: int = SAMPLE_RATE) -> None:
    ext = _ext(path)
    codec = EXPORT_FORMATS.get(ext)
    if codec is None:
        raise ValueError(f"Unsupported export format: .{ext}")
    if audio.ndim == 1:
        audio = np.stack([audio, audio])
    if use_pyav():
        _pyav_encode(audio, path, sr)
        return
    interleaved = np.clip(audio.T, -1.0, 1.0).astype(np.float32).tobytes()
    run([
        ffmpeg_path(), "-hide_banner", "-nostdin", "-y",
        "-f", "f32le", "-ar", str(sr), "-ac", str(audio.shape[0]), "-i", "-",
        *codec, path,
    ], input_bytes=interleaved)


def extract_audio(video_path: str, out_path: str) -> None:
    """Separate the audio track of a video into a standalone file."""
    ext = _ext(out_path)
    codec = EXPORT_FORMATS.get(ext)
    if codec is None:
        raise ValueError(f"Unsupported audio format: .{ext}")
    if use_pyav():
        save_audio(load_audio(video_path), out_path)
        return
    run([
        ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-i", video_path,
        "-vn", "-map", "0:a:0", *codec, out_path,
    ])


# ---------------------------------------------------------------- PyAV (Android)
def _pyav_decode(path: str, sr: int) -> np.ndarray:
    import av

    chunks: list[np.ndarray] = []
    try:
        container = av.open(path)
    except av.error.FFmpegError as exc:
        raise RuntimeError(f"Could not open the file: {exc}") from None
    with container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise RuntimeError("The file does not contain any audio.")
        resampler = av.AudioResampler(format="flt", layout="stereo", rate=sr)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    return np.concatenate(chunks) if chunks else np.zeros(0, np.float32)


def _open_encoder(container, ext: str, sr: int):
    import av

    errors = []
    for name, bit_rate, options in PYAV_CODECS[ext]:
        if name not in av.codecs_available:
            continue
        rate = 48000 if "opus" in name else sr  # Opus only supports 48 kHz (and lower)
        try:
            stream = container.add_stream(name, rate=rate, options=dict(options))
            stream.layout = "stereo"
            if bit_rate:
                stream.bit_rate = bit_rate
            return stream
        except Exception as exc:  # pragma: no cover - depends on the ffmpeg build
            errors.append(f"{name}: {exc}")
    raise RuntimeError(f"No encoder for .{ext} is available. " + "; ".join(errors))


def _pyav_encode(audio: np.ndarray, path: str, sr: int) -> None:
    import av

    ext = _ext(path)
    pcm = np.clip(audio, -1.0, 1.0).astype(np.float32)
    with av.open(path, mode="w", format=_CONTAINERS[ext]) as container:
        stream = _open_encoder(container, ext, sr)
        ctx = stream.codec_context
        ctx.open()  # so format and frame size are known
        resampler = av.AudioResampler(format=ctx.format.name, layout="stereo", rate=ctx.sample_rate,
                                      frame_size=ctx.frame_size or None)
        block = sr  # one second at a time keeps memory low
        for start in range(0, pcm.shape[1], block):
            part = np.ascontiguousarray(pcm[:, start:start + block].T).reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(part, format="flt", layout="stereo")
            frame.sample_rate = sr
            frame.pts = start
            frame.time_base = Fraction(1, sr)
            for out in resampler.resample(frame):
                container.mux(stream.encode(out))
        for out in resampler.resample(None):
            container.mux(stream.encode(out))
        container.mux(stream.encode(None))
