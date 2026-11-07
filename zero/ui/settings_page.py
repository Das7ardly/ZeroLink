"""Settings page."""
from __future__ import annotations

from PySide6.QtCore import QProcess, QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from ..binaries import Tools, child_env, version_of
from ..convert import ENCODER_LABELS
from ..paths import bin_dir, data_dir
from ..presets import MP3_QUALITY, PRESETS, VCODECS
from .queue_view import reveal
from .theme import C, icon

BROWSERS = [("", "Don't use cookies (fine for most videos)"), ("firefox", "Firefox (recommended)"),
            ("file", "cookies.txt file…"),
            ("chrome", "Chrome (often fails on Windows)"), ("edge", "Edge (often fails on Windows)"),
            ("brave", "Brave (often fails on Windows)"), ("opera", "Opera (often fails on Windows)"),
            ("vivaldi", "Vivaldi (often fails on Windows)")]


class VersionProbe(QThread):
    result = Signal(dict)

    def __init__(self, tools: Tools, parent=None):
        super().__init__(parent)
        self.tools = tools

    def run(self):
        self.result.emit({
            "yt-dlp": version_of(self.tools.ytdlp),
            "ffmpeg": version_of(self.tools.ffmpeg, "-version"),
            "deno": version_of(self.tools.deno),
        })


class SettingsPage(QWidget):
    changed = Signal()
    request_setup = Signal()

    def __init__(self, settings, tools_getter, parent=None):
        super().__init__(parent)
        self.s = settings
        self.tools_getter = tools_getter
        self._vp: VersionProbe | None = None
        self._upd: QProcess | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        body.setObjectName("root")
        scroll.setWidget(body)
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        inner = QWidget()
        inner.setMaximumWidth(860)
        bl.addWidget(inner, 1)
        bl.addStretch(0)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(32, 28, 32, 28)
        lay.setSpacing(16)
        t = QLabel("Settings")
        t.setObjectName("h1")
        lay.addWidget(t)

        # Downloads ---------------------------------------------------------
        g = QGroupBox("Downloads")
        f = QFormLayout(g)
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(10)
        row = QHBoxLayout()
        self.dir = QLineEdit(settings.download_dir)
        self.dir.editingFinished.connect(self._save)
        browse = QPushButton("Browse…")
        browse.setIcon(icon("folder-open", C["text"], 16))
        browse.clicked.connect(self._browse)
        row.addWidget(self.dir, 1)
        row.addWidget(browse)
        f.addRow("Default folder", row)

        self.tmpl = QLineEdit(settings.filename_template)
        self.tmpl.setToolTip("yt-dlp output template. Examples:\n"
                             "%(title)s [%(id)s].%(ext)s\n"
                             "%(uploader)s/%(upload_date>%Y-%m-%d)s - %(title)s.%(ext)s")
        self.tmpl.editingFinished.connect(self._save)
        f.addRow("File name", self.tmpl)

        self.preset = QComboBox()
        for k, lbl in PRESETS:
            self.preset.addItem(lbl, k)
        self.preset.setCurrentIndex(max(0, self.preset.findData(settings.default_preset)))
        self.preset.currentIndexChanged.connect(self._save)
        f.addRow("Default quality", self.preset)

        self.vcodec = QComboBox()
        for k, lbl in VCODECS:
            self.vcodec.addItem(lbl, k)
        self.vcodec.setCurrentIndex(max(0, self.vcodec.findData(settings.vcodec)))
        self.vcodec.currentIndexChanged.connect(self._save)
        f.addRow("Default video codec", self.vcodec)

        self.convert = QCheckBox("If a site doesn't offer H.264 / H.265, convert after download")
        self.convert.setToolTip("YouTube never offers H.265 and has no H.264 above 1080p.\n"
                                "With this on, ZeroLink downloads the best stream and re-encodes it with ffmpeg.\n"
                                "With it off, ZeroLink picks the closest codec the site has (H.264 then limits to 1080p).")
        self.convert.setChecked(settings.convert_codecs)
        self.convert.toggled.connect(self._save)
        f.addRow("", self.convert)

        self.encoder = QComboBox()
        for k, lbl in ENCODER_LABELS:
            self.encoder.addItem(lbl, k)
        self.encoder.setCurrentIndex(max(0, self.encoder.findData(settings.encoder)))
        self.encoder.setToolTip("GPU encoding is 5-20x faster. If the GPU encoder isn't available, ZeroLink falls back to the CPU.")
        self.encoder.currentIndexChanged.connect(self._save)
        f.addRow("Converter", self.encoder)

        self.mp3q = QComboBox()
        for k, lbl in MP3_QUALITY:
            self.mp3q.addItem(lbl, k)
        self.mp3q.setCurrentIndex(max(0, self.mp3q.findData(settings.mp3_quality)))
        self.mp3q.currentIndexChanged.connect(self._save)
        f.addRow("Default MP3 quality", self.mp3q)

        self.conc = self._number_combo(1, 8, int(settings.max_concurrent))
        f.addRow("Downloads at once", self.conc)
        self.fconc = self._number_combo(1, 8, int(settings.files_concurrent or 3))
        self.fconc.setToolTip("How many links on the Files page download at the same time.")
        f.addRow("File downloads at once", self.fconc)

        self.frags = self._number_combo(1, 16, int(settings.concurrent_fragments))
        self.frags.setToolTip("Parallel chunks per download for HLS/DASH streams. Higher is faster on good connections.")
        f.addRow("Chunks per download", self.frags)

        self.rate = QLineEdit(settings.rate_limit)
        self.rate.setPlaceholderText("unlimited  (e.g. 5M or 800K)")
        self.rate.setToolTip("Applies to video and file downloads. Examples: 5M = 5 MB/s, 800K = 800 KB/s.")
        self.rate.editingFinished.connect(self._save)
        f.addRow("Speed limit", self.rate)

        self.archive = QCheckBox("Skip videos I already downloaded (keeps an archive of video IDs)")
        self.archive.setChecked(settings.use_archive)
        self.archive.toggled.connect(self._save)
        f.addRow("", self.archive)
        lay.addWidget(g)

        # Subtitles & metadata ----------------------------------------------
        g = QGroupBox("Subtitles and metadata")
        f = QFormLayout(g)
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(10)
        self.langs = QLineEdit(settings.sub_langs)
        self.langs.setToolTip("Comma separated, regex allowed.\n"
                              "en.*  = all English variants\nbg = Bulgarian\nall = everything\n"
                              "all,-live_chat = everything except chat replay")
        self.langs.editingFinished.connect(self._save)
        f.addRow("Subtitle languages", self.langs)
        self.auto = QCheckBox("Include auto-generated subtitles when no human ones exist")
        self.auto.setChecked(settings.auto_subs)
        self.esubs = QCheckBox("Embed subtitles into the video file")
        self.esubs.setChecked(settings.embed_subs)
        self.ethumb = QCheckBox("Embed thumbnail as cover art")
        self.ethumb.setChecked(settings.embed_thumbnail)
        self.emeta = QCheckBox("Embed title, artist, date and description")
        self.emeta.setChecked(settings.embed_metadata)
        self.echap = QCheckBox("Embed chapters")
        self.echap.setChecked(settings.embed_chapters)
        for cb in (self.auto, self.esubs, self.ethumb, self.emeta, self.echap):
            cb.toggled.connect(self._save)
            f.addRow("", cb)
        lay.addWidget(g)

        # Access ----------------------------------------------------------------
        g = QGroupBox("Access and behaviour")
        f = QFormLayout(g)
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(10)
        self.cookies = QComboBox()
        for k, lbl in BROWSERS:
            self.cookies.addItem(lbl, k)
        self.cookies.setCurrentIndex(max(0, self.cookies.findData(settings.cookies_browser)))
        self.cookies.setToolTip("Only needed for age-restricted, members-only or private videos.\n"
                                "Firefox works. Chrome, Edge and other Chromium browsers lock and encrypt\n"
                                "their cookies on Windows, so yt-dlp usually can't read them.\n"
                                "For a Chromium browser, export a cookies.txt with a browser extension instead.\n"
                                "If cookies fail, ZeroLink continues without them for the rest of the session.")
        self.cookies.currentIndexChanged.connect(self._cookies_changed)
        self.cookie_file_lbl = QLabel(self._cookie_file_text())
        self.cookie_file_lbl.setObjectName("muted")
        crow = QHBoxLayout()
        crow.addWidget(self.cookies, 1)
        crow.addWidget(self.cookie_file_lbl)
        f.addRow("Browser cookies", crow)
        self.extra = QLineEdit(settings.extra_args)
        self.extra.setPlaceholderText("Extra yt-dlp options, e.g. --sponsorblock-remove sponsor")
        self.extra.editingFinished.connect(self._save)
        f.addRow("Extra options", self.extra)
        self.clip = QCheckBox("Watch clipboard: analyze links as soon as I copy them")
        self.clip.setChecked(settings.watch_clipboard)
        self.notify = QCheckBox("Show a notification when a download finishes")
        self.notify.setChecked(settings.notify_on_finish)
        for cb in (self.clip, self.notify):
            cb.toggled.connect(self._save)
            f.addRow("", cb)
        lay.addWidget(g)

        # Components --------------------------------------------------------------
        g = QGroupBox("Components")
        v = QVBoxLayout(g)
        self.ver = QLabel("Checking…")
        self.ver.setObjectName("muted")
        self.ver.setWordWrap(True)
        v.addWidget(self.ver)
        b = QHBoxLayout()
        self.upd_btn = QPushButton("Update yt-dlp")
        self.upd_btn.setObjectName("primary")
        self.upd_btn.setIcon(icon("refresh-cw", C["on_accent"], 16))
        self.upd_btn.setToolTip("Run this when a site suddenly stops working. YouTube changes often.")
        self.upd_btn.clicked.connect(self.update_ytdlp)
        setup = QPushButton("Install / repair components")
        setup.clicked.connect(self.request_setup.emit)
        folder = QPushButton("Open data folder")
        folder.setObjectName("ghost")
        folder.setIcon(icon("external-link", C["muted"], 16))
        setup.setIcon(icon("download", C["text"], 16))
        folder.clicked.connect(lambda: reveal(str(data_dir() / "settings.json")))
        b.addWidget(self.upd_btn)
        b.addWidget(setup)
        b.addStretch()
        b.addWidget(folder)
        v.addLayout(b)
        self.upd_out = QLabel("")
        self.upd_out.setObjectName("muted")
        self.upd_out.setWordWrap(True)
        v.addWidget(self.upd_out)
        lay.addWidget(g)
        lay.addStretch()

    # -------------------------------------------------------------------------
    def _number_combo(self, lo: int, hi: int, value: int) -> QComboBox:
        c = QComboBox()
        for n in range(lo, hi + 1):
            c.addItem(str(n), n)
        c.setCurrentIndex(max(0, c.findData(min(max(value, lo), hi))))
        c.setFixedWidth(90)
        c.currentIndexChanged.connect(self._save)
        return c

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh_versions()

    def refresh_versions(self):
        if self._vp and self._vp.isRunning():
            return
        self.ver.setText("Checking versions…")
        self._vp = VersionProbe(self.tools_getter(), self)
        self._vp.result.connect(self._show_versions)
        self._vp.start()

    def _show_versions(self, v: dict):
        t = self.tools_getter()
        lines = [
            f"yt-dlp: <b>{v['yt-dlp']}</b>",
            f"ffmpeg: <b>{v['ffmpeg']}</b>",
            f"deno (YouTube JS solver): <b>{v['deno']}</b>",
        ]
        self.ver.setText("<br>".join(lines) + f"<br><span style='font-size:8pt'>Tools folder: {bin_dir()}</span>")
        self.upd_btn.setEnabled(bool(t.ytdlp))

    def update_ytdlp(self):
        t = self.tools_getter()
        if not t.ytdlp:
            self.request_setup.emit()
            return
        self.upd_btn.setEnabled(False)
        self.upd_out.setText("Updating yt-dlp…")
        p = QProcess(self)
        env = p.processEnvironment()
        for k, val in child_env().items():
            env.insert(k, val)
        p.setProcessEnvironment(env)
        p.setProcessChannelMode(QProcess.MergedChannels)

        def done(code, _st):
            out = bytes(p.readAll()).decode("utf-8", "replace").strip().splitlines()
            msg = out[-1] if out else ("Done" if code == 0 else f"Failed (code {code})")
            if code != 0 and "pip" in " ".join(out).lower():
                msg = "This yt-dlp was installed with pip. Update it with: pip install -U yt-dlp"
            self.upd_out.setText(msg)
            self.upd_btn.setEnabled(True)
            p.deleteLater()
            self.refresh_versions()

        p.finished.connect(done)
        p.start(t.ytdlp, ["-U"])

    def _cookie_file_text(self) -> str:
        if self.s.cookies_browser == "file" and self.s.cookies_file:
            from pathlib import Path
            return Path(self.s.cookies_file).name
        return ""

    def _cookies_changed(self, *_):
        if self.cookies.currentData() == "file":
            path, _f = QFileDialog.getOpenFileName(self, "Choose cookies.txt", "", "Cookies (*.txt);;All files (*)")
            if not path:
                self.cookies.blockSignals(True)
                self.cookies.setCurrentIndex(max(0, self.cookies.findData(self.s.cookies_browser if self.s.cookies_browser != "file" else "")))
                self.cookies.blockSignals(False)
                return
            self.s.cookies_file = path
        self.s._cookies_broken = False  # give the new choice a fresh try
        self._save()
        self.cookie_file_lbl.setText(self._cookie_file_text())

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Default download folder", self.dir.text())
        if d:
            self.dir.setText(d)
            self._save()

    def _save(self, *_):
        s = self.s
        s.download_dir = self.dir.text().strip() or s.download_dir
        s.filename_template = self.tmpl.text().strip() or "%(title)s [%(id)s].%(ext)s"
        s.default_preset = self.preset.currentData()
        s.vcodec = self.vcodec.currentData()
        s.convert_codecs = self.convert.isChecked()
        s.encoder = self.encoder.currentData()
        s.mp3_quality = self.mp3q.currentData()
        s.max_concurrent = self.conc.currentData()
        s.files_concurrent = self.fconc.currentData()
        s.concurrent_fragments = self.frags.currentData()
        s.rate_limit = self.rate.text().strip()
        s.use_archive = self.archive.isChecked()
        s.sub_langs = self.langs.text().strip() or "en.*"
        s.auto_subs = self.auto.isChecked()
        s.embed_subs = self.esubs.isChecked()
        s.embed_thumbnail = self.ethumb.isChecked()
        s.embed_metadata = self.emeta.isChecked()
        s.embed_chapters = self.echap.isChecked()
        s.cookies_browser = self.cookies.currentData() or ""
        s.extra_args = self.extra.text().strip()
        s.watch_clipboard = self.clip.isChecked()
        s.notify_on_finish = self.notify.isChecked()
        s.save()
        self.changed.emit()
