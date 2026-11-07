"""Tiny preview player (PortAudio via sounddevice). Fails gracefully."""
from __future__ import annotations

import os
import sys

import numpy as np


def _use_bundled_portaudio() -> None:
    """Linux builds ship libportaudio.so.2 in the program folder, but sounddevice only
    looks for it with ctypes.util.find_library (system paths). Point it at our copy."""
    if not (sys.platform.startswith("linux") and getattr(sys, "frozen", False)):
        return
    from .tools import resource_dir

    bundled = os.path.join(resource_dir(), "libportaudio.so.2")
    if not os.path.isfile(bundled):
        return
    import ctypes.util

    original = ctypes.util.find_library

    def find_library(name):
        found = original(name)
        if found is None and name == "portaudio":
            return bundled
        return found

    ctypes.util.find_library = find_library


class Player:
    def __init__(self):
        try:
            _use_bundled_portaudio()
            import sounddevice as sd

            self._sd = sd
            self.error = None
        except Exception as exc:  # no audio device / library
            self._sd = None
            self.error = str(exc)

    @property
    def available(self) -> bool:
        return self._sd is not None

    def play(self, audio: np.ndarray, sr: int) -> None:
        if not self._sd:
            raise RuntimeError(f"Audio playback is not available: {self.error}")
        self._sd.stop()
        self._sd.play(np.ascontiguousarray(audio.T), sr)

    def stop(self) -> None:
        if self._sd:
            try:
                self._sd.stop()
            except Exception:
                pass
