#!/bin/sh
# Turns dist/EasyRemix/ (PyInstaller output) into the Linux downloads:
#   dist/EasyRemix-linux-x86_64.tar.gz   folder + install.sh (adds a menu entry)
#   dist/EasyRemix-x86_64.AppImage       single file, needs appimagetool on PATH or $APPIMAGETOOL
set -e
cd "$(dirname "$0")/../.."
P=packaging/linux
cp $P/easyremix.desktop $P/install.sh dist/EasyRemix/
cp assets/icon.png dist/EasyRemix/easyremix.png
chmod +x dist/EasyRemix/install.sh dist/EasyRemix/EasyRemix
tar -C dist -czf dist/EasyRemix-linux-x86_64.tar.gz EasyRemix
echo "Built dist/EasyRemix-linux-x86_64.tar.gz"

TOOL="${APPIMAGETOOL:-$(command -v appimagetool || true)}"
if [ -z "$TOOL" ]; then
  echo "appimagetool not found - skipping the AppImage."
  exit 0
fi
APPDIR=dist/EasyRemix.AppDir
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib"
cp -a dist/EasyRemix "$APPDIR/usr/lib/easyremix"
rm -f "$APPDIR/usr/lib/easyremix/install.sh"
cp $P/AppRun "$APPDIR/AppRun" && chmod +x "$APPDIR/AppRun"
cp $P/easyremix.desktop "$APPDIR/easyremix.desktop"
cp assets/icon.png "$APPDIR/easyremix.png"
ln -sf easyremix.png "$APPDIR/.DirIcon"
ARCH=x86_64 "$TOOL" --no-appstream "$APPDIR" dist/EasyRemix-x86_64.AppImage
echo "Built dist/EasyRemix-x86_64.AppImage"
