import os
import shutil
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from easyremix import downloader, ytlogin  # noqa: E402


@pytest.fixture
def data_home(tmp_path, monkeypatch):
    monkeypatch.setattr(ytlogin, "app_dir", lambda: str(tmp_path))
    return tmp_path


COOKIES = [
    {"name": "LOGIN_INFO", "value": "AFmm:abc==", "domain": ".youtube.com", "path": "/", "secure": True,
     "expires": 1900000000.5, "session": False},
    {"name": "PREF", "value": "f6=40000000", "domain": ".youtube.com", "path": "/", "secure": True,
     "expires": -1, "session": True},
    {"name": "SID", "value": "g.a0", "domain": ".google.com", "path": "/", "secure": False, "expires": 1900000000},
    {"name": "tracker", "value": "1", "domain": ".example.com", "path": "/", "expires": 1900000000},
]


def test_cookies_txt_keeps_only_youtube_and_google():
    text = ytlogin.to_netscape(COOKIES)
    assert text.startswith("# Netscape HTTP Cookie File")
    rows = [line.split("\t") for line in text.splitlines() if line and not line.startswith("#")]
    assert [r[5] for r in rows] == ["LOGIN_INFO", "PREF", "SID"]
    login = rows[0]
    assert login == [".youtube.com", "TRUE", "/", "TRUE", "1900000000", "LOGIN_INFO", "AFmm:abc=="]
    assert rows[1][4] == "0"  # session cookie


def test_cookies_txt_is_readable_by_yt_dlp(tmp_path):
    from yt_dlp.cookies import YoutubeDLCookieJar

    path = ytlogin.save_cookies(COOKIES, str(tmp_path / "c.txt"))
    jar = YoutubeDLCookieJar(path)
    jar.load(ignore_discard=True, ignore_expires=True)
    assert {c.name for c in jar} == {"LOGIN_INFO", "PREF", "SID"}


def test_login_is_detected_only_with_youtube_login_cookie():
    assert ytlogin.has_login(COOKIES)
    assert not ytlogin.has_login([c for c in COOKIES if c["name"] != "LOGIN_INFO"])


def test_signed_in_and_sign_out(data_home):
    assert not ytlogin.signed_in()
    ytlogin.save_cookies(COOKIES)
    os.makedirs(ytlogin.profile_dir())
    assert ytlogin.signed_in() and ytlogin.saved_at()
    ytlogin.sign_out()
    assert not ytlogin.signed_in()
    assert not os.path.exists(ytlogin.profile_dir())


def test_default_browser_is_offered_first(monkeypatch, tmp_path):
    exes = {}
    for name in ("Chrome", "Opera", "Edge"):
        exes[name] = str(tmp_path / f"{name}.exe")
        open(exes[name], "w").close()
    monkeypatch.setattr(ytlogin, "_candidates", lambda: [(n, [exes[n]]) for n in ("Chrome", "Opera", "Edge")])
    monkeypatch.setattr(ytlogin, "_default_browser_name", lambda: "Opera")
    assert [n for n, _ in ytlogin.find_browsers()] == ["Opera", "Chrome", "Edge"]


def test_bot_check_error_points_to_sign_in():
    msg = downloader.friendly_error("ERROR: [youtube] abc: Sign in to confirm you're not a bot.")
    assert "Sign in" in msg and "YouTube account" in msg


def test_download_uses_saved_login(monkeypatch, tmp_path):
    seen = {}

    class FakeYDL:
        def __init__(self, opts):
            seen.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def download(self, urls):
            raise RuntimeError("stop here")

    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    cookies = tmp_path / "c.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    with pytest.raises(Exception):
        downloader.download("https://youtu.be/x", str(tmp_path), cookie_file=str(cookies), resume_attempts=0,
                            sleep=lambda s: None)
    assert seen.get("cookiefile") == str(cookies)
    assert "cookiesfrombrowser" not in seen


def _chrome():
    for p in ("/usr/bin/google-chrome", "/opt/pw-browsers/chromium/chrome-linux/chrome"):
        if os.path.isfile(p):
            return p
    return shutil.which("chromium") or shutil.which("google-chrome")


@pytest.mark.skipif(not _chrome(), reason="no Chromium browser installed")
def test_real_browser_sign_in_flow(data_home):
    """Starts a real (headless) browser, 'signs in' by setting the login cookie, checks it is saved."""
    window = ytlogin.LoginWindow(_chrome(), url="about:blank")
    window.open(["--headless=new", "--no-sandbox"])

    def sign_in_later():
        for _ in range(60):
            if window.ready():
                break
            time.sleep(0.5)
        time.sleep(1)
        window._call("Storage.setCookies", {"cookies": [
            {"name": "LOGIN_INFO", "value": "AFmm:test", "domain": ".youtube.com", "path": "/", "secure": True,
             "expires": time.time() + 86400}]})

    threading.Thread(target=sign_in_later, daemon=True).start()
    path = ytlogin.wait_for_login(window, poll=0.5, timeout=60)
    assert ytlogin.signed_in()
    assert "\tLOGIN_INFO\tAFmm:test" in open(path).read()
    time.sleep(1)
    assert not window.ready()  # window closed after saving
