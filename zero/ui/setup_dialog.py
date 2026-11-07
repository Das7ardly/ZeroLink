"""First-run dialog that downloads yt-dlp, ffmpeg and deno."""
from __future__ import annotations

from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout)

from ..binaries import ToolInstaller
from ..paths import IS_WINDOWS, bin_dir
from ..probe import human_size

INFO = {
    "yt-dlp": "the download engine (~18 MB)",
    "ffmpeg": "merges video + audio, converts to MP3 (~150 MB)",
    "deno": "solves YouTube's JavaScript challenges (~45 MB)",
}


class SetupDialog(QDialog):
    def __init__(self, missing: list[str], parent=None, force_all=False):
        super().__init__(parent)
        self.setWindowTitle("Set up ZeroLink")
        self.setMinimumWidth(520)
        self.components = ["yt-dlp", "ffmpeg", "deno"] if force_all else missing
        self.worker: ToolInstaller | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        t = QLabel("Install components" if force_all else "A few components are needed")
        t.setObjectName("h2")
        lay.addWidget(t)
        if IS_WINDOWS:
            text = ("ZeroLink will download these official builds from GitHub into<br>"
                    f"<span style='color:#8b93a7'>{bin_dir()}</span>")
        else:
            text = ("On this system, install them yourself and make sure they are on PATH:<br>"
                    "<code>pip install -U yt-dlp</code>, plus ffmpeg and deno from your package manager.")
        intro = QLabel(text)
        intro.setWordWrap(True)
        lay.addWidget(intro)

        self.bars: dict[str, QProgressBar] = {}
        self.sizes: dict[str, QLabel] = {}
        for c in self.components:
            row = QHBoxLayout()
            name = QLabel(f"<b>{c}</b>  <span style='color:#8b93a7'>{INFO[c]}</span>")
            row.addWidget(name, 1)
            size = QLabel("")
            size.setObjectName("muted")
            row.addWidget(size)
            lay.addLayout(row)
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(0)
            bar.setFixedHeight(8)
            lay.addWidget(bar)
            self.bars[c], self.sizes[c] = bar, size

        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        btns = QHBoxLayout()
        btns.addStretch()
        self.cancel_btn = QPushButton("Later")
        self.cancel_btn.clicked.connect(self._cancel)
        self.go = QPushButton("Download and install")
        self.go.setObjectName("primary")
        self.go.clicked.connect(self._start)
        self.go.setEnabled(IS_WINDOWS and bool(self.components))
        btns.addWidget(self.cancel_btn)
        btns.addWidget(self.go)
        lay.addLayout(btns)

    def _start(self):
        self.go.setEnabled(False)
        self.cancel_btn.setText("Cancel")
        self.worker = ToolInstaller(self.components, self)
        self.worker.progress.connect(self._progress)
        self.worker.status.connect(self.status.setText)
        self.worker.done.connect(self._done)
        self.worker.start()

    def _progress(self, comp, done, total):
        bar = self.bars.get(comp)
        if not bar:
            return
        if total:
            bar.setValue(int(done * 1000 / total))
            self.sizes[comp].setText(f"{human_size(done)} / {human_size(total)}")
        else:
            bar.setRange(0, 0)
            self.sizes[comp].setText(human_size(done))

    def _done(self, ok, msg):
        self.worker = None
        if ok:
            for b in self.bars.values():
                b.setRange(0, 1000)
                b.setValue(1000)
            self.accept()
        else:
            self.status.setText(f"<span style='color:#f2555a'>Setup failed: {msg}</span>")
            self.go.setEnabled(True)
            self.go.setText("Try again")
            self.cancel_btn.setText("Close")

    def _cancel(self):
        if self.worker:
            self.worker.stop()
            self.worker.wait(5000)
        self.reject()

    def closeEvent(self, e):
        if self.worker:
            self.worker.stop()
            self.worker.wait(5000)
        super().closeEvent(e)
