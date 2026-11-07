"""Locating and installing the external tools ZeroLink drives.

ZeroLink does not bundle yt-dlp as a Python library on purpose: the standalone
yt-dlp.exe can update itself in seconds (`yt-dlp -U`) whenever YouTube changes
something, without rebuilding ZeroLink.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .paths import EXE, IS_WINDOWS, bin_dir, legacy_dir

NO_WINDOW = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW

YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
FFMPEG_URL = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
DENO_URL = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"


def _find(name: str) -> str | None:
    for folder in (bin_dir(), legacy_dir() / "bin"):  # old ZERO tools are reused, no re-download
        local = folder / f"{name}{EXE}"
        if local.is_file():
            return str(local)
    return shutil.which(name)


@dataclass
class Tools:
    ytdlp: str | None
    ffmpeg: str | None
    ffprobe: str | None
    deno: str | None

    @classmethod
    def detect(cls) -> "Tools":
        return cls(_find("yt-dlp"), _find("ffmpeg"), _find("ffprobe"), _find("deno"))

    @property
    def ready(self) -> bool:
        return bool(self.ytdlp and self.ffmpeg)

    @property
    def missing(self) -> list[str]:
        out = []
        if not self.ytdlp:
            out.append("yt-dlp")
        if not (self.ffmpeg and self.ffprobe):
            out.append("ffmpeg")
        if not self.deno:
            out.append("deno")
        return out

    def base_args(self) -> list[str]:
        """Arguments every yt-dlp call gets."""
        args = ["--ignore-config", "--color", "never", "--no-warnings"]
        if self.ffmpeg:
            args += ["--ffmpeg-location", str(Path(self.ffmpeg).parent)]
        if self.deno:
            args += ["--js-runtimes", f"deno:{self.deno}"]
        return args


def child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run_quiet(cmd: list[str], timeout: float = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, env=child_env(),
                           creationflags=NO_WINDOW)
        out = (p.stdout + p.stderr).decode("utf-8", "replace").strip()
        return p.returncode, out
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def version_of(path: str | None, flag: str = "--version") -> str:
    if not path:
        return "not installed"
    code, out = run_quiet([path, flag], timeout=20)
    if code != 0 or not out:
        return "unknown"
    first = out.splitlines()[0]
    # "ffmpeg version N-1234-g..." / "deno 2.5.1 (stable...)" / "2026.08.19"
    parts = first.split()
    if first.startswith("ffmpeg version") and len(parts) > 2:
        return parts[2][:24]
    if first.startswith("deno") and len(parts) > 1:
        return parts[1]
    return first[:32]


def kill_tree(pid: int) -> None:
    """Kill a process and its children (yt-dlp.exe spawns ffmpeg and a bootloader child)."""
    if not pid:
        return
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                       creationflags=NO_WINDOW)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


class ToolInstaller(QThread):
    """Downloads yt-dlp, ffmpeg/ffprobe and deno into the app's bin folder (Windows)."""

    progress = Signal(str, int, int)  # component, bytes done, bytes total
    status = Signal(str)
    done = Signal(bool, str)  # ok, message

    def __init__(self, components: list[str], parent=None):
        super().__init__(parent)
        self.components = components
        self._stop = False

    def stop(self):
        self._stop = True

    def _download(self, url: str, dest: Path, label: str) -> None:
        req = urllib.request.Request(url, headers={"User-Agent": "ZeroLink/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if self._stop:
                    raise RuntimeError("Canceled")
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                self.progress.emit(label, done, total)

    @staticmethod
    def _extract(zip_path: Path, wanted: list[str], dest_dir: Path) -> None:
        with zipfile.ZipFile(zip_path) as z:
            for member in z.namelist():
                name = member.rsplit("/", 1)[-1]
                if name in wanted:
                    target = dest_dir / name
                    with z.open(member) as src, open(target.with_suffix(".new"), "wb") as out:
                        shutil.copyfileobj(src, out)
                    target.with_suffix(".new").replace(target)

    def run(self):
        if not IS_WINDOWS:
            self.done.emit(False, "Automatic setup is only available on Windows. "
                                  "Install yt-dlp, ffmpeg and deno with your package manager.")
            return
        bdir = bin_dir()
        try:
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                if "yt-dlp" in self.components:
                    self.status.emit("Downloading yt-dlp…")
                    self._download(YTDLP_URL, tmp / "yt-dlp.exe", "yt-dlp")
                    shutil.move(str(tmp / "yt-dlp.exe"), bdir / "yt-dlp.exe")
                if "ffmpeg" in self.components:
                    self.status.emit("Downloading ffmpeg…")
                    self._download(FFMPEG_URL, tmp / "ffmpeg.zip", "ffmpeg")
                    self.status.emit("Unpacking ffmpeg…")
                    self._extract(tmp / "ffmpeg.zip", ["ffmpeg.exe", "ffprobe.exe"], bdir)
                if "deno" in self.components:
                    self.status.emit("Downloading deno (needed for YouTube)…")
                    self._download(DENO_URL, tmp / "deno.zip", "deno")
                    self._extract(tmp / "deno.zip", ["deno.exe"], bdir)
        except Exception as e:  # network errors, disk errors, cancel
            self.done.emit(False, str(e))
            return
        self.done.emit(True, "All components installed.")
