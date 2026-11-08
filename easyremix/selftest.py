"""`EasyRemix.exe --selftest` checks that every bundled component works."""
from __future__ import annotations

import os
import sys
import tempfile
import traceback


def run() -> int:
    import numpy as np

    out = []
    ok = True

    def check(name, fn):
        nonlocal ok
        try:
            out.append(f"[ OK ] {name}: {fn()}")
        except Exception:
            ok = False
            out.append(f"[FAIL] {name}\n{traceback.format_exc()}")

    from . import analysis, audio_io, dsp, tools

    sr = audio_io.SAMPLE_RATE
    t = np.arange(sr * 6) / sr
    tone = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    audio = np.stack([tone, tone])
    tmp = tempfile.mkdtemp()

    check("ffmpeg", lambda: tools.ffmpeg_path())
    check("deno (YouTube JS runtime)", lambda: tools.deno_path() or "not bundled")

    def io():
        p = os.path.join(tmp, "t.mp3")
        audio_io.save_audio(audio, p)
        back = audio_io.load_audio(p)
        return f"mp3 roundtrip {back.shape}"

    check("audio encode/decode", io)
    check("key detection", lambda: analysis.detect_key(audio, sr).key.name)
    check("effects", lambda: dsp.process(audio, sr, dsp.RemixSettings(
        speed=0.84, reverb_mix=0.5, rain=0.4, eq_gains=dsp.EQ_PRESETS["Rock"])).shape)
    check("time-stretch", lambda: dsp.process(audio, sr, dsp.RemixSettings(speed=1.2, keep_pitch=True)).shape)
    check("yt-dlp", lambda: __import__("yt_dlp").version.__version__)
    check("customtkinter", lambda: __import__("customtkinter").__version__)

    def logo():
        from PIL import Image

        path = os.path.join(tools.resource_dir(), "assets", "logo-128.png")
        return f"{Image.open(path).size} {os.path.basename(path)}"

    check("app logo", logo)
    ok_before = ok
    check("playback library (preview only)", lambda: __import__("sounddevice").__version__)
    ok = ok_before  # preview playback is optional; exporting still works without it

    text = "\n".join(out)
    print(text)
    try:  # also write next to the exe because a windowed exe has no console
        log = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "easyremix-selftest.txt")
        with open(log, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass
    return 0 if ok else 1
