# Buildozer configuration for the EasyRemix Android app.
# Build with:  sh android/build.sh   (or let GitHub Actions do it)
# build.sh copies the shared engine (../easyremix) and the icon next to main.py first.

[app]
title = EasyRemix
package.name = easyremix
package.domain = io.github.akermankira67
source.dir = .
source.include_exts = py,png,json
source.exclude_dirs = bin,.buildozer,tests,__pycache__,recipes,share_aar,aar
version.regex = __version__ = ["'](.*)["']
version.filename = %(source.dir)s/easyremix/__init__.py

# python-for-android recipes: numpy (DSP), av = PyAV + ffmpeg 8 (decoding, mp3/aac/flac/
# ogg encoding, and the ffmpeg program used to merge HD video), plus pure-Python packages.
requirements = python3,kivy==2.3.1,numpy,av,charset_normalizer,yt-dlp,certifi,androidstorage4kivy,filetype,pyjnius,android

icon.filename = %(source.dir)s/icon.png
presplash.filename = %(source.dir)s/icon.png
android.presplash_color = #121216
orientation = portrait
fullscreen = 0

android.permissions = INTERNET, ACCESS_NETWORK_STATE, WAKE_LOCK, READ_EXTERNAL_STORAGE, WRITE_EXTERNAL_STORAGE, READ_MEDIA_AUDIO, READ_MEDIA_VIDEO
android.api = 35
android.minapi = 24
android.ndk = 28c
android.ndk_api = 24
android.archs = arm64-v8a
android.accept_sdk_license = True
android.allow_backup = True
android.release_artifact = apk
# FileProvider (androidx) so videos can be shared from the private cache - see share_aar/
android.enable_androidx = True
android.gradle_dependencies = androidx.core:core:1.13.1
android.add_aars = aar/easyremix-share.aar
android.debug_artifact = apk

# a pinned python-for-android release, so builds don't change by themselves
p4a.branch = v2026.05.09
p4a.bootstrap = sdl2
# our own recipe for PyAV 17.1 (see recipes/av/__init__.py)
p4a.local_recipes = ./recipes

[buildozer]
log_level = 2
warn_on_root = 0
