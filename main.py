"""ZeroLink: a desktop video/audio downloader built on yt-dlp."""
import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtNetwork import QNetworkProxyFactory
from PySide6.QtWidgets import QApplication

from zero.ui import theme
from zero.ui.main_window import MainWindow


def main() -> int:
    if sys.platform == "win32":
        try:  # own taskbar icon instead of python.exe's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ZeroLink.App")
        except Exception:
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("ZeroLink")
    app.setOrganizationName("ZeroLink")
    QNetworkProxyFactory.setUseSystemConfiguration(True)
    theme.apply(app)
    win = MainWindow()
    win.show()
    shot = os.environ.get("ZERO_SELFTEST")  # build check: save screenshots of open windows and exit
    if shot:
        from PySide6.QtCore import QTimer

        def snap():
            for i, w in enumerate(w for w in app.topLevelWidgets() if w.isVisible()):
                w.grab().save(f"{shot}-{i}.png")
            app.exit(0)
        QTimer.singleShot(5000, snap)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
