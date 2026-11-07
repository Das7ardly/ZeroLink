"""Filesystem locations used by ZeroLink."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "ZeroLink"
LEGACY_NAME = "ZERO"  # earlier name: settings, history and tools are carried over
IS_WINDOWS = sys.platform == "win32"
EXE = ".exe" if IS_WINDOWS else ""
_CARRY_OVER = ("settings.json", "history.db", "archive.txt", "queue.json")


def app_dir() -> Path:
    """Folder holding ZeroLink.exe (or main.py when run from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def is_portable() -> bool:
    """A portable.txt next to the exe keeps all data in a 'data' folder beside it."""
    return (app_dir() / "portable.txt").is_file()


def _user_base(name: str) -> Path:
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / name
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / name


def legacy_dir() -> Path:
    return _user_base(LEGACY_NAME)


_cached: Path | None = None


def data_dir() -> Path:
    global _cached
    if _cached is not None:
        return _cached
    override = os.environ.get("ZEROLINK_DATA_DIR") or os.environ.get("ZERO_DATA_DIR")
    if override:
        base = Path(override)
    elif is_portable():
        base = app_dir() / "data"
    else:
        base = _user_base(APP_NAME)
    fresh = not base.exists()
    base.mkdir(parents=True, exist_ok=True)
    if fresh and not override:
        _carry_over(base)
    _cached = base
    return base


def _carry_over(base: Path) -> None:
    """First start: copy settings and history from the old ZERO folder (tools are found in place)."""
    old = legacy_dir()
    if not old.is_dir():
        return
    for name in _CARRY_OVER:
        src = old / name
        if src.is_file():
            try:
                shutil.copy2(src, base / name)
            except OSError:
                pass


def bin_dir() -> Path:
    d = data_dir() / "bin"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_download_dir() -> Path:
    return Path.home() / "Downloads"


def resource_path(rel: str) -> Path:
    """Path to a bundled resource, works both from source and from a PyInstaller build."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / rel
