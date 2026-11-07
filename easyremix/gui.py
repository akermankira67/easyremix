"""EasyRemix graphical interface (customtkinter)."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback
from tkinter import filedialog, messagebox

import customtkinter as ctk

from . import APP_NAME, __version__
from .audio_io import EXPORT_FORMATS, SAMPLE_RATE, extract_audio, load_audio, save_audio
from .theory import Key, describe_speed, semitones_for_speed, speed_suggestions

ACCENT = "#7c4dff"
MUTED = ("gray40", "gray65")
VIDEO_TYPES = [("Video files", "*.mp4 *.mkv *.webm *.mov *.avi *.flv *.m4v *.wmv"), ("All files", "*.*")]
AUDIO_TYPES = [("Audio & video", "*.mp3 *.wav *.flac *.m4a *.ogg *.opus *.aac *.wma *.mp4 *.mkv *.webm *.mov"),
               ("All files", "*.*")]
PREVIEW_SECONDS = 25


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


class Section(ctk.CTkFrame):
    """A titled card used to group controls."""

    def __init__(self, master, title: str, subtitle: str = ""):
        super().__init__(master, corner_radius=12)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(head, text=title, font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")
        if subtitle:
            ctk.CTkLabel(head, text=subtitle, text_color=MUTED).pack(side="left", padx=10)
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=14, pady=(0, 12))


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title(f"{APP_NAME} {__version__}")
        self.geometry("1180x820")
        self.minsize(980, 680)

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

        self._build_header()
        self.tabs = ctk.CTkTabview(self, corner_radius=12, segmented_button_selected_color=ACCENT)
        self.tabs.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        self._build_download(self.tabs.add("  1. Download  "))
        self._build_extract(self.tabs.add("  2. Extract audio  "))
        self._build_remix(self.tabs.add("  3. Remix  "))
        self._build_statusbar()
        self.after(80, self._poll_queue)

    # ------------------------------------------------------------ plumbing
    @property
    def player(self):
        if self._player is None:
            from .player import Player

            self._player = Player()
        return self._player

    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=18, pady=(12, 0))
        ctk.CTkLabel(bar, text="EasyRemix", font=ctk.CTkFont(size=26, weight="bold"),
                     text_color=ACCENT).pack(side="left")
        ctk.CTkLabel(bar, text="download  •  extract  •  remix", text_color=MUTED,
                     font=ctk.CTkFont(size=14)).pack(side="left", padx=14, pady=(6, 0))
        self.theme_switch = ctk.CTkSegmentedButton(bar, values=["Dark", "Light"],
                                                   command=lambda v: ctk.set_appearance_mode(v.lower()),
                                                   selected_color=ACCENT)
        self.theme_switch.set("Dark")
        self.theme_switch.pack(side="right")

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self, height=36, corner_radius=0)
        bar.pack(fill="x", side="bottom")
        self.progress = ctk.CTkProgressBar(bar, width=260, progress_color=ACCENT)
        self.progress.set(0)
        self.progress.pack(side="right", padx=14, pady=8)
        self.status = ctk.CTkLabel(bar, text="Ready.", anchor="w")
        self.status.pack(side="left", fill="x", expand=True, padx=14)

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
                elif kind == "error":
                    self._busy = False
                    self.set_status("Something went wrong.", 0)
                    messagebox.showerror(APP_NAME, f"{item[1].strip('. ')} failed:\n\n{item[2]}")
                    if item[3]:
                        item[3](item[2])
        except queue.Empty:
            pass
        self.after(80, self._poll_queue)

    def _file_row(self, parent, label, var, browse, row, button="Browse..."):
        ctk.CTkLabel(parent, text=label, anchor="w", width=120).grid(row=row, column=0, sticky="w", pady=6)
        ctk.CTkEntry(parent, textvariable=var, height=36).grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        ctk.CTkButton(parent, text=button, width=110, height=36, command=browse,
                      fg_color="gray30", hover_color="gray25").grid(row=row, column=2, pady=6)

    # ------------------------------------------------------------ tab 1: download
    def _build_download(self, tab):
        card = Section(tab, "Download a video", "Paste a YouTube link (most other video sites work too)")
        card.pack(fill="both", expand=True, padx=6, pady=6)
        b = card.body
        b.grid_columnconfigure(1, weight=1)

        self.url_var = ctk.StringVar()
        ctk.CTkLabel(b, text="Video link", anchor="w", width=120).grid(row=0, column=0, sticky="w", pady=6)
        entry = ctk.CTkEntry(b, textvariable=self.url_var, height=40,
                             placeholder_text="https://www.youtube.com/watch?v=...")
        entry.grid(row=0, column=1, sticky="ew", padx=8, pady=6)
        entry.bind("<Return>", lambda e: self.start_download())
        ctk.CTkButton(b, text="Paste", width=110, height=40, fg_color="gray30", hover_color="gray25",
                      command=self._paste_url).grid(row=0, column=2, pady=6)

        ctk.CTkLabel(b, text="Save as", anchor="w", width=120).grid(row=1, column=0, sticky="w", pady=6)
        opts = ctk.CTkFrame(b, fg_color="transparent")
        opts.grid(row=1, column=1, columnspan=2, sticky="w", padx=8)
        self.dl_mode = ctk.StringVar(value="video")
        ctk.CTkRadioButton(opts, text="Video (MP4)", variable=self.dl_mode, value="video",
                           command=self._dl_mode_changed, fg_color=ACCENT).pack(side="left", padx=(0, 18))
        ctk.CTkRadioButton(opts, text="Audio only (MP3 320 kbps)", variable=self.dl_mode, value="audio",
                           command=self._dl_mode_changed, fg_color=ACCENT).pack(side="left", padx=(0, 18))
        ctk.CTkLabel(opts, text="Quality:").pack(side="left", padx=(12, 6))
        from .downloader import QUALITIES

        self.quality_box = ctk.CTkComboBox(opts, values=list(QUALITIES), width=160, state="readonly")
        self.quality_box.set("Best available")
        self.quality_box.pack(side="left")
        ctk.CTkLabel(opts, text="Use login from:").pack(side="left", padx=(18, 6))
        self.cookies_box = ctk.CTkComboBox(opts, values=["No (anonymous)", "Firefox", "Edge", "Chrome", "Brave",
                                                         "Opera"], width=150, state="readonly")
        self.cookies_box.set("No (anonymous)")
        self.cookies_box.pack(side="left")

        self.dl_dir = ctk.StringVar(value=default_out_dir())
        self._file_row(b, "Save to folder", self.dl_dir,
                       lambda: self._pick_dir(self.dl_dir), row=2)

        actions = ctk.CTkFrame(b, fg_color="transparent")
        actions.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(12, 6))
        ctk.CTkButton(actions, text="⬇  Download", height=44, width=200, fg_color=ACCENT,
                      font=ctk.CTkFont(size=15, weight="bold"),
                      command=self.start_download).pack(side="left")
        self.dl_resume = ctk.CTkButton(actions, text="⟳  Resume", height=44, width=130, state="disabled",
                                       fg_color="gray30", hover_color="#1b5e20",
                                       command=self.start_download)
        self.dl_resume.pack(side="left", padx=(8, 0))
        self.dl_open = ctk.CTkButton(actions, text="Open folder", height=44, width=130, state="disabled",
                                     fg_color="gray30", hover_color="gray25",
                                     command=lambda: open_in_explorer(self.last_download))
        self.dl_open.pack(side="left", padx=8)
        self.dl_to_extract = ctk.CTkButton(actions, text="→ Extract audio", height=44, width=150,
                                           state="disabled", fg_color="gray30", hover_color="gray25",
                                           command=self._download_to_extract)
        self.dl_to_extract.pack(side="left", padx=8)
        self.dl_to_remix = ctk.CTkButton(actions, text="→ Remix it", height=44, width=130,
                                         state="disabled", fg_color="gray30", hover_color="gray25",
                                         command=lambda: self._send_to_remix(self.last_download))
        self.dl_to_remix.pack(side="left", padx=8)

        ctk.CTkLabel(b, text="Log", anchor="w", text_color=MUTED).grid(row=4, column=0, sticky="nw", pady=(10, 0))
        self.log = ctk.CTkTextbox(b, height=240, font=ctk.CTkFont(family="Consolas", size=12))
        self.log.grid(row=4, column=1, columnspan=2, sticky="nsew", padx=8, pady=(10, 0))
        b.grid_rowconfigure(4, weight=1)
        self.log.configure(state="disabled")

    def _dl_mode_changed(self):
        self.quality_box.configure(state="disabled" if self.dl_mode.get() == "audio" else "readonly")

    def _paste_url(self):
        try:
            self.url_var.set(self.clipboard_get().strip())
        except Exception:
            pass

    def _pick_dir(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or os.path.expanduser("~"))
        if d:
            var.set(d)

    def _append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start_download(self):
        url = self.url_var.get().strip()
        if not url.startswith(("http://", "https://", "www.", "youtu")):
            messagebox.showwarning(APP_NAME, "Please paste a valid video link first.")
            return
        if not url.startswith("http"):
            url = "https://" + url
        audio_only = self.dl_mode.get() == "audio"
        quality = self.quality_box.get()
        browser = self.cookies_box.get()
        browser = None if browser.startswith("No") else browser
        out_dir = self.dl_dir.get().strip() or default_out_dir()
        self._append_log(f"--- {url}")
        self.dl_resume.configure(state="disabled", fg_color="gray30")

        def work(progress):
            from .downloader import download

            return download(url, out_dir, audio_only=audio_only, quality=quality,
                            on_progress=progress, on_log=lambda m: self._queue.put(("log", m)),
                            cookies_browser=browser)

        def done(path):
            self.last_download = path
            self._append_log(f"Saved: {path}")
            self.set_status(f"Downloaded: {os.path.basename(path)}", 1)
            for btn in (self.dl_open, self.dl_to_remix):
                btn.configure(state="normal")
            self.dl_to_extract.configure(state="disabled" if audio_only else "normal")

        def failed(exc):
            if getattr(exc, "resumable", False):
                self.dl_resume.configure(state="normal", fg_color="#2e7d32")
                self._append_log("Download paused. The downloaded part is kept - press 'Resume' to continue.")
                self.set_status("Download paused - press Resume to continue.", None)

        self.run_task("Starting download...", work, done, failed)

    def _download_to_extract(self):
        self.ex_src.set(self.last_download)
        self._suggest_extract_out()
        self.tabs.set("  2. Extract audio  ")

    # ------------------------------------------------------------ tab 2: extract
    def _build_extract(self, tab):
        card = Section(tab, "Separate audio from video", "Save the sound of any video as a standalone audio file")
        card.pack(fill="x", padx=6, pady=6)
        b = card.body
        b.grid_columnconfigure(1, weight=1)
        self.ex_src = ctk.StringVar()
        self.ex_out = ctk.StringVar()
        self._file_row(b, "Video file", self.ex_src, self._pick_extract_src, row=0)
        ctk.CTkLabel(b, text="Audio format", anchor="w", width=120).grid(row=1, column=0, sticky="w", pady=6)
        self.ex_fmt = ctk.CTkSegmentedButton(b, values=list(EXPORT_FORMATS),
                                             command=lambda v: self._suggest_extract_out(),
                                             selected_color=ACCENT)
        self.ex_fmt.set("mp3")
        self.ex_fmt.grid(row=1, column=1, sticky="w", padx=8)
        self._file_row(b, "Save as", self.ex_out, self._pick_extract_out, row=2)

        hint = ("mp3 = small & universal   •   wav = lossless, big   •   flac = lossless, compressed   "
                "•   m4a/ogg = efficient")
        ctk.CTkLabel(b, text=hint, text_color=MUTED).grid(row=3, column=1, sticky="w", padx=8)

        actions = ctk.CTkFrame(b, fg_color="transparent")
        actions.grid(row=4, column=0, columnspan=3, sticky="w", pady=(14, 4))
        ctk.CTkButton(actions, text="♪  Extract audio", height=44, width=200, fg_color=ACCENT,
                      font=ctk.CTkFont(size=15, weight="bold"),
                      command=self.start_extract).pack(side="left")
        self.ex_open = ctk.CTkButton(actions, text="Open folder", height=44, width=130, state="disabled",
                                     fg_color="gray30", hover_color="gray25",
                                     command=lambda: open_in_explorer(self.last_extract))
        self.ex_open.pack(side="left", padx=8)
        self.ex_to_remix = ctk.CTkButton(actions, text="→ Remix it", height=44, width=130, state="disabled",
                                         fg_color="gray30", hover_color="gray25",
                                         command=lambda: self._send_to_remix(self.last_extract))
        self.ex_to_remix.pack(side="left", padx=8)

    def _pick_extract_src(self):
        p = filedialog.askopenfilename(filetypes=VIDEO_TYPES)
        if p:
            self.ex_src.set(p)
            self._suggest_extract_out()

    def _suggest_extract_out(self):
        src = self.ex_src.get()
        if src:
            self.ex_out.set(os.path.splitext(src)[0] + "." + self.ex_fmt.get())

    def _pick_extract_out(self):
        fmt = self.ex_fmt.get()
        p = filedialog.asksaveasfilename(defaultextension="." + fmt, filetypes=[(fmt.upper(), "*." + fmt)],
                                         initialfile=os.path.basename(self.ex_out.get()))
        if p:
            self.ex_out.set(p)

    def start_extract(self):
        src, out = self.ex_src.get().strip(), self.ex_out.get().strip()
        if not os.path.isfile(src):
            messagebox.showwarning(APP_NAME, "Please choose a video file first.")
            return
        if not out:
            self._suggest_extract_out()
            out = self.ex_out.get()
        ext = os.path.splitext(out)[1].lower().lstrip(".")
        if ext not in EXPORT_FORMATS:
            out = os.path.splitext(out)[0] + "." + self.ex_fmt.get()
            self.ex_out.set(out)
        if os.path.abspath(out) == os.path.abspath(src):
            messagebox.showwarning(APP_NAME, "The output file must be different from the video file.")
            return

        def work(progress):
            progress(0.3, "Extracting audio...")
            extract_audio(src, out)
            return out

        def done(path):
            self.last_extract = path
            self.set_status(f"Audio saved: {path}", 1)
            self.ex_open.configure(state="normal")
            self.ex_to_remix.configure(state="normal")

        self.run_task("Extracting audio...", work, done)

    # ------------------------------------------------------------ tab 3: remix
    def _build_remix(self, tab):
        from .dsp import EQ_LABELS, EQ_PRESETS, REMIX_PRESETS

        top = ctk.CTkFrame(tab, corner_radius=12)
        top.pack(fill="x", padx=6, pady=(6, 4))
        top.grid_columnconfigure(1, weight=1)
        self.rx_src = ctk.StringVar()
        ctk.CTkLabel(top, text="Track", font=ctk.CTkFont(weight="bold"), width=60).grid(
            row=0, column=0, padx=(14, 4), pady=10)
        ctk.CTkEntry(top, textvariable=self.rx_src, height=36,
                     placeholder_text="Choose an audio or video file...").grid(row=0, column=1, sticky="ew", padx=6)
        ctk.CTkButton(top, text="Open...", width=100, height=36, fg_color=ACCENT,
                      command=self._pick_remix_src).grid(row=0, column=2, padx=(4, 14))

        info = ctk.CTkFrame(top, fg_color="transparent")
        info.grid(row=1, column=0, columnspan=3, sticky="ew", padx=14, pady=(0, 10))
        big = ctk.CTkFont(size=15, weight="bold")
        self.lbl_key = ctk.CTkLabel(info, text="Key: –", font=big)
        self.lbl_key.pack(side="left", padx=(0, 22))
        self.lbl_bpm = ctk.CTkLabel(info, text="BPM: –", font=big)
        self.lbl_bpm.pack(side="left", padx=(0, 22))
        self.lbl_len = ctk.CTkLabel(info, text="Length: –", font=big)
        self.lbl_len.pack(side="left", padx=(0, 22))
        self.lbl_alt = ctk.CTkLabel(info, text="", text_color=MUTED)
        self.lbl_alt.pack(side="left")
        ctk.CTkButton(info, text="Reset all", width=90, fg_color="gray30", hover_color="gray25",
                      command=self.reset_remix).pack(side="right")
        self.preset_box = ctk.CTkComboBox(info, values=["— One-click remix presets —", *REMIX_PRESETS],
                                          width=250, state="readonly", command=self.apply_remix_preset)
        self.preset_box.set("— One-click remix presets —")
        self.preset_box.pack(side="right", padx=8)

        # bottom action bar (packed before the scroll area so it is always visible)
        act = ctk.CTkFrame(tab, corner_radius=12)
        act.pack(fill="x", side="bottom", padx=6, pady=(4, 6))
        ctk.CTkLabel(act, text="Preview from").pack(side="left", padx=(14, 6), pady=10)
        self.preview_from = ctk.CTkEntry(act, width=70)
        self.preview_from.insert(0, "0:30")
        self.preview_from.pack(side="left")
        ctk.CTkButton(act, text="▶  Preview", width=120, height=38, fg_color="#2e7d32", hover_color="#1b5e20",
                      command=self.preview).pack(side="left", padx=(10, 4))
        ctk.CTkButton(act, text="■  Stop", width=90, height=38, fg_color="gray30", hover_color="gray25",
                      command=lambda: self.player.stop()).pack(side="left", padx=4)
        ctk.CTkButton(act, text="▶ Original", width=100, height=38, fg_color="gray30", hover_color="gray25",
                      command=lambda: self.preview(original=True)).pack(side="left", padx=4)
        self.export_btn = ctk.CTkButton(act, text="💾  Export remix", width=170, height=38, fg_color=ACCENT,
                                        font=ctk.CTkFont(size=14, weight="bold"), command=self.export)
        self.export_btn.pack(side="right", padx=14)
        self.export_fmt = ctk.CTkComboBox(act, values=list(EXPORT_FORMATS), width=80, state="readonly")
        self.export_fmt.set("mp3")
        self.export_fmt.pack(side="right")
        ctk.CTkLabel(act, text="Format").pack(side="right", padx=6)

        scroll = ctk.CTkScrollableFrame(tab, corner_radius=12, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=0, pady=0)
        self.vars: dict[str, ctk.Variable] = {}

        # --- speed & pitch
        sp = Section(scroll, "Speed & Pitch",
                     "Slow down / speed up. Classic mode changes pitch too, like a record player.")
        sp.pack(fill="x", padx=6, pady=6)
        b = sp.body
        b.grid_columnconfigure(1, weight=1)
        self._slider(b, 0, "speed", "Speed", 0.5, 1.5, 1.0, lambda v: f"x{v:.3f}", steps=1000)
        self._slider(b, 1, "pitch_semitones", "Extra pitch", -12, 12, 0.0, lambda v: f"{v:+.1f} st", steps=48)
        self.vars["keep_pitch"] = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(b, text="Keep original pitch (change tempo only - time-stretch)",
                      variable=self.vars["keep_pitch"], progress_color=ACCENT,
                      command=self._update_speed_info).grid(row=2, column=1, sticky="w", pady=4)
        self.speed_info = ctk.CTkLabel(b, text="", anchor="w", justify="left",
                                       font=ctk.CTkFont(size=13, weight="bold"))
        self.speed_info.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 2))

        ctk.CTkLabel(b, text="Best speeds (always land exactly on a real key, so the song stays in tune):",
                     anchor="w", text_color=MUTED).grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 4))
        self.sugg_frame = ctk.CTkFrame(b, fg_color="transparent")
        self.sugg_frame.grid(row=5, column=0, columnspan=3, sticky="ew")
        self._render_suggestions()

        # --- equalizer
        eq = Section(scroll, "Equalizer", "10-band graphic EQ, ±12 dB")
        eq.pack(fill="x", padx=6, pady=6)
        row = ctk.CTkFrame(eq.body, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkLabel(row, text="Preset").pack(side="left")
        self.eq_preset = ctk.CTkComboBox(row, values=list(EQ_PRESETS), width=240, state="readonly",
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
                              command=lambda v, idx=i: self._eq_changed(idx))
            s.grid(row=1, column=i, pady=4)
            ctk.CTkLabel(bands, text=lab).grid(row=2, column=i)
            self.eq_vars.append(var)
            self.eq_value_labels.append(val)

        # --- effects
        fx = Section(scroll, "Effects", "Drag a slider above 0 to switch an effect on")
        fx.pack(fill="x", padx=6, pady=6)
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
            ("lofi_amount", "Lo-Fi / bitcrush", 0, 1, 0.0, pct),
            ("distortion_db", "Distortion", 0, 30, 0.0, lambda v: f"{v:.0f} dB"),
            ("rotate_8d", "8D rotation", 0, 1, 0.0, pct),
            ("rotate_speed", "  Rotation speed", 0.03, 0.5, 0.12, lambda v: f"{v:.2f} Hz"),
        ]
        for r, spec in enumerate(left):
            self._slider(b, r, *spec, col=0)
        for r, spec in enumerate(right):
            self._slider(b, r, *spec, col=3)

        # --- ambience
        amb = Section(scroll, "Ambience layers", "Mix atmospheric sounds under the music (generated, royalty-free)")
        amb.pack(fill="x", padx=6, pady=6)
        b = amb.body
        b.grid_columnconfigure(1, weight=1)
        b.grid_columnconfigure(4, weight=1)
        from .dsp import AMBIENCE

        for i, (attr, (label, _)) in enumerate(AMBIENCE.items()):
            self._slider(b, i % 3, attr, label, 0, 1, 0.0, pct, col=0 if i < 3 else 3)

        # --- volume
        vol = Section(scroll, "Volume & Output")
        vol.pack(fill="x", padx=6, pady=6)
        b = vol.body
        b.grid_columnconfigure(1, weight=1)
        b.grid_columnconfigure(4, weight=1)
        self._slider(b, 0, "volume_db", "Volume", -24, 12, 0.0, lambda v: f"{v:+.1f} dB", steps=72)
        self._slider(b, 0, "fade_in", "Fade in", 0, 10, 0.0, lambda v: f"{v:.1f} s", steps=40, col=3)
        self._slider(b, 1, "fade_out", "Fade out", 0, 15, 0.0, lambda v: f"{v:.1f} s", steps=60, col=3)
        self.vars["normalize"] = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(b, text="Normalize loudness (make it as loud as modern tracks)",
                      variable=self.vars["normalize"], progress_color=ACCENT).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=4)
        ctk.CTkLabel(b, text="A built-in limiter always prevents clipping/distortion.", text_color=MUTED).grid(
            row=2, column=0, columnspan=3, sticky="w")

        self._update_speed_info()

    def _slider(self, parent, row, attr, label, lo, hi, default, fmt, steps=200, col=0):
        var = ctk.DoubleVar(value=default)
        self.vars[attr] = var
        ctk.CTkLabel(parent, text=label, anchor="w", width=130).grid(row=row, column=col, sticky="w", pady=3)
        val = ctk.CTkLabel(parent, text=fmt(default), width=80, anchor="e")

        def changed(v):
            val.configure(text=fmt(float(v)))
            if attr in ("speed", "pitch_semitones"):
                self._update_speed_info()

        s = ctk.CTkSlider(parent, from_=lo, to=hi, number_of_steps=steps, variable=var,
                          progress_color=ACCENT, button_color=ACCENT, command=changed)
        s.grid(row=row, column=col + 1, sticky="ew", padx=8, pady=3)
        val.grid(row=row, column=col + 2, sticky="e", padx=(0, 18))
        var.trace_add("write", lambda *_: val.configure(text=fmt(var.get())))

    def _eq_changed(self, idx):
        self.eq_value_labels[idx].configure(text=f"{self.eq_vars[idx].get():+.0f}")
        if not self._loading_preset:
            self.eq_preset.set("Custom")

    def apply_eq_preset(self, name):
        from .dsp import EQ_PRESETS

        gains = EQ_PRESETS.get(name)
        if gains is None:
            return
        self._loading_preset = True
        for var, lab, g in zip(self.eq_vars, self.eq_value_labels, gains):
            var.set(g)
            lab.configure(text=f"{g:+.0f}")
        self._loading_preset = False
        self.eq_preset.set(name)

    def _set_speed(self, speed: float):
        self.vars["speed"].set(speed)
        self.vars["keep_pitch"].set(False)
        self._update_speed_info()

    def _update_speed_info(self):
        if not hasattr(self, "speed_info"):
            return
        speed = self.vars["speed"].get()
        pitch = self.vars["pitch_semitones"].get()
        text = describe_speed(speed, self.key, self.bpm, self.vars["keep_pitch"].get(), pitch)
        color = ("#c62828", "#ff8a80") if "OUT OF TUNE" in text else ("#2e7d32", "#81c784")
        self.speed_info.configure(text=text, text_color=color)

    def _render_suggestions(self):
        for w in self.sugg_frame.winfo_children():
            w.destroy()
        f = self.sugg_frame
        headers = ["", "Rating", "Style", "Speed", "Pitch", "New key", "BPM", "Why"]
        for c, h in enumerate(headers):
            ctk.CTkLabel(f, text=h, font=ctk.CTkFont(weight="bold"), text_color=MUTED).grid(
                row=0, column=c, sticky="w", padx=6)
        f.grid_columnconfigure(7, weight=1)
        for r, s in enumerate(speed_suggestions(self.key, self.bpm), start=1):
            ctk.CTkButton(f, text="Use", width=54, height=26, fg_color=ACCENT,
                          command=lambda sp=s.speed: self._set_speed(sp)).grid(row=r, column=0, padx=6, pady=2)
            ctk.CTkLabel(f, text="★" * s.stars + "☆" * (5 - s.stars), text_color="#ffb300").grid(
                row=r, column=1, sticky="w", padx=6)
            ctk.CTkLabel(f, text=s.label).grid(row=r, column=2, sticky="w", padx=6)
            ctk.CTkLabel(f, text=f"x{s.speed:.3f}").grid(row=r, column=3, sticky="w", padx=6)
            ctk.CTkLabel(f, text=f"{s.semitones:+d} st").grid(row=r, column=4, sticky="w", padx=6)
            key_txt = f"{s.new_key.short} ({s.new_key.camelot})" if s.new_key else "load a track"
            ctk.CTkLabel(f, text=key_txt).grid(row=r, column=5, sticky="w", padx=6)
            ctk.CTkLabel(f, text=f"{s.new_bpm:.0f}" if s.new_bpm else "–").grid(row=r, column=6, sticky="w", padx=6)
            ctk.CTkLabel(f, text=s.description, text_color=MUTED, anchor="w", justify="left",
                         wraplength=560).grid(row=r, column=7, sticky="w", padx=6)

    def reset_remix(self):
        from .dsp import RemixSettings

        defaults = RemixSettings()
        for attr, var in self.vars.items():
            var.set(getattr(defaults, attr))
        self.apply_eq_preset("Flat")
        self.preset_box.set("— One-click remix presets —")
        self._update_speed_info()

    def apply_remix_preset(self, name):
        from .dsp import EQ_PRESETS, REMIX_PRESETS

        preset = REMIX_PRESETS.get(name)
        if not preset:
            return
        self.reset_remix()
        for attr, value in preset.items():
            if attr == "eq_gains":
                match = next((k for k, v in EQ_PRESETS.items() if v == value), None)
                self.apply_eq_preset("Flat")
                self._loading_preset = True
                for var, lab, g in zip(self.eq_vars, self.eq_value_labels, value):
                    var.set(g)
                    lab.configure(text=f"{g:+.0f}")
                self._loading_preset = False
                self.eq_preset.set(match or "Custom")
            elif attr in self.vars:
                self.vars[attr].set(value)
        self.preset_box.set(name)
        self._update_speed_info()
        self.set_status(f"Preset '{name}' applied - press Preview to listen.")

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
            self.tabs.set("  3. Remix  ")
            self.load_track(path)

    def load_track(self, path):
        self.rx_src.set(path)

        def work(progress):
            from .analysis import detect_bpm, detect_key

            progress(0.1, "Loading track...")
            audio = load_audio(path)
            progress(0.5, "Detecting musical key...")
            key = detect_key(audio, SAMPLE_RATE)
            progress(0.8, "Detecting tempo (BPM)...")
            bpm = detect_bpm(audio, SAMPLE_RATE)
            return audio, key, bpm

        def done(res):
            audio, kres, bpm = res
            self.audio, self.audio_path = audio, path
            self.key, self.bpm = kres.key, (bpm or None)
            self.lbl_key.configure(text=f"Key: {kres.key.name}  ({kres.key.camelot})")
            self.lbl_bpm.configure(text=f"BPM: ~{bpm:.0f}" if bpm else "BPM: –")
            self.lbl_len.configure(text=f"Length: {fmt_time(audio.shape[1] / SAMPLE_RATE)}")
            alts = ", ".join(k.short for k, _ in kres.alternatives[:2])
            self.lbl_alt.configure(text=f"confidence {kres.confidence * 100:.0f}%  •  could also be: {alts}")
            self._render_suggestions()
            self._update_speed_info()
            self.set_status(f"Loaded {os.path.basename(path)} - pick a preset or tweak the settings.", 1)

        self.run_task("Loading track...", work, done)

    def _require_audio(self) -> bool:
        if self.audio is None:
            src = self.rx_src.get().strip()
            if src and os.path.isfile(src):
                self.load_track(src)
                messagebox.showinfo(APP_NAME, "The track is loading. Try again in a moment.")
            else:
                messagebox.showwarning(APP_NAME, "Open a track first (button 'Open...').")
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
                self.set_status(f"Playing {what} preview from {fmt_time(start)} ({PREVIEW_SECONDS}s)...", 1)
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc))

        self.run_task("Rendering preview...", work, done)

    def export(self):
        if not self._require_audio():
            return
        fmt = self.export_fmt.get()
        base = os.path.splitext(os.path.basename(self.audio_path))[0]
        suffix = self.preset_box.get()
        suffix = "remix" if suffix.startswith("—") else suffix.lower().replace(" ", "_").replace("+", "and")
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
            if messagebox.askyesno(APP_NAME, f"Remix saved!\n\n{path}\n\nOpen the folder?"):
                open_in_explorer(path)

        self.run_task("Rendering remix...", work, done)


def main():
    app = App()
    app.mainloop()
