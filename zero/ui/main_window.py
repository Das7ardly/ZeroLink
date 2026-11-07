"""Main window: sidebar navigation + pages, tray notifications, clipboard watcher."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QPushButton, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget)

from .. import __version__
from .. import jobs as J
from ..binaries import Tools
from ..history import History
from ..paths import IS_WINDOWS, resource_path
from ..settings import Settings
from .download_panel import DownloadPanel, _looks_like_url
from .convert_page import ConvertPage
from .files_page import FilesPage
from ..filedl import looks_like_file_url
from .history_page import HistoryPage
from .queue_view import QueuePage, reveal
from .settings_page import SettingsPage
from .setup_dialog import SetupDialog
from . import theme
from .theme import C


class NavButton(QPushButton):
    """Sidebar entry: Lucide icon, label, optional count badge. Checked = green bar + green icon."""

    def __init__(self, text: str, icon_name: str):
        super().__init__("  " + text)
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setIcon(theme.icon(icon_name, C["muted"], 18, on_color=C["accent"]))
        from PySide6.QtCore import QSize
        self.setIconSize(QSize(18, 18))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.addStretch()
        self.badge = QLabel("")
        self.badge.setObjectName("navBadge")
        self.badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.badge.setFixedHeight(20)
        self.badge.setMinimumWidth(20)
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.hide()
        lay.addWidget(self.badge, 0, Qt.AlignVCenter)
        self._name = text.replace("&&", "&")
        self.setAccessibleName(self._name)

    def set_badge(self, n: int):
        self.badge.setText(str(n))
        self.badge.setVisible(n > 0)
        self.setAccessibleName(self._name + (f", {n} active" if n else ""))


PAGE_DOWNLOADS, PAGE_FILES, PAGE_CONVERT, PAGE_HISTORY, PAGE_SETTINGS = range(5)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ZeroLink")
        self.resize(1180, 780)
        self.setMinimumSize(980, 640)
        icon_path = resource_path("assets/icon.png")
        self.app_icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
        self.setWindowIcon(self.app_icon)

        self.settings = Settings.load()
        self.tools = Tools.detect()
        self.history = History()
        self.manager = J.QueueManager(self.settings, lambda: self.tools, self)
        self.manager.job_finished.connect(self._job_finished)
        for sig in (self.manager.job_added, self.manager.job_changed, self.manager.job_removed):
            sig.connect(self._update_badge)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        # sidebar
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(216)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(0)
        brand = QLabel()
        brand.setObjectName("brand")
        wm_path = resource_path("assets/wordmark@2x.png")
        if wm_path.exists():
            pm = QPixmap(str(wm_path))
            pm.setDevicePixelRatio(2.0)
            brand.setPixmap(pm)
        else:
            brand.setText("ZeroLink")
        tag = QLabel("DOWNLOAD  ·  CONVERT")
        tag.setObjectName("tagline")
        f = tag.font()
        f.setLetterSpacing(QFont.AbsoluteSpacing, 2.5)
        tag.setFont(f)
        sv.addWidget(brand)
        sv.addWidget(tag)
        sv.addSpacing(12)
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        self.nav_buttons: list[NavButton] = []
        nav_items = (("Video && audio", "download", "Ctrl+1"), ("Files", "file-down", "Ctrl+2"),
                     ("Convert", "repeat-2", "Ctrl+3"), ("History", "history", "Ctrl+4"),
                     ("Settings", "settings", "Ctrl+5"))
        for i, (name, ic, key) in enumerate(nav_items):
            b = NavButton(name, ic)
            b.setToolTip(f"{name.replace('&&', '&')}  ({key})")
            self.nav.addButton(b, i)
            self.nav_buttons.append(b)
            sv.addWidget(b)
            QShortcut(QKeySequence(key), self, activated=b.click)
        sv.addStretch()
        self.foot = QLabel(f"v{__version__}  ·  yt-dlp + ffmpeg")
        self.foot.setObjectName("sideFoot")
        self.foot.setWordWrap(True)
        sv.addWidget(self.foot)
        h.addWidget(side)

        # pages
        self.pages = QStackedWidget()
        h.addWidget(self.pages, 1)

        dl = QWidget()
        dv = QVBoxLayout(dl)
        dv.setContentsMargins(32, 28, 32, 28)
        dv.setSpacing(24)
        self.panel = DownloadPanel(self.settings, lambda: self.tools)
        self.panel.jobs_created.connect(self._add_jobs)
        self.panel.need_tools.connect(self.run_setup)
        self.queue = QueuePage(self.manager)
        dv.addWidget(self.panel)
        dv.addWidget(self.queue, 1)
        self.pages.addWidget(dl)

        self.files_page = FilesPage(self.settings)
        self.pages.addWidget(self.files_page)
        fq = self.files_page.q
        for sig in (fq.job_added, fq.job_changed, fq.job_removed):
            sig.connect(self._update_files_badge)
        fq.job_finished.connect(self._file_finished)
        self.panel.as_file.connect(self._send_to_files)

        self.convert_page = ConvertPage(self.settings, lambda: self.tools)
        self.pages.addWidget(self.convert_page)
        self.queue.convert_requested.connect(self.send_to_convert)
        self.convert_page.q.item_changed.connect(self._update_convert_badge)
        self.convert_page.q.item_removed.connect(self._update_convert_badge)

        self.history_page = HistoryPage(self.history)
        self.history_page.convert_requested.connect(self.send_to_convert)
        self.history_page.redownload.connect(self._redownload)
        self.pages.addWidget(self.history_page)

        self.settings_page = SettingsPage(self.settings, lambda: self.tools)
        self.settings_page.request_setup.connect(lambda: self.run_setup(force_all=True))
        self.settings_page.changed.connect(self._settings_changed)
        self.pages.addWidget(self.settings_page)

        self.nav.idClicked.connect(self._go)
        self.nav_buttons[0].setChecked(True)

        # tray for notifications
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable() and not self.app_icon.isNull():
            self.tray = QSystemTrayIcon(self.app_icon, self)
            self.tray.setToolTip("ZeroLink")
            self.tray.activated.connect(lambda *_: (self.showNormal(), self.activateWindow()))
            self.tray.messageClicked.connect(self._open_last)
            self.tray.show()
        self._last_file = ""

        # clipboard watcher
        self._last_clip = QGuiApplication.clipboard().text().strip()
        QGuiApplication.clipboard().dataChanged.connect(self._clipboard_changed)

        theme.keyboard_focus_only(self)
        QTimer.singleShot(0, self.panel.url.setFocus)
        self.manager.restore()
        QTimer.singleShot(300, self._first_run_check)

    # -- navigation ----------------------------------------------------------
    def _go(self, idx: int):
        self.pages.setCurrentIndex(idx)
        # park focus on the page's main input so no button shows a focus ring after a mouse click
        target = {PAGE_DOWNLOADS: self.panel.url, PAGE_FILES: self.files_page.links,
                  PAGE_CONVERT: self.convert_page.table,
                  PAGE_HISTORY: self.history_page.search}.get(idx, self.pages.currentWidget())
        target.setFocus(Qt.OtherFocusReason)
        if idx == PAGE_HISTORY:
            self.history_page.refresh()

    def send_to_convert(self, paths):
        self.nav_buttons[PAGE_CONVERT].click()
        self.convert_page.add_paths(list(paths) if isinstance(paths, (list, tuple)) else [paths])

    def _update_badge(self, *_):
        active = sum(j.status in J.ACTIVE | {J.QUEUED} for j in self.manager.jobs.values())
        self.nav_buttons[PAGE_DOWNLOADS].set_badge(active)

    def _update_files_badge(self, *_):
        self.nav_buttons[PAGE_FILES].set_badge(self.files_page.active_count())

    def _send_to_files(self, url: str):
        self.nav_buttons[PAGE_FILES].click()
        self.files_page.add_urls([url])

    def _file_finished(self, job):
        if self.tray and self.settings.notify_on_finish and not self.isActiveWindow():
            if job.status == "done":
                self._last_file = job.path
                self.tray.showMessage("File downloaded", job.display_name, QSystemTrayIcon.Information, 4000)
            else:
                self.tray.showMessage("File download failed", f"{job.display_name}\n{job.error}"[:250],
                                      QSystemTrayIcon.Warning, 6000)

    def _update_convert_badge(self, *_):
        q = self.convert_page.q
        self.nav_buttons[PAGE_CONVERT].set_badge(sum(q.items[i].status in ("waiting", "converting") and
                                                     q.items[i].options is not None for i in q.order))

    # -- jobs -----------------------------------------------------------------
    def _add_jobs(self, jobs):
        for j in jobs:
            self.manager.add(j)

    def _redownload(self, row: dict):
        job = J.Job(url=row["url"], title=row["title"] or "", preset=row["preset"] or "best",
                    quality_label=row["quality"] or "", container=row["container"] or "mp4",
                    vcodec=row["vcodec"] or "auto", audio_quality=row["audio_quality"] or "0",
                    subs=self.settings.subs_enabled, download_dir=self.settings.download_dir, force=True)
        self.manager.add(job)
        self.nav_buttons[PAGE_DOWNLOADS].click()

    def _job_finished(self, job):
        self.history.add(job)
        if self.pages.currentIndex() == PAGE_HISTORY:
            self.history_page.refresh()
        if self.tray and self.settings.notify_on_finish and not self.isActiveWindow():
            if job.status == J.DONE:
                self._last_file = job.filepath
                self.tray.showMessage("Download finished", job.title or job.url,
                                      QSystemTrayIcon.Information, 4000)
            else:
                self.tray.showMessage("Download failed", f"{job.title or job.url}\n{job.error}"[:250],
                                      QSystemTrayIcon.Warning, 6000)

    def _open_last(self):
        if self._last_file:
            reveal(self._last_file)

    def _settings_changed(self):
        self.manager.schedule()  # concurrency may have increased
        self.files_page.q.apply_speed_limit()
        self.files_page.q.schedule()

    # -- clipboard --------------------------------------------------------------
    def _clipboard_changed(self):
        text = QGuiApplication.clipboard().text().strip()
        if text == self._last_clip:
            return
        self._last_clip = text
        if self.settings.watch_clipboard and _looks_like_url(text):
            if looks_like_file_url(text):
                self.nav_buttons[PAGE_FILES].click()
                self.files_page.offer_text(text)
                return
            self.nav_buttons[PAGE_DOWNLOADS].click()
            self.panel.offer_url(text)

    # -- setup -------------------------------------------------------------------
    def _first_run_check(self):
        if not self.tools.ready or (IS_WINDOWS and not self.tools.deno):
            self.run_setup()

    def run_setup(self, force_all: bool = False):
        missing = self.tools.missing
        if not missing and not force_all:
            return
        dlg = SetupDialog(missing, self, force_all=force_all)
        dlg.exec()
        self.tools = Tools.detect()
        self.settings_page.refresh_versions()
        self.manager.schedule()

    # -- close --------------------------------------------------------------------
    def closeEvent(self, e):
        if self.convert_page.busy():
            r = QMessageBox.question(self, "Conversion in progress",
                                     "A file is still being converted. Quit and stop it?")
            if r != QMessageBox.Yes:
                e.ignore()
                return
        running = self.manager.active_count() + self.files_page.q.active_count()
        if running:
            r = QMessageBox.question(
                self, "Downloads in progress",
                f"{running} download{'s are' if running > 1 else ' is'} still running.\n"
                "Quit anyway? They will continue from where they stopped next time.")
            if r != QMessageBox.Yes:
                e.ignore()
                return
        self.convert_page.shutdown()
        self.files_page.shutdown()
        self.panel.prober.cancel()
        self.manager.shutdown()
        if self.tray:
            self.tray.hide()
        e.accept()
