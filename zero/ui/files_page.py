"""Files page: download any direct link (installers, archives, ISOs, PDFs...) with several connections."""
from __future__ import annotations

import os
import urllib.parse
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, Qt
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QTableView,
                               QVBoxLayout, QWidget)

from PySide6.QtWidgets import QStyledItemDelegate

from .. import filedl as F
from ..probe import human_size
from .queue_view import ProgressDelegate, fmt_eta, open_file, reveal
from .theme import C, icon, mono_font
from .widgets import EmptyState, TwoLineDelegate

COLS = ["FILE", "STATUS", "PROGRESS", "SPEED", "ETA", "SIZE"]
C_FILE, C_STATUS, C_PROG, C_SPEED, C_ETA, C_SIZE = range(6)
STATUS_TEXT = {F.QUEUED: "Waiting", F.CONNECTING: "Connecting…", F.PAUSED: "Paused", F.DONE: "Done",
               F.ERROR: "Failed", F.CANCELED: "Canceled"}
STATUS_COLOR = {F.QUEUED: C["muted"], F.CONNECTING: C["muted"], F.RUNNING: C["accent"], F.PAUSED: C["warn"],
                F.DONE: C["ok"], F.ERROR: C["bad"], F.CANCELED: C["muted"]}
_MONO = None


class FilesModel(QAbstractTableModel):
    def __init__(self, q: F.FileQueue):
        super().__init__()
        self.q = q
        global _MONO
        _MONO = mono_font(8.5)
        q.job_added.connect(self._reset)
        q.job_removed.connect(self._reset)
        q.job_changed.connect(self._changed)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.q.order)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, s, o, role=Qt.DisplayRole):
        return COLS[s] if o == Qt.Horizontal and role == Qt.DisplayRole else None

    def job_at(self, row) -> F.FileJob:
        return self.q.jobs[self.q.order[row]]

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        j = self.job_at(idx.row())
        c = idx.column()
        if role == Qt.UserRole:
            return j
        if role == Qt.UserRole + 1:
            if j.status == F.ERROR and j.error:
                return j.error
            host = urllib.parse.urlparse(j.url).netloc
            parts = [host]
            if j.sort and j.name:
                parts.append(F.category_of(j.name))
            if j.status == F.RUNNING and j.segments_active > 1:
                parts.append(f"{j.segments_active} connections")
            if j.status in F.ACTIVE | {F.PAUSED} and j.size and not j.resumable:
                parts.append("can't resume, restarts if paused")
            return "  ·  ".join(p for p in parts if p)
        if role == Qt.UserRole + 2:
            return j.status == F.ERROR and bool(j.error)
        if role == Qt.DisplayRole:
            if c == C_FILE:
                return j.display_name
            if c == C_STATUS:
                if j.status == F.RUNNING:
                    return "Downloading"
                return STATUS_TEXT.get(j.status, j.status)
            if c == C_SPEED:
                return f"{human_size(j.speed)}/s" if j.status == F.RUNNING and j.speed else ""
            if c == C_ETA:
                return fmt_eta(j.eta) if j.status == F.RUNNING else ""
            if c == C_SIZE:
                if j.status == F.DONE or (j.size and j.done >= j.size):
                    return human_size(j.size)
                if j.size:
                    return f"{human_size(j.done)} / {human_size(j.size)}"
                return human_size(j.done) if j.done else ""
        if role == Qt.ToolTipRole:
            lines = [j.url]
            if j.path:
                lines.append(j.path)
            if j.error:
                lines.append(j.error)
            return "\n".join(lines)
        if role == Qt.FontRole and c in (C_SPEED, C_ETA, C_SIZE):
            return _MONO
        if role == Qt.ForegroundRole:
            if c == C_STATUS:
                return QColor(STATUS_COLOR.get(j.status, C["text"]))
            if c in (C_SPEED, C_ETA, C_SIZE):
                return QColor(C["muted"])
        return None

    def _reset(self, *_):
        self.beginResetModel()
        self.endResetModel()

    def _changed(self, j):
        if j.id in self.q.order:
            r = self.q.order.index(j.id)
            self.dataChanged.emit(self.index(r, 0), self.index(r, len(COLS) - 1))


class _FileProgressDelegate(ProgressDelegate):
    def paint(self, painter, option, index):
        j = index.data(Qt.UserRole)
        if j.status in F.ACTIVE and not j.size:
            QStyledItemDelegate.paint(self, painter, option, index)  # background/selection only
            painter.save()
            painter.setPen(QColor(C["faint"]))
            painter.drawText(option.rect.adjusted(10, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, "size unknown")
            painter.restore()
            return

        class _Shim:
            pass
        shim = _Shim()
        shim.percent = j.percent
        shim.status = {F.RUNNING: "downloading", F.CONNECTING: "downloading", F.DONE: "done", F.ERROR: "error",
                       F.PAUSED: "paused"}.get(j.status, "queued")
        self._paint_for(painter, option, index, shim)


class LinkBox(QPlainTextEdit):
    """Multi-line link input; Enter adds, Shift+Enter makes a new line."""

    def __init__(self, on_enter, parent=None):
        super().__init__(parent)
        self.on_enter = on_enter

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not e.modifiers() & Qt.ShiftModifier:
            self.on_enter()
            return
        super().keyPressEvent(e)


class FilesPage(QWidget):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.s = settings
        self.q = F.FileQueue(settings, self)

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 28)
        root.setSpacing(24)

        # add card -----------------------------------------------------------------------
        card = QFrame()
        card.setObjectName("card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(20, 20, 20, 20)
        cv.setSpacing(12)
        head = QHBoxLayout()
        t = QLabel("Files")
        t.setObjectName("h2")
        sub = QLabel("Installers, archives, ISOs, PDFs: any direct link. Video pages go to Video & audio.")
        sub.setObjectName("caption")
        head.addWidget(t)
        head.addSpacing(8)
        head.addWidget(sub, 1)
        cv.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.links = LinkBox(self.add_from_box)
        self.links.setPlaceholderText("Paste one or more links, one per line. Enter adds them, Shift+Enter starts a new line.")
        self.links.setAccessibleName("Links to download")
        self.links.setFixedHeight(84)
        self.links.setTabChangesFocus(True)
        self.links.textChanged.connect(self._update_add)
        row.addWidget(self.links, 1)
        col = QVBoxLayout()
        col.setSpacing(8)
        paste = QPushButton("Paste")
        paste.setIcon(icon("clipboard-paste", C["text"], 16))
        paste.setToolTip("Paste links from the clipboard")
        paste.clicked.connect(self._paste)
        col.addWidget(paste)
        col.addStretch()
        row.addLayout(col)
        cv.addLayout(row)

        opts = QHBoxLayout()
        opts.setSpacing(12)
        self.folder_btn = QPushButton()
        self.folder_btn.setIcon(icon("folder-open", C["muted"], 16))
        self.folder_btn.setToolTip("Where files are saved")
        self.folder_btn.setStyleSheet("text-align: left;")
        self.folder_btn.clicked.connect(self._choose_folder)
        opts.addWidget(self.folder_btn, 1)
        lab = QLabel("Connections")
        lab.setObjectName("fieldLabel")
        self.conns = QComboBox()
        for n in (1, 2, 4, 8, 16):
            self.conns.addItem(str(n), n)
        self.conns.setCurrentIndex(max(0, self.conns.findData(int(self.s.files_connections or 8))))
        self.conns.setToolTip("Parallel connections per file. More is faster on servers that allow it;\n"
                              "use 1 or 2 if a site refuses or limits downloads.")
        self.conns.setAccessibleName("Connections per file")
        lab.setBuddy(self.conns)
        self.conns.currentIndexChanged.connect(self._save_opts)
        opts.addWidget(lab)
        opts.addWidget(self.conns)
        self.sort = QCheckBox("Sort into folders by type")
        self.sort.setToolTip("Puts files into Programs, Compressed, Documents, Disk images, Video, Music,\n"
                             "Images or Other inside the chosen folder.")
        self.sort.setChecked(bool(self.s.files_sort))
        self.sort.toggled.connect(self._save_opts)
        opts.addWidget(self.sort)
        self.add_btn = QPushButton("Download")
        self.add_btn.setObjectName("primary")
        self.add_btn.setIcon(icon("download", C["on_accent"], 16))
        self.add_btn.setShortcut(QKeySequence("Ctrl+Return"))
        self.add_btn.setToolTip("Start downloading (Enter in the link box or Ctrl+Enter)")
        self.add_btn.clicked.connect(self.add_from_box)
        opts.addWidget(self.add_btn)
        cv.addLayout(opts)
        self.err = QLabel("")
        self.err.setObjectName("error")
        self.err.setWordWrap(True)
        self.err.hide()
        cv.addWidget(self.err)
        root.addWidget(card)

        # queue ------------------------------------------------------------------------------
        qv = QVBoxLayout()
        qv.setSpacing(12)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.summary = QLabel("Downloads")
        self.summary.setObjectName("h2")
        bar.addWidget(self.summary)
        bar.addStretch()
        self.bar_buttons = []
        for text, ic, slot, name in (("Pause all", "pause", self.q.pause_all, ""),
                                     ("Resume all", "play", self.q.resume_all, ""),
                                     ("Clear finished", "list-x", self.q.clear_finished, "ghost")):
            b = QPushButton(text)
            b.setIcon(icon(ic, C["muted"] if name else C["text"], 16))
            if name:
                b.setObjectName(name)
            b.clicked.connect(slot)
            bar.addWidget(b)
            self.bar_buttons.append(b)
        qv.addLayout(bar)

        self.model = FilesModel(self.q)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(C_FILE, TwoLineDelegate(self.table))
        self.table.setItemDelegateForColumn(C_PROG, _FileProgressDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(56)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(C_FILE, QHeaderView.Stretch)
        for c, w in ((C_STATUS, 150), (C_PROG, 150), (C_SPEED, 100), (C_ETA, 70), (C_SIZE, 170)):
            h.setSectionResizeMode(c, QHeaderView.Interactive)
            self.table.setColumnWidth(c, w)
        h.setHighlightSections(False)
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.doubleClicked.connect(self._double)
        qv.addWidget(self.table, 1)
        self.empty = EmptyState("file-down", "No file downloads yet",
                                "Paste a direct link above. Downloads continue after a pause, a crash or a restart.",
                                self.table.viewport())
        self.table.viewport().installEventFilter(self)
        root.addLayout(qv, 1)

        for sig in (self.q.job_added, self.q.job_changed, self.q.job_removed):
            sig.connect(self._update_summary)
        self._set_folder(self.s.files_dir if self.s.files_dir and os.path.isdir(self.s.files_dir) else "")
        self._update_add()
        self._update_summary()
        self.q.restore()

    # -- events -----------------------------------------------------------------------------
    def eventFilter(self, obj, ev):
        if obj is self.table.viewport() and ev.type() == QEvent.Resize:
            self.empty.resize(obj.size())
        return False

    # -- options ----------------------------------------------------------------------------
    def folder(self) -> str:
        return self.s.files_dir or self.s.download_dir

    def _set_folder(self, path: str):
        self.s.files_dir = path
        shown = self.folder()
        text = shown if len(shown) <= 48 else "…" + shown[-47:]
        self.folder_btn.setText(" " + text)
        self.folder_btn.setToolTip(f"Saving to {shown}. Click to change.")

    def _choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Save files to", self.folder())
        if d:
            self._set_folder(d)
            self.s.save()

    def _save_opts(self, *_):
        self.s.files_connections = self.conns.currentData()
        self.s.files_sort = self.sort.isChecked()
        self.s.save()

    # -- adding -------------------------------------------------------------------------------
    def _update_add(self):
        n = len(F.split_links(self.links.toPlainText()))
        self.add_btn.setText(f"Download {n} files" if n > 1 else "Download")
        self.add_btn.setEnabled(n > 0)
        if n:
            self.err.hide()

    def _paste(self):
        text = QGuiApplication.clipboard().text().strip()
        if not text:
            return
        cur = self.links.toPlainText().strip()
        self.links.setPlainText((cur + "\n" + text).strip() if cur else text)
        self.links.setFocus()

    def offer_text(self, text: str):
        """Clipboard watcher: put a file link in the box without starting it."""
        cur = self.links.toPlainText()
        if text not in cur:
            self.links.setPlainText((cur.strip() + "\n" + text).strip())

    def add_from_box(self):
        urls = F.split_links(self.links.toPlainText())
        if not urls:
            self.err.setText("No links found. Links start with http:// or https://")
            self.err.show()
            return
        folder = self.folder()
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.err.setText(f"Can't use the folder {folder}: {e.strerror}")
            self.err.show()
            return
        busy = {self.q.jobs[i].url for i in self.q.order if self.q.jobs[i].status not in F.FINISHED}
        added = 0
        for u in urls:
            if u in busy:
                continue
            self.q.add(u, folder, int(self.conns.currentData()), self.sort.isChecked())
            added += 1
        self.links.clear()
        if added < len(urls):
            self.err.setText(f"{len(urls) - added} link(s) skipped: already downloading.")
            self.err.show()

    def add_urls(self, urls: list[str]):
        for u in urls:
            self.q.add(u, self.folder(), int(self.conns.currentData()), self.sort.isChecked())

    # -- queue -------------------------------------------------------------------------------
    def _update_summary(self, *_):
        jobs = [self.q.jobs[i] for i in self.q.order]
        self.empty.setVisible(not jobs)
        for b in self.bar_buttons:
            b.setEnabled(bool(jobs))
        if not jobs:
            self.summary.setText("Downloads")
            return
        active = sum(j.status in F.ACTIVE for j in jobs)
        waiting = sum(j.status == F.QUEUED for j in jobs)
        done = sum(j.status == F.DONE for j in jobs)
        failed = sum(j.status == F.ERROR for j in jobs)
        speed = sum(j.speed for j in jobs if j.status == F.RUNNING)
        parts = [f"{active} active", f"{waiting} waiting", f"{done} done"]
        if failed:
            parts.append(f"{failed} failed")
        text = "Downloads  ·  " + "  ·  ".join(parts)
        if speed:
            text += f"  ·  {human_size(speed)}/s"
        self.summary.setText(text)

    def active_count(self) -> int:
        return sum(self.q.jobs[i].status in F.ACTIVE | {F.QUEUED} for i in self.q.order)

    def selected(self) -> list[F.FileJob]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.model.job_at(r) for r in rows if r < len(self.q.order)]

    def _double(self, idx):
        j = self.model.job_at(idx.row())
        if j.status == F.DONE:
            open_file(j.path)
        elif j.status == F.ERROR:
            QMessageBox.warning(self, "Download failed", f"{j.display_name}\n\n{j.error}\n\n{j.url}")

    def _menu(self, pos):
        jobs = self.selected()
        if not jobs:
            return
        one = jobs[0] if len(jobs) == 1 else None
        st = {j.status for j in jobs}
        m = QMenu(self)
        if st & (F.ACTIVE | {F.QUEUED}):
            m.addAction("Pause", lambda: [self.q.pause(j.id) for j in jobs])
        if st & {F.PAUSED}:
            m.addAction("Resume", lambda: [self.q.resume(j.id) for j in jobs])
        if st & {F.ERROR, F.CANCELED}:
            m.addAction("Retry", lambda: [self.q.retry(j.id) for j in jobs])
        if st & (F.ACTIVE | {F.QUEUED, F.PAUSED, F.ERROR}):
            m.addAction("Cancel and delete the partial file", lambda: [self.q.cancel(j.id) for j in jobs])
        m.addSeparator()
        if one and one.status == F.DONE:
            m.addAction("Open file", lambda: open_file(one.path))
            m.addAction("Show in folder", lambda: reveal(one.path))
        elif one:
            m.addAction("Open folder", lambda: reveal(str(Path(one.path).parent) if one.path else one.folder))
        m.addAction("Copy link" + ("s" if len(jobs) > 1 else ""),
                    lambda: QGuiApplication.clipboard().setText("\n".join(j.url for j in jobs)))
        m.addSeparator()
        m.addAction("Remove from list", lambda: [self.q.remove(j.id) for j in jobs])
        m.exec(self.table.viewport().mapToGlobal(pos))

    def shutdown(self):
        self.q.shutdown()
