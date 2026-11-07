#!/bin/sh
# Adds EasyRemix to your application menu (current user only, no root needed).
# Run it from inside the unpacked EasyRemix folder:  ./install.sh
# Undo with:  ./install.sh --uninstall
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
if [ "$1" = "--uninstall" ]; then
  rm -f "$APPS/easyremix.desktop" "$ICONS/easyremix.png"
  echo "EasyRemix removed from the application menu."
  exit 0
fi
mkdir -p "$APPS" "$ICONS"
cp "$DIR/easyremix.png" "$ICONS/easyremix.png"
sed -e "s|^Exec=.*|Exec=\"$DIR/EasyRemix\"|" -e "s|^Icon=.*|Icon=easyremix|" \
  "$DIR/easyremix.desktop" > "$APPS/easyremix.desktop"
chmod +x "$APPS/easyremix.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" || true
echo "EasyRemix added to the application menu."
