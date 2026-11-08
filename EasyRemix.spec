# PyInstaller spec: builds the folder dist/EasyRemix/ containing EasyRemix(.exe)
# and its libraries in _internal/. Nothing is unpacked at startup, so it opens fast.
#   pyinstaller EasyRemix.spec --noconfirm
import glob
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

datas, binaries, hiddenimports = [], [], []

for pkg in ("customtkinter", "pedalboard", "yt_dlp_ejs"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# app logo / window icon
datas += [(os.path.join("assets", f), "assets") for f in ("logo-128.png", "icon.ico", "icon.png")]
# static ffmpeg build shipped inside the imageio-ffmpeg wheel
datas += collect_data_files("imageio_ffmpeg", subdir="binaries")
# PortAudio dll for preview playback (the package only exists on Windows/macOS wheels)
try:
    datas += collect_data_files("_sounddevice_data")
except Exception:
    pass
# On Linux PortAudio is a system library (apt install libportaudio2); ship it so preview
# playback works on machines that don't have it. easyremix/player.py points sounddevice at it.
if sys.platform.startswith("linux"):
    found = sorted(glob.glob("/usr/lib/*/libportaudio.so.2") + glob.glob("/usr/lib/libportaudio.so.2"))
    if found:
        binaries.append((found[0], "."))  # PyInstaller copies the real file under this soname
    else:
        print("WARNING: libportaudio.so.2 not found - preview playback needs it on the target system.")
hiddenimports += collect_submodules("yt_dlp")
hiddenimports += collect_submodules("websockets")  # YouTube sign-in (imported lazily)
hiddenimports += ["scipy.signal", "scipy._lib.messagestream", "PIL.ImageTk"]

# deno = JavaScript runtime yt-dlp needs for YouTube (put into ./bin before building)
deno = os.path.join("bin", "deno.exe" if sys.platform.startswith("win") else "deno")
if os.path.isfile(deno):
    binaries.append((deno, "bin"))
else:
    print("WARNING: bin/deno not found - YouTube downloads may fail. Run build script first.")

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["matplotlib", "IPython", "pytest", "PyQt5", "PySide6", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EasyRemix",
    icon="assets/icon.ico" if os.path.isfile("assets/icon.ico") else None,
    console=False,
    upx=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="EasyRemix",
)
