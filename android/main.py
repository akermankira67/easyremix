"""EasyRemix for Android (Kivy).

The same engine as the desktop app (``easyremix`` package) with a touch interface:

* Get   - from a link (YouTube, TikTok, Instagram ...) or a file on the phone:
          full video, audio only, or straight into Remix. Share sends the link
          (or a saved file) through Android's own share sheet.
* Remix - pick a style, adjust speed/pitch, preview, export, share.

On Android the numpy DSP backend and PyAV are used automatically, because pedalboard,
scipy and a full ffmpeg are not available there. Files are created in the app's private
folder and then copied into the shared Music / Movies folders (subfolder "EasyRemix").

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
from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.properties import BooleanProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.uix.scrollview import ScrollView
from kivy.uix.slider import Slider
from kivy.uix.spinner import Spinner
from kivy.uix.switch import Switch
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.textinput import TextInput
from kivy.utils import get_color_from_hex as hexc, platform

from easyremix import APP_NAME, __version__
from easyremix.audio_io import EXPORT_FORMATS, SAMPLE_RATE, extract_audio, load_audio, save_audio
from easyremix.theory import describe_speed, speed_suggestions

ANDROID = platform == "android"
ACCENT = hexc("#7c4dff")
GREEN = hexc("#2e9d5b")
NEUTRAL = hexc("#2e2e38")
DIMMED = hexc("#24242c")
MUTED = hexc("#9e9eaa")
CARD = hexc("#1b1b22")
GOOD = hexc("#81c784")
BAD = hexc("#ff8a80")
PREVIEW_SECONDS = 25
ROW = dp(48)
FORMAT_HINTS = {
    "mp3": "MP3 - small and plays everywhere",
    "wav": "WAV - lossless, very large",
    "flac": "FLAC - lossless, compressed",
    "m4a": "M4A - small, good on Apple devices",
    "ogg": "OGG - small, open format",
}

ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")

Window.clearcolor = hexc("#111116")
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


def publish(private_path: str):
    """Copy a finished file to shared storage.

    Returns (human-readable location, handle for sharing: a content URI on Android).
    """
    if ANDROID:
        from androidstorage4kivy import SharedStorage

        uri = SharedStorage().copy_to_shared(private_path)
        if uri is None:
            raise RuntimeError("Could not save the file to the phone's shared storage.")
        mime = SharedStorage().get_file_MIME_type(os.path.basename(private_path)) or ""
        folder = "Music" if mime.startswith("audio") else "Movies" if mime.startswith("video") else "Download"
        return f"{folder}/{APP_NAME}/{os.path.basename(private_path)}", uri
    out_dir = os.path.join(os.path.expanduser("~"), "Music", APP_NAME)
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, os.path.basename(private_path))
    if os.path.abspath(dest) != os.path.abspath(private_path):
        shutil.copyfile(private_path, dest)
    return dest, dest


def private_share_dir() -> str:
    """Temporary folder for videos being shared: the app's private cache, which the
    gallery and file managers do not see. Old shares are deleted automatically."""
    from easyremix.share import share_dir

    if ANDROID:
        from jnius import autoclass

        cache = autoclass("org.kivy.android.PythonActivity").mActivity.getCacheDir().getAbsolutePath()
        return share_dir(cache)
    return share_dir()


def send_private_file(path: str) -> bool:
    """Open the share sheet with a file from private_share_dir() (FileProvider URI with a
    temporary read permission for the chosen app). Returns False if not possible."""
    import mimetypes

    if not ANDROID:
        from easyremix.share import copy_file_to_clipboard

        copy_file_to_clipboard(path)
        show_message("Send video", f"On a phone this opens the share menu.\n\nFile: {path}")
        return True
    try:
        from jnius import autoclass, cast

        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        FileProvider = autoclass("androidx.core.content.FileProvider")
        Intent = autoclass("android.content.Intent")
        JFile = autoclass("java.io.File")
        JString = autoclass("java.lang.String")
        uri = FileProvider.getUriForFile(activity, activity.getPackageName() + ".share", JFile(path))
        mime = mimetypes.guess_type(path)[0] or "video/mp4"
        send = Intent(Intent.ACTION_SEND)
        send.setType(mime)
        send.putExtra(Intent.EXTRA_STREAM, cast("android.os.Parcelable", uri))
        send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        chooser = Intent.createChooser(send, cast("java.lang.CharSequence", JString("Send with")))
        chooser.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        activity.startActivity(chooser)
        return True
    except Exception:
        traceback.print_exc()
        return False


def share_file(handle) -> None:
    """Android share sheet with a saved file (handle from publish())."""
    if ANDROID:
        from androidstorage4kivy import ShareSheet

        ShareSheet().share_file(handle)
        return
    show_message("Share", f"On a phone this opens the share menu.\n\nFile: {handle}")


def view_file(handle) -> None:
    if ANDROID:
        from androidstorage4kivy import ShareSheet

        ShareSheet().view_file(handle)


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
def rounded_bg(widget, color, radius=dp(14)):
    with widget.canvas.before:
        widget._bg_color = Color(*color)
        widget._bg_rect = RoundedRectangle(pos=widget.pos, size=widget.size, radius=[radius])
    widget.bind(pos=lambda *_: setattr(widget._bg_rect, "pos", widget.pos),
                size=lambda *_: setattr(widget._bg_rect, "size", widget.size))


def set_bg(widget, color):
    widget._bg_color.rgba = color


class WrapLabel(Label):
    """Label that wraps its text to the available width and grows in height."""

    def __init__(self, **kw):
        kw.setdefault("halign", "left")
        kw.setdefault("valign", "middle")
        kw.setdefault("size_hint_y", None)
        kw.setdefault("font_size", sp(14))
        super().__init__(**kw)
        self.bind(width=lambda *_: setattr(self, "text_size", (self.width, None)),
                  texture_size=lambda *_: setattr(self, "height", max(self.texture_size[1], dp(20))))


class RoundButton(Button):
    """Flat button with rounded corners."""

    def __init__(self, color=NEUTRAL, radius=dp(12), **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", ROW)
        kw.setdefault("font_size", sp(15))
        kw.setdefault("halign", "center")
        kw.setdefault("valign", "middle")
        super().__init__(background_normal="", background_down="", background_color=(0, 0, 0, 0), **kw)
        rounded_bg(self, color, radius)
        self.base_color = color
        self.bind(size=lambda *_: setattr(self, "text_size", (self.width - dp(12), None)),
                  state=lambda *_: set_bg(self, [c * (0.8 if self.state == "down" else 1) for c in
                                                 self.base_color[:3]] + [1]))

    def set_color(self, color):
        self.base_color = color
        set_bg(self, color)


def button(text, on_press, color=NEUTRAL, **kw):
    b = RoundButton(text=text, color=color, **kw)
    b.bind(on_release=lambda *_: on_press())
    return b


def column(padding=(dp(14), dp(10)), spacing=dp(8)):
    """Vertical list that grows with its content (for use inside a ScrollView)."""
    g = GridLayout(cols=1, size_hint_y=None, spacing=spacing, padding=padding)
    g.bind(minimum_height=g.setter("height"))
    return g


class Card(GridLayout):
    """Rounded panel with a title; ``collapsible`` cards fold away advanced settings."""

    def __init__(self, title="", subtitle="", collapsible=False, start_open=True, **kw):
        super().__init__(cols=1, size_hint_y=None, spacing=dp(8), padding=(dp(14), dp(12)), **kw)
        self.bind(minimum_height=self.setter("height"))
        rounded_bg(self, CARD, dp(16))
        self.collapsible = collapsible
        self.is_open = start_open or not collapsible
        self.body = column(padding=(0, 0))
        if title:
            if collapsible:
                self.header = RoundButton(text="", color=CARD, height=dp(40), halign="left", font_size=sp(17))
                self.header.bind(on_release=lambda *_: self.toggle())
                self._title, self._summary = title, ""
                self._update_header()
            else:
                self.header = WrapLabel(text=f"[b]{title}[/b]", markup=True, font_size=sp(18))
            self.add_widget(self.header)
            if subtitle:
                self.sub = WrapLabel(text=subtitle, color=MUTED, font_size=sp(13))
                self.add_widget(self.sub)
        if self.is_open:
            self.add_widget(self.body)

    def _update_header(self):
        arrow = "v" if self.is_open else ">"
        summary = f"   [color=9e9eaa][size=13sp]{self._summary}[/size][/color]" if self._summary else ""
        self.header.markup = True
        self.header.text = f"[b]{arrow}   {self._title}[/b]{summary}"

    def toggle(self):
        self.is_open = not self.is_open
        if self.is_open:
            self.add_widget(self.body)
        else:
            self.remove_widget(self.body)
        self._update_header()

    def set_summary(self, text):
        if self.collapsible:
            self._summary = text
            self._update_header()


class Choice(GridLayout):
    """Row of buttons where exactly one is selected (like a segmented control)."""

    def __init__(self, options, on_change, selected=None, cols=None, height=ROW, font_size=sp(14), **kw):
        super().__init__(cols=cols or len(options), size_hint_y=None, spacing=dp(6), **kw)
        self.bind(minimum_height=self.setter("height"))
        self.on_change = on_change
        self.buttons = {}
        self.disabled_keys = set()
        for key, label in options:
            b = RoundButton(text=label, height=height, font_size=font_size)
            b.bind(on_release=lambda _b, k=key: self.select(k))
            self.buttons[key] = b
            self.add_widget(b)
        self.value = None
        self.select(selected or options[0][0], notify=False)

    def select(self, key, notify=True):
        if key in self.disabled_keys:
            return
        self.value = key
        for k, b in self.buttons.items():
            b.set_color(ACCENT if k == key else (DIMMED if k in self.disabled_keys else NEUTRAL))
            b.color = (0.5, 0.5, 0.55, 1) if k in self.disabled_keys else (1, 1, 1, 1)
        if notify:
            self.on_change(key)

    def set_disabled(self, keys):
        self.disabled_keys = set(keys)
        self.select(self.value if self.value not in self.disabled_keys else
                    next(k for k in self.buttons if k not in self.disabled_keys), notify=False)


class SliderRow(BoxLayout):
    """Label + slider + value, one row."""

    def __init__(self, label, lo, hi, value, fmt, step=0, on_change=None, **kw):
        super().__init__(orientation="horizontal", size_hint_y=None, height=ROW, spacing=dp(6), **kw)
        self.fmt = fmt
        self.on_change = on_change
        self.add_widget(Label(text=label, size_hint_x=0.34, halign="left", valign="middle",
                              font_size=sp(14), text_size=(dp(130), None), shorten=True))
        self.slider = Slider(min=lo, max=hi, value=value, step=step, size_hint_x=0.46,
                             cursor_size=(dp(26), dp(26)), value_track=True, value_track_color=ACCENT)
        self.value_label = Label(text=fmt(value), size_hint_x=0.2, font_size=sp(13), bold=True)
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
        self.add_widget(WrapLabel(text=label, size_hint_x=0.75))
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


def scrolling(content):
    sv = ScrollView(do_scroll_x=False, bar_width=dp(4), scroll_type=["bars", "content"])
    sv.add_widget(content)
    return sv


def show_message(title, text):
    box = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10))
    c = column(padding=(0, 0))
    c.add_widget(WrapLabel(text=text))
    box.add_widget(scrolling(c))
    popup = Popup(title=title, content=box, size_hint=(0.92, 0.55), separator_color=ACCENT)
    box.add_widget(button("OK", popup.dismiss, ACCENT))
    popup.open()


# ---------------------------------------------------------------- file picking
class Picker:
    """Opens the Android file chooser (or a simple file dialog on desktop)."""

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
        row.add_widget(button("Cancel", popup.dismiss))
        row.add_widget(button("Open", ok, ACCENT))
        box.add_widget(row)
        popup.open()


# ---------------------------------------------------------------- the app
class EasyRemixApp(App):
    busy = BooleanProperty(False)

    def build(self):
        self.title = APP_NAME
        self.icon = ICON
        self.picker = Picker()
        self.audio = None
        self.audio_path = ""
        self.key = None
        self.bpm = None
        self.get_file = ""
        self.last_saved = None      # (location, share handle, private path)
        self.last_remix = None
        self.current_preset = ""
        self._sound = None
        self._loading_preset = False

        root = BoxLayout(orientation="vertical")
        head = BoxLayout(size_hint_y=None, height=dp(68), padding=(dp(14), dp(10)), spacing=dp(12))
        if os.path.isfile(ICON):
            head.add_widget(Image(source=ICON, size_hint=(None, None), size=(dp(44), dp(44)),
                                  pos_hint={"center_y": 0.5}))
        title = Label(text=f"[b]{APP_NAME}[/b]\n[color=9e9eaa][size=13sp]Get it. Remix it. Share it.[/size][/color]",
                      markup=True, font_size=sp(20), halign="left", valign="middle", line_height=1.1)
        title.bind(size=lambda *_: setattr(title, "text_size", (title.width, None)))
        head.add_widget(title)
        root.add_widget(head)

        self.tabs = TabbedPanel(do_default_tab=False, tab_width=Window.width / 2 - dp(4),
                                tab_height=dp(48), background_color=(0, 0, 0, 0))
        self.tab_get = TabbedPanelItem(text="Get video or audio", font_size=sp(15))
        self.tab_remix = TabbedPanelItem(text="Remix", font_size=sp(15))
        self.tabs.add_widget(self.tab_get)
        self.tabs.add_widget(self.tab_remix)
        self.tab_get.add_widget(self._build_get())
        self.tab_remix.add_widget(self._build_remix())
        self.tabs.default_tab = self.tab_get
        Clock.schedule_once(lambda dt: self.tabs.switch_to(self.tab_get))
        Window.bind(width=lambda *_: setattr(self.tabs, "tab_width", Window.width / 2 - dp(4)))
        root.add_widget(self.tabs)

        status = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(44), padding=(dp(14), dp(2)))
        self.status = Label(text="Ready.", font_size=sp(13), color=MUTED, halign="left", valign="middle",
                            shorten=True, shorten_from="right")
        self.status.bind(size=lambda *_: setattr(self.status, "text_size", self.status.size))
        self.progress = ProgressBar(max=1.0, value=0, size_hint_y=None, height=dp(6))
        status.add_widget(self.status)
        status.add_widget(self.progress)
        root.add_widget(status)
        return root

    def on_start(self):
        request_permissions()
        try:
            clean_work_dir()
            from easyremix.share import clean_share_dir

            clean_share_dir(os.path.dirname(private_share_dir()))
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
            show_message("Something went wrong", str(exc) or exc.__class__.__name__)

    # ------------------------------------------------------------ tab 1: get
    def _build_get(self):
        from easyremix.downloader import QUALITIES
        from easyremix.tools import merge_ffmpeg_path

        c = column()

        src = Card("1   Where from?")
        b = src.body
        self.src_choice = Choice([("link", "Video link"), ("file", "File on phone")], self._on_source)
        b.add_widget(self.src_choice)
        self.link_box = column(padding=(0, 0))
        self.url = TextInput(hint_text="Paste a YouTube, TikTok, Instagram ... link", multiline=False,
                             size_hint_y=None, height=ROW, font_size=sp(15), padding=(dp(12), dp(14)))
        self.link_box.add_widget(self.url)
        row = GridLayout(cols=2, size_hint_y=None, height=ROW, spacing=dp(6))
        row.add_widget(button("Paste", lambda: setattr(self.url, "text", (Clipboard.paste() or "").strip())))
        row.add_widget(button("Send video", self.send_video, ACCENT))
        self.link_box.add_widget(row)
        self.link_box.add_widget(WrapLabel(text="Send video shares the video itself to Telegram, VK, WhatsApp ... "
                                                "It is not kept in your files.", color=MUTED, font_size=sp(12)))
        self.file_box = column(padding=(0, 0))
        self.file_box.add_widget(button("Choose a file...", lambda: self.picker.pick("*/*", self._file_chosen)))
        self.file_label = WrapLabel(text="Any video or audio file on your phone.", color=MUTED)
        self.file_box.add_widget(self.file_label)
        b.add_widget(self.link_box)
        c.add_widget(src)

        what = Card("2   What do you want?")
        w = what.body
        self.get_choice = Choice([("video", "Full video"), ("audio", "Audio only"), ("remix", "Remix it")],
                                 self._on_choice, font_size=sp(14))
        w.add_widget(self.get_choice)
        self.choice_hint = WrapLabel(text="", color=MUTED, font_size=sp(13))
        w.add_widget(self.choice_hint)
        self.quality = Spinner(text="Best available", values=list(QUALITIES), size_hint_y=None, height=ROW)
        self.fmt = Choice([(f, f) for f in EXPORT_FORMATS], lambda v: self._on_choice(self.get_choice.value),
                          height=dp(42), font_size=sp(13))
        self.opts_box = column(padding=(0, 0))
        w.add_widget(self.opts_box)
        self.get_button = button("Download video", self.start_get, ACCENT, height=dp(54), font_size=sp(17))
        w.add_widget(self.get_button)
        c.add_widget(what)
        self._single_file_note = not merge_ffmpeg_path()

        # result card (added when something was saved)
        self.result = Card()
        self.result_label = WrapLabel(text="", markup=True, font_size=sp(15))
        self.result.body.add_widget(self.result_label)
        r = GridLayout(cols=3, size_hint_y=None, height=ROW, spacing=dp(6))
        r.add_widget(button("Open", lambda: view_file(self.last_saved[1]) if self.last_saved else None))
        r.add_widget(button("Share", lambda: share_file(self.last_saved[1]) if self.last_saved else None))
        r.add_widget(button("Remix it", lambda: self.send_to_remix(self.last_saved[2]) if self.last_saved else None,
                            ACCENT))
        self.result.body.add_widget(r)
        self.get_column = c

        log_card = Card("Details", collapsible=True, start_open=False)
        self.log = TextInput(readonly=True, size_hint_y=None, height=dp(220), font_size=sp(12),
                             background_color=hexc("#15151b"), foreground_color=(0.85, 0.85, 0.85, 1))
        log_card.body.add_widget(self.log)
        c.add_widget(log_card)
        self._on_choice("video")
        return scrolling(c)

    def _on_source(self, key):
        box = self.src_choice.parent
        for wdg in (self.link_box, self.file_box):
            if wdg.parent:
                box.remove_widget(wdg)
        box.add_widget(self.link_box if key == "link" else self.file_box)
        self.get_choice.set_disabled({"video"} if key == "file" else set())
        self._on_choice(self.get_choice.value)

    def _on_choice(self, key):
        self.opts_box.clear_widgets()
        if key == "video":
            self.opts_box.add_widget(self.quality)
            note = " On this phone videos come as one file (YouTube: up to 360p)." if self._single_file_note else ""
            self.choice_hint.text = "The video with picture and sound (MP4)." + note
            self.get_button.text = "Download video"
        elif key == "audio":
            self.opts_box.add_widget(self.fmt)
            self.choice_hint.text = "Just the sound.  " + FORMAT_HINTS.get(self.fmt.value, "")
            self.get_button.text = "Get the audio"
        else:
            self.choice_hint.text = "Takes the sound straight into the Remix tab."
            self.get_button.text = "Open in Remix"

    def _file_chosen(self, path):
        self.get_file = path
        self.file_label.text = os.path.basename(path)
        self.file_label.color = (1, 1, 1, 1)

    def append_log(self, text):
        def add(dt):
            self.log.text = (self.log.text + text.rstrip() + "\n")[-20000:]
            self.log.cursor = (0, len(self.log._lines))
        Clock.schedule_once(add)

    def send_video(self):
        """Download into the private cache and open the share sheet - nothing lands in the
        user's files. With 'Audio only' / 'Remix it' selected, the sound is sent instead."""
        from easyremix import downloader
        from easyremix.share import clean_share_dir

        url = downloader.normalize_url(self.url.text)
        if not url:
            show_message(APP_NAME, "Paste a video link first. Send video shares that video with your friends.")
            return
        self.url.text = url
        audio_only = self.get_choice.value != "video"
        quality = self.quality.text
        folder = private_share_dir()
        clean_share_dir(os.path.dirname(folder))
        self.append_log(f"> send {url}")

        def work(progress):
            return downloader.download(url, folder, audio_only=audio_only, quality=quality,
                                       on_progress=progress, on_log=self.append_log)

        def done(path):
            self.set_status("Choose who to send it to...", 1)
            if not send_private_file(path):
                # no FileProvider: fall back to saving it, then sharing the saved copy
                where, handle = publish(path)
                share_file(handle)
                self.set_status(f"Saved to {where} and shared.", 1)

        def failed(exc):
            self.append_log(f"Failed: {exc}")
            show_message("Could not get the video", str(exc))

        self.run_task("Getting the video to send...", work, done, failed)

    def _show_result(self, where, handle, private):
        self.last_saved = (where, handle, private)
        self.result_label.text = f"[b][color=81c784]Saved[/color][/b]  {where}"
        if not self.result.parent:
            self.get_column.add_widget(self.result, index=1)

    def start_get(self):
        choice = self.get_choice.value
        if self.src_choice.value == "file":
            if not self.get_file or not os.path.isfile(self.get_file):
                show_message(APP_NAME, "Choose a file first.")
                return
            if choice == "remix":
                self.send_to_remix(self.get_file)
            else:
                self._extract(self.get_file)
            return
        from easyremix.downloader import normalize_url

        url = normalize_url(self.url.text)
        if not url:
            show_message(APP_NAME, "Paste a video link first (in the app: Share -> Copy link).")
            return
        self.url.text = url
        self._download(url, choice)

    def _download(self, url, choice):
        from easyremix import downloader

        audio_only = choice != "video"
        fmt = self.fmt.value if choice == "audio" else "mp3"
        quality = self.quality.text
        out_dir = os.path.join(work_dir(), "downloads")
        self.append_log(f"> {url}")

        def work(progress):
            path = downloader.download(url, out_dir, audio_only=audio_only, quality=quality,
                                       on_progress=progress, on_log=self.append_log)
            if audio_only and not path.lower().endswith("." + fmt):
                progress(1.0, f"Converting to {fmt.upper()}...")
                out = os.path.splitext(path)[0] + "." + fmt
                extract_audio(path, out)
                path = out
            if choice == "remix":
                return path, None, None
            progress(1.0, "Saving to the phone...")
            where, handle = publish(path)
            return path, where, handle

        def done(res):
            path, where, handle = res
            if choice == "remix":
                self.send_to_remix(path)
                return
            self.append_log(f"Saved: {where}")
            self.set_status(f"Saved: {where}", 1)
            self._show_result(where, handle, path)

        def failed(exc):
            self.append_log(f"Failed: {exc}")
            show_message("Download failed", str(exc))

        self.run_task("Starting download...", work, done, failed)

    def _extract(self, src):
        fmt = self.fmt.value
        out = os.path.join(work_dir(), os.path.splitext(os.path.basename(src))[0] + "." + fmt)
        if os.path.abspath(out) == os.path.abspath(src):
            out = os.path.splitext(out)[0] + " (audio)." + fmt

        def work(progress):
            progress(0.2, "Taking the audio out...")
            extract_audio(src, out)
            progress(0.9, "Saving to the phone...")
            return publish(out)

        def done(res):
            where, handle = res
            self.set_status(f"Saved: {where}", 1)
            self._show_result(where, handle, out)

        self.run_task("Taking the audio out...", work, done)

    # ------------------------------------------------------------ tab 2: remix
    def _build_remix(self):
        from easyremix.dsp import AMBIENCE, EQ_LABELS, EQ_PRESETS, REMIX_PRESETS

        self.remix_root = BoxLayout(orientation="vertical")

        # empty state
        self.remix_empty = column(padding=(dp(24), dp(40)), spacing=dp(14))
        if os.path.isfile(ICON):
            self.remix_empty.add_widget(Image(source=ICON, size_hint_y=None, height=dp(96)))
        self.remix_empty.add_widget(WrapLabel(text="[b]Open a song to start remixing[/b]", markup=True,
                                              font_size=sp(21), halign="center"))
        self.remix_empty.add_widget(WrapLabel(text="Any song or video on your phone. EasyRemix finds its key and "
                                                   "tempo, then you pick a style.", color=MUTED, halign="center"))
        self.remix_empty.add_widget(button("Open a song...", lambda: self.picker.pick("*/*", self.load_track),
                                           ACCENT, height=dp(54), font_size=sp(17)))
        self.remix_root.add_widget(scrolling(self.remix_empty))

        # loaded state
        self.remix_main = BoxLayout(orientation="vertical")
        c = column()
        top = Card()
        self.track_label = WrapLabel(text="", markup=True, font_size=sp(17))
        top.body.add_widget(self.track_label)
        self.info_label = WrapLabel(text="", markup=True, font_size=sp(14))
        top.body.add_widget(self.info_label)
        r = GridLayout(cols=2, size_hint_y=None, height=dp(42), spacing=dp(6))
        r.add_widget(button("Open another", lambda: self.picker.pick("*/*", self.load_track), height=dp(42),
                            font_size=sp(14)))
        r.add_widget(button("Reset all", self.reset_remix, height=dp(42), font_size=sp(14)))
        top.body.add_widget(r)
        c.add_widget(top)

        presets = Card("1   Pick a style", "One tap sets everything. Then press Preview.")
        grid = GridLayout(cols=2, size_hint_y=None, spacing=dp(6))
        grid.bind(minimum_height=grid.setter("height"))
        self.preset_buttons = {}
        for name in REMIX_PRESETS:
            btn = button(name, lambda n=name: self.apply_remix_preset(n), height=dp(46), font_size=sp(14))
            self.preset_buttons[name] = btn
            grid.add_widget(btn)
        presets.body.add_widget(grid)
        c.add_widget(presets)

        self.rows: dict[str, SliderRow | SwitchRow] = {}
        pct = lambda v: f"{v * 100:.0f}%"  # noqa: E731

        def slider(card, attr, label, lo, hi, default, fmt, step=0):
            r = SliderRow(label, lo, hi, default, fmt, step, on_change=lambda v, a=attr: self._on_slider(a))
            self.rows[attr] = r
            card.body.add_widget(r)

        speed = Card("2   Speed & pitch", "Slower = deeper, faster = brighter.")
        slider(speed, "speed", "Speed", 0.5, 1.5, 1.0, lambda v: f"x{v:.3f}")
        self.speed_info = WrapLabel(text="", markup=True, font_size=sp(13))
        speed.body.add_widget(self.speed_info)
        speed.body.add_widget(WrapLabel(text="Speeds that keep the song in tune (tap one):", color=MUTED,
                                        font_size=sp(12)))
        self.sugg_box = GridLayout(cols=2, size_hint_y=None, spacing=dp(6))
        self.sugg_box.bind(minimum_height=self.sugg_box.setter("height"))
        speed.body.add_widget(self.sugg_box)
        self.sugg_why = WrapLabel(text="", color=MUTED, font_size=sp(12))
        speed.body.add_widget(self.sugg_why)
        slider(speed, "pitch_semitones", "Extra pitch", -12, 12, 0.0, lambda v: f"{v:+.1f} st", step=0.5)
        self.rows["keep_pitch"] = SwitchRow("Keep the original pitch", on_change=self._update_speed_info)
        speed.body.add_widget(self.rows["keep_pitch"])
        c.add_widget(speed)

        self.cards = {}
        eq = Card("Equalizer", collapsible=True, start_open=False)
        self.cards["eq"] = eq
        self.eq_preset = Spinner(text="Flat", values=list(EQ_PRESETS), size_hint_y=None, height=ROW)
        self.eq_preset.bind(text=lambda _s, name: self.apply_eq_preset(name))
        eq.body.add_widget(self.eq_preset)
        self.eq_rows = []
        for lab in EQ_LABELS:
            r = SliderRow(f"{lab} Hz", -12, 12, 0.0, lambda v: f"{v:+.0f} dB", step=0.5,
                          on_change=lambda v: self._eq_changed())
            self.eq_rows.append(r)
            eq.body.add_widget(r)
        c.add_widget(eq)

        fx = Card("Effects", collapsible=True, start_open=False)
        self.cards["fx"] = fx
        slider(fx, "reverb_mix", "Reverb", 0, 1, 0.0, pct)
        slider(fx, "reverb_size", "Room size", 0, 1, 0.7, pct)
        slider(fx, "echo_mix", "Echo", 0, 1, 0.0, pct)
        slider(fx, "echo_delay", "Echo delay", 0.05, 1.5, 0.35, lambda v: f"{v * 1000:.0f} ms")
        slider(fx, "echo_feedback", "Echo repeats", 0, 0.9, 0.35, pct)
        slider(fx, "chorus_mix", "Chorus", 0, 1, 0.0, pct)
        slider(fx, "lofi_amount", "Lo-Fi", 0, 1, 0.0, pct)
        slider(fx, "distortion_db", "Distortion", 0, 30, 0.0, lambda v: f"{v:.0f} dB")
        slider(fx, "rotate_8d", "8D rotation", 0, 1, 0.0, pct)
        slider(fx, "rotate_speed", "Rotation speed", 0.03, 0.5, 0.12, lambda v: f"{v:.2f} Hz")
        fx.body.add_widget(WrapLabel(text="8D sounds best with headphones.", color=MUTED, font_size=sp(12)))
        c.add_widget(fx)

        amb = Card("Ambience", collapsible=True, start_open=False)
        self.cards["amb"] = amb
        for attr, (label, _) in AMBIENCE.items():
            slider(amb, attr, label, 0, 1, 0.0, pct)
        c.add_widget(amb)

        vol = Card("Volume & fades", collapsible=True, start_open=False)
        self.cards["vol"] = vol
        slider(vol, "volume_db", "Volume", -24, 12, 0.0, lambda v: f"{v:+.1f} dB", step=0.5)
        slider(vol, "fade_in", "Fade in", 0, 10, 0.0, lambda v: f"{v:.1f} s", step=0.25)
        slider(vol, "fade_out", "Fade out", 0, 15, 0.0, lambda v: f"{v:.1f} s", step=0.25)
        self.rows["normalize"] = SwitchRow("As loud as modern tracks", on_change=self._refresh_summaries)
        vol.body.add_widget(self.rows["normalize"])
        c.add_widget(vol)
        self.remix_main.add_widget(scrolling(c))

        # action bar, always visible
        bar = GridLayout(cols=1, size_hint_y=None, spacing=dp(6), padding=(dp(10), dp(8)))
        bar.bind(minimum_height=bar.setter("height"))
        rounded_bg(bar, CARD, dp(16))
        r1 = BoxLayout(size_hint_y=None, height=ROW, spacing=dp(6))
        self.preview_from = TextInput(text="0:30", multiline=False, size_hint_x=0.2, font_size=sp(15),
                                      halign="center", padding=(dp(6), dp(14)))
        r1.add_widget(self.preview_from)
        r1.add_widget(button("Preview", self.preview, GREEN, size_hint_x=0.32))
        r1.add_widget(button("Original", lambda: self.preview(original=True), size_hint_x=0.26))
        r1.add_widget(button("Stop", self.stop_sound, size_hint_x=0.22))
        r2 = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(6))
        self.export_fmt = Spinner(text="mp3", values=list(EXPORT_FORMATS), size_hint_x=0.24)
        r2.add_widget(self.export_fmt)
        r2.add_widget(button("Export remix", self.export, ACCENT, size_hint_x=0.52, height=dp(52), font_size=sp(16)))
        self.share_remix_btn = button("Share", lambda: share_file(self.last_remix[1]) if self.last_remix else
                                      show_message(APP_NAME, "Export the remix first, then share it."),
                                      size_hint_x=0.24, height=dp(52))
        r2.add_widget(self.share_remix_btn)
        bar.add_widget(r1)
        bar.add_widget(r2)
        self.remix_main.add_widget(bar)

        self._render_suggestions()
        self._update_speed_info()
        self._refresh_summaries()
        return self.remix_root

    # --- remix helpers
    def _on_slider(self, attr):
        if attr in ("speed", "pitch_semitones"):
            self._update_speed_info()
        self._mark_custom()
        self._refresh_summaries()

    def _refresh_summaries(self, *_):
        if not getattr(self, "cards", None) or "vol" not in self.cards:
            return
        from easyremix.dsp import AMBIENCE

        v = {k: r.value for k, r in self.rows.items()}
        self.cards["eq"].set_summary(self.eq_preset.text)
        names = [("reverb_mix", "Reverb"), ("echo_mix", "Echo"), ("chorus_mix", "Chorus"),
                 ("lofi_amount", "Lo-Fi"), ("distortion_db", "Distortion"), ("rotate_8d", "8D")]
        on = [n for k, n in names if v.get(k, 0) > (0.1 if k == "distortion_db" else 0.01)]
        self.cards["fx"].set_summary(", ".join(on) if on else "off")
        amb = [label.split(" ")[0] for k, (label, _) in AMBIENCE.items() if v.get(k, 0) > 0.005]
        self.cards["amb"].set_summary(", ".join(amb) if amb else "off")
        parts = []
        if abs(v.get("volume_db", 0)) > 0.05:
            parts.append(f"{v['volume_db']:+.1f} dB")
        if v.get("normalize"):
            parts.append("loud")
        if v.get("fade_in", 0) > 0 or v.get("fade_out", 0) > 0:
            parts.append("fades")
        self.cards["vol"].set_summary(", ".join(parts) if parts else "unchanged")

    def _mark_custom(self):
        if not self._loading_preset and self.current_preset:
            self._highlight_preset("")

    def _highlight_preset(self, name):
        self.current_preset = name
        for n, btn in self.preset_buttons.items():
            btn.set_color(ACCENT if n == name else NEUTRAL)

    def _eq_changed(self):
        if not self._loading_preset:
            self._loading_preset = True
            self.eq_preset.text = "Custom"
            self._loading_preset = False
            self._mark_custom()
        self._refresh_summaries()

    def apply_eq_preset(self, name):
        from easyremix.dsp import EQ_PRESETS

        if self._loading_preset or name not in EQ_PRESETS:
            return
        self._set_eq(EQ_PRESETS[name], name)
        self._mark_custom()

    def _set_eq(self, gains, name):
        was = self._loading_preset
        self._loading_preset = True
        for row, g in zip(self.eq_rows, gains):
            row.value = g
        self.eq_preset.text = name
        self._loading_preset = was
        self._refresh_summaries()

    def _set_speed(self, speed, why=""):
        self.rows["speed"].value = speed
        self.rows["keep_pitch"].value = False
        self.sugg_why.text = why
        self._update_speed_info()

    def _update_speed_info(self, *_):
        if not hasattr(self, "speed_info") or "keep_pitch" not in self.rows:
            return
        speed = self.rows["speed"].value
        text = describe_speed(speed, self.key, self.bpm, self.rows["keep_pitch"].value,
                              self.rows["pitch_semitones"].value)
        bad = "OUT OF TUNE" in text
        color = "ff8a80" if bad else "81c784"
        self.speed_info.text = f"[color={color}]{text.replace('  |  ', '  ·  ')}[/color]"
        for btn, chip_speed in getattr(self, "_chips", []):
            btn.set_color(ACCENT if abs(chip_speed - speed) < 5e-4 else NEUTRAL)

    def _render_suggestions(self):
        self.sugg_box.clear_widgets()
        self._chips = []
        for s in speed_suggestions(self.key, self.bpm):
            key_txt = f"  {s.new_key.short}" if s.new_key else ""
            bpm_txt = f"  {s.new_bpm:.0f} BPM" if s.new_bpm else ""
            # the default font has no star glyphs, so the rating is written as "4/5"
            text = f"[b]{s.label}[/b]\n[size=12sp]x{s.speed:.3f}  {s.semitones:+d} st{key_txt}{bpm_txt}  {s.stars}/5[/size]"
            b = button(text, lambda sp_=s.speed, d=s.description: self._set_speed(sp_, d), height=dp(58),
                       font_size=sp(13))
            b.markup = True
            self.sugg_box.add_widget(b)
            self._chips.append((b, s.speed))
        self._update_speed_info()

    def reset_remix(self):
        from easyremix.dsp import RemixSettings

        defaults = RemixSettings()
        self._loading_preset = True
        for attr, row in self.rows.items():
            row.value = getattr(defaults, attr)
        self._set_eq([0.0] * 10, "Flat")
        self._loading_preset = False
        self._highlight_preset("")
        self.sugg_why.text = ""
        self._update_speed_info()
        self._refresh_summaries()

    def apply_remix_preset(self, name):
        from easyremix.dsp import EQ_PRESETS, REMIX_PRESETS

        preset = REMIX_PRESETS.get(name)
        if not preset:
            return
        self.reset_remix()
        self._loading_preset = True
        for attr, value in preset.items():
            if attr == "eq_gains":
                match = next((k for k, v in EQ_PRESETS.items() if v == value), "Custom")
                self._set_eq(value, match)
            elif attr in self.rows:
                self.rows[attr].value = value
        self._loading_preset = False
        self._highlight_preset(name)
        self._update_speed_info()
        self._refresh_summaries()
        self.set_status(f"Style '{name}' applied - press Preview to listen.")

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
        def work(progress):
            from easyremix.analysis import detect_bpm, detect_key

            progress(0.1, "Loading song...")
            audio = load_audio(path)
            progress(0.5, "Finding the musical key...")
            key = detect_key(audio, SAMPLE_RATE)
            progress(0.8, "Measuring the tempo...")
            bpm = detect_bpm(audio, SAMPLE_RATE)
            return audio, key, bpm

        def done(res):
            audio, kres, bpm = res
            self.audio, self.audio_path = audio, path
            self.key, self.bpm = kres.key, (bpm or None)
            self.last_remix = None
            bpm_txt = f"{bpm:.0f} BPM" if bpm else "– BPM"
            self.track_label.text = f"[b]{os.path.basename(path)}[/b]"
            self.info_label.text = (f"[b]{kres.key.name}[/b] ({kres.key.camelot})   [b]{bpm_txt}[/b]   "
                                    f"[b]{fmt_time(audio.shape[1] / SAMPLE_RATE)}[/b]")
            if self.remix_empty.parent:
                self.remix_root.clear_widgets()
                self.remix_root.add_widget(self.remix_main)
            self._render_suggestions()
            self._update_speed_info()
            self.set_status(f"Loaded {os.path.basename(path)} - pick a style, then Preview.", 1)

        self.run_task("Loading song...", work, done)

    def _require_audio(self):
        if self.audio is None:
            show_message(APP_NAME, "Open a song first.")
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

        self.run_task("Preparing preview...", work, done)

    def export(self):
        if not self._require_audio():
            return
        fmt = self.export_fmt.text
        base = os.path.splitext(os.path.basename(self.audio_path))[0]
        suffix = (self.current_preset or "remix").lower().replace(" ", "_").replace("+", "and")
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

        def done(res):
            where, handle = res
            self.last_remix = (where, handle)
            self.share_remix_btn.set_color(ACCENT)
            self.set_status(f"Exported: {where}", 1)
            box = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10))
            box.add_widget(WrapLabel(text=f"Your remix was saved to\n[b]{where}[/b]\n\n"
                                          "You'll find it in your music app.", markup=True))
            box.add_widget(Label())  # pushes the text to the top, buttons to the bottom
            popup = Popup(title="Remix saved", content=box, size_hint=(0.92, 0.5), separator_color=ACCENT)
            row = GridLayout(cols=2, size_hint_y=None, height=ROW, spacing=dp(6))
            row.add_widget(button("Close", popup.dismiss))
            row.add_widget(button("Share", lambda: (popup.dismiss(), share_file(handle)), ACCENT))
            box.add_widget(row)
            popup.open()

        self.run_task("Rendering remix (this can take a minute)...", work, done)


if __name__ == "__main__":
    EasyRemixApp().run()
