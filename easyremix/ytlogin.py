"""Sign in to YouTube once, inside EasyRemix, so downloads pass YouTube's "confirm you're not a bot" check.

Why not just read the cookies of the user's everyday browser (yt-dlp --cookies-from-browser)?
On Windows that often fails: Chrome / Opera / Edge keep their cookie file locked while they run
(and Opera keeps running in the background after its window is closed), and new Chrome versions
encrypt cookies so that other programs cannot read them.

So instead EasyRemix opens a browser window with its *own* small profile and a local debugging
port. The user signs in there; EasyRemix asks the browser for the cookies over that port (no
files, no decryption), saves them as a cookies.txt for yt-dlp and closes the window. The login
stays in the separate profile, so signing in again later is one click.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

LOGIN_URL = ("https://accounts.google.com/ServiceLogin?service=youtube&passive=true"
             "&continue=https%3A%2F%2Fwww.youtube.com%2F")
# cookies that only exist on youtube.com when someone is signed in
SIGNED_IN_COOKIES = ("LOGIN_INFO", "__Secure-3PSID", "SAPISID")
KEEP_DOMAINS = ("youtube.com", "google.com", "youtu.be")


# ----------------------------------------------------------------------------- places
def app_dir() -> str:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    path = os.path.join(base, "EasyRemix")
    os.makedirs(path, exist_ok=True)
    return path


def cookie_file() -> str:
    return os.path.join(app_dir(), "youtube-cookies.txt")


def profile_dir() -> str:
    return os.path.join(app_dir(), "youtube-login-browser")


def signed_in() -> bool:
    """True if a saved YouTube login exists."""
    path = cookie_file()
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    return any(f"\t{name}\t" in text for name in SIGNED_IN_COOKIES)


def saved_at() -> float | None:
    try:
        return os.path.getmtime(cookie_file())
    except OSError:
        return None


def sign_out() -> None:
    for path in (cookie_file(),):
        try:
            os.remove(path)
        except OSError:
            pass
    shutil.rmtree(profile_dir(), ignore_errors=True)


# ----------------------------------------------------------------------------- browsers
def _candidates() -> list[tuple[str, list[str]]]:
    """(name, possible executable paths) for Chromium browsers, most common first."""
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA", "")
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")

        def under(*rel):
            return [os.path.join(base, *rel) for base in (local, pf, pf86) if base]

        return [
            ("Opera", under("Programs", "Opera", "opera.exe") + under("Programs", "Opera", "launcher.exe")
             + under("Opera", "opera.exe") + under("Opera", "launcher.exe")),
            ("Opera GX", under("Programs", "Opera GX", "opera.exe") + under("Programs", "Opera GX", "launcher.exe")),
            ("Chrome", under("Google", "Chrome", "Application", "chrome.exe")),
            ("Edge", under("Microsoft", "Edge", "Application", "msedge.exe")),
            ("Brave", under("BraveSoftware", "Brave-Browser", "Application", "brave.exe")),
            ("Vivaldi", under("Vivaldi", "Application", "vivaldi.exe")),
            ("Yandex", under("Yandex", "YandexBrowser", "Application", "browser.exe")),
        ]
    if sys.platform == "darwin":
        app = "/Applications/{0}.app/Contents/MacOS/{1}"
        return [
            ("Chrome", [app.format("Google Chrome", "Google Chrome")]),
            ("Edge", [app.format("Microsoft Edge", "Microsoft Edge")]),
            ("Brave", [app.format("Brave Browser", "Brave Browser")]),
            ("Opera", [app.format("Opera", "Opera")]),
            ("Vivaldi", [app.format("Vivaldi", "Vivaldi")]),
        ]

    def which(*names):
        return [p for p in (shutil.which(n) for n in names) if p]

    return [
        ("Chrome", which("google-chrome", "google-chrome-stable")),
        ("Chromium", which("chromium", "chromium-browser")),
        ("Edge", which("microsoft-edge", "microsoft-edge-stable")),
        ("Brave", which("brave-browser", "brave")),
        ("Opera", which("opera")),
        ("Vivaldi", which("vivaldi", "vivaldi-stable")),
    ]


# Windows "default browser" ProgId -> our name
_PROGIDS = {"opera": "Opera", "chrome": "Chrome", "msedge": "Edge", "brave": "Brave", "vivaldi": "Vivaldi",
            "yandex": "Yandex"}


def _default_browser_name() -> str | None:
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\Shell\Associations"
                                                       r"\UrlAssociations\https\UserChoice")
        progid = str(winreg.QueryValueEx(key, "ProgId")[0]).lower()
        winreg.CloseKey(key)
    except Exception:
        return None
    if "gx" in progid and "opera" in progid:
        return "Opera GX"
    for part, name in _PROGIDS.items():
        if part in progid:
            return name
    return None


def find_browsers() -> list[tuple[str, str]]:
    """Installed Chromium browsers as (name, exe); the user's default browser comes first."""
    found = []
    for name, paths in _candidates():
        exe = next((p for p in paths if p and os.path.isfile(p)), None)
        if exe:
            found.append((name, exe))
    default = _default_browser_name()
    found.sort(key=lambda item: item[0] != default)
    return found


# ----------------------------------------------------------------------------- cookies.txt
def to_netscape(cookies: list[dict]) -> str:
    """Chrome DevTools cookies -> Netscape cookies.txt text (what yt-dlp --cookies reads)."""
    lines = ["# Netscape HTTP Cookie File", "# Saved by EasyRemix (YouTube sign-in)", ""]
    for c in cookies:
        domain = c.get("domain", "")
        if not any(domain.endswith(d) for d in KEEP_DOMAINS):
            continue
        name, value = c.get("name", ""), c.get("value", "")
        if not name or "\t" in value or "\n" in value:
            continue
        expires = c.get("expires", -1)
        expires = int(expires) if not c.get("session") and expires and expires > 0 else 0
        lines.append("\t".join([
            domain,
            "TRUE" if domain.startswith(".") else "FALSE",
            c.get("path") or "/",
            "TRUE" if c.get("secure") else "FALSE",
            str(expires),
            name,
            value,
        ]))
    return "\n".join(lines) + "\n"


def has_login(cookies: list[dict]) -> bool:
    return any(c.get("name") in SIGNED_IN_COOKIES and c.get("domain", "").endswith("youtube.com")
               for c in cookies)


# ----------------------------------------------------------------------------- DevTools
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_json(url: str, timeout: float = 2.0):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # localhost, never via a proxy
    with opener.open(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class LoginWindow:
    """A browser window with EasyRemix's own profile, readable over a local DevTools port."""

    def __init__(self, browser_exe: str, url: str = LOGIN_URL, profile: str | None = None):
        self.exe = browser_exe
        self.url = url
        self.profile = profile or profile_dir()
        self.port = _free_port()
        self.proc: subprocess.Popen | None = None

    def open(self, extra_args: list[str] | None = None) -> None:
        os.makedirs(self.profile, exist_ok=True)
        args = [self.exe, f"--user-data-dir={self.profile}", f"--remote-debugging-port={self.port}",
                "--remote-debugging-address=127.0.0.1", "--no-first-run", "--no-default-browser-check",
                "--new-window", *(extra_args or []), self.url]
        flags = 0x08000000 if sys.platform.startswith("win") else 0  # CREATE_NO_WINDOW for the console
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=flags)

    def _ws_url(self) -> str | None:
        try:
            return _http_json(f"http://127.0.0.1:{self.port}/json/version")["webSocketDebuggerUrl"]
        except Exception:
            return None

    def ready(self) -> bool:
        return self._ws_url() is not None

    def _call(self, method: str, params: dict | None = None):
        from websockets.sync.client import connect

        ws_url = self._ws_url()
        if not ws_url:
            raise ConnectionError("The browser window is closed.")
        try:
            conn = connect(ws_url, max_size=None, open_timeout=5, proxy=None)  # websockets >= 15
        except TypeError:
            conn = connect(ws_url, max_size=None, open_timeout=5)
        with conn as ws:
            ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
            deadline = time.time() + 10
            while time.time() < deadline:
                msg = json.loads(ws.recv(timeout=10))
                if msg.get("id") == 1:
                    if "error" in msg:
                        raise RuntimeError(msg["error"].get("message", "DevTools error"))
                    return msg.get("result", {})
        raise TimeoutError("The browser did not answer.")

    def cookies(self) -> list[dict]:
        return self._call("Storage.getCookies").get("cookies", [])

    def close(self) -> None:
        try:
            self._call("Browser.close")
        except Exception:
            pass
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()


def save_cookies(cookies: list[dict], path: str | None = None) -> str:
    path = path or cookie_file()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(to_netscape(cookies))
    os.replace(tmp, path)
    return path


def wait_for_login(window: LoginWindow, should_stop=lambda: False, timeout: float = 900,
                   poll: float = 1.5, on_status=None) -> str:
    """Wait until the user is signed in in ``window``; save the cookies and close it.

    Returns the cookies.txt path. Raises ``LoginCancelled`` if the window was closed or
    ``should_stop()`` became true, ``TimeoutError`` after ``timeout`` seconds.
    """
    start = time.time()
    seen_browser = False
    while time.time() - start < timeout:
        if should_stop():
            window.close()
            raise LoginCancelled("Sign-in cancelled.")
        if window.ready():
            seen_browser = True
            try:
                cookies = window.cookies()
            except Exception:
                cookies = []
            if has_login(cookies):
                path = save_cookies(cookies)
                window.close()
                return path
            if on_status:
                on_status("Waiting for you to sign in in the browser window...")
        elif seen_browser:
            raise LoginCancelled("The browser window was closed before signing in.")
        elif time.time() - start > 40:
            raise LoginCancelled("The browser did not start. Close all its windows (and its tray icon) "
                                 "and try again, or pick another browser.")
        time.sleep(poll)
    window.close()
    raise TimeoutError("Signing in took too long.")


class LoginCancelled(RuntimeError):
    pass
