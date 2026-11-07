"""Locating bundled helper binaries (ffmpeg, deno) and running them quietly.

Everything the app needs ships in the program folder (``_internal/``):
  * ffmpeg comes from the ``imageio-ffmpeg`` wheel (static build),
  * deno (JavaScript runtime yt-dlp uses for YouTube) is copied into
    ``bin/`` at build time.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import lru_cache

IS_WINDOWS = sys.platform.startswith("win")
IS_ANDROID = hasattr(sys, "getandroidapilevel") or "ANDROID_ARGUMENT" in os.environ
# Hide the console window that would otherwise flash for every ffmpeg call.
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def resource_dir() -> str:
    """Folder that holds bundled files (``_internal`` next to the exe when frozen)."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@lru_cache(maxsize=None)
def ffmpeg_path() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        found = shutil.which("ffmpeg")
        if found:
            return found
        raise RuntimeError("ffmpeg was not found. The installation seems to be broken.")


def ffmpeg_available() -> bool:
    """False on Android, where there is no ffmpeg program (PyAV is used instead)."""
    try:
        ffmpeg_path()
        return True
    except RuntimeError:
        return False


@lru_cache(maxsize=None)
def android_ffmpeg_path() -> str | None:
    """The ffmpeg program of the Android build (python-for-android ships it as a library file,
    ``libffmpegbin.so``, because only the native-library folder may contain executables)."""
    if not IS_ANDROID:
        return None
    try:
        from jnius import autoclass

        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        folder = activity.getApplicationInfo().nativeLibraryDir
    except Exception:
        return None
    path = os.path.join(folder, "libffmpegbin.so")
    return path if os.access(path, os.X_OK) else None


def merge_ffmpeg_path() -> str | None:
    """An ffmpeg program yt-dlp can use to merge video and audio, or None."""
    if ffmpeg_available():
        return ffmpeg_path()
    return android_ffmpeg_path()


@lru_cache(maxsize=None)
def deno_path() -> str | None:
    name = "deno.exe" if IS_WINDOWS else "deno"
    bundled = os.path.join(resource_dir(), "bin", name)
    if os.path.isfile(bundled):
        return bundled
    return shutil.which("deno")


def run(cmd: list[str], input_bytes: bytes | None = None) -> bytes:
    """Run a command, return stdout, raise RuntimeError with stderr on failure."""
    proc = subprocess.run(
        cmd,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=NO_WINDOW,
    )
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-6:]
        raise RuntimeError("Command failed:\n" + "\n".join(tail))
    return proc.stdout
