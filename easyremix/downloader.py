"""YouTube (and 1000+ other sites) downloading through yt-dlp."""
from __future__ import annotations

import os
from typing import Callable

from .tools import deno_path, ffmpeg_path

QUALITIES = {
    "Best available": "bestvideo*+bestaudio/best",
    "1080p": "bestvideo*[height<=1080]+bestaudio/best[height<=1080]",
    "720p": "bestvideo*[height<=720]+bestaudio/best[height<=720]",
    "480p": "bestvideo*[height<=480]+bestaudio/best[height<=480]",
}


class _QuietLogger:
    def __init__(self, on_log: Callable[[str], None] | None):
        self.on_log = on_log

    def debug(self, msg):  # yt-dlp sends info messages through debug()
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
) -> str:
    """Download ``url`` into ``out_dir``. Returns the path of the final file."""
    import yt_dlp

    os.makedirs(out_dir, exist_ok=True)
    result: dict[str, str] = {}

    def hook(d):
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

    opts: dict = {
        "outtmpl": os.path.join(out_dir, "%(title).150B [%(id)s].%(ext)s"),
        "ffmpeg_location": ffmpeg_path(),
        "noplaylist": True,
        "progress_hooks": [hook],
        "postprocessor_hooks": [post_hook],
        "logger": _QuietLogger(on_log),
        "windowsfilenames": True,
        "overwrites": True,
    }
    if cookies_browser:
        # lets age-restricted / members-only videos work using the user's own login
        opts["cookiesfrombrowser"] = (cookies_browser.lower(),)
    deno = deno_path()
    if deno:
        opts["js_runtimes"] = {"deno": {"path": deno}}

    if audio_only:
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

    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as exc:
            raise RuntimeError(friendly_error(str(exc))) from None
        path = result.get("path")
        if not path:
            path = info.get("requested_downloads", [{}])[0].get("filepath") or ydl.prepare_filename(info)
    return path


def friendly_error(msg: str) -> str:
    msg = msg.replace("ERROR: ", "").strip()
    low = msg.lower()
    if "sign in to confirm" in low or ("age" in low and "restrict" in low):
        hint = "YouTube wants you to be signed in. Choose your browser under 'Use login from'."
    elif "403" in low:
        hint = ("YouTube refused the download (HTTP 403). This happens on VPN/cloud networks or when "
                "YouTube changes something. Try again later, without a VPN, or with 'Use login from'.")
    elif "unsupported url" in low:
        hint = "This link is not supported. Check that it is a full video link."
    elif "private video" in low or "unavailable" in low:
        hint = "The video is private, deleted or not available in your country."
    elif "could not copy" in low and "cookie" in low:
        hint = "Close the browser completely and try again (its cookie file is locked)."
    else:
        return msg
    return f"{hint}\n\nDetails: {msg}"
