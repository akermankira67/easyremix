"""Downloads over a deliberately broken local server must never produce files with holes.

The server imitates an unstable / throttled connection: HTTP 503 errors, connections
that drop in the middle of a transfer, fragments that fail several times in a row and
a server that is completely down for a while.
"""
import os
import re
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from easyremix import downloader  # noqa: E402
from easyremix.tools import ffmpeg_path, run  # noqa: E402


class Flaky:
    """Shared failure plan for the handler."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.down_until = 0.0        # server answers 503 to everything until this time
        self.segment_failures = {}   # segment name -> remaining failures
        self.fail_every = 0          # every N-th request of the big file fails
        self.drop_after = 0          # drop the connection after this many bytes (0 = never)
        self.counter = 0
        self.requests = 0


FLAKY = Flaky()


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        with FLAKY.lock:
            FLAKY.requests += 1
            FLAKY.counter += 1
            n = FLAKY.counter
            down = time.time() < FLAKY.down_until
            name = os.path.basename(self.path.split("?")[0])
            seg_fail = FLAKY.segment_failures.get(name, 0)
            if seg_fail:
                FLAKY.segment_failures[name] = seg_fail - 1
        if down or seg_fail:
            # alternate between "503" and "connection reset" like a real bad network
            if n % 2:
                self.send_error(503, "Service Unavailable")
            else:
                self.connection.close()
            return
        if name.endswith(".mp4") and FLAKY.fail_every and n % FLAKY.fail_every == 0:
            self.send_error(503, "Service Unavailable")
            return
        if name.endswith(".mp4") and FLAKY.drop_after:
            return self._send_partial()
        super().do_GET()

    def _send_partial(self):
        """Serve the file honouring Range, but cut the connection after ``drop_after`` bytes."""
        path = self.translate_path(self.path)
        size = os.path.getsize(path)
        start, end = 0, size - 1
        m = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
        length = end - start + 1
        self.send_response(206 if m else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if m:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(min(length, FLAKY.drop_after))
        try:
            self.wfile.write(data)
        finally:
            self.close_connection = True
            self.connection.close()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("www")
    ff = ffmpeg_path()
    # 20 s test video (moving test pattern + tone)
    run([ff, "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-f", "lavfi",
         "-i", "sine=frequency=440", "-t", "20", "-c:v", "libx264", "-preset", "ultrafast",
         "-g", "25", "-c:a", "aac", "-shortest", str(root / "video.mp4")])
    # the same video as a fragmented HLS stream with 2 s fragments
    hls = root / "hls"
    hls.mkdir()
    run([ff, "-y", "-i", str(root / "video.mp4"), "-c", "copy", "-f", "hls", "-hls_time", "2",
         "-hls_list_size", "0", "-hls_segment_filename", str(hls / "seg%02d.ts"), str(hls / "index.m3u8")])

    handler = lambda *a, **kw: Handler(*a, directory=str(root), **kw)  # noqa: E731
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", root
    httpd.shutdown()


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    """Same logic as in the app, but without minutes of waiting."""
    monkeypatch.setattr(downloader, "_backoff", lambda n: 0.05)
    monkeypatch.setattr(downloader, "SOCKET_TIMEOUT", 5)
    FLAKY.reset()


def video_frames(path):
    import subprocess

    proc = subprocess.run([ffmpeg_path(), "-hide_banner", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frames = re.findall(rb"frame=\s*(\d+)", proc.stderr)
    return int(frames[-1])


def test_options_never_skip_fragments(monkeypatch):
    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            return {"requested_downloads": [{"filepath": "x.mp4"}]}

    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    downloader.download("https://example.com/v", "/tmp")
    assert captured["skip_unavailable_fragments"] is False
    assert captured["continuedl"] is True
    assert "overwrites" not in captured  # force-overwrite would disable resuming
    assert captured["format_sort"][:2] == ["res", "proto"]
    assert captured["fragment_retries"] >= 20


def test_dropping_connection_still_gives_identical_file(server, tmp_path):
    url, root = server
    FLAKY.drop_after = 150_000   # every response is cut after 150 KB
    FLAKY.fail_every = 4         # and every 4th request is a 503
    path = downloader.download(f"{url}/video.mp4", str(tmp_path), quality="Best available")
    assert open(path, "rb").read() == (root / "video.mp4").read_bytes()


def test_failing_fragments_are_retried_not_skipped(server, tmp_path):
    url, root = server
    # several fragments fail 3-6 times in a row (yt-dlp's old default would skip some of them)
    FLAKY.segment_failures = {"seg01.ts": 4, "seg03.ts": 6, "seg05.ts": 3, "seg08.ts": 5}
    path = downloader.download(f"{url}/hls/index.m3u8", str(tmp_path))
    assert video_frames(path) == video_frames(root / "video.mp4") == 500
    assert not any(FLAKY.segment_failures.values())


def test_automatic_restart_after_total_outage(server, tmp_path, monkeypatch):
    url, root = server
    monkeypatch.setattr(downloader, "HTTP_RETRIES", 1)
    monkeypatch.setattr(downloader, "FRAGMENT_RETRIES", 1)
    FLAKY.segment_failures = {"seg04.ts": 6}  # fails longer than the per-fragment retries allow
    logs, waits = [], []
    path = downloader.download(f"{url}/hls/index.m3u8", str(tmp_path), on_log=logs.append,
                               sleep=waits.append)
    assert waits, "the download should have been restarted automatically"
    assert any("continuing in" in m for m in logs)
    assert video_frames(path) == 500


def test_gives_up_with_resumable_error_and_resume_finishes(server, tmp_path, monkeypatch):
    url, root = server
    monkeypatch.setattr(downloader, "HTTP_RETRIES", 1)
    monkeypatch.setattr(downloader, "FRAGMENT_RETRIES", 1)
    FLAKY.segment_failures = {"seg06.ts": 1000}
    with pytest.raises(downloader.DownloadFailed) as err:
        downloader.download(f"{url}/hls/index.m3u8", str(tmp_path), resume_attempts=2, sleep=lambda s: None)
    assert err.value.resumable
    assert "Resume" in str(err.value)
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".mp4") and ".part" not in f], \
        "no broken final file may be saved"
    # the network is back: pressing Resume continues and finishes the file
    FLAKY.segment_failures = {}
    before = FLAKY.requests
    path = downloader.download(f"{url}/hls/index.m3u8", str(tmp_path))
    assert video_frames(path) == 500
    assert FLAKY.requests - before < 10 + 1, "resume should not download everything again"


def test_permanent_errors_are_not_retried():
    assert not downloader.is_retryable("ERROR: [youtube] abc: Private video. Sign in if you've been granted")
    assert not downloader.is_retryable("ERROR: Unsupported URL: https://example.com")
    assert downloader.is_retryable("ERROR: [download] Got error: HTTP Error 503: Service Unavailable")
    assert downloader.is_retryable("Read timed out. (read timeout=20.0)")


def _capture_opts(monkeypatch, filepath="x.mp4"):
    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            return {"requested_downloads": [{"filepath": filepath}]}

    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    return captured


def test_without_ffmpeg_only_single_file_formats(monkeypatch):
    """Without any ffmpeg program nothing may need merging or converting by yt-dlp."""
    monkeypatch.setattr(downloader.tools, "ffmpeg_available", lambda: False)
    monkeypatch.setattr(downloader.tools, "merge_ffmpeg_path", lambda: None)
    captured = _capture_opts(monkeypatch)
    downloader.download("https://example.com/v", "/tmp", quality="720p")
    assert "ffmpeg_location" not in captured
    assert "postprocessors" not in captured and "merge_output_format" not in captured
    assert "+" not in captured["format"]  # no "video+audio" merge
    assert "[acodec!=none]" in captured["format"]


def test_without_ffmpeg_audio_is_converted_to_mp3(monkeypatch, tmp_path):
    pytest.importorskip("av")
    import numpy as np

    from easyremix import audio_io

    monkeypatch.setattr(downloader.tools, "ffmpeg_available", lambda: False)
    monkeypatch.setattr(downloader.tools, "merge_ffmpeg_path", lambda: "/app/lib/libffmpegbin.so")
    monkeypatch.setenv("EASYREMIX_AUDIO", "pyav")
    m4a = str(tmp_path / "song [abc].m4a")
    audio_io.save_audio(np.zeros((2, 44100), np.float32), m4a)
    captured = _capture_opts(monkeypatch, filepath=m4a)
    path = downloader.download("https://example.com/v", str(tmp_path), audio_only=True)
    assert captured["format"].startswith("bestaudio")
    assert path.endswith(".mp3") and os.path.isfile(path) and not os.path.exists(m4a)


def test_android_ffmpeg_program_is_used_for_merging_video(monkeypatch):
    """On Android the ffmpeg program (libffmpegbin.so) merges HD video + audio."""
    monkeypatch.setattr(downloader.tools, "ffmpeg_available", lambda: False)
    monkeypatch.setattr(downloader.tools, "merge_ffmpeg_path", lambda: "/app/lib/libffmpegbin.so")
    captured = _capture_opts(monkeypatch)
    downloader.download("https://example.com/v", "/tmp", quality="1080p")
    assert captured["ffmpeg_location"] == "/app/lib/libffmpegbin.so"
    assert captured["format"] == downloader.QUALITIES["1080p"]
    assert captured["postprocessor_args"]["merger"][-3:-1] == ["aac", "-b:a"]
