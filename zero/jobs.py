"""Download jobs, the process runner and the queue scheduler."""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
import uuid
from dataclasses import asdict, dataclass, field, fields

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from . import presets
from .binaries import child_env, kill_tree
from .convert import Converter
from .paths import data_dir

QUEUED, RUNNING, PROCESSING, PAUSED, DONE, SKIPPED, ERROR, CANCELED = (
    "queued", "downloading", "processing", "paused", "done", "skipped", "error", "canceled")
ACTIVE = {RUNNING, PROCESSING}
FINISHED = {DONE, SKIPPED, ERROR, CANCELED}


@dataclass
class Job:
    url: str
    title: str = ""
    preset: str = "best"
    quality_label: str = ""
    container: str = "mp4"
    vcodec: str = "auto"
    audio_quality: str = "0"
    subs: bool = False
    download_dir: str = ""
    playlist_title: str = ""
    playlist_index: int = 0
    force: bool = False  # ignore the download archive
    convert_pending: bool = False  # downloaded, still needs re-encoding to vcodec
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = QUEUED
    stage: str = ""
    percent: float = 0.0
    speed: float = 0.0
    eta: int | None = None
    total_bytes: int = 0
    parts_done: int = 0
    filepath: str = ""
    error: str = ""
    created: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})

    def reset(self) -> None:
        self.status, self.stage, self.percent = QUEUED, "", 0.0
        self.speed, self.eta, self.parts_done, self.error = 0.0, None, 0, ""


def cleanup_temp(job: Job, settings) -> None:
    """Delete a job's partial files folder (and the parent .zero-tmp if now empty)."""
    base = job.download_dir or (settings.download_dir if settings else "")
    if not base:
        return
    d = Path(presets.job_temp_dir(job, settings)) if settings else Path(base) / ".zero-tmp" / job.id
    shutil.rmtree(d, ignore_errors=True)
    try:
        d.parent.rmdir()
    except OSError:
        pass


class JobRunner(QObject):
    """Runs one yt-dlp process for one job and translates its output into job state."""

    changed = Signal(object)
    ended = Signal(object)

    def __init__(self, job: Job, command: list[str], parent=None):
        super().__init__(parent)
        self.job = job
        self.command = command
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        env = self.proc.processEnvironment()
        for k, v in child_env().items():
            env.insert(k, v)
        self.proc.setProcessEnvironment(env)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(self._error)
        self._buf = b""
        self._tail: list[str] = []
        self._stopping_as: str | None = None
        self._done_bytes = 0
        self.converter: Converter | None = None

    def _ctx(self):
        """(settings, tools) from the owning QueueManager."""
        m = self.parent()
        return getattr(m, "settings", None), (m.tools_getter() if hasattr(m, "tools_getter") else None)

    def start(self):
        job = self.job
        if job.convert_pending and job.filepath and os.path.exists(job.filepath):
            self._start_convert()  # resumed after a pause during conversion
            return
        job.convert_pending = False
        job.status, job.stage = RUNNING, "Starting"
        self.changed.emit(job)
        self.proc.start(self.command[0], self.command[1:])

    def stop(self, as_status: str):
        """Pause or cancel: kill the process tree, then report the given status."""
        self._stopping_as = as_status
        if self.converter is not None:
            self.converter.stop()
            self.converter = None
            if as_status == CANCELED:
                self.job.convert_pending = False
            self._finalize(as_status)
            return
        if self.proc.state() != QProcess.NotRunning:
            kill_tree(int(self.proc.processId()))
            self.proc.kill()
        else:
            self._finalize(as_status)

    # -- output parsing -------------------------------------------------
    def _read(self):
        self._buf += bytes(self.proc.readAllStandardOutput())
        *lines, self._buf = self._buf.split(b"\n")
        for raw in lines:
            self.handle_line(raw.decode("utf-8", "replace").rstrip("\r"))

    def handle_line(self, line: str) -> None:
        job = self.job
        if not line.strip():
            return
        if line.startswith(presets.MARK_PROGRESS):
            try:
                p = json.loads(line[len(presets.MARK_PROGRESS):])
            except ValueError:
                return
            total = p.get("total_bytes") or p.get("total_bytes_estimate") or 0
            got = p.get("downloaded_bytes") or 0
            if p.get("status") == "finished":
                job.parts_done += 1
                job.percent = 100.0
                job.speed, job.eta = 0.0, None
            else:
                pct = p.get("_percent")
                if pct is None and total:
                    pct = got * 100.0 / total
                if pct is None:
                    # live streams / unknown size: show fragment progress if any
                    fi, fc = p.get("fragment_index"), p.get("fragment_count")
                    pct = (fi * 100.0 / fc) if fi and fc else job.percent
                job.percent = float(pct)
                job.speed = float(p.get("speed") or 0.0)
                job.eta = p.get("eta")
            if total:
                job.total_bytes = self._done_bytes + int(total)
            if p.get("status") == "finished":
                self._done_bytes = job.total_bytes
            job.status = RUNNING
            part = f" (part {job.parts_done + 1})" if job.parts_done and p.get("status") != "finished" else ""
            job.stage = "Downloading" + part
        elif line.startswith(presets.MARK_PP):
            name, _, state = line[len(presets.MARK_PP):].partition("|")
            if state == "started":
                job.status = PROCESSING
                job.stage = presets.PP_NAMES.get(name, name)
                job.speed, job.eta = 0.0, None
        elif line.startswith(presets.MARK_FILE):
            job.filepath = line[len(presets.MARK_FILE):].strip()
        else:
            self._tail = (self._tail + [line])[-25:]
            if line.startswith("ERROR:"):
                job.error = line[6:].strip()
            return
        self.changed.emit(job)

    # -- lifecycle --------------------------------------------------------
    def _error(self, err):
        if err == QProcess.FailedToStart:
            self.job.error = f"Could not start yt-dlp ({self.command[0]})."
            self._finalize(ERROR)

    def _finished(self, code, _status):
        if self._buf:
            self.handle_line(self._buf.decode("utf-8", "replace"))
            self._buf = b""
        if self._stopping_as:
            self._finalize(self._stopping_as)
        elif code == 0:
            if self.job.filepath and self._needs_convert():
                self._start_convert()
            else:
                self._finalize(DONE if self.job.filepath else SKIPPED)
        else:
            if not self.job.error:
                self.job.error = self._tail[-1] if self._tail else f"yt-dlp exited with code {code}"
            settings, _ = self._ctx()
            if settings and presets.is_cookie_error(self.job.error) and not getattr(settings, "_cookies_broken", False):
                # cookies unreadable: turn them off for this session and let the queue retry once
                settings._cookies_broken = True
                self.job.error = ""
                self.job.status = QUEUED  # _finalize ignores; manager reschedules
                self.job.stage = "Retrying without cookies"
                self.changed.emit(self.job)
                self.ended.emit(self.job)
                return
            self._finalize(ERROR)

    # -- codec conversion ------------------------------------------------------
    def _needs_convert(self) -> bool:
        settings, _ = self._ctx()
        job = self.job
        return bool(settings and settings.convert_codecs and job.vcodec in presets.CONVERTIBLE
                    and not presets.is_audio(job.preset) and not job.preset.startswith("fmt:")
                    and Path(job.filepath).suffix.lower() in (".mp4", ".mkv", ".webm"))

    def _start_convert(self):
        settings, tools = self._ctx()
        job = self.job
        if not (tools and tools.ffmpeg and tools.ffprobe):
            self._finalize(DONE)
            return
        job.convert_pending = True
        job.status, job.percent = PROCESSING, 0.0
        job.stage = f"Converting to {presets.VCODEC_SHORT.get(job.vcodec, job.vcodec)}"
        job.speed, job.eta = 0.0, None
        self.changed.emit(job)
        c = Converter(tools.ffmpeg, tools.ffprobe, self)
        c.progress.connect(self._convert_progress)
        c.done.connect(self._convert_done)
        self.converter = c
        c.start(job.filepath, job.vcodec, settings.encoder if settings else "cpu")

    def _convert_progress(self, pct, eta):
        self.job.percent, self.job.eta = pct, eta
        self.changed.emit(self.job)

    def _convert_done(self, ok: bool, msg: str):
        c, self.converter = self.converter, None
        if c is not None:
            self._tail = (self._tail + c.log.splitlines() + [msg])[-25:]
            c.deleteLater()
        self.job.convert_pending = False
        if ok:
            self._finalize(DONE)
        else:
            self.job.error = msg
            self._finalize(ERROR)

    def _finalize(self, status: str):
        job = self.job
        if job.status in FINISHED or job.status == PAUSED:
            self.ended.emit(job)
            return
        job.status = status
        job.speed, job.eta = 0.0, None
        job.stage = {DONE: "Done", SKIPPED: "Already downloaded", ERROR: "Failed",
                     CANCELED: "Canceled", PAUSED: "Paused"}.get(status, status)
        if status == DONE:
            job.percent = 100.0
            try:
                job.total_bytes = os.path.getsize(job.filepath)
            except OSError:
                pass
        if status in (DONE, SKIPPED, CANCELED):
            cleanup_temp(job, self.parent().settings if hasattr(self.parent(), "settings") else None)
        self.changed.emit(job)
        self.ended.emit(job)

    @property
    def log(self) -> str:
        return "\n".join(self._tail)


class QueueManager(QObject):
    """Holds all jobs, starts up to N at a time and persists unfinished work."""

    job_added = Signal(object)
    job_changed = Signal(object)
    job_removed = Signal(str)
    job_finished = Signal(object)

    def __init__(self, settings, tools_getter, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.tools_getter = tools_getter
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []
        self.runners: dict[str, JobRunner] = {}
        self.logs: dict[str, str] = {}
        self._save_timer = QTimer(self, singleShot=True, interval=800)
        self._save_timer.timeout.connect(self.save)

    @property
    def queue_file(self):
        return data_dir() / "queue.json"

    # -- public API -------------------------------------------------------
    def add(self, job: Job) -> None:
        self.jobs[job.id] = job
        self.order.append(job.id)
        self.job_added.emit(job)
        self._dirty()
        self.schedule()

    def remove(self, job_id: str) -> None:
        if job_id in self.runners:
            self.runners[job_id].stop(CANCELED)
        job = self.jobs.pop(job_id, None)
        if job and job.status in (PAUSED, ERROR, QUEUED):
            cleanup_temp(job, self.settings)
        if job_id in self.order:
            self.order.remove(job_id)
        self.job_removed.emit(job_id)
        self._dirty()

    def pause(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if not job:
            return
        if job_id in self.runners:
            self.runners[job_id].stop(PAUSED)
        elif job.status == QUEUED:
            job.status, job.stage = PAUSED, "Paused"
            self._changed(job)

    def resume(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job and job.status == PAUSED:
            job.status, job.stage = QUEUED, ""
            self._changed(job)
            self.schedule()

    def retry(self, job_id: str, force: bool = False) -> None:
        job = self.jobs.get(job_id)
        if job and job.status in FINISHED | {PAUSED}:
            job.reset()
            job.force = job.force or force
            self._changed(job)
            self.schedule()

    def cancel(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if not job:
            return
        if job_id in self.runners:
            self.runners[job_id].stop(CANCELED)
        elif job.status in (QUEUED, PAUSED):
            job.status, job.stage = CANCELED, "Canceled"
            self._changed(job)

    def pause_all(self):
        for jid in list(self.order):
            self.pause(jid)

    def resume_all(self):
        for jid in list(self.order):
            self.resume(jid)

    def clear_finished(self):
        for jid in [j for j in self.order if self.jobs[j].status in FINISHED]:
            self.remove(jid)

    def active_count(self) -> int:
        return len(self.runners)

    def log_for(self, job_id: str) -> str:
        r = self.runners.get(job_id)
        return r.log if r else self.logs.get(job_id, "")

    def shutdown(self):
        """App closing: stop running jobs as paused so they resume next launch."""
        for r in list(self.runners.values()):
            r.stop(PAUSED)
        self.save()

    # -- scheduling -------------------------------------------------------
    def schedule(self):
        tools = self.tools_getter()
        if not tools.ytdlp:
            return
        limit = max(1, int(self.settings.max_concurrent))
        for jid in self.order:
            if len(self.runners) >= limit:
                break
            job = self.jobs[jid]
            if job.status != QUEUED or jid in self.runners:
                continue
            cmd = presets.build_command(job, self.settings, tools)
            runner = JobRunner(job, cmd, self)
            runner.changed.connect(self._changed)
            runner.ended.connect(self._ended)
            self.runners[jid] = runner
            runner.start()

    def _changed(self, job):
        self.job_changed.emit(job)
        self._dirty()

    def _ended(self, job):
        runner = self.runners.pop(job.id, None)
        if runner:
            self.logs[job.id] = runner.log
            runner.deleteLater()
        if job.status in (DONE, ERROR):
            self.job_finished.emit(job)
        self._dirty()
        QTimer.singleShot(0, self.schedule)

    # -- persistence -------------------------------------------------------
    def _dirty(self):
        self._save_timer.start()

    def save(self):
        keep = []
        for jid in self.order:
            j = self.jobs[jid]
            if j.status in FINISHED:
                continue
            d = j.to_dict()
            if d["status"] in ACTIVE:
                d["status"], d["stage"] = PAUSED, "Paused"
            keep.append(d)
        try:
            self.queue_file.write_text(json.dumps(keep, indent=1, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def restore(self):
        try:
            items = json.loads(self.queue_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for d in items:
            job = Job.from_dict(d)
            if job.status in ACTIVE:
                job.status, job.stage = PAUSED, "Paused"
            self.jobs[job.id] = job
            self.order.append(job.id)
            self.job_added.emit(job)
        self.schedule()
