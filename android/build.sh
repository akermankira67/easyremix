#!/bin/sh
# Builds the Android APK with buildozer (Linux or WSL; needs Java 17 and the usual
# buildozer packages - see the android-apk job in .github/workflows/build.yml).
# The result is android/bin/easyremix-<version>-arm64-v8a-debug.apk
set -e
cd "$(dirname "$0")"
rm -rf easyremix
cp -r ../easyremix ./easyremix
cp ../assets/icon.png ./icon.png
yes | buildozer -v android debug
