"""Top panel: paste a link, inspect it, choose quality, send to the queue."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget)

from .. import presets
from ..jobs import Job
from ..estimate import codec_label, delta_text, estimate
from ..filedl import looks_like_file_url
from ..probe import ProbeResult, Prober, human_duration, human_size
from .theme import C, icon

THUMB_W, THUMB_H = 256, 144
BASE_ROLE = Qt.UserRole + 1  # undecorated item text


def _label(text="", name=None, wrap=False):
    l = QLabel(text)
    if name:
        l.setObjectName(name)
    l.setWordWrap(wrap)
    return l


def _looks_like_url(text: str) -> bool:
    t = text.strip()
    return t.startswith(("http://", "https://")) and " " not in t and len(t) < 2000


class DownloadPanel(QFrame):
    jobs_created = Signal(list)
    need_tools = Signal()
    as_file = Signal(str)  # a direct file link: hand it to the Files page

    def __init__(self, settings, tools_getter, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.settings = settings
        self.tools_getter = tools_getter
        self.result: ProbeResult | None = None
        self.prober = Prober(self)
        self.prober.succeeded.connect(self._probed)
        self.prober.failed.connect(self._probe_failed)
        self.prober.notice.connect(self._show_notice)
        self.net = QNetworkAccessManager(self)
        self._thumb_reply: QNetworkReply | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        # URL row ------------------------------------------------------
        row = QHBoxLayout()
        row.setSpacing(8)
        self.url = QLineEdit()
        self.url.setObjectName("url")
        self.url.setPlaceholderText("Paste a link from YouTube, Vimeo, TikTok, X, SoundCloud, Twitch…")
        self.url.setClearButtonEnabled(True)
        self.url.returnPressed.connect(self.analyze)
        paste = QPushButton("Paste")
        paste.setIcon(icon("clipboard-paste", C["text"], 16))
        paste.setToolTip("Paste from clipboard and analyze (Ctrl+Shift+V)")
        paste.setShortcut(QKeySequence("Ctrl+Shift+V"))
        paste.clicked.connect(self.paste_and_analyze)
        self.whole_pl = QCheckBox("Whole playlist")
        self.whole_pl.setChecked(True)
        self.whole_pl.setToolTip("For links that point to both a video and a playlist,\n"
                                 "load the whole playlist instead of only the video.")
        self.analyze_btn = QPushButton("Analyze")
        self.analyze_btn.setObjectName("primary")
        self.analyze_btn.setIcon(icon("search", C["on_accent"], 16))
        self.url.setAccessibleName("Video or playlist link")
        self.analyze_btn.clicked.connect(self.analyze)
        row.addWidget(self.url, 1)
        row.addWidget(paste)
        row.addWidget(self.whole_pl)
        row.addWidget(self.analyze_btn)
        root.addLayout(row)

        self.notice = _label("", "warn", wrap=True)
        self.notice.setVisible(False)
        root.addWidget(self.notice)

        # states -------------------------------------------------------
        self.stack = QWidget()
        self._stack_lay = QVBoxLayout(self.stack)
        self._stack_lay.setContentsMargins(0, 0, 0, 0)
        self._states: list[QWidget] = []
        self.stack.addWidget = self._add_state
        root.addWidget(self.stack)

        self.empty = _label("Paste a link and press Analyze to see qualities, subtitles and playlist items.",
                            "muted", wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setMinimumHeight(60)
        self.stack.addWidget(self.empty)

        loading = QWidget()
        ll = QHBoxLayout(loading)
        self.loading_lbl = _label("Reading link…", "muted")
        cancel = QPushButton("Cancel")
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self._cancel_probe)
        ll.addStretch()
        ll.addWidget(self.loading_lbl)
        ll.addWidget(cancel)
        ll.addStretch()
        loading.setMinimumHeight(60)
        self.stack.addWidget(loading)

        err_box = QWidget()
        ev = QVBoxLayout(err_box)
        ev.setContentsMargins(0, 8, 0, 8)
        ev.setSpacing(10)
        self.err = _label("", "error", wrap=True)
        self.err.setAlignment(Qt.AlignCenter)
        self.err.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ev.addWidget(self.err)
        self.as_file_btn = QPushButton("Download as a file instead")
        self.as_file_btn.setIcon(icon("file-down", C["text"], 16))
        self.as_file_btn.setToolTip("Send this link to Files, which downloads whatever the link points to")
        self.as_file_btn.clicked.connect(lambda: self.as_file.emit(self.url.text().strip()))
        ev.addWidget(self.as_file_btn, 0, Qt.AlignHCenter)
        err_box.setMinimumHeight(60)
        self.stack.addWidget(err_box)

        self.stack.addWidget(self._build_details())
        self._show(0)

    def _add_state(self, w: QWidget):
        self._states.append(w)
        self._stack_lay.addWidget(w)

    def _show(self, idx: int):
        """Show one state (empty / loading / error / details); the card shrinks to fit it."""
        for i, w in enumerate(self._states):
            w.setVisible(i == idx)

    # -- details view ---------------------------------------------------
    def _build_details(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 4, 0, 0)
        h.setSpacing(18)

        self.thumb = QLabel("No preview")
        self.thumb.setObjectName("thumb")
        self.thumb.setFixedSize(THUMB_W, THUMB_H)
        self.thumb.setAlignment(Qt.AlignCenter)
        h.addWidget(self.thumb, 0, Qt.AlignTop)

        mid = QVBoxLayout()
        mid.setSpacing(6)
        self.title = _label("", "title", wrap=True)
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.meta = _label("", "muted", wrap=True)
        pills = QHBoxLayout()
        self.pill_site = _label("", "pill")
        self.pill_kind = _label("", "pill")
        pills.addWidget(self.pill_site)
        pills.addWidget(self.pill_kind)
        pills.addStretch()
        mid.addWidget(self.title)
        mid.addWidget(self.meta)
        mid.addLayout(pills)
        mid.addSpacing(6)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.fmt = QComboBox()
        for key, label in presets.FORMATS:
            self.fmt.addItem(label, key)
        self.fmt.currentIndexChanged.connect(self._format_changed)
        self.fmt.setMinimumWidth(240)
        self.quality = QComboBox()
        self.quality.setMinimumWidth(220)
        self.quality.setMaxVisibleItems(18)
        self.quality.setMaximumWidth(420)
        self.quality.currentIndexChanged.connect(self._quality_changed)
        self.codec = QComboBox()
        self.codec.setMaximumWidth(420)
        for key, label in presets.VCODECS:
            self.codec.addItem(label, key)
        self.codec.setToolTip(
            "AV1: smallest files for the same quality. Windows needs the free 'AV1 Video Extension' or VLC.\n"
            "VP9: widely supported, a bit larger than AV1.\n"
            "H.264: plays on any TV, phone or old PC. Biggest files.\n"
            "H.265: smaller than H.264, plays on most TVs and phones. Windows needs 'HEVC Video Extensions'.\n"
            "Sizes and the % compared to Auto are shown next to each choice.\n"
            "'converts' = the site doesn't offer this codec, so ZeroLink re-encodes after downloading.")
        self.codec.currentIndexChanged.connect(self._refresh_sizes)
        self.codec_lbl = _label("Codec", "muted")
        self.subs = QCheckBox("Subtitles")
        self.subs.setToolTip("Languages are set in Settings")
        self.folder_btn = QPushButton()
        self.folder_btn.setIcon(icon("folder-open", C["muted"], 16))
        self.folder_btn.setToolTip("Change where files are saved")
        self.folder_btn.clicked.connect(self._choose_folder)
        self.download_dir = self.settings.download_dir
        self._update_folder_btn()

        grid.addWidget(_label("Format", "muted"), 0, 0)
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(self.fmt)
        fmt_row.addWidget(self.subs)
        fmt_row.addStretch()
        grid.addLayout(fmt_row, 0, 1)
        grid.addWidget(_label("Quality", "muted"), 1, 0)
        grid.addWidget(self.quality, 1, 1)
        grid.addWidget(self.codec_lbl, 2, 0)
        grid.addWidget(self.codec, 2, 1)
        grid.addWidget(_label("Save to", "muted"), 3, 0)
        grid.addWidget(self.folder_btn, 3, 1, Qt.AlignLeft)
        self.size_lbl = _label("", "muted", wrap=True)
        grid.addWidget(self.size_lbl, 4, 1)
        grid.setColumnStretch(1, 1)
        mid.addLayout(grid)
        mid.addStretch()

        btns = QHBoxLayout()
        btns.addStretch()
        self.download_btn = QPushButton("Download")
        self.download_btn.setObjectName("primary")
        self.download_btn.setIcon(icon("download", C["on_accent"], 16))
        self.download_btn.setShortcut(QKeySequence("Ctrl+Return"))
        self.download_btn.setToolTip("Add to the queue (Ctrl+Enter)")
        self.download_btn.clicked.connect(self._download)
        btns.addWidget(self.download_btn)
        mid.addLayout(btns)
        h.addLayout(mid, 1)

        # playlist column
        self.pl_box = QWidget()
        pl = QVBoxLayout(self.pl_box)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(6)
        top = QHBoxLayout()
        self.pl_all = QCheckBox("Select all")
        self.pl_all.setChecked(True)
        self.pl_all.clicked.connect(self._check_all)
        self.pl_count = _label("", "muted")
        top.addWidget(self.pl_all)
        top.addStretch()
        top.addWidget(self.pl_count)
        self.pl_list = QListWidget()
        self.pl_list.itemChanged.connect(self._update_download_btn)
        pl.addLayout(top)
        pl.addWidget(self.pl_list)
        self.pl_box.setFixedWidth(340)
        h.addWidget(self.pl_box)
        return w

    # -- actions ----------------------------------------------------------
    def paste_and_analyze(self):
        text = QGuiApplication.clipboard().text().strip()
        if text:
            self.url.setText(text)
            self.analyze()

    def offer_url(self, url: str):
        """Called by the clipboard watcher."""
        if url != self.url.text().strip() and not self.prober.busy():
            self.url.setText(url)
            self.analyze()

    def analyze(self):
        url = self.url.text().strip()
        if not url:
            return
        if not _looks_like_url(url):
            if "://" not in url and "." in url and " " not in url:
                url = "https://" + url
                self.url.setText(url)
            else:
                self._probe_failed("That doesn't look like a link.")
                return
        if looks_like_file_url(url):
            self.as_file.emit(url)
            self.url.clear()
            return
        tools = self.tools_getter()
        if not tools.ytdlp:
            self.need_tools.emit()
            return
        self.notice.setVisible(False)
        self.loading_lbl.setText("Reading link…")
        self._show(1)
        self.analyze_btn.setEnabled(False)
        self.prober.start(url, self.whole_pl.isChecked(), self.settings, tools)

    def _show_notice(self, text: str):
        self.notice.setText(text)
        self.notice.setVisible(True)

    def _cancel_probe(self):
        self.prober.cancel()
        self.analyze_btn.setEnabled(True)
        self._show(0)

    def _probe_failed(self, msg: str):
        self.analyze_btn.setEnabled(True)
        self.err.setText(msg)
        self.as_file_btn.setVisible(_looks_like_url(self.url.text().strip()))
        self._show(2)

    def _probed(self, res: ProbeResult):
        self.analyze_btn.setEnabled(True)
        self.result = res
        self.title.setText(res.title)
        meta = []
        if res.uploader:
            meta.append(res.uploader)
        if res.duration:
            meta.append(human_duration(res.duration))
        if res.view_count:
            meta.append(f"{res.view_count:,} views".replace(",", " "))
        self.meta.setText("  ·  ".join(meta))
        self.pill_site.setText(res.extractor or "Web")
        self.pill_kind.setText(f"Playlist · {len(res.entries)} items" if res.is_playlist else "Video")
        self.pill_site.setVisible(bool(res.extractor))

        # format / quality / codec
        st = self.settings
        start_fmt = st.default_preset if presets.is_audio(st.default_preset) else st.container
        self.fmt.blockSignals(True)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(start_fmt)))
        self.fmt.blockSignals(False)
        self.codec.setCurrentIndex(max(0, self.codec.findData(st.vcodec)))
        self.subs.setChecked(st.subs_enabled)
        self._format_changed()

        # playlist list
        self.pl_box.setVisible(res.is_playlist)
        self.pl_list.blockSignals(True)
        self.pl_list.clear()
        for e in res.entries:
            dur = human_duration(e.duration)
            it = QListWidgetItem(f"{e.index:>3}.  {e.title}" + (f"   ({dur})" if dur else ""))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            it.setData(Qt.UserRole, e)
            it.setToolTip(e.url)
            self.pl_list.addItem(it)
        self.pl_list.blockSignals(False)
        self._update_download_btn()

        self._load_thumb(res.thumbnail)
        self._show(3)

    def _format_changed(self, *_):
        """Refill the Quality list for the chosen format (resolutions vs. MP3 bitrates)."""
        fmt = self.fmt.currentData() or "mp4"
        res = self.result
        st = self.settings
        self.quality.blockSignals(True)
        self.quality.clear()
        if fmt == "audio_mp3":
            for key, label in presets.MP3_QUALITY:
                self.quality.addItem(label, key)
            want = st.mp3_quality
        elif presets.is_audio(fmt):
            self.quality.addItem("Best available (no re-encoding)", "best")
            want = "best"
        else:
            for key, label in presets.PRESETS:
                if presets.is_audio(key):
                    continue
                if (res and not res.is_playlist and key.isdigit() and res.max_height
                        and int(key) > res.max_height):
                    continue
                self.quality.addItem(label, key)
            if res and res.formats:
                self.quality.insertSeparator(self.quality.count())
                for f in res.formats:
                    if not f.is_audio_only:
                        self.quality.addItem(f.label, f"fmt:{f.format_id}:{1 if f.has_audio else 0}")
            want = st.default_preset
        i = self.quality.findData(want)
        self.quality.setCurrentIndex(i if i >= 0 else 0)
        self.quality.setEnabled(self.quality.count() > 1)
        for i in range(self.quality.count()):
            self.quality.setItemData(i, self.quality.itemText(i), BASE_ROLE)
        self.quality.blockSignals(False)
        self._quality_changed()

    def _quality_changed(self, *_):
        fmt = self.fmt.currentData() or "mp4"
        key = self.quality.currentData() or ""
        video = not presets.is_audio(fmt)
        # a specific stream already fixes the codec
        self.codec.setEnabled(video and not key.startswith("fmt:"))
        self.codec.setVisible(video)
        self.codec_lbl.setVisible(video)
        self._refresh_sizes()

    # -- size estimates -------------------------------------------------------
    def _est(self, preset, fmt=None, vcodec=None, aq=None):
        res = self.result
        fmt = fmt or (self.fmt.currentData() or "mp4")
        container = fmt if not presets.is_audio(fmt) else "mp4"
        return estimate(res.raw, preset, container, vcodec or (self.codec.currentData() or "auto"),
                        aq or "0", self.settings.convert_codecs)

    @staticmethod
    def _decorate(combo: QComboBox, i: int, extra: str):
        base = combo.itemData(i, BASE_ROLE)
        if base is None:
            base = combo.itemText(i)
            combo.setItemData(i, base, BASE_ROLE)
        combo.setItemText(i, f"{base}   ·   {extra}" if extra else base)

    def _refresh_sizes(self, *_):
        """Write an estimated size next to every format, quality and codec option."""
        res = self.result
        has_info = bool(res and not res.is_playlist and res.raw.get("formats"))
        fmt = self.fmt.currentData() or "mp4"
        qkey = self.quality.currentData() or "best"
        vcodec = self.codec.currentData() or "auto"
        audio = presets.is_audio(fmt)
        if not has_info:
            for combo in (self.fmt, self.codec):
                for i in range(combo.count()):
                    self._decorate(combo, i, "")
            self.size_lbl.setText("Sizes are shown when you analyze a single video."
                                  if res and res.is_playlist else "")
            return

        # Format list: each format at the current quality (or its own default)
        for i in range(self.fmt.count()):
            f = self.fmt.itemData(i)
            if presets.is_audio(f):
                e = self._est(f, f, "auto", self.settings.mp3_quality if f != fmt else qkey)
            else:
                e = self._est(qkey if not audio else "best", f, vcodec if not audio else "auto")
            self._decorate(self.fmt, i, f"~{human_size(e.size)}" if e.known else "")

        # Quality list
        for i in range(self.quality.count()):
            k = self.quality.itemData(i)
            if not k or k.startswith("fmt:"):
                continue  # specific streams already show their size
            if fmt == "audio_mp3":
                e = self._est("audio_mp3", fmt, "auto", k)
            elif audio:
                e = self._est(fmt, fmt)
            else:
                e = self._est(k, fmt, vcodec)
            extra = f"~{human_size(e.size)}" if e.known else ""
            if not audio and e.known and e.height and k.isdigit() and e.height < int(k):
                extra += f" (max {e.height}p)"
            if not audio and e.converts:
                extra += "  converts"
            self._decorate(self.quality, i, extra)

        # Codec list, with the difference to Auto
        if not audio:
            vq = qkey
            base = self._est(vq, fmt, "auto").size
            for i in range(self.codec.count()):
                c = self.codec.itemData(i)
                e = self._est(vq, fmt, c)
                if not e.known:
                    self._decorate(self.codec, i, "")
                    continue
                parts = [f"~{human_size(e.size)}"]
                d = delta_text(e.size, base) if c != "auto" else ""
                if d:
                    parts.append(f"({d} vs Auto)")
                if c != "auto" and not e.converts and e.source_codec and \
                        codec_label(e.source_codec).split()[0] != presets.VCODEC_SHORT.get(c, ""):
                    parts.append(f"not offered, gets {codec_label(e.source_codec)}")
                if e.converts:
                    parts.append("converts")
                if c == "auto" and e.source_codec:
                    parts.append(codec_label(e.source_codec))
                self._decorate(self.codec, i, "  ".join(parts))

        # Summary line
        if audio:
            e = self._est(fmt, fmt, "auto", qkey)
            self.size_lbl.setText(f"Estimated file size: <b>~{human_size(e.size)}</b>" if e.known else "")
            return
        e = self._est(qkey, fmt, vcodec)
        if not e.known:
            self.size_lbl.setText("")
            return
        base = self._est(qkey, fmt, "auto").size
        what = f"{e.height}p {codec_label(e.source_codec)}" if e.height else ""
        if e.converts:
            what += f", converted to {presets.VCODEC_SHORT[vcodec]} after download (rough estimate)"
        text = f"Estimated file size: <b>~{human_size(e.size)}</b>" + (f"  ·  {what}" if what else "")
        d = delta_text(e.size, base)
        if d and vcodec != "auto":
            color = C["warn"] if e.size > base else C["ok"]
            word = "bigger" if e.size > base else "smaller"
            text += f"<br><span style='color:{color}'>{d.lstrip('+−')} {word} than Auto</span>"
        self.size_lbl.setText(text)

    def _check_all(self, on: bool):
        self.pl_list.blockSignals(True)
        for i in range(self.pl_list.count()):
            self.pl_list.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.pl_list.blockSignals(False)
        self._update_download_btn()

    def _checked_entries(self):
        return [self.pl_list.item(i).data(Qt.UserRole) for i in range(self.pl_list.count())
                if self.pl_list.item(i).checkState() == Qt.Checked]

    def _update_download_btn(self, *_):
        if self.result and self.result.is_playlist:
            n = len(self._checked_entries())
            self.pl_count.setText(f"{n} / {self.pl_list.count()}")
            self.pl_all.setChecked(n == self.pl_list.count())
            self.download_btn.setText(f"Download {n} item{'s' if n != 1 else ''}")
            self.download_btn.setEnabled(n > 0)
        else:
            self.download_btn.setText("Download")
            self.download_btn.setEnabled(True)

    def _choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Save downloads to", self.download_dir)
        if d:
            self.download_dir = d
            self._update_folder_btn()

    def _update_folder_btn(self):
        p = Path(self.download_dir)
        text = str(p)
        if len(text) > 48:
            text = "…" + text[-46:]
        self.folder_btn.setText(" " + text)

    def _download(self):
        res = self.result
        if not res:
            return
        fmt = self.fmt.currentData() or "mp4"
        qkey = self.quality.currentData() or "best"
        qlabel = self.quality.currentData(BASE_ROLE) or self.quality.currentText()
        if fmt == "audio_mp3":
            preset, container, vcodec, aq = fmt, self.settings.container, "auto", qkey
            label = f"MP3 · {presets.MP3_QUALITY_SHORT.get(qkey, qkey)}"
        elif presets.is_audio(fmt):
            preset, container, vcodec, aq = fmt, self.settings.container, "auto", "0"
            label = "M4A" if fmt == "audio_m4a" else "Original audio"
        else:
            preset, container, aq = qkey, fmt, "0"
            vcodec = "auto" if qkey.startswith("fmt:") else (self.codec.currentData() or "auto")
            short = qlabel.split(" · ")[0] if qkey.startswith("fmt:") else qlabel.replace("Best available", "Best")
            label = " · ".join(x for x in (short, presets.VCODEC_SHORT.get(vcodec, ""), fmt.upper()) if x)
        common = dict(preset=preset, quality_label=label, container=container, vcodec=vcodec,
                      audio_quality=aq, subs=self.subs.isChecked(), download_dir=self.download_dir)
        if res.is_playlist:
            jobs = [Job(url=e.url, title=e.title, playlist_title=res.title, playlist_index=e.index, **common)
                    for e in self._checked_entries()]
        else:
            jobs = [Job(url=res.url, title=res.title, **common)]
        if not jobs:
            return
        # remember the user's last choice as the default
        st = self.settings
        if presets.is_audio(fmt):
            st.default_preset = fmt
            if fmt == "audio_mp3":
                st.mp3_quality = qkey
        else:
            st.container = fmt
            st.vcodec = vcodec if not qkey.startswith("fmt:") else st.vcodec
            if not qkey.startswith("fmt:"):
                st.default_preset = qkey
        self.settings.subs_enabled = self.subs.isChecked()
        self.settings.save()
        self.jobs_created.emit(jobs)
        self.url.clear()
        self.result = None
        self._show(0)
        self.empty.setText(f"Added {len(jobs)} download{'s' if len(jobs) != 1 else ''} to the queue. "
                           "Paste another link any time.")

    def _load_thumb(self, url: str):
        self.thumb.setPixmap(QPixmap())
        self.thumb.setText("No preview")
        if self._thumb_reply:
            self._thumb_reply.abort()
            self._thumb_reply = None
        if not url:
            return
        req = QNetworkRequest(QUrl(url))
        req.setAttribute(QNetworkRequest.RedirectPolicyAttribute,
                         QNetworkRequest.NoLessSafeRedirectPolicy)
        reply = self.net.get(req)
        self._thumb_reply = reply

        def done():
            if reply is not self._thumb_reply:
                reply.deleteLater()
                return
            self._thumb_reply = None
            if reply.error() == QNetworkReply.NoError:
                pm = QPixmap()
                if pm.loadFromData(bytes(reply.readAll())):
                    pm = pm.scaled(THUMB_W, THUMB_H, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                    x = max(0, (pm.width() - THUMB_W) // 2)
                    y = max(0, (pm.height() - THUMB_H) // 2)
                    self.thumb.setPixmap(pm.copy(x, y, THUMB_W, THUMB_H))
            reply.deleteLater()

        reply.finished.connect(done)
