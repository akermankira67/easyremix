"""Tiny preview player (PortAudio via sounddevice). Fails gracefully."""
from __future__ import annotations

import numpy as np


class Player:
    def __init__(self):
        try:
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
