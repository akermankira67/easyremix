"""EasyRemix for Android (Kivy).

The same engine as the desktop app (``easyremix`` package) with a touch interface.
On Android the numpy DSP backend and PyAV are used automatically, because pedalboard,
scipy and the ffmpeg program are not available there.

Files are created in the app's private folder and then copied into the shared
Music / Movies / Download folders (subfolder "EasyRemix") so other apps can see them.

The app also runs on a desktop for testing: ``python main.py`` (needs kivy).
"""
from __future__ import annotations

import os
import shutil
import threading
import time
import traceback

from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.properties import BooleanProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.uix.scrollview import ScrollView
from kivy.uix.slider import Slider
from kivy.uix.spinner import Spinner
from kivy.uix.switch import Switch
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.textinput import TextInput
from kivy.utils import get_color_from_hex, platform

from easyremix import APP_NAME, __version__
from easyremix.audio_io import EXPORT_FORMATS, SAMPLE_RATE, extract_audio, load_audio, save_audio
from easyremix.theory import describe_speed, speed_suggestions

ANDROID = platform == "android"
ACCENT = get_color_from_hex("#7c4dff")
GREEN = get_color_from_hex("#2e7d32")
GREY = get_color_from_hex("#424242")
MUTED = get_color_from_hex("#9e9e9e")
CARD = get_color_from_hex("#1e1e24")
PREVIEW_SECONDS = 25
ROW = dp(48)

Window.clearcolor = get_color_from_hex("#121216")
if not ANDROID:
    Window.size = (420, 860)


# ---------------------------------------------------------------- platform helpers
def work_dir() -> str:
    """Private folder for downloads/exports before they are copied to shared storage."""
    app = App.get_running_app()
    base = app.user_data_dir if app else os.path.expanduser("~/.easyremix")
    path = os.path.join(base, "work")
    os.makedirs(path, exist_ok=True)
    return path


def clean_work_dir(max_age_hours: float = 24) -> None:
    now = time.time()
    for name in os.listdir(work_dir()):
        p = os.path.join(work_dir(), name)
        try:
            if now - os.path.getmtime(p) > max_age_hours * 3600:
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        except OSError:
            pass


def publish(private_path: str) -> str:
    """Copy a finished file to shared storage. Returns a human-readable location."""
    if ANDROID:
        from androidstorage4kivy import SharedStorage

        uri = SharedStorage().copy_to_shared(private_path)
        if uri is None:
            raise RuntimeError("Could not save the file to the phone's shared storage.")
        mime = SharedStorage().get_file_MIME_type(os.path.basename(private_path)) or ""
        folder = "Music" if mime.startswith("audio") else "Movies" if mime.startswith("video") else "Download"
        return f"{folder}/{APP_NAME}/{os.path.basename(private_path)}"
    out_dir = os.path.join(os.path.expanduser("~"), "Music", APP_NAME)
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, os.path.basename(private_path))
    if os.path.abspath(dest) != os.path.abspath(private_path):
        shutil.copyfile(private_path, dest)
    return dest


def request_permissions() -> None:
    if not ANDROID:
        return
    try:
        from android.permissions import Permission, request_permissions as req
        from jnius import autoclass

        sdk = autoclass("android.os.Build$VERSION").SDK_INT
        if sdk >= 33:
            req([Permission.READ_MEDIA_AUDIO, Permission.READ_MEDIA_VIDEO])
        else:
            req([Permission.READ_EXTERNAL_STORAGE, Permission.WRITE_EXTERNAL_STORAGE])
    except Exception:
        traceback.print_exc()


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


def parse_time(text: str) -> float:
    text = text.strip()
    if not text:
        return 0.0
    if ":" in text:
        m, s = text.split(":", 1)
        return int(m or 0) * 60 + float(s or 0)
    return float(text)


# ---------------------------------------------------------------- small widgets
class WrapLabel(Label):
    """Label that wraps its text to the available width and grows in height."""

    def __init__(self, **kw):
        kw.setdefault("halign", "left")
        kw.setdefault("valign", "middle")
        kw.setdefault("size_hint_y", None)
        kw.setdefault("font_size", sp(14))
        super().__init__(**kw)
        self.bind(width=lambda *_: setattr(self, "text_size", (self.width, None)),
                  texture_size=lambda *_: setattr(self, "height", max(self.texture_size[1], dp(22))))


def button(text, on_press, color=ACCENT, **kw):
    kw.setdefault("size_hint_y", None)
    kw.setdefault("height", ROW)
    kw.setdefault("font_size", sp(15))
    b = Button(text=text, background_normal="", background_color=color, **kw)
    b.bind(on_release=lambda *_: on_press())
    return b


def heading(text, sub=""):
    box = BoxLayout(orientation="vertical", size_hint_y=None, padding=(0, dp(12), 0, dp(2)))
    box.add_widget(WrapLabel(text=f"[b]{text}[/b]", markup=True, font_size=sp(18)))
    if sub:
        box.add_widget(WrapLabel(text=sub, color=MUTED, font_size=sp(13)))

    def fit(*_):
        box.height = sum(c.height for c in box.children) + dp(14)
    for c in box.children:
        c.bind(height=fit)
    fit()
    return box


class SliderRow(BoxLayout):
    """Label + slider + value, one row."""

    def __init__(self, label, lo, hi, value, fmt, step=0, on_change=None, **kw):
        super().__init__(orientation="horizontal", size_hint_y=None, height=ROW, spacing=dp(6), **kw)
        self.fmt = fmt
        self.on_change = on_change
        self.add_widget(Label(text=label, size_hint_x=0.34, halign="left", valign="middle",
                              font_size=sp(14), text_size=(dp(130), None), shorten=True))
        self.slider = Slider(min=lo, max=hi, value=value, step=step, size_hint_x=0.46,
                             cursor_size=(dp(26), dp(26)), value_track=True,
                             value_track_color=ACCENT)
        self.value_label = Label(text=fmt(value), size_hint_x=0.2, font_size=sp(13), color=MUTED)
        self.slider.bind(value=self._changed)
        self.add_widget(self.slider)
        self.add_widget(self.value_label)

    def _changed(self, _s, v):
        self.value_label.text = self.fmt(v)
        if self.on_change:
            self.on_change(v)

    @property
    def value(self):
        return float(self.slider.value)

    @value.setter
    def value(self, v):
        self.slider.value = float(v)


class SwitchRow(BoxLayout):
    def __init__(self, label, active=False, on_change=None, **kw):
        super().__init__(orientation="horizontal", size_hint_y=None, height=ROW, **kw)
        lab = WrapLabel(text=label, size_hint_x=0.75)
        self.add_widget(lab)
        self.switch = Switch(active=active, size_hint_x=0.25)
        if on_change:
            self.switch.bind(active=lambda *_: on_change())
        self.add_widget(self.switch)

    @property
    def value(self):
        return bool(self.switch.active)

    @value.setter
    def value(self, v):
        self.switch.active = bool(v)


def column():
    """Vertical list that grows with its content (for use inside a ScrollView)."""
    g = GridLayout(cols=1, size_hint_y=None, spacing=dp(4), padding=(dp(12), dp(8)))
    g.bind(minimum_height=g.setter("height"))
    return g


def scrolling(content):
    sv = ScrollView(do_scroll_x=False, bar_width=dp(6), scroll_type=["bars", "content"])
    sv.add_widget(content)
    return sv


def show_message(title, text):
    box = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10))
    box.add_widget(scrolling(_wrapped(text)))
    popup = Popup(title=title, content=box, size_hint=(0.92, 0.6))
    box.add_widget(button("OK", popup.dismiss))
    popup.open()


def _wrapped(text):
    c = column()
    c.add_widget(WrapLabel(text=text))
    return c


# ---------------------------------------------------------------- file picking
class Picker:
    """Opens the Android file chooser (or a simple path prompt on desktop)."""

    def __init__(self):
        self._callback = None
        self._chooser = None
        if ANDROID:
            from androidstorage4kivy import Chooser

            self._chooser = Chooser(self._chosen)

    def pick(self, mime: str, callback) -> None:
        self._callback = callback
        if self._chooser:
            self._chooser.choose_content(mime)
        else:
            self._desktop_prompt()

    def _chosen(self, uris):
        """Runs on the Android activity thread: copy the file into the app, then report."""
        def copy():
            try:
                from androidstorage4kivy import SharedStorage

                path = SharedStorage().copy_from_shared(uris[0]) if uris else None
            except Exception:
                traceback.print_exc()
                path = None
            if path:
                Clock.schedule_once(lambda dt: self._callback(path))
        threading.Thread(target=copy, daemon=True).start()

    def _desktop_prompt(self):
        from kivy.uix.filechooser import FileChooserListView

        box = BoxLayout(orientation="vertical", spacing=dp(6))
        fc = FileChooserListView(path=os.path.expanduser("~"))
        box.add_widget(fc)
        popup = Popup(title="Choose a file", content=box, size_hint=(0.95, 0.9))

        def ok():
            if fc.selection:
                popup.dismiss()
                self._callback(fc.selection[0])
        row = BoxLayout(size_hint_y=None, height=ROW, spacing=dp(6))
        row.add_widget(button("Cancel", popup.dismiss, GREY))
        row.add_widget(button("Open", ok))
        box.add_widget(row)
        popup.open()


# ---------------------------------------------------------------- the app
class EasyRemixApp(App):
    busy = BooleanProperty(False)

    def build(self):
        self.title = APP_NAME
        self.icon = "icon.png"
        self.picker = Picker()
        self.audio = None
        self.audio_path = ""
        self.key = None
        self.bpm = None
        self.last_download = ""
        self.extract_src = ""
        self._sound = None
        self._loading_preset = False
        self._last_extract = ""

        root = BoxLayout(orientation="vertical")
        head = BoxLayout(size_hint_y=None, height=dp(52), padding=(dp(14), dp(4)))
        title = Label(text=f"[b]{APP_NAME}[/b]  [color=9e9e9e][size=13sp]{__version__}[/size][/color]",
                      markup=True, font_size=sp(22), halign="left", valign="middle")
        title.bind(size=lambda *_: setattr(title, "text_size", title.size))
        head.add_widget(title)
        root.add_widget(head)

        self.tabs = TabbedPanel(do_default_tab=False, tab_width=Window.width / 3 - dp(2),
                                tab_height=dp(46), background_color=CARD)
        self.tab_download = TabbedPanelItem(text="Download")
        self.tab_extract = TabbedPanelItem(text="Extract")
        self.tab_remix = TabbedPanelItem(text="Remix")
        for t in (self.tab_download, self.tab_extract, self.tab_remix):
            self.tabs.add_widget(t)
        self.tab_download.add_widget(self._build_download())
        self.tab_extract.add_widget(self._build_extract())
        self.tab_remix.add_widget(self._build_remix())
        self.tabs.default_tab = self.tab_remix
        Clock.schedule_once(lambda dt: self.tabs.switch_to(self.tab_download))
        Window.bind(width=lambda *_: setattr(self.tabs, "tab_width", Window.width / 3 - dp(2)))
        root.add_widget(self.tabs)

        status = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(48), padding=(dp(12), dp(2)))
        self.status = Label(text="Ready.", font_size=sp(13), color=MUTED, halign="left", valign="middle",
                            shorten=True, shorten_from="right")
        self.status.bind(size=lambda *_: setattr(self.status, "text_size", self.status.size))
        self.progress = ProgressBar(max=1.0, value=0, size_hint_y=None, height=dp(8))
        status.add_widget(self.status)
        status.add_widget(self.progress)
        root.add_widget(status)
        return root

    def on_start(self):
        request_permissions()
        try:
            clean_work_dir()
        except Exception:
            pass

    def on_pause(self):
        return True  # keep running (downloads/exports) when the app goes to the background

    # ------------------------------------------------------------ background tasks
    def set_status(self, text, frac=None):
        self.status.text = text
        if frac is not None:
            self.progress.value = max(0.0, min(1.0, frac))

    def run_task(self, label, work, on_done=None, on_error=None):
        if self.busy:
            show_message(APP_NAME, "Please wait, another task is still running.")
            return
        self.busy = True
        self.set_status(label, 0)

        def progress(frac, msg=None):
            Clock.schedule_once(lambda dt: self.set_status(msg or label, frac))

        def runner():
            try:
                result = work(progress)
            except Exception as exc:  # noqa: BLE001 - show every error to the user
                traceback.print_exc()
                Clock.schedule_once(lambda dt, e=exc: self._task_failed(e, on_error))
                return
            Clock.schedule_once(lambda dt: self._task_done(result, on_done))

        threading.Thread(target=runner, daemon=True).start()

    def _task_done(self, result, on_done):
        self.busy = False
        self.progress.value = 1
        if on_done:
            on_done(result)

    def _task_failed(self, exc, on_error):
        self.busy = False
        self.progress.value = 0
        self.set_status("Something went wrong.")
        if on_error:
            on_error(exc)
        else:
            show_message("Error", str(exc) or exc.__class__.__name__)

    # ------------------------------------------------------------ 1. download
    def _build_download(self):
        from easyremix.downloader import QUALITIES

        c = column()
        c.add_widget(heading("Download", "YouTube and 1000+ other sites. Paste a link and press Download."))
        self.url = TextInput(hint_text="https://www.youtube.com/watch?v=...", multiline=False,
                             size_hint_y=None, height=ROW, font_size=sp(15))
        c.add_widget(self.url)
        c.add_widget(button("Paste link", lambda: setattr(self.url, "text", (Clipboard.paste() or "").strip()),
                            GREY))
        self.dl_mode = Spinner(text="Audio only (mp3)", values=["Audio only (mp3)", "Video (mp4)"],
                               size_hint_y=None, height=ROW)
        self.dl_quality = Spinner(text="Best available", values=list(QUALITIES), size_hint_y=None, height=ROW)
        self.dl_quality.disabled = True  # quality only matters for video
        self.dl_mode.bind(text=lambda _s, t: setattr(self.dl_quality, "disabled", t.startswith("Audio")))
        c.add_widget(self.dl_mode)
        c.add_widget(self.dl_quality)
        from easyremix.tools import merge_ffmpeg_path

        if not merge_ffmpeg_path():
            c.add_widget(WrapLabel(text="Videos are downloaded as one file that already contains picture and "
                                        "sound (on YouTube up to 360p). Audio-only downloads always get the best "
                                        "sound quality.", color=MUTED, font_size=sp(12)))
        self.dl_button = button("Download", self.start_download)
        c.add_widget(self.dl_button)
        self.dl_after = BoxLayout(size_hint_y=None, height=ROW, spacing=dp(6), opacity=0, disabled=True)
        self.dl_after.add_widget(button("Remix it", lambda: self.send_to_remix(self.last_download), GREEN))
        self.dl_after.add_widget(button("Extract audio", lambda: self.send_to_extract(self.last_download), GREY))
        c.add_widget(self.dl_after)
        c.add_widget(heading("Log"))
        self.log = TextInput(readonly=True, size_hint_y=None, height=dp(260), font_size=sp(12),
                             background_color=CARD, foreground_color=(0.85, 0.85, 0.85, 1))
        c.add_widget(self.log)
        return scrolling(c)

    def append_log(self, text):
        def add(dt):
            self.log.text = (self.log.text + text.rstrip() + "\n")[-20000:]
            self.log.cursor = (0, len(self.log._lines))
        Clock.schedule_once(add)

    def start_download(self):
        from easyremix import downloader

        url = self.url.text.strip()
        if not url:
            show_message(APP_NAME, "Paste a video link first.")
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        audio_only = self.dl_mode.text.startswith("Audio")
        quality = self.dl_quality.text
        out_dir = os.path.join(work_dir(), "downloads")
        self.append_log(f"> {url}")

        def work(progress):
            path = downloader.download(url, out_dir, audio_only=audio_only, quality=quality,
                                       on_progress=progress, on_log=self.append_log)
            progress(1.0, "Saving to the phone...")
            return path, publish(path)

        def done(res):
            path, where = res
            self.last_download = path
            self.dl_after.opacity, self.dl_after.disabled = 1, False
            self.append_log(f"Saved: {where}")
            self.set_status(f"Downloaded: {where}", 1)

        def failed(exc):
            self.append_log(f"Failed: {exc}")
            show_message("Download failed", str(exc))

        self.run_task("Starting download...", work, done, failed)

    # ------------------------------------------------------------ 2. extract
    def _build_extract(self):
        c = column()
        c.add_widget(heading("Extract audio", "Take the sound out of any video and save it as an audio file."))
        c.add_widget(button("Choose a video...", lambda: self.picker.pick("video/*", self._extract_chosen)))
        self.extract_label = WrapLabel(text="No video chosen.", color=MUTED)
        c.add_widget(self.extract_label)
        self.extract_fmt = Spinner(text="mp3", values=list(EXPORT_FORMATS), size_hint_y=None, height=ROW)
        c.add_widget(self.extract_fmt)
        c.add_widget(button("Extract", self.start_extract))
        self.extract_after = button("Remix the audio", lambda: self.send_to_remix(self._last_extract), GREEN,
                                    opacity=0, disabled=True)
        c.add_widget(self.extract_after)
        return scrolling(c)

    def _extract_chosen(self, path):
        self.extract_src = path
        self.extract_label.text = os.path.basename(path)
        self.extract_label.color = (1, 1, 1, 1)

    def send_to_extract(self, path):
        if path:
            self._extract_chosen(path)
            self.tabs.switch_to(self.tab_extract)

    def start_extract(self):
        src = self.extract_src
        if not src or not os.path.isfile(src):
            show_message(APP_NAME, "Choose a video first.")
            return
        fmt = self.extract_fmt.text
        out = os.path.join(work_dir(), os.path.splitext(os.path.basename(src))[0] + "." + fmt)

        def work(progress):
            progress(0.2, "Extracting audio...")
            extract_audio(src, out)
            progress(0.9, "Saving to the phone...")
            return publish(out)

        def done(where):
            self._last_extract = out
            self.extract_after.opacity, self.extract_after.disabled = 1, False
            self.set_status(f"Saved: {where}", 1)
            show_message("Done", f"The audio was saved to\n{where}")

        self.run_task("Extracting audio...", work, done)

    # ------------------------------------------------------------ 3. remix
    def _build_remix(self):
        from easyremix.dsp import AMBIENCE, EQ_LABELS, EQ_PRESETS, REMIX_PRESETS

        outer = BoxLayout(orientation="vertical")
        c = column()
        c.add_widget(button("Open a song...", lambda: self.picker.pick("audio/*", self.load_track)))
        self.track_label = WrapLabel(text="No song loaded. You can also open a video.", color=MUTED)
        c.add_widget(self.track_label)
        self.info_label = WrapLabel(text="", markup=True, font_size=sp(16))
        c.add_widget(self.info_label)

        presets = BoxLayout(size_hint_y=None, height=ROW, spacing=dp(6))
        self.preset_box = Spinner(text="One-click presets", values=list(REMIX_PRESETS), size_hint_x=0.7)
        self.preset_box.bind(text=lambda _s, name: self.apply_remix_preset(name))
        presets.add_widget(self.preset_box)
        presets.add_widget(button("Reset", self.reset_remix, GREY, size_hint_x=0.3))
        c.add_widget(presets)

        self.rows: dict[str, SliderRow | SwitchRow] = {}
        pct = lambda v: f"{v * 100:.0f}%"  # noqa: E731

        def slider(attr, label, lo, hi, default, fmt, step=0):
            r = SliderRow(label, lo, hi, default, fmt, step,
                          on_change=(lambda v: self._update_speed_info())
                          if attr in ("speed", "pitch_semitones") else None)
            self.rows[attr] = r
            c.add_widget(r)

        c.add_widget(heading("Speed & Pitch", "Classic mode changes the pitch too, like a record player."))
        slider("speed", "Speed", 0.5, 1.5, 1.0, lambda v: f"x{v:.3f}")
        slider("pitch_semitones", "Extra pitch", -12, 12, 0.0, lambda v: f"{v:+.1f} st", step=0.5)
        self.rows["keep_pitch"] = SwitchRow("Keep the original pitch (tempo only)",
                                            on_change=self._update_speed_info)
        c.add_widget(self.rows["keep_pitch"])
        self.speed_info = WrapLabel(text="", markup=True, font_size=sp(13))
        c.add_widget(self.speed_info)
        c.add_widget(WrapLabel(text="Best speeds: these land exactly on a real key, so the song stays in tune. "
                                    "Tap one to use it.", color=MUTED, font_size=sp(12)))
        self.sugg_box = column()
        self.sugg_box.padding = (0, 0)
        c.add_widget(self.sugg_box)

        c.add_widget(heading("Equalizer", "10 bands, ±12 dB"))
        self.eq_preset = Spinner(text="Flat", values=list(EQ_PRESETS), size_hint_y=None, height=ROW)
        self.eq_preset.bind(text=lambda _s, name: self.apply_eq_preset(name))
        c.add_widget(self.eq_preset)
        self.eq_rows = []
        for lab in EQ_LABELS:
            r = SliderRow(f"{lab} Hz", -12, 12, 0.0, lambda v: f"{v:+.0f} dB", step=0.5,
                          on_change=lambda v: self._eq_changed())
            self.eq_rows.append(r)
            c.add_widget(r)

        c.add_widget(heading("Effects", "Move a slider above 0 to switch an effect on."))
        slider("reverb_mix", "Reverb", 0, 1, 0.0, pct)
        slider("reverb_size", "Room size", 0, 1, 0.7, pct)
        slider("echo_mix", "Echo", 0, 1, 0.0, pct)
        slider("echo_delay", "Echo delay", 0.05, 1.5, 0.35, lambda v: f"{v * 1000:.0f} ms")
        slider("echo_feedback", "Echo repeats", 0, 0.9, 0.35, pct)
        slider("chorus_mix", "Chorus", 0, 1, 0.0, pct)
        slider("lofi_amount", "Lo-Fi", 0, 1, 0.0, pct)
        slider("distortion_db", "Distortion", 0, 30, 0.0, lambda v: f"{v:.0f} dB")
        slider("rotate_8d", "8D rotation", 0, 1, 0.0, pct)
        slider("rotate_speed", "Rotation speed", 0.03, 0.5, 0.12, lambda v: f"{v:.2f} Hz")

        c.add_widget(heading("Ambience", "Atmospheric sounds under the music (generated, royalty-free)."))
        for attr, (label, _) in AMBIENCE.items():
            slider(attr, label, 0, 1, 0.0, pct)

        c.add_widget(heading("Volume & Output", "A built-in limiter always prevents clipping."))
        slider("volume_db", "Volume", -24, 12, 0.0, lambda v: f"{v:+.1f} dB", step=0.5)
        slider("fade_in", "Fade in", 0, 10, 0.0, lambda v: f"{v:.1f} s", step=0.25)
        slider("fade_out", "Fade out", 0, 15, 0.0, lambda v: f"{v:.1f} s", step=0.25)
        self.rows["normalize"] = SwitchRow("Normalize loudness (as loud as modern tracks)")
        c.add_widget(self.rows["normalize"])
        outer.add_widget(scrolling(c))

        # action bar, always visible
        bar = GridLayout(cols=1, size_hint_y=None, spacing=dp(4), padding=(dp(8), dp(6)))
        bar.bind(minimum_height=bar.setter("height"))
        r1 = BoxLayout(size_hint_y=None, height=ROW, spacing=dp(6))
        self.preview_from = TextInput(text="0:30", multiline=False, size_hint_x=0.22, font_size=sp(15),
                                      hint_text="from")
        r1.add_widget(self.preview_from)
        r1.add_widget(button("Preview", self.preview, GREEN, size_hint_x=0.3))
        r1.add_widget(button("Original", lambda: self.preview(original=True), GREY, size_hint_x=0.26))
        r1.add_widget(button("Stop", self.stop_sound, GREY, size_hint_x=0.22))
        r2 = BoxLayout(size_hint_y=None, height=ROW, spacing=dp(6))
        self.export_fmt = Spinner(text="mp3", values=list(EXPORT_FORMATS), size_hint_x=0.3)
        r2.add_widget(self.export_fmt)
        r2.add_widget(button("Export remix", self.export, size_hint_x=0.7))
        bar.add_widget(r1)
        bar.add_widget(r2)
        outer.add_widget(bar)

        self._render_suggestions()
        self._update_speed_info()
        return outer

    def _eq_changed(self):
        if not self._loading_preset and self.eq_preset.text != "Custom":
            self._loading_preset = True
            self.eq_preset.text = "Custom"
            self._loading_preset = False

    def apply_eq_preset(self, name):
        from easyremix.dsp import EQ_PRESETS

        if self._loading_preset or name not in EQ_PRESETS:
            return
        self._set_eq(EQ_PRESETS[name], name)

    def _set_eq(self, gains, name):
        self._loading_preset = True
        for row, g in zip(self.eq_rows, gains):
            row.value = g
        self.eq_preset.text = name
        self._loading_preset = False

    def _set_speed(self, speed):
        self.rows["speed"].value = speed
        self.rows["keep_pitch"].value = False
        self._update_speed_info()

    def _update_speed_info(self, *_):
        if not hasattr(self, "speed_info") or "keep_pitch" not in self.rows:
            return
        text = describe_speed(self.rows["speed"].value, self.key, self.bpm, self.rows["keep_pitch"].value,
                              self.rows["pitch_semitones"].value)
        color = "ff8a80" if "OUT OF TUNE" in text else "81c784"
        self.speed_info.text = f"[color={color}]{text}[/color]"

    def _render_suggestions(self):
        self.sugg_box.clear_widgets()
        for s in speed_suggestions(self.key, self.bpm):
            key_txt = f"{s.new_key.short} ({s.new_key.camelot})" if s.new_key else ""
            bpm_txt = f"  {s.new_bpm:.0f} BPM" if s.new_bpm else ""
            # the default font has no star glyphs, so the rating is written as "4/5"
            b = button(f"{s.stars}/5 • {s.label}  x{s.speed:.3f}  {s.semitones:+d} st  {key_txt}{bpm_txt}",
                       lambda sp_=s.speed: self._set_speed(sp_), CARD, font_size=sp(13))
            self.sugg_box.add_widget(b)
            self.sugg_box.add_widget(WrapLabel(text=s.description, color=MUTED, font_size=sp(12)))

    def reset_remix(self):
        from easyremix.dsp import RemixSettings

        defaults = RemixSettings()
        for attr, row in self.rows.items():
            row.value = getattr(defaults, attr)
        self._set_eq([0.0] * 10, "Flat")
        self._loading_preset = True
        self.preset_box.text = "One-click presets"
        self._loading_preset = False
        self._update_speed_info()

    def apply_remix_preset(self, name):
        from easyremix.dsp import EQ_PRESETS, REMIX_PRESETS

        preset = REMIX_PRESETS.get(name)
        if not preset or self._loading_preset:
            return
        self.reset_remix()
        for attr, value in preset.items():
            if attr == "eq_gains":
                match = next((k for k, v in EQ_PRESETS.items() if v == value), "Custom")
                self._set_eq(value, match)
            elif attr in self.rows:
                self.rows[attr].value = value
        self._loading_preset = True
        self.preset_box.text = name
        self._loading_preset = False
        self._update_speed_info()
        self.set_status(f"Preset '{name}' applied - press Preview to listen.")

    def current_settings(self):
        from easyremix.dsp import RemixSettings

        s = RemixSettings()
        for attr, row in self.rows.items():
            setattr(s, attr, row.value)
        s.eq_gains = [r.value for r in self.eq_rows]
        return s

    def send_to_remix(self, path):
        if path:
            self.tabs.switch_to(self.tab_remix)
            self.load_track(path)

    def load_track(self, path):
        self.track_label.text = os.path.basename(path)
        self.track_label.color = (1, 1, 1, 1)

        def work(progress):
            from easyremix.analysis import detect_bpm, detect_key

            progress(0.1, "Loading song...")
            audio = load_audio(path)
            progress(0.5, "Detecting the musical key...")
            key = detect_key(audio, SAMPLE_RATE)
            progress(0.8, "Detecting the tempo (BPM)...")
            bpm = detect_bpm(audio, SAMPLE_RATE)
            return audio, key, bpm

        def done(res):
            audio, kres, bpm = res
            self.audio, self.audio_path = audio, path
            self.key, self.bpm = kres.key, (bpm or None)
            bpm_txt = f"~{bpm:.0f}" if bpm else "–"
            alts = ", ".join(k.short for k, _ in kres.alternatives[:2])
            self.info_label.text = (f"Key: [b]{kres.key.name}[/b] ({kres.key.camelot})   BPM: [b]{bpm_txt}[/b]   "
                                    f"Length: [b]{fmt_time(audio.shape[1] / SAMPLE_RATE)}[/b]\n"
                                    f"[size=12sp][color=9e9e9e]confidence {kres.confidence * 100:.0f}%  •  "
                                    f"could also be: {alts}[/color][/size]")
            self._render_suggestions()
            self._update_speed_info()
            self.set_status(f"Loaded {os.path.basename(path)} - pick a preset or change the settings.", 1)

        self.run_task("Loading song...", work, done)

    def _require_audio(self):
        if self.audio is None:
            show_message(APP_NAME, "Open a song first (button 'Open a song...').")
            return False
        return True

    def stop_sound(self):
        if self._sound:
            try:
                self._sound.stop()
            except Exception:
                pass

    def preview(self, original=False):
        if not self._require_audio():
            return
        try:
            start = parse_time(self.preview_from.text)
        except ValueError:
            start = 0.0
        total = self.audio.shape[1] / SAMPLE_RATE
        if start >= total - 1:
            start = 0.0
        settings = self.current_settings()
        settings.fade_in = settings.fade_out = 0.0
        a = int(start * SAMPLE_RATE)
        span = PREVIEW_SECONDS * max(1.0, settings.speed if not original else 1.0)
        clip = self.audio[:, a: a + int(span * SAMPLE_RATE)]
        path = os.path.join(work_dir(), f"preview-{int(time.time() * 1000)}.wav")
        self.stop_sound()

        def work(progress):
            from easyremix.dsp import process

            audio = clip if original else process(clip, SAMPLE_RATE, settings, progress)
            save_audio(audio, path)
            return path

        def done(p):
            from kivy.core.audio import SoundLoader

            if self._sound:
                self._sound.unload()
            self._sound = SoundLoader.load(p)
            if not self._sound:
                show_message(APP_NAME, "This phone could not play the preview.")
                return
            self._sound.play()
            what = "original" if original else "remix"
            self.set_status(f"Playing the {what} from {fmt_time(start)} ({PREVIEW_SECONDS} s)...", 1)

        self.run_task("Rendering preview...", work, done)

    def export(self):
        if not self._require_audio():
            return
        fmt = self.export_fmt.text
        base = os.path.splitext(os.path.basename(self.audio_path))[0]
        suffix = self.preset_box.text
        suffix = "remix" if suffix == "One-click presets" else suffix.lower().replace(" ", "_").replace("+", "and")
        out = os.path.join(work_dir(), f"{base} ({suffix}).{fmt}")
        settings = self.current_settings()
        audio = self.audio

        def work(progress):
            from easyremix.dsp import process

            result = process(audio, SAMPLE_RATE, settings, lambda f, m: progress(f * 0.9, m))
            progress(0.92, "Saving file...")
            save_audio(result, out)
            progress(0.97, "Saving to the phone...")
            return publish(out)

        def done(where):
            self.set_status(f"Exported: {where}", 1)
            show_message("Remix saved", f"Your remix was saved to\n{where}\n\nYou'll find it in your music app.")

        self.run_task("Rendering remix (this can take a minute)...", work, done)


if __name__ == "__main__":
    EasyRemixApp().run()
