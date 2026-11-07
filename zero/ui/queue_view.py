"""Queue table: model, progress delegate and the page widget with actions."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QStyle, QStyleOptionViewItem, QHBoxLayout, QHeaderView, QLabel, QMenu,
                               QMessageBox, QPushButton, QStyledItemDelegate, QTableView,
                               QVBoxLayout, QWidget)

from .. import jobs as J
from ..probe import human_size
from .theme import C, icon, mono_font
from .widgets import EmptyState

_MONO = None

COLS = ["Title", "Quality", "Status", "Progress", "Speed", "ETA", "Size"]
STATUS_COLOR = {
    J.QUEUED: C["muted"], J.RUNNING: C["accent"], J.PROCESSING: C["warn"], J.PAUSED: C["muted"],
    J.DONE: C["ok"], J.SKIPPED: C["ok"], J.ERROR: C["bad"], J.CANCELED: C["muted"],
}


def fmt_eta(eta) -> str:
    if eta is None:
        return ""
    eta = int(eta)
    h, rem = divmod(eta, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def reveal(path: str):
    """Show a file selected in Explorer (or open its folder elsewhere)."""
    p = Path(path)
    if sys.platform == "win32" and p.exists():
        subprocess.Popen(["explorer", "/select,", str(p)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent if p.is_file() else p)))


def open_file(path: str):
    if path and Path(path).exists():
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


class QueueModel(QAbstractTableModel):
    def __init__(self, manager: J.QueueManager):
        super().__init__()
        self.m = manager
        global _MONO
        _MONO = mono_font(9)
        manager.job_added.connect(self._added)
        manager.job_changed.connect(self._changed)
        manager.job_removed.connect(self._removed)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.m.order)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orient, role=Qt.DisplayRole):
        if orient == Qt.Horizontal and role == Qt.DisplayRole:
            return COLS[section].upper()
        return None

    def job_at(self, row: int) -> J.Job:
        return self.m.jobs[self.m.order[row]]

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        job = self.job_at(idx.row())
        col = idx.column()
        if role == Qt.UserRole:
            return job
        if role == Qt.DisplayRole:
            if col == 0:
                t = job.title or job.url
                return f"{job.playlist_index:03d} · {t}" if job.playlist_index else t
            if col == 1:
                return job.quality_label
            if col == 2:
                return job.stage or job.status.capitalize()
            if col == 3:
                return f"{job.percent:.0f}%"
            if col == 4:
                return (human_size(job.speed) + "/s") if job.speed else ""
            if col == 5:
                return fmt_eta(job.eta)
            if col == 6:
                return human_size(job.total_bytes)
        if role == Qt.ToolTipRole:
            if col == 2 and job.error:
                return job.error
            if col == 0:
                return f"{job.title}\n{job.url}" + (f"\n{job.filepath}" if job.filepath else "")
        if role == Qt.FontRole and col in (4, 5, 6):
            return _MONO
        if role == Qt.ForegroundRole and col == 2:
            return QColor(STATUS_COLOR.get(job.status, C["text"]))
        if role == Qt.ForegroundRole and col in (4, 5, 6, 1):
            return QColor(C["muted"])
        return None

    def _added(self, job):
        row = self.m.order.index(job.id)
        self.beginInsertRows(QModelIndex(), row, row)
        self.endInsertRows()

    def _changed(self, job):
        if job.id in self.m.order:
            row = self.m.order.index(job.id)
            self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLS) - 1))

    def _removed(self, _job_id):
        # Row already gone from manager.order; a reset is simplest and cheap for queue sizes.
        self.beginResetModel()
        self.endResetModel()


class ProgressDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option, index):
        self._paint_for(painter, option, index, index.data(Qt.UserRole))

    def _paint_for(self, painter: QPainter, option, index, job):
        """job needs .percent and .status (download status names)."""
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        w = opt.widget
        (w.style() if w else QApplication.style()).drawControl(QStyle.CE_ItemViewItem, opt, painter, w)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(10, 0, -8, 0)
        bar_h = 6
        bar = QRectF(r.x(), r.center().y() - bar_h / 2, r.width() - 40, bar_h)
        path = QPainterPath()
        path.addRoundedRect(bar, 3, 3)
        painter.fillPath(path, QColor(C["track"]))
        pct = max(0.0, min(100.0, job.percent))
        if pct > 0:
            fill = QRectF(bar.x(), bar.y(), bar.width() * pct / 100.0, bar_h)
            fp = QPainterPath()
            fp.addRoundedRect(fill, 3, 3)
            painter.fillPath(fp, QColor(STATUS_COLOR.get(job.status, C["accent"])))
        painter.setPen(QColor(C["muted"]))
        painter.setFont(mono_font(8.5))
        painter.drawText(QRectF(bar.right() + 6, r.y(), 40, r.height()),
                         Qt.AlignVCenter | Qt.AlignLeft, f"{pct:.0f}%")
        painter.restore()


class QueuePage(QWidget):
    convert_requested = Signal(list)
    def __init__(self, manager: J.QueueManager, parent=None):
        super().__init__(parent)
        self.m = manager
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.summary = QLabel("Queue")
        self.summary.setObjectName("h2")
        bar.addWidget(self.summary)
        bar.addStretch()
        self.bar_buttons = []
        for text, ic, slot, name in (("Pause all", "pause", manager.pause_all, ""),
                                     ("Resume all", "play", manager.resume_all, ""),
                                     ("Clear finished", "list-x", manager.clear_finished, "ghost")):
            b = QPushButton(text)
            b.setIcon(icon(ic, C["muted"] if name else C["text"], 16))
            if name:
                b.setObjectName(name)
            b.clicked.connect(slot)
            bar.addWidget(b)
            self.bar_buttons.append(b)
        lay.addLayout(bar)

        self.model = QueueModel(manager)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(3, ProgressDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideRight)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for col, w in ((1, 150), (2, 170), (3, 170), (4, 95), (5, 70), (6, 85)):
            h.setSectionResizeMode(col, QHeaderView.Interactive)
            self.table.setColumnWidth(col, w)
        h.setHighlightSections(False)
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.doubleClicked.connect(self._double)
        lay.addWidget(self.table)
        self.empty = EmptyState("download", "Nothing downloading yet",
                                "Paste a link above and press Analyze. Copied links are picked up automatically.",
                                self.table.viewport())
        self.table.viewport().installEventFilter(self)

        for sig in (manager.job_added, manager.job_changed, manager.job_removed):
            sig.connect(self._update_summary)
        self._update_summary()

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if obj is self.table.viewport() and ev.type() == QEvent.Resize:
            self.empty.resize(obj.size())
        return False

    def _update_summary(self, *_):
        jobs = [self.m.jobs[j] for j in self.m.order]
        self.empty.setVisible(not jobs)
        for b in self.bar_buttons:
            b.setEnabled(bool(jobs))
        if not jobs:
            self.summary.setText("Queue")
            return
        active = sum(j.status in J.ACTIVE for j in jobs)
        waiting = sum(j.status == J.QUEUED for j in jobs)
        done = sum(j.status in (J.DONE, J.SKIPPED) for j in jobs)
        failed = sum(j.status == J.ERROR for j in jobs)
        parts = [f"{active} active", f"{waiting} waiting", f"{done} done"]
        if failed:
            parts.append(f"{failed} failed")
        self.summary.setText("Queue  ·  " + "  ·  ".join(parts))

    def selected_jobs(self) -> list[J.Job]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.model.job_at(r) for r in rows if r < len(self.m.order)]

    def _double(self, idx):
        job = self.model.job_at(idx.row())
        if job.status == J.DONE:
            open_file(job.filepath)
        elif job.status == J.ERROR:
            self._show_error(job)

    def _show_error(self, job):
        box = QMessageBox(self)
        box.setWindowTitle("Download failed")
        box.setText(job.error or "Unknown error")
        log = self.m.log_for(job.id)
        if log:
            box.setDetailedText(log)
        box.exec()

    def _menu(self, pos):
        jobs = self.selected_jobs()
        if not jobs:
            return
        one = jobs[0] if len(jobs) == 1 else None
        menu = QMenu(self)
        st = {j.status for j in jobs}
        if st & (J.ACTIVE | {J.QUEUED}):
            menu.addAction("Pause", lambda: [self.m.pause(j.id) for j in jobs])
        if J.PAUSED in st:
            menu.addAction("Resume", lambda: [self.m.resume(j.id) for j in jobs])
        if st & {J.ERROR, J.CANCELED}:
            menu.addAction("Retry", lambda: [self.m.retry(j.id) for j in jobs])
        if st & {J.SKIPPED, J.DONE}:
            menu.addAction("Download again (ignore archive)",
                           lambda: [self.m.retry(j.id, force=True) for j in jobs])
        if st & (J.ACTIVE | {J.QUEUED, J.PAUSED}):
            menu.addAction("Cancel", lambda: [self.m.cancel(j.id) for j in jobs])
        menu.addSeparator()
        done = [j.filepath for j in jobs if j.status == J.DONE and j.filepath]
        if done:
            menu.addAction("Convert / compress…", lambda: self.convert_requested.emit(done))
        if one and one.filepath:
            menu.addAction("Open file", lambda: open_file(one.filepath))
            menu.addAction("Show in folder", lambda: reveal(one.filepath))
        elif one:
            menu.addAction("Open download folder",
                           lambda: reveal(one.download_dir or self.m.settings.download_dir))
        if one and one.status == J.ERROR:
            menu.addAction("Show error details", lambda: self._show_error(one))
        menu.addAction("Copy link" + ("s" if len(jobs) > 1 else ""),
                       lambda: QGuiApplication.clipboard().setText("\n".join(j.url for j in jobs)))
        menu.addSeparator()
        menu.addAction("Remove from list", lambda: [self.m.remove(j.id) for j in jobs])
        menu.exec(self.table.viewport().mapToGlobal(pos))
