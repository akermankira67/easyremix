"""Decode any audio/video file to numpy and encode numpy back to a file (via ffmpeg)."""
from __future__ import annotations

import os

import numpy as np

from .tools import ffmpeg_path, run

SAMPLE_RATE = 44100

# extension -> ffmpeg codec arguments
EXPORT_FORMATS: dict[str, list[str]] = {
    "mp3": ["-c:a", "libmp3lame", "-b:a", "320k"],
    "wav": ["-c:a", "pcm_s16le"],
    "flac": ["-c:a", "flac"],
    "ogg": ["-c:a", "libvorbis", "-q:a", "8"],
    "m4a": ["-c:a", "aac", "-b:a", "256k"],
}


def load_audio(path: str, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Return float32 array shaped (2, samples) at ``sr`` Hz (always stereo)."""
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
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    codec = EXPORT_FORMATS.get(ext)
    if codec is None:
        raise ValueError(f"Unsupported export format: .{ext}")
    if audio.ndim == 1:
        audio = np.stack([audio, audio])
    interleaved = np.clip(audio.T, -1.0, 1.0).astype(np.float32).tobytes()
    run([
        ffmpeg_path(), "-hide_banner", "-nostdin", "-y",
        "-f", "f32le", "-ar", str(sr), "-ac", str(audio.shape[0]), "-i", "-",
        *codec, path,
    ], input_bytes=interleaved)


def extract_audio(video_path: str, out_path: str) -> None:
    """Separate the audio track of a video into a standalone file."""
    ext = os.path.splitext(out_path)[1].lower().lstrip(".")
    codec = EXPORT_FORMATS.get(ext)
    if codec is None:
        raise ValueError(f"Unsupported audio format: .{ext}")
    run([
        ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-i", video_path,
        "-vn", "-map", "0:a:0", *codec, out_path,
    ])
