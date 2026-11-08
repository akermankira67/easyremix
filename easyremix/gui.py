"""EasyRemix graphical interface (customtkinter).

Layout principles: one clear task per tab, the main action is always the big violet
button, things you rarely need are folded away, and every number explains itself.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from tkinter import filedialog, messagebox

import customtkinter as ctk

from . import APP_NAME, __version__
from .audio_io import EXPORT_FORMATS, SAMPLE_RATE, extract_audio, load_audio, save_audio
from .theory import Key, describe_speed, speed_suggestions

# ---------------------------------------------------------------- look & feel
ACCENT = "#7c4dff"
ACCENT_HOVER = "#6a3bef"
PINK = "#ec4899"
GOOD = ("#2e7d32", "#81c784")
BAD = ("#c62828", "#ff8a80")
MUTED = ("gray40", "gray62")
SECONDARY = dict(fg_color=("gray82", "gray25"), hover_color=("gray74", "gray31"),
                 text_color=("gray10", "gray92"))
PLAY = dict(fg_color="#2e9d5b", hover_color="#23804a")
PILL = ("gray86", "gray22")
CARD = ("gray95", "gray14")

VIDEO_TYPES = [("Video files", "*.mp4 *.mkv *.webm *.mov *.avi *.flv *.m4v *.wmv"), ("All files", "*.*")]
AUDIO_TYPES = [("Audio & video", "*.mp3 *.wav *.flac *.m4a *.ogg *.opus *.aac *.wma *.mp4 *.mkv *.webm *.mov"),
               ("All files", "*.*")]
PREVIEW_SECONDS = 25
FORMAT_HINTS = {
    "mp3": "MP3 - small and plays everywhere (recommended)",
    "wav": "WAV - lossless, very large files",
    "flac": "FLAC - lossless and compressed, for archiving",
    "m4a": "M4A - small, great on iPhone / Apple Music",
    "ogg": "OGG - small, open format",
}

TAB_GET = "  ⬇  Get video or audio  "
TAB_REMIX = "  ✦  Remix  "


def default_out_dir() -> str:
    music = os.path.join(os.path.expanduser("~"), "Music")
    base = music if os.path.isdir(music) else os.path.expanduser("~")
    return os.path.join(base, "EasyRemix")


def open_in_explorer(path: str) -> None:
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    try:
        if sys.platform.startswith("win"):
            if os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
    except Exception:
        pass


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


def short_path(path: str, keep: int = 2) -> str:
    """'~/Music/EasyRemix' instead of a long absolute path."""
    home = os.path.expanduser("~")
    if path.startswith(home):
        path = "~" + path[len(home):]
    parts = path.replace("\\", "/").split("/")
    return path if len(parts) <= keep + 2 else ".../" + "/".join(parts[-keep:])


def font(size=13, weight="normal"):
    return ctk.CTkFont(size=size, weight=weight)


def load_logo(size: int):
    """The app logo as a CTkImage (None if Pillow or the file is missing)."""
    try:
        from PIL import Image

        from .tools import resource_dir

        path = os.path.join(resource_dir(), "assets", "logo-128.png")
        img = Image.open(path)
        return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
    except Exception:
        return None


# ---------------------------------------------------------------- building blocks
class Card(ctk.CTkFrame):
    """A rounded panel with a title. ``collapsible`` cards fold away rarely used settings;
    their header then shows a one-line summary of what is set inside."""

    def __init__(self, master, title: str, subtitle: str = "", collapsible: bool = False,
                 start_open: bool = True):
        super().__init__(master, corner_radius=14, fg_color=CARD)
        self.collapsible = collapsible
        self.is_open = start_open or not collapsible
        head = ctk.CTkFrame(self, fg_color="transparent", cursor="hand2" if collapsible else "")
        head.pack(fill="x", padx=16, pady=(12, 6 if self.is_open else 12))
        self._head = head
        self.chevron = ctk.CTkLabel(head, text=("▾" if self.is_open else "▸") if collapsible else "",
                                    width=14 if collapsible else 0, font=font(14, "bold"))
        self.chevron.pack(side="left")
        ctk.CTkLabel(head, text=title, font=font(16, "bold")).pack(side="left", padx=(4 if collapsible else 0, 0))
        if subtitle:
            ctk.CTkLabel(head, text=subtitle, text_color=MUTED, font=font(13)).pack(side="left", padx=10)
        self.summary = ctk.CTkLabel(head, text="", text_color=MUTED, font=font(13))
        self.summary.pack(side="right")
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        if self.is_open:
            self.body.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        if collapsible:
            for w in (head, self.chevron, *head.winfo_children()):
                w.bind("<Button-1>", lambda e: self.toggle())

    def toggle(self, force: bool | None = None):
        if not self.collapsible:
            return
        self.is_open = (not self.is_open) if force is None else force
        self.chevron.configure(text="▾" if self.is_open else "▸")
        if self.is_open:
            self._head.pack_configure(pady=(12, 6))
            self.body.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        else:
            self._head.pack_configure(pady=12)
            self.body.pack_forget()

    def set_summary(self, text: str):
        self.summary.configure(text=text)


def pill(master, text="", **kw):
    """Small rounded info label (key, BPM, length...)."""
    return ctk.CTkLabel(master, text=text, corner_radius=10, fg_color=PILL, height=30,
                        font=font(14, "bold"), padx=12, **kw)


def secondary_button(master, text, command, **kw):
    kw.setdefault("height", 38)
    return ctk.CTkButton(master, text=text, command=command, **SECONDARY, **kw)


def primary_button(master, text, command, **kw):
    kw.setdefault("height", 46)
    kw.setdefault("font", font(15, "bold"))
    return ctk.CTkButton(master, text=text, command=command, fg_color=ACCENT, hover_color=ACCENT_HOVER, **kw)


# ---------------------------------------------------------------- the window
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title(f"{APP_NAME} {__version__}")
        self.geometry("1180x840")
        self.minsize(1000, 700)
        self._set_window_icon()

        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        self.audio = None           # loaded source audio (2, n)
        self.audio_path = ""
        self.key: Key | None = None
        self.bpm: float | None = None
        self.last_download = ""
        self.last_extract = ""
        self._loading_preset = False
        self._player = None
        self.current_preset = ""

        self._build_header()
        self._build_statusbar()
        self.tabs = ctk.CTkTabview(self, corner_radius=16, segmented_button_selected_color=ACCENT,
                                   segmented_button_selected_hover_color=ACCENT_HOVER)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=(4, 8))
        try:  # bigger, friendlier tab buttons
            self.tabs._segmented_button.configure(font=font(14, "bold"), height=38)
        except Exception:
            pass
        self._build_get(self.tabs.add(TAB_GET))
        self._build_remix(self.tabs.add(TAB_REMIX))
        self.after(80, self._poll_queue)

    # ------------------------------------------------------------ plumbing
    @property
    def player(self):
        if self._player is None:
            from .player import Player

            self._player = Player()
        return self._player

    def _set_window_icon(self):
        try:
            from .tools import resource_dir

            assets = os.path.join(resource_dir(), "assets")
            if sys.platform.startswith("win") and os.path.isfile(os.path.join(assets, "icon.ico")):
                self.after(250, lambda: self.iconbitmap(os.path.join(assets, "icon.ico")))
            else:
                from PIL import Image, ImageTk

                self._icon_photo = ImageTk.PhotoImage(Image.open(os.path.join(assets, "logo-128.png")))
                self.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=20, pady=(14, 2))
        logo = load_logo(40)
        if logo:
            ctk.CTkLabel(bar, text="", image=logo).pack(side="left", padx=(0, 10))
        names = ctk.CTkFrame(bar, fg_color="transparent")
        names.pack(side="left")
        ctk.CTkLabel(names, text=APP_NAME, font=font(24, "bold")).pack(anchor="w")
        ctk.CTkLabel(names, text="Download a video, pull out the audio, make it your own remix.",
                     text_color=MUTED, font=font(13)).pack(anchor="w", pady=(0, 2))
        self.theme_switch = ctk.CTkSegmentedButton(bar, values=["Dark", "Light"],
                                                   command=lambda v: ctk.set_appearance_mode(v.lower()),
                                                   selected_color=ACCENT, selected_hover_color=ACCENT_HOVER)
        self.theme_switch.set("Dark")
        self.theme_switch.pack(side="right")

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self, height=38, corner_radius=0)
        bar.pack(fill="x", side="bottom")
        self.progress = ctk.CTkProgressBar(bar, width=260, progress_color=ACCENT)
        self.progress.set(0)
        self.progress.pack(side="right", padx=16, pady=10)
        self.status = ctk.CTkLabel(bar, text="Ready.", anchor="w", font=font(13))
        self.status.pack(side="left", fill="x", expand=True, padx=16)

    def set_status(self, text: str, frac: float | None = None):
        self.status.configure(text=text)
        if frac is not None:
            self.progress.set(max(0.0, min(1.0, frac)))

    def run_task(self, label: str, work, on_done=None, on_error=None):
        """Run ``work(progress)`` in a background thread; ``on_done(result)`` in the UI thread.

        ``on_error(exc)`` (optional) also runs in the UI thread after the error was shown.
        """
        if self._busy:
            messagebox.showinfo(APP_NAME, "Please wait, another task is still running.")
            return
        self._busy = True
        self.set_status(label, 0)

        def progress(frac, msg=None):
            self._queue.put(("progress", frac, msg or label))

        def runner():
            try:
                result = work(progress)
                self._queue.put(("done", on_done, result))
            except Exception as exc:  # report every failure to the user
                traceback.print_exc()
                self._queue.put(("error", label, exc, on_error))

        threading.Thread(target=runner, daemon=True).start()

    def _poll_queue(self):
        try:
            while True:
                item = self._queue.get_nowait()
                kind = item[0]
                if kind == "progress":
                    self.set_status(item[2], item[1])
                elif kind == "log":
                    self._append_log(item[1])
                elif kind == "done":
                    self._busy = False
                    self.set_status("Done.", 1)
                    if item[1]:
                        item[1](item[2])
                elif kind == "error" and getattr(item[2], "cancelled", False):
                    self._busy = False
                    self.set_status("Cancelled.", 0)
                    if item[3]:
                        item[3](item[2])
                elif kind == "error":
                    self._busy = False
                    self.set_status("Something went wrong.", 0)
                    messagebox.showerror(APP_NAME, f"{item[1].strip('. ')} failed:\n\n{item[2]}")
                    if item[3]:
                        item[3](item[2])
        except queue.Empty:
            pass
        self.after(80, self._poll_queue)

    # ------------------------------------------------------------ tab 1: get (download / extract)
    def _build_get(self, tab):
        """One place to get media: from a link or a file -> full video, audio only, or remix."""
        from .downloader import QUALITIES

        tab.grid_columnconfigure(0, weight=1)
        self.get_source = "link"
        self.get_choice = "video"
        self.ex_src = ctk.StringVar()      # chosen local file
        self.url_var = ctk.StringVar()
        self.dl_mode = ctk.StringVar(value="video")

        # --- step 1: where from
        src = Card(tab, "1   Where from?")
        src.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 8))
        b = src.body
        b.grid_columnconfigure(0, weight=1)
        self.src_switch = ctk.CTkSegmentedButton(
            b, values=["Video link", "File on this computer"], height=38, font=font(13, "bold"),
            selected_color=ACCENT, selected_hover_color=ACCENT_HOVER, command=self._on_source)
        self.src_switch.set("Video link")
        self.src_switch.grid(row=0, column=0, sticky="w", pady=(2, 12))

        self.link_row = ctk.CTkFrame(b, fg_color="transparent")
        self.link_row.grid(row=1, column=0, sticky="ew")
        self.link_row.grid_columnconfigure(0, weight=1)
        entry = ctk.CTkEntry(self.link_row, textvariable=self.url_var, height=46, font=font(15),
                             placeholder_text="Paste a link from YouTube, TikTok, Instagram, VK, X ...")
        entry.grid(row=0, column=0, sticky="ew")
        entry.bind("<Return>", lambda e: self.start_get())
        secondary_button(self.link_row, "Paste", self._paste_url, width=96, height=46).grid(row=0, column=1,
                                                                                         padx=(10, 0))
        self.share_link_btn = secondary_button(self.link_row, "↗  Send to...",
                                               lambda: self._share_menu(self.share_link_btn), width=130, height=46)
        self.share_link_btn.grid(row=0, column=2, padx=(10, 0))
        ctk.CTkLabel(self.link_row, text="YouTube, TikTok, Instagram, VK, X and 1000+ other sites.   "
                                         "'Send to' shares the video itself - it is not kept in your files.",
                     text_color=MUTED, wraplength=860, justify="left", anchor="w").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        self.file_row = ctk.CTkFrame(b, corner_radius=12, fg_color=PILL)
        self.file_row.grid_columnconfigure(1, weight=1)
        secondary_button(self.file_row, "Choose a file...", self._pick_get_file, width=190, height=44,
                         font=font(14, "bold")).grid(row=0, column=0, rowspan=2, padx=12, pady=12)
        self.ex_name = ctk.CTkLabel(self.file_row, text="No file chosen yet", font=font(15, "bold"), anchor="w")
        self.ex_name.grid(row=0, column=1, sticky="sw", padx=6, pady=(12, 0))
        self.ex_folder = ctk.CTkLabel(self.file_row, text="Any video or audio file: MP4, MKV, WEBM, MOV, MP3, WAV ...",
                                      text_color=MUTED, anchor="w")
        self.ex_folder.grid(row=1, column=1, sticky="nw", padx=6, pady=(0, 12))

        # --- step 2: what do you want
        what = Card(tab, "2   What do you want?")
        what.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 8))
        w = what.body
        tiles = ctk.CTkFrame(w, fg_color="transparent")
        tiles.pack(fill="x")
        self.choice_tiles: dict[str, ctk.CTkButton] = {}
        for i, (key, title, sub) in enumerate([
            ("video", "▶   Full video", "MP4 with picture and sound"),
            ("audio", "♪   Audio only", "MP3, WAV, FLAC and more"),
            ("remix", "✦   Remix it", "open the sound in Remix"),
        ]):
            tiles.grid_columnconfigure(i, weight=1, uniform="tiles")
            t = ctk.CTkButton(tiles, text=f"{title}\n{sub}", height=64, corner_radius=14, font=font(14, "bold"),
                              command=lambda k=key: self._on_choice(k), **SECONDARY)
            t.grid(row=0, column=i, sticky="ew", padx=4)
            self.choice_tiles[key] = t

        self.choice_opts = ctk.CTkFrame(w, fg_color="transparent")
        self.choice_opts.pack(fill="x", pady=(12, 0))
        self.quality_label = ctk.CTkLabel(self.choice_opts, text="Quality", font=font(14))
        self.quality_box = ctk.CTkComboBox(self.choice_opts, values=list(QUALITIES), width=170, height=36,
                                           state="readonly")
        self.quality_box.set("Best available")
        self.fmt_label = ctk.CTkLabel(self.choice_opts, text="Format", font=font(14))
        self.ex_fmt = ctk.CTkSegmentedButton(self.choice_opts, values=list(EXPORT_FORMATS), height=36,
                                             font=font(13, "bold"), command=lambda v: self._on_choice(self.get_choice),
                                             selected_color=ACCENT, selected_hover_color=ACCENT_HOVER)
        self.ex_fmt.set("mp3")
        self.choice_hint = ctk.CTkLabel(self.choice_opts, text="", text_color=MUTED)

        actions = ctk.CTkFrame(w, fg_color="transparent")
        actions.pack(fill="x", pady=(16, 0))
        self.get_btn = primary_button(actions, "⬇   Download video", self.start_get, width=240)
        self.get_btn.pack(side="left")
        self.dl_resume = ctk.CTkButton(actions, text="⟳  Resume", height=46, width=140, font=font(14, "bold"),
                                       command=self.start_get, **PLAY)
        self.dl_cancel = ctk.CTkButton(actions, text="✕  Cancel", height=46, width=130, font=font(14, "bold"),
                                       command=self._cancel_download, fg_color=("#d84343", "#b83232"),
                                       hover_color=("#c23535", "#9c2626"))
        self._cancel_event = None
        self.dl_where = ctk.CTkLabel(actions, text="", text_color=MUTED, font=font(13))
        self.dl_where.pack(side="left", padx=16)

        # result: appears when something was saved
        self.dl_result = ctk.CTkFrame(w, corner_radius=12, fg_color=PILL)
        self.dl_result_label = ctk.CTkLabel(self.dl_result, text="", font=font(14, "bold"), text_color=GOOD,
                                            anchor="w")
        self.dl_result_label.pack(side="left", padx=14, pady=10, fill="x", expand=True)
        self.dl_to_remix = primary_button(self.dl_result, "✦  Remix it", lambda: self._send_to_remix(self.last_download),
                                          width=124, height=36, font=font(13, "bold"))
        self.dl_to_remix.pack(side="right", padx=(6, 10), pady=8)
        self.dl_share = secondary_button(self.dl_result, "↗  Send to...",
                                         lambda: self._share_menu(self.dl_share, self.last_extract), width=120,
                                         height=36)
        self.dl_share.pack(side="right", padx=6, pady=8)
        self.dl_open = secondary_button(self.dl_result, "Open folder", lambda: open_in_explorer(self.last_download),
                                        width=116, height=36)
        self.dl_open.pack(side="right", padx=6, pady=8)

        # --- folded extras
        self.dl_options = Card(tab, "Options", collapsible=True, start_open=False)
        self.dl_options.grid(row=2, column=0, sticky="ew", padx=6, pady=(0, 8))
        o = self.dl_options.body
        o.grid_columnconfigure(1, weight=1)
        self.dl_dir = ctk.StringVar(value=default_out_dir())
        ctk.CTkLabel(o, text="Save downloads to", width=140, anchor="w").grid(row=0, column=0, sticky="w", pady=6)
        ctk.CTkEntry(o, textvariable=self.dl_dir, height=36).grid(row=0, column=1, sticky="ew", padx=8, pady=6)
        secondary_button(o, "Change...", lambda: self._pick_dir(self.dl_dir), width=110, height=36).grid(
            row=0, column=2, pady=6)
        ctk.CTkLabel(o, text="YouTube account", width=140, anchor="w").grid(row=1, column=0, sticky="w", pady=6)
        acct = ctk.CTkFrame(o, fg_color="transparent")
        acct.grid(row=1, column=1, columnspan=2, sticky="ew", padx=8, pady=6)
        self.yt_status = ctk.CTkLabel(acct, text="", anchor="w")
        self.yt_status.pack(side="left")
        self.yt_signout = secondary_button(acct, "Sign out", self._youtube_sign_out, width=100, height=36)
        self.yt_signin = primary_button(acct, "Sign in...", self._youtube_sign_in, width=130, height=36,
                                        font=font(13, "bold"))
        ctk.CTkLabel(o, text="Needed when YouTube asks to confirm you're not a bot, and for age-restricted videos. "
                             "You sign in once in a separate browser window.",
                     text_color=MUTED, wraplength=520, justify="left").grid(row=2, column=1, columnspan=2,
                                                                            sticky="w", padx=8)
        self._refresh_youtube_account()
        ctk.CTkLabel(o, text="Audio taken from a file is saved next to that file.",
                     text_color=MUTED).grid(row=3, column=1, columnspan=2, sticky="w", padx=8, pady=(4, 0))
        self.dl_dir.trace_add("write", lambda *_: self._refresh_download_summary())

        self.dl_log_card = Card(tab, "Details", "what the downloader is doing", collapsible=True, start_open=False)
        self.dl_log_card.grid(row=3, column=0, sticky="nsew", padx=6, pady=(0, 6))
        self.log = ctk.CTkTextbox(self.dl_log_card.body, height=170, font=ctk.CTkFont(family="Consolas", size=12))
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

        self._on_choice("video")
        self._refresh_download_summary()

    def _refresh_download_summary(self):
        where = short_path(self.dl_dir.get() or default_out_dir())
        self.dl_options.set_summary(f"Saving to {where}")
        if self.get_source == "link" and self.get_choice != "remix":
            self.dl_where.configure(text=f"Saves to {where}")
        elif self.get_source == "file" and self.get_choice == "audio":
            self.dl_where.configure(text="Saves next to the file")
        else:
            self.dl_where.configure(text="")

    def _on_source(self, value=None):
        self.get_source = "file" if "file" in (value or self.src_switch.get()) else "link"
        if self.get_source == "file":
            self.link_row.grid_forget()
            self.file_row.grid(row=1, column=0, sticky="ew")
            if self.get_choice == "video":
                self.get_choice = "audio"
        else:
            self.file_row.grid_forget()
            self.link_row.grid(row=1, column=0, sticky="ew")
        self._on_choice(self.get_choice)

    def _on_choice(self, key):
        if key == "video" and self.get_source == "file":
            messagebox.showinfo(APP_NAME, "You already have this video as a file.\n\n"
                                          "Choose 'Audio only' to take its sound out, or 'Remix it'.")
            key = self.get_choice if self.get_choice != "video" else "audio"
        self.get_choice = key
        self.dl_mode.set("video" if key == "video" else "audio")
        for k, tile in self.choice_tiles.items():
            if k == key:
                tile.configure(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white")
            elif k == "video" and self.get_source == "file":
                tile.configure(fg_color=("gray88", "gray19"), hover_color=("gray85", "gray21"),
                               text_color=("gray60", "gray45"))
            else:
                tile.configure(**SECONDARY)
        for wdg in self.choice_opts.winfo_children():
            wdg.pack_forget()
        if key == "video":
            self.quality_label.pack(side="left", padx=(0, 8))
            self.quality_box.pack(side="left")
            self.choice_hint.configure(text="Higher quality = bigger file.")
            self.get_btn.configure(text="⬇   Download video")
        elif key == "audio":
            self.fmt_label.pack(side="left", padx=(0, 8))
            self.ex_fmt.pack(side="left")
            self.choice_hint.configure(text=FORMAT_HINTS.get(self.ex_fmt.get(), ""))
            self.get_btn.configure(text="♪   Get the audio")
        else:
            self.choice_hint.configure(text="The song opens in the Remix tab, ready for a style.")
            self.get_btn.configure(text="✦   Open in Remix")
        self.choice_hint.pack(side="left", padx=(16 if key != "remix" else 0, 0))
        self._refresh_download_summary()

    def _paste_url(self):
        try:
            self.url_var.set(self.clipboard_get().strip())
        except Exception:
            pass

    def _pick_dir(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or os.path.expanduser("~"))
        if d:
            var.set(d)

    def _pick_get_file(self):
        p = filedialog.askopenfilename(filetypes=[*AUDIO_TYPES[:1], *VIDEO_TYPES])
        if p:
            self._set_get_file(p)

    def _set_get_file(self, path):
        self.ex_src.set(path)
        self.ex_name.configure(text=os.path.basename(path))
        self.ex_folder.configure(text=short_path(os.path.dirname(path), 3))
        self.dl_result.pack_forget()

    def _append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # --- sharing: the video file itself, via the clipboard (desktop has no share sheet)
    def _share_menu(self, anchor, saved_file: str | None = None):
        """Menu of messengers. A link is first downloaded to a temporary folder (not your
        Music folder), a saved file is shared as it is."""
        import tkinter as tk

        from .downloader import normalize_url
        from .share import SHARE_APPS

        url = None
        if not saved_file:
            url = normalize_url(self.url_var.get())
            if not url:
                messagebox.showinfo(APP_NAME, "Paste a video link first - Share sends that video to your friends.")
                return
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="  Send the video to ...", state="disabled")
        for name in SHARE_APPS:
            menu.add_command(label=f"     {name}", command=lambda n=name: self._share(n, url, saved_file))
        menu.add_command(label="     Another app (just copy the video)", command=lambda: self._share(None, url, saved_file))
        if url:
            menu.add_separator()

            def copy_link():
                self.clipboard_clear()
                self.clipboard_append(url)
                self.set_status("Link copied - paste it anywhere.")
            menu.add_command(label="  Copy only the link", command=copy_link)
        try:
            menu.tk_popup(anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height())
        finally:
            menu.grab_release()

    def _share(self, app_name, url, saved_file):
        from . import share

        def deliver(path):
            self._hide_cancel()
            copied = share.copy_file_to_clipboard(path)
            if app_name:
                share.open_share_app(app_name)
            where = app_name or "the app you want"
            if copied:
                key = "Cmd+V" if sys.platform == "darwin" else "Ctrl+V"
                messagebox.showinfo(APP_NAME, f"The video is copied.\n\nOpen a chat in {where} and press {key} "
                                              "to send it.")
                self.set_status(f"Video copied - press Ctrl+V in a chat to send it ({os.path.basename(path)})", 1)
            else:
                open_in_explorer(path)
                messagebox.showinfo(APP_NAME, f"Drag the selected file into a chat in {where} to send it.")

        if saved_file:
            deliver(saved_file)
            return
        choice = self.get_choice
        audio_only = choice != "video"
        quality = self.quality_box.get()
        login = self._youtube_cookies()
        cancel = self._show_cancel()
        share.clean_share_dir()
        folder = share.share_dir()
        self._append_log(f"--- share {url}")

        def work(progress):
            from .downloader import download

            return download(url, folder, audio_only=audio_only, quality=quality, on_progress=progress,
                            on_log=lambda m: self._queue.put(("log", m)), cookie_file=login, cancel=cancel)

        def failed(exc):
            self._hide_cancel()
            if getattr(exc, "cancelled", False):
                self.set_status("Sharing cancelled.", 0)
                return
            self._offer_youtube_sign_in(exc, lambda: self._share(app_name, url, saved_file))

        self.run_task("Getting the video to share...", work, deliver, failed)

    # --- the main action
    def start_get(self):
        choice = self.get_choice
        if self.get_source == "file":
            src = self.ex_src.get().strip()
            if not os.path.isfile(src):
                messagebox.showwarning(APP_NAME, "Please choose a file first (button 'Choose a file...').")
                return
            if choice == "remix":
                self._send_to_remix(src)
            else:
                self._extract_file(src)
            return
        from .downloader import normalize_url

        url = normalize_url(self.url_var.get())
        if not url:
            messagebox.showwarning(APP_NAME, "Please paste a video link first.\n\n"
                                             "Copy it from the app or browser (Share -> Copy link), then press 'Paste'.")
            return
        self.url_var.set(url)
        self._download(url, choice)

    def _download(self, url, choice):
        audio_only = choice != "video"
        fmt = self.ex_fmt.get() if choice == "audio" else "mp3"
        quality = self.quality_box.get()
        login = self._youtube_cookies()
        out_dir = self.dl_dir.get().strip() or default_out_dir()
        self._append_log(f"--- {url}")
        self.dl_resume.pack_forget()
        self.dl_result.pack_forget()
        cancel = self._show_cancel()

        def work(progress):
            from .downloader import download

            path = download(url, out_dir, audio_only=audio_only, quality=quality,
                            on_progress=progress, on_log=lambda m: self._queue.put(("log", m)),
                            cookie_file=login, cancel=cancel)
            if audio_only and not path.lower().endswith("." + fmt):
                progress(1.0, f"Converting to {fmt.upper()}...")
                out = os.path.splitext(path)[0] + "." + fmt
                extract_audio(path, out)
                try:
                    os.remove(path)
                except OSError:
                    pass
                path = out
            return path

        def done(path):
            self._hide_cancel()
            self.last_download = path
            self._append_log(f"Saved: {path}")
            if choice == "remix":
                self.set_status(f"Downloaded {os.path.basename(path)} - opening it in Remix...", 1)
                self._send_to_remix(path)
                return
            self.set_status(f"Saved: {os.path.basename(path)}", 1)
            self._show_result(path)

        def failed(exc):
            self._hide_cancel()
            if getattr(exc, "cancelled", False):
                self._append_log("Download cancelled. Unfinished files were deleted.")
                self.set_status("Download cancelled.", 0)
                return
            self.dl_log_card.toggle(True)
            if getattr(exc, "resumable", False):
                self.dl_resume.pack(side="left", padx=(10, 0), after=self.get_btn)
                self._append_log("Download paused. The downloaded part is kept - press 'Resume' to continue.")
                self.set_status("Download paused - press Resume to continue.", None)
            self._offer_youtube_sign_in(exc, lambda: self._download(url, choice))

        self.run_task("Starting download...", work, done, failed)

    # --- Cancel button (shown while a download runs)
    def _show_cancel(self):
        self._cancel_event = threading.Event()
        self.dl_cancel.configure(state="normal", text="✕  Cancel")
        self.dl_cancel.pack(side="left", padx=(10, 0), after=self.get_btn)
        return self._cancel_event

    def _hide_cancel(self):
        self._cancel_event = None
        self.dl_cancel.pack_forget()

    def _cancel_download(self):
        if self._cancel_event is not None:
            self._cancel_event.set()
            self.dl_cancel.configure(state="disabled", text="Stopping...")
            self.set_status("Stopping the download...", None)

    # --- YouTube sign-in (passes "confirm you're not a bot")
    def _youtube_cookies(self):
        from . import ytlogin

        return ytlogin.cookie_file() if ytlogin.signed_in() else None

    def _refresh_youtube_account(self):
        from . import ytlogin

        self.yt_signin.pack_forget()
        self.yt_signout.pack_forget()
        if ytlogin.signed_in():
            when = time.strftime("%d %b %Y", time.localtime(ytlogin.saved_at() or time.time()))
            self.yt_status.configure(text=f"✓  Signed in (since {when})", text_color=GOOD)
            self.yt_signin.configure(text="Sign in again")
            self.yt_signout.pack(side="right")
            self.yt_signin.pack(side="right", padx=(0, 8))
        else:
            self.yt_status.configure(text="Not signed in", text_color=MUTED)
            self.yt_signin.configure(text="Sign in...")
            self.yt_signin.pack(side="right")

    def _offer_youtube_sign_in(self, exc, retry):
        """After a 'confirm you're not a bot' error: offer to sign in, then try again."""
        low = str(exc).lower()
        if "not a bot" not in low and "sign in" not in low and "cookie" not in low:
            return
        if messagebox.askyesno(APP_NAME, "Sign in to YouTube now?\n\nA browser window opens; sign in there once "
                                         "and EasyRemix will try the download again by itself."):
            self._youtube_sign_in(on_success=retry)

    def _youtube_sign_out(self):
        from . import ytlogin

        if messagebox.askyesno(APP_NAME, "Sign out of YouTube in EasyRemix?\n\n(Your normal browser is not "
                                         "affected.)"):
            ytlogin.sign_out()
            self._refresh_youtube_account()
            self.set_status("Signed out of YouTube.", None)

    def _youtube_sign_in(self, on_success=None):
        from . import ytlogin

        if self._busy:
            messagebox.showinfo(APP_NAME, "Please wait, another task is still running.")
            return
        browsers = ytlogin.find_browsers()
        if not browsers:
            messagebox.showerror(APP_NAME, "Signing in needs Chrome, Edge, Opera, Brave or Vivaldi, "
                                           "but none of them was found on this computer.")
            return
        name, exe = browsers[0]
        if len(browsers) > 1:
            name, exe = self._pick_browser(browsers) or (None, None)
            if not exe:
                return

        cancelled = threading.Event()
        dlg = ctk.CTkToplevel(self)
        dlg.title("Sign in to YouTube")
        dlg.geometry("460x230")
        dlg.resizable(False, False)
        dlg.transient(self)
        ctk.CTkLabel(dlg, text="Sign in to YouTube", font=ctk.CTkFont(size=18, weight="bold")).pack(
            anchor="w", padx=22, pady=(20, 6))
        ctk.CTkLabel(dlg, text=f"A {name} window opened. Sign in to your Google / YouTube account there.\n\n"
                               "This box closes by itself as soon as you are signed in. EasyRemix keeps the "
                               "login in its own browser profile; your normal browser is not touched.",
                     wraplength=410, justify="left", anchor="w").pack(anchor="w", padx=22)
        secondary_button(dlg, "Cancel", lambda: (cancelled.set(), dlg.destroy()), width=110, height=34).pack(
            side="bottom", anchor="e", padx=22, pady=16)
        dlg.protocol("WM_DELETE_WINDOW", lambda: (cancelled.set(), dlg.destroy()))
        dlg.after(150, dlg.grab_set)

        def work(progress):
            window = ytlogin.LoginWindow(exe)
            window.open()
            try:
                return ytlogin.wait_for_login(window, should_stop=cancelled.is_set,
                                              on_status=lambda m: progress(0.5, m))
            except ytlogin.LoginCancelled:
                if cancelled.is_set():
                    return None  # the user pressed Cancel: no error message
                raise

        def done(path):
            if dlg.winfo_exists():
                dlg.destroy()
            self._refresh_youtube_account()
            if not path:
                self.set_status("Sign-in cancelled.", None)
                return
            self.set_status("Signed in to YouTube.", 1)
            if on_success:
                self.after(200, on_success)

        def failed(_exc):
            if dlg.winfo_exists():
                dlg.destroy()
            self._refresh_youtube_account()

        self.run_task("Signing in to YouTube", work, done, failed)

    def _pick_browser(self, browsers):
        """Small chooser: which browser opens the sign-in window."""
        result = {}
        dlg = ctk.CTkToplevel(self)
        dlg.title("Sign in to YouTube")
        dlg.resizable(False, False)
        dlg.transient(self)
        ctk.CTkLabel(dlg, text="Which browser should open for signing in?", font=ctk.CTkFont(size=15,
                     weight="bold")).pack(anchor="w", padx=22, pady=(18, 10))
        names = [n for n, _ in browsers]
        box = ctk.CTkComboBox(dlg, values=names, state="readonly", width=260, height=36)
        box.set(names[0])
        box.pack(anchor="w", padx=22)

        def ok():
            result["pick"] = browsers[names.index(box.get())]
            dlg.destroy()

        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=18)
        primary_button(row, "Open", ok, width=110, height=36).pack(side="right")
        secondary_button(row, "Cancel", dlg.destroy, width=100, height=36).pack(side="right", padx=8)
        dlg.after(150, dlg.grab_set)
        self.wait_window(dlg)
        return result.get("pick")

    def _extract_file(self, src):
        fmt = self.ex_fmt.get()
        out = os.path.splitext(src)[0] + "." + fmt
        if os.path.abspath(out) == os.path.abspath(src):
            out = os.path.splitext(src)[0] + " (audio)." + fmt

        def work(progress):
            progress(0.3, "Taking the audio out...")
            extract_audio(src, out)
            return out

        def done(path):
            self.last_download = path
            self.set_status(f"Audio saved: {path}", 1)
            self._show_result(path)

        self.run_task("Taking the audio out...", work, done)

    def _show_result(self, path):
        self.last_extract = path
        self.dl_result_label.configure(text=f"✓  Saved {os.path.basename(path)}")
        self.dl_result.pack(fill="x", pady=(14, 0))

    # ------------------------------------------------------------ tab 3: remix
    def _build_remix(self, tab):
        from .dsp import AMBIENCE, EQ_LABELS, EQ_PRESETS, REMIX_PRESETS

        self.rx_src = ctk.StringVar()
        self.vars: dict[str, ctk.Variable] = {}

        # --- empty state: shown until a song is loaded
        self.rx_empty = ctk.CTkFrame(tab, corner_radius=16, fg_color=CARD)
        inner = ctk.CTkFrame(self.rx_empty, fg_color="transparent")
        inner.place(relx=0.5, rely=0.45, anchor="center")
        logo = load_logo(84)
        ctk.CTkLabel(inner, text="" if logo else "♫", image=logo, font=font(60)).pack(pady=(0, 10))
        ctk.CTkLabel(inner, text="Open a song to start remixing", font=font(22, "bold")).pack()
        ctk.CTkLabel(inner, text="MP3, WAV, FLAC, M4A, OGG ... or any video file.\n"
                                 "EasyRemix finds its key and tempo, then you pick a style.",
                     text_color=MUTED, font=font(14), justify="center").pack(pady=(6, 18))
        primary_button(inner, "Open a song...", self._pick_remix_src, width=240).pack()
        self.rx_empty.pack(fill="both", expand=True, padx=6, pady=6)

        # --- loaded state
        self.rx_main = ctk.CTkFrame(tab, fg_color="transparent")

        top = ctk.CTkFrame(self.rx_main, corner_radius=14, fg_color=CARD)
        top.pack(fill="x", padx=6, pady=(6, 6))
        top.grid_columnconfigure(0, weight=1)
        self.rx_title = ctk.CTkLabel(top, text="", font=font(17, "bold"), anchor="w")
        self.rx_title.grid(row=0, column=0, sticky="w", padx=16, pady=(12, 2))
        pills = ctk.CTkFrame(top, fg_color="transparent")
        pills.grid(row=1, column=0, sticky="w", padx=16, pady=(4, 12))
        self.lbl_key = pill(pills, "Key –")
        self.lbl_key.pack(side="left", padx=(0, 8))
        self.lbl_bpm = pill(pills, "– BPM")
        self.lbl_bpm.pack(side="left", padx=(0, 8))
        self.lbl_len = pill(pills, "0:00")
        self.lbl_len.pack(side="left", padx=(0, 12))
        self.lbl_alt = ctk.CTkLabel(pills, text="", text_color=MUTED)
        self.lbl_alt.pack(side="left")
        btns = ctk.CTkFrame(top, fg_color="transparent")
        btns.grid(row=0, column=1, rowspan=2, padx=16)
        secondary_button(btns, "Open another...", self._pick_remix_src, width=140).pack(side="left", padx=4)
        secondary_button(btns, "Reset all", self.reset_remix, width=100).pack(side="left", padx=4)

        # action bar (packed before the scroll area so it is always visible)
        act = ctk.CTkFrame(self.rx_main, corner_radius=14, fg_color=CARD)
        act.pack(fill="x", side="bottom", padx=6, pady=(6, 6))
        ctk.CTkLabel(act, text="Listen from", font=font(13)).pack(side="left", padx=(16, 6), pady=12)
        self.preview_from = ctk.CTkEntry(act, width=64, height=38, justify="center")
        self.preview_from.insert(0, "0:30")
        self.preview_from.pack(side="left")
        ctk.CTkButton(act, text="▶  Preview remix", width=150, height=40, font=font(14, "bold"),
                      command=self.preview, **PLAY).pack(side="left", padx=(10, 4))
        secondary_button(act, "▶  Original", lambda: self.preview(original=True), width=110, height=40).pack(
            side="left", padx=4)
        secondary_button(act, "■  Stop", lambda: self.player.stop(), width=80, height=40).pack(side="left", padx=4)
        self.export_btn = primary_button(act, "Export remix", self.export, width=190, height=42)
        self.export_btn.pack(side="right", padx=16)
        self.export_fmt = ctk.CTkComboBox(act, values=list(EXPORT_FORMATS), width=84, height=38, state="readonly")
        self.export_fmt.set("mp3")
        self.export_fmt.pack(side="right")
        ctk.CTkLabel(act, text="as", font=font(13)).pack(side="right", padx=8)

        scroll = ctk.CTkScrollableFrame(self.rx_main, corner_radius=0, fg_color="transparent")
        scroll.pack(fill="both", expand=True)
        self._cards: dict[str, Card] = {}

        # --- one-click styles
        pc = Card(scroll, "1   Pick a style", "one click sets everything - then press Preview")
        pc.pack(fill="x", padx=6, pady=6)
        grid = ctk.CTkFrame(pc.body, fg_color="transparent")
        grid.pack(fill="x")
        self.preset_buttons: dict[str, ctk.CTkButton] = {}
        cols = 5
        for i, name in enumerate(REMIX_PRESETS):
            grid.grid_columnconfigure(i % cols, weight=1, uniform="presets")
            btn = ctk.CTkButton(grid, text=name, height=38, corner_radius=19, font=font(13, "bold"),
                                command=lambda n=name: self.apply_remix_preset(n), **SECONDARY)
            btn.grid(row=i // cols, column=i % cols, sticky="ew", padx=4, pady=4)
            self.preset_buttons[name] = btn

        # --- speed & pitch
        sp = Card(scroll, "2   Speed & pitch", "slower = deeper, faster = brighter (like a record player)")
        sp.pack(fill="x", padx=6, pady=6)
        b = sp.body
        b.grid_columnconfigure(1, weight=1)
        self._slider(b, 0, "speed", "Speed", 0.5, 1.5, 1.0, lambda v: f"x{v:.3f}", steps=1000)
        self.speed_info = ctk.CTkLabel(b, text="", anchor="w", justify="left", font=font(13, "bold"))
        self.speed_info.grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 10))
        ctk.CTkLabel(b, text="Speeds that keep the song in tune  (★ = how good it usually sounds)",
                     anchor="w", text_color=MUTED).grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self.sugg_frame = ctk.CTkFrame(b, fg_color="transparent")
        self.sugg_frame.grid(row=3, column=0, columnspan=3, sticky="ew")
        self.sugg_why = ctk.CTkLabel(b, text="", anchor="w", justify="left", text_color=MUTED, wraplength=900)
        self.sugg_why.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 8))
        self._slider(b, 5, "pitch_semitones", "Extra pitch", -12, 12, 0.0, lambda v: f"{v:+.1f} st", steps=48)
        self.vars["keep_pitch"] = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(b, text="Keep the original pitch (change only the tempo)",
                      variable=self.vars["keep_pitch"], progress_color=ACCENT,
                      command=self._update_speed_info).grid(row=6, column=1, sticky="w", pady=4)
        self._suggestion_chips: list[tuple[ctk.CTkButton, float]] = []
        self._render_suggestions()

        # --- equalizer (folded)
        eq = Card(scroll, "Equalizer", "bass, mids and treble", collapsible=True, start_open=False)
        eq.pack(fill="x", padx=6, pady=6)
        self._cards["eq"] = eq
        row = ctk.CTkFrame(eq.body, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkLabel(row, text="Preset").pack(side="left")
        self.eq_preset = ctk.CTkComboBox(row, values=list(EQ_PRESETS), width=260, state="readonly",
                                         command=self.apply_eq_preset)
        self.eq_preset.set("Flat")
        self.eq_preset.pack(side="left", padx=8)
        bands = ctk.CTkFrame(eq.body, fg_color="transparent")
        bands.pack(fill="x", pady=(8, 0))
        self.eq_vars = []
        self.eq_value_labels = []
        for i, lab in enumerate(EQ_LABELS):
            bands.grid_columnconfigure(i, weight=1)
            var = ctk.DoubleVar(value=0.0)
            val = ctk.CTkLabel(bands, text="0", width=40, text_color=MUTED)
            val.grid(row=0, column=i)
            s = ctk.CTkSlider(bands, from_=-12, to=12, number_of_steps=48, orientation="vertical",
                              height=150, variable=var, progress_color=ACCENT, button_color=ACCENT,
                              button_hover_color=ACCENT_HOVER, command=lambda v, idx=i: self._eq_changed(idx))
            s.grid(row=1, column=i, pady=4)
            ctk.CTkLabel(bands, text=lab).grid(row=2, column=i)
            self.eq_vars.append(var)
            self.eq_value_labels.append(val)
        ctk.CTkLabel(eq.body, text="Left = bass, right = treble. Up = louder, down = quieter (±12 dB).",
                     text_color=MUTED).pack(anchor="w", pady=(6, 0))

        # --- effects (folded)
        fx = Card(scroll, "Effects", "reverb, echo, lo-fi, 8D ...", collapsible=True, start_open=False)
        fx.pack(fill="x", padx=6, pady=6)
        self._cards["fx"] = fx
        b = fx.body
        b.grid_columnconfigure(1, weight=1)
        b.grid_columnconfigure(4, weight=1)
        pct = lambda v: f"{v * 100:.0f}%"  # noqa: E731
        left = [
            ("reverb_mix", "Reverb", 0, 1, 0.0, pct),
            ("reverb_size", "  Room size", 0, 1, 0.7, pct),
            ("echo_mix", "Echo", 0, 1, 0.0, pct),
            ("echo_delay", "  Echo delay", 0.05, 1.5, 0.35, lambda v: f"{v * 1000:.0f} ms"),
            ("echo_feedback", "  Echo repeats", 0, 0.9, 0.35, pct),
        ]
        right = [
            ("chorus_mix", "Chorus", 0, 1, 0.0, pct),
            ("lofi_amount", "Lo-Fi", 0, 1, 0.0, pct),
            ("distortion_db", "Distortion", 0, 30, 0.0, lambda v: f"{v:.0f} dB"),
            ("rotate_8d", "8D rotation", 0, 1, 0.0, pct),
            ("rotate_speed", "  Rotation speed", 0.03, 0.5, 0.12, lambda v: f"{v:.2f} Hz"),
        ]
        for r, spec in enumerate(left):
            self._slider(b, r, *spec, col=0)
        for r, spec in enumerate(right):
            self._slider(b, r, *spec, col=3)
        ctk.CTkLabel(b, text="Drag a slider above 0 to switch an effect on. 8D sounds best with headphones.",
                     text_color=MUTED).grid(row=5, column=0, columnspan=6, sticky="w", pady=(6, 0))

        # --- ambience (folded)
        amb = Card(scroll, "Ambience", "rain, vinyl, ocean ... under the music", collapsible=True, start_open=False)
        amb.pack(fill="x", padx=6, pady=6)
        self._cards["amb"] = amb
        b = amb.body
        b.grid_columnconfigure(1, weight=1)
        b.grid_columnconfigure(4, weight=1)
        for i, (attr, (label, _)) in enumerate(AMBIENCE.items()):
            self._slider(b, i % 3, attr, label, 0, 1, 0.0, pct, col=0 if i < 3 else 3)
        ctk.CTkLabel(b, text="All sounds are generated by EasyRemix, so they are royalty-free.",
                     text_color=MUTED).grid(row=3, column=0, columnspan=6, sticky="w", pady=(6, 0))

        # --- volume (folded)
        vol = Card(scroll, "Volume & fades", collapsible=True, start_open=False)
        vol.pack(fill="x", padx=6, pady=6)
        self._cards["vol"] = vol
        b = vol.body
        b.grid_columnconfigure(1, weight=1)
        b.grid_columnconfigure(4, weight=1)
        self._slider(b, 0, "volume_db", "Volume", -24, 12, 0.0, lambda v: f"{v:+.1f} dB", steps=72)
        self._slider(b, 0, "fade_in", "Fade in", 0, 10, 0.0, lambda v: f"{v:.1f} s", steps=40, col=3)
        self._slider(b, 1, "fade_out", "Fade out", 0, 15, 0.0, lambda v: f"{v:.1f} s", steps=60, col=3)
        self.vars["normalize"] = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(b, text="Make it as loud as modern tracks", variable=self.vars["normalize"],
                      progress_color=ACCENT, command=self._refresh_summaries).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=4)
        ctk.CTkLabel(b, text="A built-in limiter always prevents clipping and distortion.", text_color=MUTED).grid(
            row=2, column=0, columnspan=6, sticky="w")

        self._update_speed_info()
        self._refresh_summaries()

    def _slider(self, parent, row, attr, label, lo, hi, default, fmt, steps=200, col=0):
        var = ctk.DoubleVar(value=default)
        self.vars[attr] = var
        ctk.CTkLabel(parent, text=label, anchor="w", width=130).grid(row=row, column=col, sticky="w", pady=4)
        val = ctk.CTkLabel(parent, text=fmt(default), width=80, anchor="e", font=font(13, "bold"))

        def changed(v):
            val.configure(text=fmt(float(v)))
            if attr in ("speed", "pitch_semitones"):
                self._update_speed_info()
            self._mark_custom()
            self._refresh_summaries()

        s = ctk.CTkSlider(parent, from_=lo, to=hi, number_of_steps=steps, variable=var,
                          progress_color=ACCENT, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                          command=changed)
        s.grid(row=row, column=col + 1, sticky="ew", padx=8, pady=4)
        val.grid(row=row, column=col + 2, sticky="e", padx=(0, 18))
        var.trace_add("write", lambda *_: val.configure(text=fmt(var.get())))

    # --- summaries in the folded card headers
    def _refresh_summaries(self, *_):
        if not getattr(self, "_cards", None) or "vol" not in self._cards:
            return
        v = {k: var.get() for k, var in self.vars.items()}
        self._cards["eq"].set_summary(self.eq_preset.get())
        names = [("reverb_mix", "Reverb"), ("echo_mix", "Echo"), ("chorus_mix", "Chorus"),
                 ("lofi_amount", "Lo-Fi"), ("distortion_db", "Distortion"), ("rotate_8d", "8D")]
        on = [n for k, n in names if v.get(k, 0) > (0.1 if k == "distortion_db" else 0.01)]
        self._cards["fx"].set_summary(", ".join(on) if on else "off")
        from .dsp import AMBIENCE

        amb = [label.split(" ")[0] for k, (label, _) in AMBIENCE.items() if v.get(k, 0) > 0.005]
        self._cards["amb"].set_summary(", ".join(amb) if amb else "off")
        parts = []
        if abs(v.get("volume_db", 0)) > 0.05:
            parts.append(f"{v['volume_db']:+.1f} dB")
        if v.get("normalize"):
            parts.append("loud")
        if v.get("fade_in", 0) > 0 or v.get("fade_out", 0) > 0:
            parts.append("fades")
        self._cards["vol"].set_summary(", ".join(parts) if parts else "unchanged")

    def _mark_custom(self):
        if not self._loading_preset and self.current_preset:
            self._highlight_preset("")

    def _highlight_preset(self, name: str):
        self.current_preset = name
        for n, btn in self.preset_buttons.items():
            if n == name:
                btn.configure(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white")
            else:
                btn.configure(**SECONDARY)

    def _eq_changed(self, idx):
        self.eq_value_labels[idx].configure(text=f"{self.eq_vars[idx].get():+.0f}")
        if not self._loading_preset:
            self.eq_preset.set("Custom")
            self._mark_custom()
        self._refresh_summaries()

    def apply_eq_preset(self, name):
        from .dsp import EQ_PRESETS

        gains = EQ_PRESETS.get(name)
        if gains is None:
            return
        self._set_eq(gains, name)
        if not self._loading_preset:
            self._mark_custom()

    def _set_eq(self, gains, name):
        was = self._loading_preset
        self._loading_preset = True
        for var, lab, g in zip(self.eq_vars, self.eq_value_labels, gains):
            var.set(g)
            lab.configure(text=f"{g:+.0f}")
        self._loading_preset = was
        self.eq_preset.set(name)
        self._refresh_summaries()

    def _set_speed(self, speed: float, why: str = ""):
        self.vars["speed"].set(speed)
        self.vars["keep_pitch"].set(False)
        self.sugg_why.configure(text=why)
        self._mark_custom()
        self._update_speed_info()

    def _update_speed_info(self):
        if not hasattr(self, "speed_info"):
            return
        speed = self.vars["speed"].get()
        pitch = self.vars["pitch_semitones"].get()
        text = describe_speed(speed, self.key, self.bpm, self.vars["keep_pitch"].get(), pitch)
        out_of_tune = "OUT OF TUNE" in text
        self.speed_info.configure(text=("⚠  " if out_of_tune else "✓  ") + text.replace("  |  ", "   ·   "),
                                  text_color=BAD if out_of_tune else GOOD)
        for chip, chip_speed in getattr(self, "_suggestion_chips", []):
            if abs(chip_speed - speed) < 5e-4:
                chip.configure(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white", border_width=0)
            else:
                chip.configure(**SECONDARY, border_width=0)

    def _render_suggestions(self):
        for w in self.sugg_frame.winfo_children():
            w.destroy()
        self._suggestion_chips = []
        cols = 4
        for i, s in enumerate(speed_suggestions(self.key, self.bpm)):
            self.sugg_frame.grid_columnconfigure(i % cols, weight=1, uniform="sugg")
            detail = f"x{s.speed:.3f}   {s.semitones:+d} st"
            if s.new_key:
                detail += f"   {s.new_key.short}"
            if s.new_bpm:
                detail += f"   {s.new_bpm:.0f} BPM"
            text = f"{'★' * s.stars}{'☆' * (5 - s.stars)}   {s.label}\n{detail}"
            chip = ctk.CTkButton(self.sugg_frame, text=text, height=52, corner_radius=12, font=font(13),
                                 command=lambda sp=s.speed, d=s.description: self._set_speed(sp, d), **SECONDARY)
            chip.grid(row=i // cols, column=i % cols, sticky="ew", padx=4, pady=4)
            self._suggestion_chips.append((chip, s.speed))
        self._update_speed_info()

    def reset_remix(self):
        from .dsp import RemixSettings

        defaults = RemixSettings()
        self._loading_preset = True
        for attr, var in self.vars.items():
            var.set(getattr(defaults, attr))
        self._set_eq([0.0] * 10, "Flat")
        self._loading_preset = False
        self._highlight_preset("")
        self.sugg_why.configure(text="")
        self._update_speed_info()
        self._refresh_summaries()

    def apply_remix_preset(self, name):
        from .dsp import EQ_PRESETS, REMIX_PRESETS

        preset = REMIX_PRESETS.get(name)
        if not preset:
            return
        self.reset_remix()
        self._loading_preset = True
        for attr, value in preset.items():
            if attr == "eq_gains":
                match = next((k for k, v in EQ_PRESETS.items() if v == value), None)
                self._set_eq(value, match or "Custom")
            elif attr in self.vars:
                self.vars[attr].set(value)
        self._loading_preset = False
        self._highlight_preset(name)
        self._update_speed_info()
        self._refresh_summaries()
        self.set_status(f"Style '{name}' applied - press 'Preview remix' to listen.")

    def current_settings(self):
        from .dsp import RemixSettings

        s = RemixSettings()
        for attr, var in self.vars.items():
            setattr(s, attr, var.get())
        s.eq_gains = [v.get() for v in self.eq_vars]
        return s

    def _pick_remix_src(self):
        p = filedialog.askopenfilename(filetypes=AUDIO_TYPES)
        if p:
            self.load_track(p)

    def _send_to_remix(self, path):
        if path:
            self.tabs.set(TAB_REMIX)
            self.load_track(path)

    def load_track(self, path):
        self.rx_src.set(path)

        def work(progress):
            from .analysis import detect_bpm, detect_key

            progress(0.1, "Loading song...")
            audio = load_audio(path)
            progress(0.5, "Finding the musical key...")
            key = detect_key(audio, SAMPLE_RATE)
            progress(0.8, "Measuring the tempo (BPM)...")
            bpm = detect_bpm(audio, SAMPLE_RATE)
            return audio, key, bpm

        def done(res):
            audio, kres, bpm = res
            self.audio, self.audio_path = audio, path
            self.key, self.bpm = kres.key, (bpm or None)
            self.rx_title.configure(text=os.path.basename(path))
            self.lbl_key.configure(text=f"{kres.key.name}  ·  {kres.key.camelot}")
            self.lbl_bpm.configure(text=f"{bpm:.0f} BPM" if bpm else "– BPM")
            self.lbl_len.configure(text=fmt_time(audio.shape[1] / SAMPLE_RATE))
            alts = ", ".join(k.short for k, _ in kres.alternatives[:2])
            self.lbl_alt.configure(text=f"{kres.confidence * 100:.0f}% sure  ·  could also be {alts}")
            if self.rx_empty.winfo_ismapped():
                self.rx_empty.pack_forget()
                self.rx_main.pack(fill="both", expand=True)
            self._render_suggestions()
            self._update_speed_info()
            self.set_status(f"Loaded {os.path.basename(path)} - pick a style, then press 'Preview remix'.", 1)

        self.run_task("Loading song...", work, done)

    def _require_audio(self) -> bool:
        if self.audio is None:
            messagebox.showwarning(APP_NAME, "Open a song first.")
            return False
        return True

    def preview(self, original=False):
        if not self._require_audio():
            return
        try:
            start = parse_time(self.preview_from.get())
        except ValueError:
            start = 0.0
        total = self.audio.shape[1] / SAMPLE_RATE
        if start >= total - 1:
            start = 0.0
        settings = self.current_settings()
        settings.fade_in = 0.0
        settings.fade_out = 0.0
        a = int(start * SAMPLE_RATE)
        span = PREVIEW_SECONDS * max(1.0, settings.speed if not original else 1.0)
        clip = self.audio[:, a: a + int(span * SAMPLE_RATE)]

        def work(progress):
            from .dsp import process

            if original:
                return clip
            return process(clip, SAMPLE_RATE, settings, progress)

        def done(audio):
            try:
                self.player.play(audio, SAMPLE_RATE)
                what = "original" if original else "remix"
                self.set_status(f"Playing the {what} from {fmt_time(start)} ({PREVIEW_SECONDS} s)...", 1)
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc))

        self.run_task("Preparing preview...", work, done)

    def export(self):
        if not self._require_audio():
            return
        fmt = self.export_fmt.get()
        base = os.path.splitext(os.path.basename(self.audio_path))[0]
        suffix = self.current_preset or "remix"
        suffix = suffix.lower().replace(" ", "_").replace("+", "and")
        out = filedialog.asksaveasfilename(
            defaultextension="." + fmt, filetypes=[(fmt.upper(), "*." + fmt)],
            initialdir=os.path.dirname(self.audio_path), initialfile=f"{base} ({suffix}).{fmt}")
        if not out:
            return
        settings = self.current_settings()
        audio = self.audio

        def work(progress):
            from .dsp import process

            result = process(audio, SAMPLE_RATE, settings, lambda f, m: progress(f * 0.9, m))
            progress(0.92, "Saving file...")
            save_audio(result, out)
            return out

        def done(path):
            self.set_status(f"Exported: {path}", 1)
            if messagebox.askyesno(APP_NAME, f"Your remix is saved!\n\n{path}\n\nOpen the folder?"):
                open_in_explorer(path)

        self.run_task("Rendering remix...", work, done)


def main():
    app = App()
    app.mainloop()
