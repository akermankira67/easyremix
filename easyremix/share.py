"""Sharing a video (the file itself) with friends, without keeping it in the user's files.

The video is downloaded into a private temporary folder, handed to the messenger, and the
temporary copy is deleted later (old shares are cleaned up automatically).

* Android: the system share sheet (Telegram, VK, WhatsApp, Facebook ... whatever is
  installed) gets the file through a FileProvider - see android/main.py.
* Desktop: there is no common share sheet, so the file is put on the clipboard and the
  chosen messenger is opened; pressing Ctrl+V (Cmd+V) in a chat sends the video.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

SHARE_MAX_AGE_HOURS = 6  # temporary shares older than this are deleted

# desktop: name -> (app link, web fallback). The app link opens the installed desktop app.
SHARE_APPS: dict[str, tuple[str | None, str]] = {
    "Telegram": ("tg://", "https://web.telegram.org"),
    "WhatsApp": ("whatsapp://", "https://web.whatsapp.com"),
    "VK": (None, "https://vk.com/im"),
    "Facebook Messenger": (None, "https://www.messenger.com"),
    "Discord": ("discord://", "https://discord.com/app"),
    "Instagram": (None, "https://www.instagram.com/direct/inbox/"),
}


def share_dir(base: str | None = None) -> str:
    """Private temporary folder for shared files (created if needed)."""
    path = os.path.join(base or tempfile.gettempdir(), "EasyRemix-share")
    os.makedirs(path, exist_ok=True)
    return path


def clean_share_dir(base: str | None = None, max_age_hours: float = SHARE_MAX_AGE_HOURS,
                    now: float | None = None) -> int:
    """Delete temporary shares older than ``max_age_hours``. Returns how many were removed."""
    folder = share_dir(base)
    now = time.time() if now is None else now
    removed = 0
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        try:
            if now - os.path.getmtime(path) > max_age_hours * 3600:
                shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
                removed += 1
        except OSError:
            pass
    return removed


def file_uri(path: str) -> str:
    from pathlib import Path

    return Path(os.path.abspath(path)).as_uri()


def copy_file_to_clipboard(path: str) -> bool:
    """Put the file itself on the clipboard, like 'Copy' in the file manager.

    Messengers (Telegram, WhatsApp, VK, Discord - desktop apps and web pages in
    Chrome/Edge/Firefox) then send it when you press Ctrl+V in a chat.
    """
    path = os.path.abspath(path)
    try:
        if sys.platform.startswith("win"):
            return _windows_copy_file(path)
        if sys.platform == "darwin":
            script = f'set the clipboard to (POSIX file "{path}")'
            return subprocess.run(["osascript", "-e", script], capture_output=True).returncode == 0
        uri = file_uri(path) + "\n"
        for cmd in (["wl-copy", "--type", "text/uri-list"],
                    ["xclip", "-selection", "clipboard", "-t", "text/uri-list"]):
            if shutil.which(cmd[0]):
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                proc.communicate(uri.encode())
                return proc.returncode == 0
    except Exception:
        return False
    return False


def _windows_copy_file(path: str) -> bool:
    """CF_HDROP clipboard data = what Explorer puts there when you copy a file."""
    import ctypes
    from ctypes import wintypes

    CF_HDROP, GMEM_MOVEABLE = 15, 0x0002
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]

    files = (path + "\0\0").encode("utf-16-le")
    header = 20  # DROPFILES: pFiles(4) pt(8) fNC(4) fWide(4)
    data = header.to_bytes(4, "little") + bytes(8) + bytes(4) + (1).to_bytes(4, "little") + files
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        return False
    ptr = kernel32.GlobalLock(handle)
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(handle)
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        return bool(user32.SetClipboardData(CF_HDROP, handle))
    finally:
        user32.CloseClipboard()


def open_share_app(name: str) -> None:
    """Open the messenger: its desktop app if installed, else its web version."""
    import webbrowser

    app_link, web = SHARE_APPS[name]
    if app_link and _open_app_link(app_link):
        return
    webbrowser.open(web)


def _open_app_link(link: str) -> bool:
    try:
        if sys.platform.startswith("win"):
            import winreg

            scheme = link.split(":", 1)[0]
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, scheme))  # registered?
            os.startfile(link)  # type: ignore[attr-defined]
            return True
        if sys.platform == "darwin":
            return subprocess.run(["open", link], capture_output=True).returncode == 0
        if shutil.which("xdg-open"):
            # xdg-open succeeds only if some application handles the scheme
            scheme = link.split(":", 1)[0]
            handler = subprocess.run(["xdg-mime", "query", "default", f"x-scheme-handler/{scheme}"],
                                     capture_output=True, text=True).stdout.strip() if shutil.which("xdg-mime") else ""
            if handler:
                subprocess.Popen(["xdg-open", link])
                return True
    except Exception:
        pass
    return False
