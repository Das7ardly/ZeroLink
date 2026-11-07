"""History page: everything downloaded, searchable, with re-download."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..history import History
from ..probe import human_size
from .queue_view import open_file, reveal
from .theme import C, icon, mono_font
from .widgets import EmptyState

COLS = ["DATE", "TITLE", "QUALITY", "SIZE", "STATUS"]


class HistoryPage(QWidget):
    redownload = Signal(dict)
    convert_requested = Signal(list)

    def __init__(self, history: History, parent=None):
        super().__init__(parent)
        self.h = history
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 28, 32, 28)
        lay.setSpacing(16)

        top = QHBoxLayout()
        title = QLabel("History")
        title.setObjectName("h1")
        top.addWidget(title)
        top.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search titles and links…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(320)
        self.search.textChanged.connect(self.refresh)
        top.addWidget(self.search)
        lay.addLayout(top)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for col, w in ((0, 140), (2, 150), (3, 90), (4, 110)):
            self.table.setColumnWidth(col, w)
        hh.setHighlightSections(False)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.doubleClicked.connect(lambda *_: self._open())
        self.table.itemSelectionChanged.connect(self._sel_changed)
        lay.addWidget(self.table, 1)
        self.empty = EmptyState("history", "No downloads yet",
                                "Finished downloads appear here, so you can open, convert or download them again.",
                                self.table.viewport())
        self.table.viewport().installEventFilter(self)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.b_open = QPushButton("Open file")
        self.b_folder = QPushButton("Show in folder")
        self.b_again = QPushButton("Download again")
        self.b_conv = QPushButton("Convert…")
        self.b_conv.clicked.connect(self._convert)
        self.b_del = QPushButton("Remove entry")
        clear = QPushButton("Clear history")
        clear.setObjectName("ghost")
        for b, ic in ((self.b_open, "play"), (self.b_folder, "folder-open"), (self.b_again, "rotate-ccw"),
                      (self.b_conv, "repeat-2"), (self.b_del, "trash-2"), (clear, "list-x")):
            b.setIcon(icon(ic, C["muted"] if b is clear else C["text"], 16))
        self.search.setAccessibleName("Search history")
        self.b_open.clicked.connect(self._open)
        self.b_folder.clicked.connect(self._folder)
        self.b_again.clicked.connect(self._again)
        self.b_del.clicked.connect(self._delete)
        clear.clicked.connect(self._clear)
        for b in (self.b_open, self.b_folder, self.b_conv, self.b_again, self.b_del):
            btns.addWidget(b)
        btns.addStretch()
        btns.addWidget(clear)
        lay.addLayout(btns)
        self.refresh()

    def refresh(self, *_):
        rows = self.h.search(self.search.text().strip())
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            when = dt.datetime.fromtimestamp(row["finished_at"] or 0).strftime("%d.%m.%Y %H:%M")
            exists = bool(row["filepath"]) and Path(row["filepath"]).exists()
            status = {"done": "Saved" if exists else "File moved", "error": "Failed"}.get(row["status"], row["status"])
            vals = [when, row["title"] or row["url"], row["quality"] or "", human_size(row["size"]), status]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 3:
                    it.setFont(mono_font(8.5))
                if c == 0:
                    it.setData(Qt.UserRole, dict(row))
                if c == 1:
                    it.setToolTip(f"{row['title']}\n{row['url']}\n{row['filepath'] or ''}".strip())
                if c in (0, 2, 3):
                    it.setForeground(QColor(C["muted"]))
                if c == 4:
                    it.setForeground(QColor(C["ok"] if status == "Saved" else C["bad"] if status == "Failed" else C["muted"]))
                    if row["error"]:
                        it.setToolTip(row["error"])
                self.table.setItem(r, c, it)
        self._sel_changed()
        q = self.search.text().strip()
        self.empty.title.setText(f"Nothing matches “{q}”" if q else "No downloads yet")
        self.empty.text.setVisible(not q)
        self.empty.setVisible(not rows)

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if obj is self.table.viewport() and ev.type() == QEvent.Resize:
            self.empty.resize(obj.size())
        return False

    def _current(self) -> dict | None:
        r = self.table.currentRow()
        if r < 0 or not self.table.selectionModel().hasSelection():
            return None
        it = self.table.item(r, 0)
        return it.data(Qt.UserRole) if it else None

    def _sel_changed(self):
        row = self._current()
        has_file = bool(row and row["filepath"] and Path(row["filepath"]).exists())
        self.b_open.setEnabled(has_file)
        self.b_folder.setEnabled(has_file)
        self.b_conv.setEnabled(has_file)
        self.b_again.setEnabled(row is not None)
        self.b_del.setEnabled(row is not None)

    def _open(self):
        row = self._current()
        if row:
            open_file(row["filepath"])

    def _folder(self):
        row = self._current()
        if row and row["filepath"]:
            reveal(row["filepath"])

    def _convert(self):
        row = self._current()
        if row and row["filepath"]:
            self.convert_requested.emit([row["filepath"]])

    def _again(self):
        row = self._current()
        if row:
            self.redownload.emit(row)

    def _delete(self):
        row = self._current()
        if row:
            self.h.delete(row["id"])
            self.refresh()

    def _clear(self):
        if QMessageBox.question(self, "Clear history",
                                "Remove all history entries? Downloaded files are not deleted.") \
                == QMessageBox.Yes:
            self.h.clear()
            self.refresh()
