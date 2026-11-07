# EasyRemix

![EasyRemix remix tab](assets/screenshot.png)

A friendly desktop app that does three things:

1. **Download** a YouTube video (or audio only) from a link
2. **Extract** the audio from any video into a standalone file (mp3, wav, flac, m4a, ogg)
3. **Remix** a track:
   - **Key & BPM detection**, including the Camelot code DJs use (e.g. `8A` = A minor)
   - **Speed up / slow down**, either classic (pitch changes too, like a record player: *slowed*, *nightcore*) or time-stretch (tempo only)
   - A **best-speeds table** listing every speed that moves the song a whole number of semitones, so it stays in tune. For each one it shows the new key, new BPM, a star rating and a short explanation
   - **10-band equalizer** with 16 presets (Bass Boost, Vocal, Lo-Fi, Party next door, Telephone, ...)
   - **Effects**: reverb, echo, chorus, lo-fi/bitcrush, distortion, 8D rotation
   - **Ambience layers**: rain, vinyl crackle, tape hiss, ocean waves, wind, fireplace. These sounds are generated in code, so they are royalty-free
   - **Volume**, loudness normalize, fade in/out, and a built-in limiter so the output never clips
   - **One-click presets**: Slowed + Reverb, Nightcore, Lo-Fi Rainy Night, 8D Audio, Bass Boosted, ...
   - **Preview** any 25-second part before you export

The program is one folder with nothing else to install:

```
EasyRemix\
  EasyRemix.exe      <- double-click this
  _internal\         <- libraries, ffmpeg, deno (keep it next to the exe)
```

Because nothing has to be unpacked at launch, the window opens in about half a second.

## Getting the program

* **From GitHub Actions:** every push runs the *Build Windows EXE* workflow. Open the run,
  download the `EasyRemix-windows` artifact, unzip it anywhere and start `EasyRemix.exe`. Push a
  tag like `v0.3` and the workflow also attaches `EasyRemix-windows.zip` to a GitHub Release.
* **Build it yourself on Windows:** install Python 3.12 from python.org, then double-click
  `build.bat`. The result is the folder `dist\EasyRemix\`.

The folder is a few hundred MB, mostly ffmpeg and deno. To get a desktop shortcut,
right-click `EasyRemix.exe` → *Send to* → *Desktop (create shortcut)*. Move the whole folder,
never only the exe.

If Windows SmartScreen warns you, click *More info → Run anyway*. The exe is not code-signed.

Run `EasyRemix.exe --selftest` to check every built-in component. It writes
`easyremix-selftest.txt` next to the exe.

## The speed / key math

Changing playback speed by resampling shifts the pitch by

```
semitones = 12 × log2(speed)          speed = 2^(semitones / 12)
```

So only these speeds land exactly on another key:

| Shift | Speed | Feel |
|------:|------:|------|
| −1 | 0.944× | subtle slow |
| −2 | 0.891× | light *slowed* |
| −3 | 0.841× | classic *slowed + reverb* |
| −4 | 0.794× | heavy slowed |
| −5 / −7 | 0.749× / 0.667× | perfect 4th / 5th, a neighbouring key on the circle of fifths |
| +2 | 1.122× | *sped up* |
| +3 / +4 | 1.189× / 1.260× | *nightcore* |

Any other speed, for example 0.90× (−1.82 semitones), leaves the song *between* keys, 18 cents
off standard tuning. The app warns about that and suggests the nearest in-tune speed.

## Running from source

```bash
pip install -r requirements.txt
```

```bash
python app.py
```

To run the tests:

```bash
pip install pytest && pytest -q tests
```

## Project layout

| File | Purpose |
|------|---------|
| `app.py` | entry point |
| `easyremix/gui.py` | customtkinter interface |
| `easyremix/downloader.py` | yt-dlp wrapper |
| `easyremix/audio_io.py` | decode/encode/extract via the bundled ffmpeg |
| `easyremix/analysis.py` | key (Krumhansl–Kessler profiles + overtone-suppressed chroma) and BPM detection |
| `easyremix/theory.py` | keys, Camelot wheel, speed ↔ semitone math, suggestions |
| `easyremix/dsp.py` | speed/pitch, EQ, effects, ambience generators, presets |
| `EasyRemix.spec` | PyInstaller one-file build |

## Open-source components

yt-dlp (Unlicense), FFmpeg via imageio-ffmpeg (LGPL/GPL), Deno (MIT), Spotify Pedalboard and
Rubber Band (GPLv3), NumPy/SciPy (BSD), customtkinter (MIT), sounddevice/PortAudio (MIT).
Because Pedalboard is GPLv3, any distributed EasyRemix build falls under GPLv3 too.

Only download content you have the rights to use.
