"""Direct file downloads (any http/https link): multi-connection, pause/resume, survives restarts.

Each file downloads into "<name>.zlpart" next to the final path. A small "<name>.zlpart.json"
holds the URL, size and how far each segment got, so a download continues after a pause,
a crash or a reboot. When the server supports byte ranges the file is split into up to
N segments fetched in parallel; otherwise one connection is used.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from email.message import Message
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from .paths import data_dir

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
CHUNK = 256 * 1024
MIN_SEGMENT = 2 * 1024 * 1024
MAX_RETRIES = 6

QUEUED, CONNECTING, RUNNING, PAUSED, DONE, ERROR, CANCELED = (
    "queued", "connecting", "downloading", "paused", "done", "error", "canceled")
ACTIVE = {CONNECTING, RUNNING}
FINISHED = {DONE, ERROR, CANCELED}

CATEGORIES = {
    "Compressed": {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".cab", ".lz", ".lzma"},
    "Programs": {".exe", ".msi", ".msix", ".appx", ".appxbundle", ".msixbundle", ".bat", ".apk", ".dmg", ".pkg",
                 ".deb", ".rpm", ".appimage", ".jar"},
    "Disk images": {".iso", ".img", ".vhd", ".vhdx", ".vmdk", ".wim", ".esd"},
    "Documents": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".txt",
                  ".rtf", ".csv", ".epub", ".mobi", ".djvu", ".md", ".json", ".xml"},
    "Video": {".mp4", ".mkv", ".webm", ".avi", ".mov", ".wmv", ".flv", ".m4v", ".ts", ".mpg", ".mpeg", ".3gp"},
    "Music": {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".alac", ".aiff"},
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg", ".heic", ".avif", ".psd",
               ".raw", ".cr2", ".nef", ".ico"},
    "Fonts": {".ttf", ".otf", ".woff", ".woff2"},
    "Torrents": {".torrent"},
}
# extensions that mean "a file", not "a page with a video" (clipboard routing)
FILE_EXT = set().union(*(v for k, v in CATEGORIES.items() if k not in ("Video", "Music", "Images")))


def category_of(name: str) -> str:
    ext = Path(name).suffix.lower()
    return next((k for k, v in CATEGORIES.items() if ext in v), "Other")


def looks_like_file_url(text: str) -> bool:
    try:
        u = urllib.parse.urlparse(text.strip())
    except ValueError:
        return False
    if u.scheme not in ("http", "https"):
        return False
    return Path(urllib.parse.unquote(u.path)).suffix.lower() in FILE_EXT


def split_links(text: str) -> list[str]:
    """Every http(s) link in a blob of text, in order, without duplicates."""
    seen, out = set(), []
    for m in re.finditer(r"https?://[^\s<>\"']+", text):
        url = m.group(0).rstrip(").,;]")
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_name(name: str) -> str:
    name = _BAD.sub("_", name).strip().strip(". ")
    if not name:
        name = "download"
    stem, ext = os.path.splitext(name)
    if stem.upper() in _RESERVED:
        stem = "_" + stem
    if len(stem) > 180:
        stem = stem[:180]
    return stem + ext[:20]


def unique_path(folder: Path, name: str) -> Path:
    p = folder / name
    stem, ext = os.path.splitext(name)
    i = 1
    while p.exists() or Path(str(p) + ".zlpart").exists():
        p = folder / f"{stem} ({i}){ext}"
        i += 1
    return p


def parse_speed_limit(text: str) -> int:
    """'5M' / '800K' / '1.5m' -> bytes per second; '' or junk -> 0 (unlimited)."""
    m = re.fullmatch(r"\s*([\d.]+)\s*([kKmMgG]?)[iI]?[bB]?\s*", text or "")
    if not m:
        return 0
    try:
        n = float(m.group(1))
    except ValueError:
        return 0
    return int(n * {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[m.group(2).lower()])


class RateLimiter:
    """One token bucket shared by every connection of every file download."""

    def __init__(self):
        self.rate = 0
        self._lock = threading.Lock()
        self._allow = 0.0
        self._t = time.monotonic()

    def take(self, n: int, stop: threading.Event) -> None:
        while self.rate and not stop.is_set():
            with self._lock:
                now = time.monotonic()
                self._allow = min(self.rate, self._allow + (now - self._t) * self.rate)
                self._t = now
                if self._allow >= n or self._allow >= self.rate:
                    self._allow -= n
                    return
                wait = (n - self._allow) / self.rate
            stop.wait(min(wait, 0.25))


LIMITER = RateLimiter()


@dataclass
class FileJob:
    url: str
    folder: str
    connections: int = 8
    sort: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = ""            # final file name once known
    path: str = ""            # final path once known
    size: int = 0             # 0 = unknown
    done: int = 0
    resumable: bool = False
    status: str = QUEUED
    error: str = ""
    speed: float = 0.0
    eta: int | None = None
    added: float = field(default_factory=time.time)
    finished: float = 0.0
    segments_active: int = 0

    @property
    def percent(self) -> float:
        if self.status == DONE:
            return 100.0
        return self.done * 100.0 / self.size if self.size else 0.0

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        tail = urllib.parse.unquote(urllib.parse.urlparse(self.url).path.rsplit("/", 1)[-1])
        return tail or self.url

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("speed", "eta", "segments_active"):
            d.pop(k)
        return d


def _ctx():
    return ssl.create_default_context()


def _open(url: str, headers: dict, timeout: float = 30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", **headers})
    return urllib.request.urlopen(req, timeout=timeout, context=_ctx())


def probe(url: str) -> dict:
    """Ask for the first byte: tells size, range support and the file name without downloading it."""
    try:
        r = _open(url, {"Range": "bytes=0-0", "Accept-Encoding": "identity"})
    except urllib.error.HTTPError as e:
        if e.code != 416:
            raise
        r = _open(url, {"Accept-Encoding": "identity"})
    with r:
        size, resumable = 0, False
        cr = r.headers.get("Content-Range", "")
        if r.status == 206 and "/" in cr:
            total = cr.rsplit("/", 1)[1].strip()
            if total.isdigit():
                size, resumable = int(total), True
        elif r.headers.get("Content-Length", "").isdigit():
            size = int(r.headers["Content-Length"])
        name = ""
        cd = r.headers.get("Content-Disposition")
        if cd:
            m = Message()
            m["content-disposition"] = cd
            name = m.get_filename() or ""
        if not name:
            name = urllib.parse.unquote(urllib.parse.urlparse(r.geturl()).path.rsplit("/", 1)[-1])
        if not name:
            name = urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if "." not in name:
            ext = {"application/zip": ".zip", "application/pdf": ".pdf", "application/x-msdownload": ".exe",
                   "application/x-7z-compressed": ".7z", "application/vnd.rar": ".rar",
                   "application/x-iso9660-image": ".iso", "video/mp4": ".mp4", "audio/mpeg": ".mp3",
                   "image/jpeg": ".jpg", "image/png": ".png", "text/html": ".html"}.get(ctype, "")
            name = (name or "download") + ext
        return {"size": size, "resumable": resumable, "name": safe_name(name), "type": ctype}


def _human_http_error(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        return {401: "The server asks for a login (401).", 403: "The server refused the download (403). "
                "The link may have expired or need a login.", 404: "File not found on the server (404).",
                410: "The link has expired (410).", 429: "The server says too many requests (429). Try later."
                }.get(e.code, f"Server error {e.code} {e.reason}.")
    if isinstance(e, urllib.error.URLError):
        reason = e.reason
        if isinstance(reason, ssl.SSLError):
            return f"Secure connection failed: {reason}"
        return f"Can't reach the server: {reason}"
    if isinstance(e, TimeoutError):
        return "The server stopped responding."
    if isinstance(e, OSError) and e.errno == 28:
        return "The disk is full."
    return str(e) or e.__class__.__name__


class FileWorker(QThread):
    """Downloads one FileJob. Emits progress from its own thread; the queue owns the job object."""

    progress = Signal(str, int, int, float, int)   # id, done, size, speed, active connections
    info = Signal(str, str, str, int, bool)        # id, name, path, size, resumable
    finished_job = Signal(str, str, str)           # id, status, error

    def __init__(self, job: FileJob, parent=None):
        super().__init__(parent)
        self.job = job
        self.stop_ev = threading.Event()
        self._lock = threading.Lock()
        self._errors: list[str] = []
        self.pause_requested = False

    # -- control ------------------------------------------------------------------
    def request_pause(self):
        self.pause_requested = True
        self.stop_ev.set()

    # -- meta -----------------------------------------------------------------------
    @staticmethod
    def _meta_path(path: str) -> Path:
        return Path(path + ".zlpart.json")

    def _save_meta(self, segs):
        j = self.job
        meta = {"url": j.url, "size": j.size, "resumable": j.resumable, "segments": segs}
        mp = self._meta_path(j.path)
        tmp = mp.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(meta), encoding="utf-8")
            tmp.replace(mp)
        except OSError:
            pass

    def _load_meta(self):
        try:
            m = json.loads(self._meta_path(self.job.path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if m.get("url") != self.job.url or not Path(self.job.path + ".zlpart").exists():
            return None
        return m

    # -- main -----------------------------------------------------------------------
    def run(self):
        j = self.job
        try:
            segs = None
            if j.path and (m := self._load_meta()) and m.get("resumable") and m.get("size"):
                j.size, j.resumable, segs = m["size"], True, m["segments"]
            else:
                p = probe(j.url)
                folder = Path(j.folder)
                if j.sort:
                    folder = folder / category_of(p["name"])
                folder.mkdir(parents=True, exist_ok=True)
                if not j.path:
                    j.path = str(unique_path(folder, p["name"]))
                    j.name = Path(j.path).name
                j.size, j.resumable = p["size"], p["resumable"]
                self.info.emit(j.id, j.name, j.path, j.size, j.resumable)
                if j.size:
                    free = shutil.disk_usage(Path(j.path).parent).free
                    if free < j.size + 50 * 1024 * 1024:
                        raise OSError(28, "disk full")
            self.info.emit(j.id, j.name, j.path, j.size, j.resumable)
            tmp = j.path + ".zlpart"
            if segs is None:
                segs = self._plan(j)
                with open(tmp, "wb") as f:
                    if j.size and j.resumable:
                        f.truncate(j.size)
            if self.stop_ev.is_set():
                self._save_meta(segs)
                self.finished_job.emit(j.id, PAUSED, "")
                return
            self._download(tmp, segs)
            if self.stop_ev.is_set():
                if j.resumable:
                    self._save_meta(segs)
                self.finished_job.emit(j.id, PAUSED if self.pause_requested else CANCELED, "")
                return
            if self._errors:
                if j.resumable:
                    self._save_meta(segs)
                self.finished_job.emit(j.id, ERROR, self._errors[0])
                return
            got = sum(s[2] for s in segs)
            if j.size and got < j.size:
                self._save_meta(segs)
                self.finished_job.emit(j.id, ERROR, f"The server closed the connection early "
                                                    f"({got:,} of {j.size:,} bytes). Retry to continue.")
                return
            if not j.size:
                j.size = got
                with open(tmp, "r+b") as f:
                    f.truncate(got)
            for attempt in range(10):  # antivirus scanners briefly lock fresh files on Windows
                try:
                    os.replace(tmp, j.path)
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(0.5)
            try:
                self._meta_path(j.path).unlink()
            except OSError:
                pass
            self.progress.emit(j.id, j.size, j.size, 0.0, 0)
            self.finished_job.emit(j.id, DONE, "")
        except Exception as e:  # network, disk, bad URL
            self.finished_job.emit(j.id, ERROR, _human_http_error(e))

    @staticmethod
    def _plan(j: FileJob) -> list[list[int]]:
        """[[start, end_inclusive, bytes_done], ...]; end -1 = unknown length."""
        if not (j.resumable and j.size):
            return [[0, (j.size - 1) if j.size else -1, 0]]
        n = max(1, min(j.connections, j.size // MIN_SEGMENT))
        step = j.size // n
        segs, start = [], 0
        for i in range(n):
            end = j.size - 1 if i == n - 1 else start + step - 1
            segs.append([start, end, 0])
            start = end + 1
        return segs

    def _download(self, tmp: str, segs: list[list[int]]):
        j = self.job
        threads = [threading.Thread(target=self._segment, args=(tmp, s), daemon=True)
                   for s in segs if s[1] < 0 or s[2] < s[1] - s[0] + 1]
        for t in threads:
            t.start()
        samples: list[tuple[float, int]] = []
        last_meta = time.monotonic()
        while any(t.is_alive() for t in threads):
            time.sleep(0.4)
            done = sum(s[2] for s in segs)
            now = time.monotonic()
            samples = [x for x in samples if now - x[0] < 4] + [(now, done)]
            speed = (done - samples[0][1]) / (now - samples[0][0]) if len(samples) > 1 and now > samples[0][0] else 0.0
            self.progress.emit(j.id, done, j.size, speed, sum(t.is_alive() for t in threads))
            if j.resumable and now - last_meta > 2:
                self._save_meta(segs)
                last_meta = now
        for t in threads:
            t.join()

    def _segment(self, tmp: str, seg: list[int]):
        j = self.job
        failures = 0
        while not self.stop_ev.is_set():
            start, end, pos = seg
            if end >= 0 and start + pos > end:
                return
            before = pos
            try:
                headers = {"Accept-Encoding": "identity"}
                if j.resumable:
                    headers["Range"] = f"bytes={start + pos}-{end}"
                elif pos:
                    seg[2] = pos = 0  # server can't resume: start this file over
                with _open(j.url, headers) as r:
                    if j.resumable and r.status != 206:
                        raise OSError("The server stopped accepting resumed downloads.")
                    with open(tmp, "r+b") as f:
                        f.seek(start + pos)
                        while not self.stop_ev.is_set():
                            want = CHUNK if end < 0 else min(CHUNK, end + 1 - start - seg[2])
                            if want <= 0:
                                break
                            b = r.read(want)
                            if not b:
                                break
                            LIMITER.take(len(b), self.stop_ev)
                            f.write(b)
                            seg[2] += len(b)
                if end < 0:
                    return  # unknown length: end of stream is the end of the file
                if start + seg[2] > end:
                    return
                if seg[2] == before:
                    raise OSError("The server sent no data.")
            except Exception as e:
                if self.stop_ev.is_set():
                    return
                if isinstance(e, urllib.error.HTTPError) and e.code in (401, 403, 404, 410) and failures >= 1:
                    with self._lock:
                        self._errors.append(_human_http_error(e))
                    return
                if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
                    with self._lock:
                        self._errors.append(_human_http_error(e))
                    self.stop_ev.set()
                    return
                failures = failures + 1 if seg[2] == before else 1
                if failures > MAX_RETRIES:
                    with self._lock:
                        self._errors.append(_human_http_error(e))
                    return
                self.stop_ev.wait(min(2 ** failures, 30))


class FileQueue(QObject):
    """Holds file downloads, runs up to `settings.files_concurrent` at once, saves itself to files.json."""

    job_added = Signal(object)
    job_changed = Signal(object)
    job_removed = Signal(str)
    job_finished = Signal(object)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.s = settings
        self.jobs: dict[str, FileJob] = {}
        self.order: list[str] = []
        self.workers: dict[str, FileWorker] = {}
        self._save_timer = QTimer(self, singleShot=True, interval=1500)
        self._save_timer.timeout.connect(self.save)
        self.apply_speed_limit()

    @property
    def store(self) -> Path:
        return data_dir() / "files.json"

    def apply_speed_limit(self):
        LIMITER.rate = parse_speed_limit(self.s.rate_limit)

    # -- persistence -------------------------------------------------------------
    def restore(self):
        try:
            raw = json.loads(self.store.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for d in raw:
            try:
                j = FileJob(**{k: v for k, v in d.items() if k in FileJob.__dataclass_fields__})
            except TypeError:
                continue
            if j.status in ACTIVE:
                j.status = QUEUED  # was running when the app closed: continue
            self.jobs[j.id] = j
            self.order.append(j.id)
            self.job_added.emit(j)
        self.schedule()

    def save(self):
        data = [self.jobs[i].to_dict() for i in self.order]
        tmp = self.store.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.store)
        except OSError:
            pass

    def _dirty(self):
        if not self._save_timer.isActive():
            self._save_timer.start()

    # -- adding ------------------------------------------------------------------------
    def add(self, url: str, folder: str, connections: int, sort: bool) -> FileJob:
        j = FileJob(url=url, folder=folder, connections=connections, sort=sort)
        self.jobs[j.id] = j
        self.order.append(j.id)
        self.job_added.emit(j)
        self._dirty()
        self.schedule()
        return j

    # -- scheduling -------------------------------------------------------------------
    def active_count(self) -> int:
        return len(self.workers)

    def schedule(self):
        limit = max(1, int(self.s.files_concurrent or 3))
        for jid in self.order:
            if len(self.workers) >= limit:
                break
            j = self.jobs[jid]
            if j.status == QUEUED and jid not in self.workers:
                self._start(j)

    def _start(self, j: FileJob):
        j.status, j.error, j.speed, j.eta = CONNECTING, "", 0.0, None
        w = FileWorker(j, self)
        w.progress.connect(self._progress)
        w.info.connect(self._info)
        w.finished_job.connect(self._finished)
        w.finished.connect(w.deleteLater)
        self.workers[j.id] = w
        self.job_changed.emit(j)
        w.start()

    def _info(self, jid, name, path, size, resumable):
        j = self.jobs.get(jid)
        if j:
            j.name, j.path, j.size, j.resumable = name, path, size, resumable
            self.job_changed.emit(j)
            self._dirty()

    def _progress(self, jid, done, size, speed, conns):
        j = self.jobs.get(jid)
        if not j or j.status not in ACTIVE:
            return
        j.done, j.size, j.speed, j.segments_active = done, size, speed, conns
        j.status = RUNNING
        j.eta = int((size - done) / speed) if size and speed > 1 else None
        self.job_changed.emit(j)

    def _finished(self, jid, status, error):
        self.workers.pop(jid, None)
        j = self.jobs.get(jid)
        if j:
            if j.status == CANCELED or status == CANCELED:
                j.status = CANCELED
                self._delete_partial(j)
            else:
                j.status, j.error = status, error
            j.speed, j.eta, j.segments_active = 0.0, None, 0
            if status == DONE:
                j.done, j.finished = j.size, time.time()
            self.job_changed.emit(j)
            if status in (DONE, ERROR):
                self.job_finished.emit(j)
        self._dirty()
        QTimer.singleShot(0, self.schedule)

    # -- actions ------------------------------------------------------------------------
    def pause(self, jid):
        j = self.jobs.get(jid)
        if not j:
            return
        if jid in self.workers:
            j.status = PAUSED
            self.workers[jid].request_pause()
        elif j.status == QUEUED:
            j.status = PAUSED
        self.job_changed.emit(j)
        self._dirty()

    def resume(self, jid):
        j = self.jobs.get(jid)
        if j and j.status in (PAUSED, ERROR, CANCELED) and jid not in self.workers:
            if j.status == CANCELED:
                j.done, j.path, j.name = 0, "", ""
            j.status, j.error = QUEUED, ""
            self.job_changed.emit(j)
            self._dirty()
            self.schedule()

    retry = resume

    def cancel(self, jid):
        j = self.jobs.get(jid)
        if not j or j.status in (DONE, CANCELED):
            return
        if jid in self.workers:
            j.status = CANCELED
            self.workers[jid].stop_ev.set()  # _finished deletes the partial file
        else:
            j.status = CANCELED
            self._delete_partial(j)
        self.job_changed.emit(j)
        self._dirty()

    def remove(self, jid):
        j = self.jobs.get(jid)
        if not j:
            return
        if jid in self.workers:
            self.cancel(jid)
        elif j.status != DONE:
            self._delete_partial(j)
        self.jobs.pop(jid, None)
        if jid in self.order:
            self.order.remove(jid)
        self.job_removed.emit(jid)
        self._dirty()

    def pause_all(self):
        for jid in list(self.order):
            if self.jobs[jid].status in ACTIVE | {QUEUED}:
                self.pause(jid)

    def resume_all(self):
        for jid in list(self.order):
            if self.jobs[jid].status in (PAUSED, ERROR):
                self.resume(jid)

    def clear_finished(self):
        for jid in [i for i in self.order if self.jobs[i].status in (DONE, CANCELED)]:
            self.remove(jid)

    @staticmethod
    def _delete_partial(j: FileJob):
        if not j.path:
            return
        for p in (j.path + ".zlpart", j.path + ".zlpart.json"):
            try:
                os.remove(p)
            except OSError:
                pass

    def shutdown(self):
        """Pause everything (kept resumable) and save; called when the app closes."""
        canceled = [jid for jid in self.workers if self.jobs[jid].status == CANCELED]
        for jid, w in list(self.workers.items()):
            for sig in (w.finished_job, w.progress, w.info):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
            w.pause_requested = jid not in canceled
            w.stop_ev.set()
        for w in list(self.workers.values()):
            w.wait(5000)
        for jid in list(self.workers):
            if jid in canceled:
                self._delete_partial(self.jobs[jid])
            else:
                self.jobs[jid].status = QUEUED  # continue on next start
        self.workers.clear()
        self.save()
