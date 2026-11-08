"""YouTube (and 1000+ other sites) downloading through yt-dlp."""
from __future__ import annotations

import os
import time
from typing import Callable

from . import tools
from .tools import deno_path

QUALITIES = {
    "Best available": "bestvideo*+bestaudio/best",
    "1080p": "bestvideo*[height<=1080]+bestaudio/best[height<=1080]",
    "720p": "bestvideo*[height<=720]+bestaudio/best[height<=720]",
    "480p": "bestvideo*[height<=480]+bestaudio/best[height<=480]",
}

# Without any ffmpeg program video and audio can't be merged, so only formats that
# already contain both are used. On YouTube these single-file formats go up to 360p.
SINGLE_FILE_QUALITIES = {
    "Best available": "best[vcodec!=none][acodec!=none][ext=mp4]/best[vcodec!=none][acodec!=none]",
    "1080p": "best[height<=1080][vcodec!=none][acodec!=none][ext=mp4]/best[height<=1080][acodec!=none]",
    "720p": "best[height<=720][vcodec!=none][acodec!=none][ext=mp4]/best[height<=720][acodec!=none]",
    "480p": "best[height<=480][vcodec!=none][acodec!=none][ext=mp4]/best[height<=480][acodec!=none]",
}
SINGLE_FILE_AUDIO = "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio/best"


# --- reliability settings for slow / unstable / throttled connections -------------------
SOCKET_TIMEOUT = 60          # seconds without data before a request counts as failed
HTTP_RETRIES = 20            # retries of a whole-file (https) request
FRAGMENT_RETRIES = 30        # retries of a single fragment of a fragmented (m3u8/DASH) stream
CHUNK_SIZE = 10 * 1024 * 1024  # download https streams in 10 MB requests (less throttling)
RESUME_ATTEMPTS = 5          # automatic restarts after the download failed completely
RESUME_WAITS = [5, 10, 20, 40, 60]  # seconds to wait before each automatic restart

# Errors that will not go away by trying again
_PERMANENT = ("unsupported url", "private video", "video unavailable", "is not available",
              "sign in to confirm", "age-restricted", "age restricted", "could not copy",
              "cookie", "removed by the uploader", "copyright", "members-only", "join this channel",
              "unexpected response from webpage", "impersonat")


class DownloadCancelled(RuntimeError):
    """The user pressed Cancel."""

    cancelled = True


def normalize_url(text: str) -> str | None:
    """Turn what the user pasted into a full link, or None if it is not a link.

    Accepts links without ``https://`` (``tiktok.com/@user/video/123``, ``vm.tiktok.com/x``,
    ``youtu.be/x``) and text around the link (TikTok's share text: "Check this out! https://...").
    """
    import re

    text = (text or "").strip()
    m = re.search(r"https?://\S+", text)
    if m:
        return m.group(0).rstrip(").,!")
    m = re.search(r"(?<![\w@])((?:[\w-]+\.)+[a-z]{2,}(?:/\S*)?)", text, re.IGNORECASE)
    if m and " " not in m.group(1):
        return "https://" + m.group(1).rstrip(").,!")
    return None


_preloaded = False


def preload_android_curl() -> str:
    """Android: load curl_cffi's own C++ runtime before yt-dlp imports curl_cffi.

    The library is found through ``RUNPATH $ORIGIN/../curl_cffi.libs`` anyway; loading it
    first (globally) is a safety net for phones whose loader resolves the path differently.
    Returns a short status for the log.
    """
    global _preloaded
    if _preloaded or not tools.IS_ANDROID:
        return ""
    _preloaded = True
    import ctypes
    import glob
    import importlib.util

    try:
        spec = importlib.util.find_spec("curl_cffi")
        if not spec or not spec.origin:
            return "curl_cffi is not installed (TikTok may not work)"
        libs = os.path.join(os.path.dirname(os.path.dirname(spec.origin)), "curl_cffi.libs")
        for lib in sorted(glob.glob(os.path.join(libs, "*.so"))):
            ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
        __import__("curl_cffi")

        return "browser impersonation ready (TikTok)"
    except Exception as exc:  # never block downloads because of this
        return f"browser impersonation unavailable: {exc}"


def _backoff(n: int) -> float:
    """Wait 1, 2, 4, 8, 16, 30, 30... seconds between retries."""
    return float(min(30, 2 ** n))


def is_retryable(message: str) -> bool:
    low = message.lower()
    return not any(p in low for p in _PERMANENT)


class _QuietLogger:
    def __init__(self, on_log: Callable[[str], None] | None, check_cancel: Callable[[], None] = lambda: None):
        self.on_log = on_log
        self.check_cancel = check_cancel

    def debug(self, msg):  # yt-dlp sends info messages through debug()
        self.check_cancel()  # also stops the slow "extracting" phase quickly
        if self.on_log and not msg.startswith("[debug]"):
            self.on_log(msg)

    def info(self, msg):
        self.debug(msg)

    def warning(self, msg):
        if self.on_log:
            self.on_log("Warning: " + msg)

    def error(self, msg):
        if self.on_log:
            self.on_log(msg)


def download(
    url: str,
    out_dir: str,
    audio_only: bool = False,
    quality: str = "Best available",
    on_progress: Callable[[float, str], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    cookies_browser: str | None = None,
    cookie_file: str | None = None,
    resume_attempts: int = RESUME_ATTEMPTS,
    sleep: Callable[[float], None] | None = None,
    cancel=None,
) -> str:
    """Download ``url`` into ``out_dir``. Returns the path of the final file.

    Never produces a file with holes: missing fragments are retried (with growing pauses)
    instead of skipped. If the download still fails, it is restarted automatically and
    continues from the already downloaded part (``.part`` files are kept).

    ``cancel`` is a ``threading.Event``: setting it stops the download, deletes its partial
    files and raises ``DownloadCancelled``.
    """
    status = preload_android_curl()
    if status and on_log:
        on_log(status)
    import yt_dlp

    os.makedirs(out_dir, exist_ok=True)
    result: dict[str, str] = {}

    def check_cancel():
        if cancel is not None and cancel.is_set():
            raise yt_dlp.utils.DownloadCancelled("Cancelled")

    if sleep is None:
        def sleep(seconds):
            if cancel is not None:
                cancel.wait(seconds)
            else:
                time.sleep(seconds)

    def hook(d):
        vid = (d.get("info_dict") or {}).get("id")
        if vid:
            result["id"] = vid
        check_cancel()
        if d.get("status") == "downloading" and on_progress:
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            frac = done / total if total else 0.0
            speed = d.get("_speed_str", "").strip()
            eta = d.get("_eta_str", "").strip()
            on_progress(frac, f"Downloading {frac * 100:5.1f}%  {speed}  ETA {eta}")
        elif d.get("status") == "finished" and on_progress:
            on_progress(1.0, "Processing file...")

    def post_hook(d):
        if d.get("status") == "finished":
            path = d.get("info_dict", {}).get("filepath")
            if path:
                result["path"] = path

    has_ffmpeg = tools.ffmpeg_available()
    # Android: no full ffmpeg, but its ffmpeg program can still merge video + audio
    merger = None if has_ffmpeg else tools.merge_ffmpeg_path()
    opts: dict = {
        "outtmpl": os.path.join(out_dir, "%(title).150B [%(id)s].%(ext)s"),
        "noplaylist": True,
        "progress_hooks": [hook],
        "postprocessor_hooks": [post_hook],
        "logger": _QuietLogger(on_log, check_cancel),
        "windowsfilenames": True,
        # --- 1. never skip fragments, retry patiently instead
        "skip_unavailable_fragments": False,
        "retries": HTTP_RETRIES,
        "fragment_retries": FRAGMENT_RETRIES,
        "extractor_retries": 5,
        "file_access_retries": 5,
        "socket_timeout": SOCKET_TIMEOUT,
        "retry_sleep_functions": {"http": _backoff, "fragment": _backoff, "extractor": _backoff},
        # --- 2. prefer single-file https streams (resumable byte by byte) over fragmented
        #        m3u8 streams, without lowering the resolution
        "format_sort": ["res", "proto"],
        "http_chunk_size": CHUNK_SIZE,
        # --- 3. keep partial downloads so a restart continues where it stopped
        "continuedl": True,
        "keep_fragments": False,
    }
    if has_ffmpeg:
        opts["ffmpeg_location"] = tools.ffmpeg_path()
    elif merger:
        opts["ffmpeg_location"] = merger
    if cookie_file and os.path.isfile(cookie_file):
        # the YouTube login saved by "Sign in to YouTube" (see ytlogin.py)
        opts["cookiefile"] = cookie_file
    elif cookies_browser:
        # lets age-restricted / members-only videos work using the user's own login
        opts["cookiesfrombrowser"] = (cookies_browser.lower(),)
    deno = deno_path()
    if deno:
        opts["js_runtimes"] = {"deno": {"path": deno}}

    if not has_ffmpeg and (audio_only or not merger):
        # no merging / converting by yt-dlp; audio is turned into mp3 with PyAV afterwards
        opts["format"] = SINGLE_FILE_AUDIO if audio_only else SINGLE_FILE_QUALITIES.get(
            quality, SINGLE_FILE_QUALITIES["Best available"])
    elif audio_only:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "320",
        }]
    else:
        opts["format"] = QUALITIES.get(quality, QUALITIES["Best available"])
        opts["merge_output_format"] = "mp4"
        # Re-encode audio to AAC so the mp4 plays everywhere (Windows player etc.).
        opts["postprocessor_args"] = {"merger": ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]}

    attempt = 0
    while True:
        try:
            check_cancel()
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                path = result.get("path")
                if not path:
                    path = (info.get("requested_downloads", [{}])[0].get("filepath")
                            or ydl.prepare_filename(info))
            if audio_only and not has_ffmpeg and not path.lower().endswith(".mp3"):
                path = _convert_to_mp3(path, on_progress)
            return path
        except yt_dlp.utils.DownloadCancelled:
            _remove_partial(out_dir, result.get("id"))
            raise DownloadCancelled("Download cancelled.") from None
        except yt_dlp.utils.DownloadError as exc:
            if cancel is not None and cancel.is_set():
                _remove_partial(out_dir, result.get("id"))
                raise DownloadCancelled("Download cancelled.") from None
            message = str(exc)
            if attempt >= resume_attempts or not is_retryable(message):
                raise DownloadFailed(friendly_error(message), resumable=is_retryable(message)) from None
            wait = RESUME_WAITS[min(attempt, len(RESUME_WAITS) - 1)]
            attempt += 1
            if on_log:
                on_log(f"Connection problem. The downloaded part is kept - continuing in {wait} s "
                       f"(automatic restart {attempt}/{resume_attempts})...")
            if on_progress:
                on_progress(0.0, f"Connection lost - continuing in {wait} s ({attempt}/{resume_attempts})")
            sleep(wait)


def _remove_partial(out_dir: str, video_id: str | None) -> None:
    """Delete the unfinished files of a cancelled download (``... [id].mp4.part`` etc.)."""
    if not video_id:
        return
    tag = f"[{video_id}]"
    for name in os.listdir(out_dir):
        if tag in name and (".part" in name or ".temp." in name or name.endswith(".ytdl")
                           or ".f" in name.rsplit(tag, 1)[-1]):
            try:
                os.remove(os.path.join(out_dir, name))
            except OSError:
                pass


def _convert_to_mp3(path: str, on_progress: Callable[[float, str], None] | None) -> str:
    """Turn a downloaded audio file into mp3 without the ffmpeg program (PyAV)."""
    from .audio_io import load_audio, save_audio

    if on_progress:
        on_progress(1.0, "Converting to mp3...")
    out = os.path.splitext(path)[0] + ".mp3"
    save_audio(load_audio(path), out)
    try:
        os.remove(path)
    except OSError:
        pass
    return out


class DownloadFailed(RuntimeError):
    """Download error; ``resumable`` means pressing Resume later can continue it."""

    def __init__(self, message: str, resumable: bool):
        super().__init__(message)
        self.resumable = resumable


def friendly_error(msg: str) -> str:
    msg = msg.replace("ERROR: ", "").strip()
    low = msg.lower()
    if "sign in to confirm" in low or ("age" in low and "restrict" in low):
        hint = ("YouTube wants to check that you are not a bot. This happens on some networks and VPNs. "
                "Try again later or turn the VPN off." if tools.IS_ANDROID else
                "YouTube wants to check that you are not a bot. Sign in to YouTube once "
                "(Options -> YouTube account -> Sign in) and try again.")
    elif any(k in low for k in ("timed out", "503", "502", "connection", "fragment", "incompleteread",
                                 "getaddrinfo", "network")):
        hint = ("The connection kept dropping, so the download was paused. Nothing broken was saved: "
                "the downloaded part is kept. Press 'Resume' when the connection is better.")
    elif "403" in low:
        hint = ("YouTube refused the download (HTTP 403). This happens on VPN/cloud networks or when "
                "YouTube changes something. Try again later, without a VPN, or sign in to YouTube (Options).")
    elif "unexpected response from webpage" in low or "impersonat" in low:
        hint = ("The site refused to talk to EasyRemix (it only answers real browsers). "
                "Update EasyRemix to the latest version; if it still happens, the site changed "
                "something and a newer version is needed.")
    elif "unsupported url" in low:
        hint = "This link is not supported. Check that it is a full video link."
    elif "private video" in low or "unavailable" in low:
        hint = "The video is private, deleted or not available in your country."
    elif "could not copy" in low and "cookie" in low:
        hint = ("The browser keeps its cookie file locked while it runs. "
                "Use Options -> YouTube account -> Sign in instead.")
    else:
        return msg
    return f"{hint}\n\nDetails: {msg}"
