"""Fetch metadata (title, formats, playlist entries) for a URL without downloading."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from . import presets
from .binaries import child_env, kill_tree


@dataclass
class FormatOption:
    format_id: str
    label: str
    has_audio: bool
    height: int
    is_audio_only: bool


@dataclass
class Entry:
    url: str
    title: str
    duration: float | None
    index: int


@dataclass
class ProbeResult:
    url: str
    title: str
    uploader: str = ""
    duration: float | None = None
    view_count: int | None = None
    thumbnail: str = ""
    extractor: str = ""
    is_playlist: bool = False
    entries: list[Entry] = field(default_factory=list)
    formats: list[FormatOption] = field(default_factory=list)
    max_height: int = 0
    raw: dict = field(default_factory=dict, repr=False)


def human_size(n: float | None) -> str:
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def human_duration(sec: float | None) -> str:
    if not sec:
        return ""
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def pick_thumbnail(info: dict) -> str:
    """Prefer a JPEG thumbnail (always decodable by Qt), largest first."""
    thumbs = info.get("thumbnails") or []
    jpgs = [t for t in thumbs if t.get("url", "").split("?")[0].lower().endswith((".jpg", ".jpeg"))]
    pool = jpgs or thumbs
    if pool:
        pool = sorted(pool, key=lambda t: (t.get("preference") or 0, t.get("width") or 0))
        return pool[-1].get("url", "")
    return info.get("thumbnail") or ""


def _format_options(info: dict) -> tuple[list[FormatOption], int]:
    out: list[FormatOption] = []
    max_h = 0
    fmts = [f for f in info.get("formats") or [] if f.get("format_id")]
    duration = info.get("duration") or 0

    def size_of(f):
        s = f.get("filesize") or f.get("filesize_approx")
        if not s and f.get("tbr") and duration:
            s = f["tbr"] * 1000 / 8 * duration
        return s

    video = [f for f in fmts if f.get("vcodec") not in (None, "none") and f.get("height")]
    audio = [f for f in fmts if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    video.sort(key=lambda f: (f.get("height") or 0, f.get("fps") or 0, f.get("tbr") or 0), reverse=True)
    audio.sort(key=lambda f: f.get("abr") or f.get("tbr") or 0, reverse=True)

    for f in video:
        h = int(f.get("height") or 0)
        max_h = max(max_h, h)
        fps = f.get("fps")
        fps_s = str(int(fps)) if fps and fps > 30 else ""
        vcodec = (f.get("vcodec") or "").split(".")[0]
        has_audio = f.get("acodec") not in (None, "none")
        parts = [f"{h}p{fps_s}", (f.get("ext") or "").upper(), vcodec]
        if f.get("dynamic_range") and f["dynamic_range"] != "SDR":
            parts.append(f["dynamic_range"])
        size = human_size(size_of(f))
        if size:
            parts.append(("~" if not f.get("filesize") else "") + size + ("" if has_audio else " + audio"))
        out.append(FormatOption(f["format_id"], " · ".join(p for p in parts if p), has_audio, h, False))
    for f in audio:
        abr = f.get("abr") or f.get("tbr")
        parts = ["Audio", (f.get("ext") or "").upper(), (f.get("acodec") or "").split(".")[0]]
        if abr:
            parts.append(f"{int(abr)} kbps")
        lang = f.get("language")
        if lang:
            parts.append(lang)
        size = human_size(size_of(f))
        if size:
            parts.append(size)
        out.append(FormatOption(f["format_id"], " · ".join(p for p in parts if p), True, 0, True))
    return out, max_h


def parse_info(url: str, info: dict) -> ProbeResult:
    is_pl = info.get("_type") in ("playlist", "multi_video") or "entries" in info
    res = ProbeResult(
        url=info.get("webpage_url") or url,
        title=info.get("title") or info.get("id") or url,
        uploader=info.get("uploader") or info.get("channel") or info.get("uploader_id") or "",
        duration=info.get("duration"),
        view_count=info.get("view_count"),
        thumbnail=pick_thumbnail(info),
        extractor=info.get("extractor_key") or info.get("extractor") or "",
        is_playlist=is_pl,
        raw=info,
    )
    if is_pl:
        for i, e in enumerate(info.get("entries") or [], start=1):
            if not e:
                continue
            eurl = e.get("url") or e.get("webpage_url") or ""
            if eurl and not eurl.startswith("http") and e.get("ie_key") == "Youtube":
                eurl = f"https://www.youtube.com/watch?v={eurl}"
            if not eurl:
                continue
            res.entries.append(Entry(eurl, e.get("title") or e.get("id") or eurl,
                                     e.get("duration"), e.get("playlist_index") or i))
        if not res.thumbnail and info.get("entries"):
            first = next((e for e in info["entries"] if e), None)
            if first:
                res.thumbnail = pick_thumbnail(first)
    else:
        res.formats, res.max_height = _format_options(info)
    return res


COOKIE_HINT = ("Your browser's cookies couldn't be read, so ZeroLink continued without them. "
               "Chrome, Edge and other Chromium browsers lock and encrypt their cookies on Windows. "
               "If you need a login (private or age-restricted videos), choose Firefox or a cookies.txt file "
               "in Settings → Browser cookies.")


class Prober(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    notice = Signal(str)  # non-fatal hint shown above the result

    TIMEOUT_MS = 120_000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc: QProcess | None = None
        self._url = ""
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self._timeout)

    def busy(self) -> bool:
        return self.proc is not None

    def start(self, url: str, whole_playlist: bool, settings, tools):
        self.cancel()
        self._url = url
        self._args = (url, whole_playlist, settings, tools)
        cmd = presets.probe_command(url, whole_playlist, settings, tools)
        p = QProcess(self)
        env = p.processEnvironment()
        for k, v in child_env().items():
            env.insert(k, v)
        p.setProcessEnvironment(env)
        p.finished.connect(self._finished)
        p.errorOccurred.connect(self._error)
        self.proc = p
        self._timer.start(self.TIMEOUT_MS)
        p.start(cmd[0], cmd[1:])

    def cancel(self):
        if self.proc:
            p, self.proc = self.proc, None
            p.finished.disconnect()
            p.errorOccurred.disconnect()
            if p.state() != QProcess.NotRunning:
                kill_tree(int(p.processId()))
                p.kill()
            p.deleteLater()
        self._timer.stop()

    def _timeout(self):
        self.cancel()
        self.failed.emit("Timed out while reading the link.")

    def _error(self, err):
        if err == QProcess.FailedToStart:
            self.cancel()
            self.failed.emit("Could not start yt-dlp. Open Settings → Components to install it.")

    def _finished(self, code, _status):
        p, self.proc = self.proc, None
        self._timer.stop()
        if p is None:
            return
        out = bytes(p.readAllStandardOutput()).decode("utf-8", "replace")
        err = bytes(p.readAllStandardError()).decode("utf-8", "replace").strip()
        p.deleteLater()
        if code != 0 or not out.strip():
            lines = [l for l in err.splitlines() if l.strip()]
            msg = next((l[6:].strip() for l in reversed(lines) if l.startswith("ERROR:")),
                       lines[-1] if lines else "yt-dlp could not read this link.")
            url, whole, settings, tools = self._args
            if presets.is_cookie_error(msg) and not getattr(settings, "_cookies_broken", False):
                settings._cookies_broken = True  # off for this session, downloads too
                self.notice.emit(COOKIE_HINT)
                self.start(url, whole, settings, tools)
                return
            self.failed.emit(msg)
            return
        try:
            info = json.loads(out)
        except ValueError:
            self.failed.emit("yt-dlp returned data ZeroLink could not read.")
            return
        self.succeeded.emit(parse_info(self._url, info))
