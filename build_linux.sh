#!/bin/sh
# Builds dist/EasyRemix/ (EasyRemix + _internal) on Linux, plus
# dist/EasyRemix-linux-x86_64.tar.gz and, if possible, dist/EasyRemix-x86_64.AppImage.
# Needs Python 3.10+ with tkinter (Debian/Ubuntu: sudo apt install python3-tk python3-venv libportaudio2).
set -e
DENO_VERSION=v2.5.6
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt pyinstaller
if [ ! -x bin/deno ]; then
  echo "Downloading deno $DENO_VERSION ..."
  mkdir -p bin
  curl -fsSL -o deno.zip "https://github.com/denoland/deno/releases/download/$DENO_VERSION/deno-x86_64-unknown-linux-gnu.zip"
  python3 -m zipfile -e deno.zip bin && rm deno.zip && chmod +x bin/deno
fi
python3 -m PyInstaller EasyRemix.spec --noconfirm
sh packaging/linux/package.sh
