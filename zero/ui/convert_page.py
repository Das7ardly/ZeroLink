"""Convert page: drop files, pick a format and size, convert in a queue."""
from __future__ import annotations

import os
import re
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
                               QScrollArea, QTableView, QVBoxLayout, QWidget)

from .. import convert_queue as Q
from .. import transcode as T
from ..probe import human_size
from .queue_view import ProgressDelegate, fmt_eta, open_file, reveal
from .theme import C, icon, mono_font
from .widgets import EmptyState, TwoLineDelegate

MEDIA_EXT = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".wmv", ".flv", ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts", ".mts",
    ".3gp", ".vob", ".ogv", ".mxf", ".divx", ".rm", ".rmvb", ".asf", ".f4v",
    ".mp3", ".m4a", ".aac", ".opus", ".ogg", ".oga", ".flac", ".wav", ".wma", ".alac", ".aiff", ".aif", ".ape",
    ".ac3", ".eac3", ".dts", ".mka", ".amr", ".wv", ".gif",
}
COLS = ["FILE", "STATUS", "PROGRESS", "SIZE"]
C_FILE, C_STATUS, C_PROG, C_SIZE = range(4)
STATUS_COLOR = {Q.READY: C["muted"], Q.WAITING: C["muted"], Q.PROBING: C["muted"], Q.RUNNING: C["accent"],
                Q.DONE: C["ok"], Q.FAILED: C["bad"], Q.CANCELED: C["muted"]}


def parse_time(text: str) -> float | None:
    """'90', '1:30', '1:02:03', '1:30.5' -> seconds; '' -> None."""
    t = text.strip().replace(",", ".")
    if not t:
        return None
    if not re.fullmatch(r"\d+(:\d{1,2}){0,2}(\.\d+)?", t):
        raise ValueError(t)
    secs = 0.0
    for part in t.split(":"):
        secs = secs * 60 + float(part)
    return secs


class ProbeThread(QThread):
    result = Signal(str, object)

    def __init__(self, jobs, ffprobe, parent=None):
        super().__init__(parent)
        self.jobs, self.ffprobe = jobs, ffprobe

    def run(self):
        for item_id, path in self.jobs:
            self.result.emit(item_id, T.probe(path, self.ffprobe))


_MONO = None


class ConvertModel(QAbstractTableModel):
    def __init__(self, q: Q.ConvertQueue, estimator):
        super().__init__()
        self.q = q
        self.estimator = estimator  # item -> (bytes, note)
        global _MONO
        _MONO = mono_font(8.5)
        q.item_added.connect(self._reset)
        q.item_removed.connect(self._reset)
        q.item_changed.connect(self._changed)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.q.order)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, s, o, role=Qt.DisplayRole):
        return COLS[s] if o == Qt.Horizontal and role == Qt.DisplayRole else None

    def item_at(self, row) -> Q.ConvertItem:
        return self.q.items[self.q.order[row]]

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        it = self.item_at(idx.row())
        c = idx.column()
        if role == Qt.UserRole:
            # ProgressDelegate expects .percent and .status
            return it
        if role == Qt.DisplayRole:
            if c == C_FILE:
                return it.name
            if c == C_STATUS:
                if it.status == Q.RUNNING:
                    return "Converting" + (f" · {fmt_eta(it.eta)}" if it.eta else "")
                return {Q.READY: "Ready", Q.WAITING: "Waiting", Q.PROBING: "Reading…", Q.DONE: "Done",
                        Q.FAILED: it.stage or "Failed", Q.CANCELED: "Canceled"}.get(it.status, it.status)
            if c == C_SIZE:
                before = human_size(it.size_before)
                if it.status == Q.DONE and it.size_after:
                    pct = (it.size_after - it.size_before) * 100 / it.size_before if it.size_before else 0
                    return f"{before} → {human_size(it.size_after)} ({pct:+.0f}%)"
                est, note = self.estimator(it)
                return f"{before} → ~{human_size(est)}" if est else before
        if role == Qt.ToolTipRole:
            if c == C_STATUS and it.error:
                return it.error
            if c == C_STATUS and it.status == Q.RUNNING:
                return it.stage
            if c in (C_FILE, C_STATUS):
                lines = [it.path] + [f"→ {o}" for o in it.outputs]
                if it.info and not it.info.error:
                    lines.append(it.info.summary())
                return "\n".join(lines)
        if role == Qt.UserRole + 2:  # second line is an error
            return it.status == Q.FAILED and bool(it.error)
        if role == Qt.UserRole + 1:  # second line under the file name
            if it.status == Q.FAILED and it.error:
                return it.error
            return it.info.summary() if it.info and not it.info.error else ""
        if role == Qt.FontRole and c == C_SIZE:
            return _MONO
        if role == Qt.ForegroundRole:
            if c == C_STATUS:
                return QColor(STATUS_COLOR.get(it.status, C["text"]))
            if c == C_SIZE:
                return QColor(C["muted"])
        return None

    def _reset(self, *_):
        self.beginResetModel()
        self.endResetModel()

    def _changed(self, it):
        if it.id in self.q.order:
            r = self.q.order.index(it.id)
            self.dataChanged.emit(self.index(r, 0), self.index(r, len(COLS) - 1))

    def refresh_all(self):
        if self.q.order:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.q.order) - 1, len(COLS) - 1))


class DropTable(QTableView):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.empty = EmptyState("file-video", "Drop video or audio files here",
                                "Or use Add files. MKV, MP4, WebM, MOV, AVI, FLAC, WAV, MP3 and 30 more.",
                                self.viewport())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.empty.resize(self.viewport().size())

    def set_empty(self, on: bool):
        self.empty.setVisible(on)

    def _drag_glow(self, on: bool):
        self.setStyleSheet(f"QTableView {{ border: 2px dashed {C['accent']}; }}" if on else "")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_glow(True)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._drag_glow(False)

    def dropEvent(self, e):
        self._drag_glow(False)
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            e.acceptProposedAction()


class ConvertPage(QWidget):
    def __init__(self, settings, tools_getter, parent=None):
        super().__init__(parent)
        self.s = settings
        self.tools_getter = tools_getter
        self.q = Q.ConvertQueue(tools_getter, self)
        self.q.item_changed.connect(self._item_changed)
        self.q.all_done.connect(self._update_buttons)
        self._probes: list[ProbeThread] = []
        self._opts_cache: T.Options | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 28)
        root.setSpacing(24)

        # left: files ---------------------------------------------------------
        left = QVBoxLayout()
        left.setSpacing(16)
        top = QHBoxLayout()
        t = QLabel("Convert")
        t.setObjectName("h1")
        top.addWidget(t)
        top.addStretch()
        add = QPushButton("Add files")
        add.setIcon(icon("file-plus", C["text"], 16))
        add.setShortcut(QKeySequence("Ctrl+O"))
        add.setToolTip("Add video or audio files (Ctrl+O)")
        add.clicked.connect(self._add_files)
        addf = QPushButton("Add folder")
        addf.setIcon(icon("folder-plus", C["text"], 16))
        addf.setToolTip("Add every media file in a folder and its subfolders")
        addf.clicked.connect(self._add_folder)
        clr = QPushButton("Clear finished")
        clr.setObjectName("ghost")
        clr.setIcon(icon("list-x", C["muted"], 16))
        clr.clicked.connect(self.q.clear_finished)
        for b in (add, addf, clr):
            top.addWidget(b)
        left.addLayout(top)

        self.model = ConvertModel(self.q, self._estimate_item)
        self.table = DropTable()
        self.table.setModel(self.model)
        self.table.files_dropped.connect(self.add_paths)
        self.table.setItemDelegateForColumn(C_PROG, _ConvertProgressDelegate(self.table))
        self.table.setItemDelegateForColumn(C_FILE, TwoLineDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(56)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideRight)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for col, w in ((C_STATUS, 104), (C_PROG, 104), (C_SIZE, 156)):
            h.setSectionResizeMode(col, QHeaderView.Interactive)
            self.table.setColumnWidth(col, w)
        h.setHighlightSections(False)
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.doubleClicked.connect(self._double)
        self.table.selectionModel().selectionChanged.connect(self._refresh_summary)
        left.addWidget(self.table, 1)
        self.q.item_added.connect(lambda *_: self.table.set_empty(False))
        self.q.item_removed.connect(lambda *_: self.table.set_empty(not self.q.order))
        root.addLayout(left, 1)

        # right: options -------------------------------------------------------
        card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(340)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(0, 16, 0, 16)
        cv.setSpacing(12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        f = QVBoxLayout(body)
        f.setContentsMargins(20, 0, 16, 0)
        f.setSpacing(12)
        scroll.setWidget(body)
        cv.addWidget(scroll, 1)

        def section(text):
            l = QLabel(text.upper())
            l.setObjectName("section")
            fnt = l.font()
            fnt.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
            l.setFont(fnt)
            f.addWidget(l)
            return l

        def field(label, widget):
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(6)
            if label:
                lab = QLabel(label)
                lab.setObjectName("fieldLabel")
                buddy = widget if isinstance(widget, QWidget) else widget.itemAt(0).widget()
                lab.setBuddy(buddy)
                v.addWidget(lab)
            if isinstance(widget, QWidget):
                v.addWidget(widget)
            else:
                v.addLayout(widget)
            f.addWidget(box)
            return box

        # format
        section("Format")
        self.target = QComboBox()
        last_kind = None
        for tg in T.TARGETS:
            if last_kind and tg.kind != last_kind:
                self.target.insertSeparator(self.target.count())
            self.target.addItem(tg.label, tg.key)
            last_kind = tg.kind
        self.target.setAccessibleName("Convert to")
        field("&Convert to", self.target)

        self.vcodec = QComboBox()
        for k, lbl in T.VCODECS:
            self.vcodec.addItem(lbl, k)
        self.vcodec.setToolTip("Auto keeps the original video when the new format can hold it\n"
                               "(a remux: seconds, no quality loss) and re-encodes only when needed.")
        self.vcodec.setAccessibleName("Video codec")
        self.vcodec_lbl = field("&Video codec", self.vcodec)

        # quality
        self.quality_sec = section("Size and quality")
        self.size = QComboBox()
        for k, lbl in T.QUALITIES:
            self.size.addItem(lbl, f"q:{k}")
        self.size.insertSeparator(self.size.count())
        for k, lbl, mb in T.SIZE_PRESETS:
            self.size.addItem(("Fit " + lbl) if mb else lbl, f"t:{mb}")
        self.size.setToolTip("Quality: the encoder decides the size.\n"
                             "Fit: ZeroLink encodes twice to land just under that size,\n"
                             "and lowers the resolution if the size is too small for it.")
        self.size.setAccessibleName("Size or quality")
        self.size_lbl = field("&Size", self.size)
        self.custom_mb = QLineEdit()
        self.custom_mb.setPlaceholderText("e.g. 50")
        self.custom_mb.setAccessibleName("Target size in megabytes")
        self.custom_mb_lbl = field("Target size (MB)", self.custom_mb)

        self.height = QComboBox()
        for hh, lbl in T.HEIGHTS:
            self.height.addItem(lbl, hh)
        self.height.setAccessibleName("Resolution")
        self.height_lbl = field("&Resolution", self.height)

        self.anim_w = QComboBox()
        for w, lbl in ((0, "By quality"), (320, "320 px"), (480, "480 px"), (640, "640 px"), (720, "720 px"), (1080, "1080 px")):
            self.anim_w.addItem(lbl, w)
        self.anim_fps = QComboBox()
        for fp, lbl in ((0, "By quality"), (10, "10 fps"), (15, "15 fps"), (20, "20 fps"), (25, "25 fps"), (30, "30 fps")):
            self.anim_fps.addItem(lbl, fp)
        anim_row = QHBoxLayout()
        anim_row.setSpacing(8)
        wcol, fcol = QVBoxLayout(), QVBoxLayout()
        for col, lab_text, w in ((wcol, "Width", self.anim_w), (fcol, "Frame rate", self.anim_fps)):
            col.setSpacing(6)
            lab = QLabel(lab_text)
            lab.setObjectName("fieldLabel")
            lab.setBuddy(w)
            w.setAccessibleName(lab_text)
            col.addWidget(lab)
            col.addWidget(w)
            anim_row.addLayout(col, 1)
        self.anim_w_lbl = field("", anim_row)
        self.anim_fps_lbl = self.anim_w_lbl

        self.abr = QComboBox()
        for k, lbl in T.AUDIO_KBPS:
            self.abr.addItem(lbl, k)
        self.abr.setAccessibleName("Audio bitrate")
        self.abr_lbl = field("&Audio bitrate", self.abr)

        # edit
        self.edit_sec = section("Edit")
        trim = QHBoxLayout()
        trim.setSpacing(8)
        self.t_from = QLineEdit()
        self.t_from.setPlaceholderText("Start  0:00")
        self.t_to = QLineEdit()
        self.t_to.setPlaceholderText("End  (full length)")
        self.t_from.setToolTip("Start time: 90, 1:30 or 1:02:03")
        self.t_to.setToolTip("End time: 90, 1:30 or 1:02:03. Empty = to the end")
        self.t_from.setAccessibleName("Trim start time")
        self.t_to.setAccessibleName("Trim end time")
        trim.addWidget(self.t_from)
        trim.addWidget(self.t_to)
        self.trim_lbl = field("&Trim", trim)

        self.norm = QCheckBox("Even loudness (EBU R128)")
        self.norm.setToolTip("Makes every file equally loud (-16 LUFS). Handy for series and playlists.")
        self.subs = QCheckBox("Keep subtitles")
        self.subs.setToolTip("MP4/MOV get text subtitles, MKV keeps all. AVI and TS can't hold them.")
        f.addWidget(self.norm)
        f.addWidget(self.subs)

        # output
        section("Save to")
        out_row = QHBoxLayout()
        out_row.setSpacing(6)
        self.out_btn = QPushButton()
        self.out_btn.setIcon(icon("folder-open", C["muted"], 16))
        self.out_btn.setStyleSheet("text-align: left;")
        self.out_btn.setToolTip("Choose a folder for the converted files")
        self.out_btn.clicked.connect(self._choose_out)
        self.out_reset = QPushButton()
        self.out_reset.setObjectName("icon")
        self.out_reset.setIcon(icon("x", C["muted"], 16))
        self.out_reset.setToolTip("Save next to the original again")
        self.out_reset.setAccessibleName("Save next to the original again")
        self.out_reset.clicked.connect(lambda: self._set_out(""))
        out_row.addWidget(self.out_btn, 1)
        out_row.addWidget(self.out_reset)
        f.addLayout(out_row)
        f.addStretch()

        foot = QVBoxLayout()
        foot.setContentsMargins(20, 0, 20, 0)
        foot.setSpacing(10)
        self.summary_box = QFrame()
        self.summary_box.setObjectName("summary")
        sb = QVBoxLayout(self.summary_box)
        sb.setContentsMargins(12, 10, 12, 10)
        self.summary = QLabel("")
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.RichText)
        sb.addWidget(self.summary)
        foot.addWidget(self.summary_box)
        self.err = QLabel("")
        self.err.setObjectName("error")
        self.err.setWordWrap(True)
        foot.addWidget(self.err)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.cancel_btn = QPushButton("Stop")
        self.cancel_btn.setObjectName("danger")
        self.cancel_btn.setIcon(icon("x", C["bad"], 16))
        self.cancel_btn.setToolTip("Stop the running conversion and clear the waiting ones")
        self.cancel_btn.clicked.connect(self.q.cancel_all)
        self.go = QPushButton("Convert")
        self.go.setObjectName("primary")
        self.go.setIcon(icon("play", C["on_accent"], 16))
        self.go.setShortcut(QKeySequence("Ctrl+Return"))
        self.go.setToolTip("Convert (Ctrl+Enter). With several rows selected, only those are converted.")
        self.go.clicked.connect(self._start)
        btns.addWidget(self.cancel_btn)
        btns.addStretch()
        btns.addWidget(self.go)
        foot.addLayout(btns)
        cv.addLayout(foot)
        root.addWidget(card)

        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for cb in body.findChildren(QComboBox):
            cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(5 if cb in (self.anim_w, self.anim_fps) else 12)
            cb.view().setMinimumWidth(300)
        self._load_options()
        for w in (self.target, self.vcodec, self.size, self.height, self.abr, self.anim_w, self.anim_fps):
            w.currentIndexChanged.connect(self._options_changed)
        for w in (self.custom_mb, self.t_from, self.t_to):
            w.textChanged.connect(self._options_changed)
        for w in (self.norm, self.subs):
            w.toggled.connect(self._options_changed)
        self._options_changed()
        self.table.set_empty(True)

    # -- helpers -------------------------------------------------------------------
    def _size_choice(self) -> tuple[str, str]:
        d = self.size.currentData() or "q:balanced"
        mode, _, val = d.partition(":")
        return mode, val

    def _set_out(self, path: str):
        self._out_dir = path
        if path:
            text = path if len(path) <= 32 else "…" + path[-31:]
        else:
            text = "Next to the original"
        self.out_btn.setText(" " + text)
        self.out_reset.setVisible(bool(path))
        self._options_changed()

    def _choose_out(self):
        d = QFileDialog.getExistingDirectory(self, "Save converted files to", self._out_dir or self.s.download_dir)
        if d:
            self._set_out(d)

    def _load_options(self):
        o = T.Options(**{k: v for k, v in (self.s.convert_options or {}).items() if k in T.Options.__dataclass_fields__})
        self.target.setCurrentIndex(max(0, self.target.findData(o.target)))
        self.vcodec.setCurrentIndex(max(0, self.vcodec.findData(o.vcodec)))
        key = f"t:{int(o.target_mb) if o.target_mb in (10, 16, 25, 200) else 0}" if o.size_mode == "target" else f"q:{o.quality}"
        i = self.size.findData(key)
        self.size.setCurrentIndex(i if i >= 0 else 1)
        if o.size_mode == "target" and o.target_mb not in (10, 16, 25, 200):
            self.custom_mb.setText(f"{o.target_mb:g}")
        self.height.setCurrentIndex(max(0, self.height.findData(o.height)))
        self.abr.setCurrentIndex(max(0, self.abr.findData(o.audio_kbps)))
        self.anim_w.setCurrentIndex(max(0, self.anim_w.findData(o.anim_width)))
        self.anim_fps.setCurrentIndex(max(0, self.anim_fps.findData(o.anim_fps)))
        self.norm.setChecked(o.normalize)
        self.subs.setChecked(o.keep_subs)
        self._set_out(o.out_dir if o.out_dir and os.path.isdir(o.out_dir) else "")

    def options(self) -> T.Options:
        """Current UI choices; raises ValueError with a message when an input is invalid."""
        mode, val = self._size_choice()
        o = T.Options(target=self.target.currentData(), vcodec=self.vcodec.currentData(),
                      height=self.height.currentData(), audio_kbps=self.abr.currentData(),
                      normalize=self.norm.isChecked(), keep_subs=self.subs.isChecked(),
                      out_dir=self._out_dir, encoder=self.s.encoder,
                      anim_width=self.anim_w.currentData(), anim_fps=self.anim_fps.currentData())
        if mode == "q":
            o.quality = val
        else:
            o.size_mode = "target"
            if float(val):
                o.target_mb = float(val)
            else:
                try:
                    o.target_mb = float(self.custom_mb.text().replace(",", ".").strip())
                except ValueError:
                    raise ValueError("Type the target size in MB, e.g. 50.")
                if o.target_mb <= 0:
                    raise ValueError("The target size must be more than 0 MB.")
        try:
            o.trim_start = parse_time(self.t_from.text())
            o.trim_end = parse_time(self.t_to.text())
        except ValueError:
            raise ValueError("Trim times look like 90, 1:30 or 1:02:03.")
        if o.trim_start and o.trim_end and o.trim_end <= o.trim_start:
            raise ValueError("The trim end must be after the start.")
        return o

    def _options_changed(self, *_):
        kind = T.TARGET[self.target.currentData()].kind
        video, audio, anim = kind == "video", kind == "audio", kind == "anim"
        mode, val = self._size_choice()
        for w in (self.vcodec, self.vcodec_lbl, self.height, self.height_lbl, self.subs):
            w.setVisible(video)
        for w in (self.size, self.size_lbl):
            w.setVisible(video or anim)
        # target sizes only make sense for video
        model = self.size.model()
        for i in range(self.size.count()):
            d = self.size.itemData(i)
            if d and d.startswith("t:"):
                model.item(i).setEnabled(video)
        if anim and mode == "t":
            self.size.setCurrentIndex(self.size.findData("q:balanced"))
        custom = (video and mode == "t" and not float(val or 0))
        self.custom_mb.setVisible(custom)
        self.custom_mb_lbl.setVisible(custom)
        for w in (self.anim_w, self.anim_w_lbl, self.anim_fps, self.anim_fps_lbl):
            w.setVisible(anim)
        lossless = self.target.currentData() in ("flac", "alac", "wav")
        for w in (self.abr, self.abr_lbl):
            w.setVisible((video or audio) and not lossless)
        for w in (self.trim_lbl, self.t_from, self.t_to):
            w.setVisible(kind != "subs")
        self.norm.setVisible(video or audio)
        self.quality_sec.setVisible(kind != "subs")
        self.edit_sec.setVisible(kind != "subs")
        try:
            o = self.options()
            self._opts_cache = o
            self.err.setText("")
            data = asdict(o)
            data.pop("encoder", None)
            self.s.convert_options = data
            self.s.save()
        except ValueError as e:
            self._opts_cache = None
            self.err.setText(str(e))
        self.model.refresh_all()
        self._refresh_summary()
        self._update_buttons()

    def _estimate_item(self, it: Q.ConvertItem):
        if not it.info or it.info.error or not self._opts_cache:
            return 0, ""
        o = it.options if it.status in (Q.RUNNING, Q.WAITING) and it.options else self._opts_cache
        return T.estimate(it.info, o)

    def _selected(self) -> list[Q.ConvertItem]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.model.item_at(r) for r in rows if r < len(self.q.order)]

    def _refresh_summary(self, *_):
        o = self._opts_cache
        items = [self.q.items[i] for i in self.q.order if self.q.items[i].info and not self.q.items[i].info.error]
        if not o or not items:
            self.summary.setText("")
            return
        sel = [i for i in self._selected() if i.info and not i.info.error] or items[:1]
        lines = []
        try:
            plan = T.build_plan(sel[0].info, o, "")
            lines.append(f"<b>{sel[0].name}</b>: {plan.description}")
        except T.PlanError as e:
            lines.append(f"<b>{sel[0].name}</b>: <span style='color:{C['bad']}'>{e}</span>")
        todo = [i for i in items if i.status in (Q.READY, Q.FAILED, Q.CANCELED)]
        total_before = sum(i.size_before for i in todo)
        total_after = sum(T.estimate(i.info, o)[0] for i in todo)
        if todo and total_after:
            lines.append(f"{len(todo)} file{'s' if len(todo) != 1 else ''}: {human_size(total_before)} → about {human_size(total_after)}")
        self.summary.setText("<br>".join(lines))

    def _update_buttons(self, *_):
        todo = [i for i in self.q.order if self.q.items[i].status in (Q.READY, Q.FAILED, Q.CANCELED)]
        n = len(todo)
        self.go.setText(f"Convert {n} file{'s' if n != 1 else ''}" if n else "Convert")
        self.go.setEnabled(n > 0 and self._opts_cache is not None)
        self.cancel_btn.setVisible(self.q.busy() or any(self.q.items[i].status == Q.WAITING for i in self.q.order))

    def _item_changed(self, it):
        self._update_buttons()
        if it.status in (Q.READY, Q.DONE, Q.FAILED):
            self._refresh_summary()

    # -- adding files -------------------------------------------------------------
    def add_paths(self, paths: list[str]):
        files = []
        for p in paths:
            if os.path.isdir(p):
                for root, _d, names in os.walk(p):
                    files += [os.path.join(root, n) for n in sorted(names) if Path(n).suffix.lower() in MEDIA_EXT]
            elif os.path.isfile(p):
                files.append(p)
        tools = self.tools_getter()
        if not tools.ffprobe:
            QMessageBox.information(self, "ffmpeg needed", "Install the components first: Settings → Install / repair components.")
            return
        jobs = []
        for p in files:
            it = self.q.add(p)
            if it:
                jobs.append((it.id, it.path))
        if jobs:
            th = ProbeThread(jobs, tools.ffprobe, self)
            th.result.connect(self.q.set_info)
            th.finished.connect(lambda th=th: self._probes.remove(th) if th in self._probes else None)
            self._probes.append(th)
            th.start()

    def _add_files(self):
        exts = " ".join(f"*{e}" for e in sorted(MEDIA_EXT))
        paths, _ = QFileDialog.getOpenFileNames(self, "Add files to convert", self.s.download_dir,
                                                f"Media files ({exts});;All files (*)")
        self.add_paths(paths)

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Add every media file in a folder", self.s.download_dir)
        if d:
            self.add_paths([d])

    # -- running -------------------------------------------------------------------
    def _start(self):
        try:
            o = self.options()
        except ValueError as e:
            self.err.setText(str(e))
            return
        sel = [i.id for i in self._selected() if i.status in (Q.READY, Q.FAILED, Q.CANCELED, Q.DONE)]
        self.q.start(o, only=sel if len(sel) > 1 else None)
        self._update_buttons()

    def busy(self) -> bool:
        return self.q.busy()

    def shutdown(self):
        self.q.cancel_all()

    # -- table actions ------------------------------------------------------------
    def _double(self, idx):
        it = self.model.item_at(idx.row())
        if it.status == Q.DONE and it.outputs:
            reveal(it.outputs[0])
        elif it.status == Q.FAILED:
            self._show_error(it)

    def _show_error(self, it):
        box = QMessageBox(self)
        box.setWindowTitle("Conversion failed")
        box.setText(it.error or "Unknown error")
        if it.log:
            box.setDetailedText(it.log)
        box.exec()

    def _menu(self, pos):
        items = self._selected()
        if not items:
            return
        one = items[0] if len(items) == 1 else None
        m = QMenu(self)
        m.addAction("Convert selected with current settings", lambda: self.q.start(self._opts_cache, only=[i.id for i in items])
                    if self._opts_cache else None)
        if any(i.status in (Q.RUNNING, Q.WAITING) for i in items):
            m.addAction("Cancel", lambda: [self.q.cancel(i.id) for i in items])
        m.addSeparator()
        if one and one.outputs:
            m.addAction("Open result", lambda: open_file(one.outputs[0]))
            m.addAction("Show result in folder", lambda: reveal(one.outputs[0]))
        if one:
            m.addAction("Show original in folder", lambda: reveal(one.path))
        if one and one.status == Q.FAILED:
            m.addAction("Show error details", lambda: self._show_error(one))
        m.addSeparator()
        m.addAction("Remove from list", lambda: [self.q.remove(i.id) for i in items])
        m.exec(self.table.viewport().mapToGlobal(pos))


class _ConvertProgressDelegate(ProgressDelegate):
    """Reuse the download progress bar look for convert items."""
    def paint(self, painter, option, index):
        it = index.data(Qt.UserRole)

        class _Shim:
            pass
        shim = _Shim()
        shim.percent = it.percent
        shim.status = {Q.RUNNING: "downloading", Q.DONE: "done", Q.FAILED: "error"}.get(it.status, "queued")
        self._paint_for(painter, option, index, shim)
