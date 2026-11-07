"""Runs conversion plans one after another with progress, cancel and GPU-to-CPU fallback."""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from . import transcode as T
from .binaries import child_env, kill_tree

WAITING, PROBING, READY, RUNNING, DONE, FAILED, CANCELED = (
    "waiting", "reading", "ready", "converting", "done", "failed", "canceled")
FINISHED = {DONE, FAILED, CANCELED}


@dataclass
class ConvertItem:
    path: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    info: T.MediaInfo | None = None
    options: T.Options | None = None
    status: str = WAITING
    stage: str = ""
    percent: float = 0.0
    eta: int | None = None
    output: str = ""
    outputs: list[str] = field(default_factory=list)
    error: str = ""
    size_before: int = 0
    size_after: int = 0
    log: str = ""

    @property
    def name(self) -> str:
        return Path(self.path).name


_GENERIC = ("conversion failed", "error while filtering", "nothing was written", "error opening output file",
            "error writing trailer", "error muxing a packet", "error submitting a packet", "could not open encoder before eof",
            "error while processing the decoded data", "error closing logfile", "task finished with error")
_KEYS = ("error", "invalid", "no such", "not supported", "unsupported", "could not", "failed", "unable",
         "only a single", "too ", "not found", "malformed", "permission denied", "no space", "incompatible")


import re as _re
_PROGRESS_KEY = _re.compile(r"^[a-z_0-9]+=\S*$")


def pick_error(tail: list[str]) -> str:
    """Return the most useful line of an ffmpeg failure, without the [muxer @ 0x...] prefix."""
    import re
    lines = [re.sub(r"^\[[^\]]*@ 0x[0-9a-f]+\]\s*", "", l).strip() for l in tail]
    lines = [l for l in lines if l and not any(g in l.lower() for g in _GENERIC)]
    hits = [l for l in lines if any(k in l.lower() for k in _KEYS)]
    if hits:
        return hits[0][:300]
    return (lines[-1] if lines else "")[:300]


class PlanRunner(QObject):
    progress = Signal(float, object)   # percent, eta
    finished = Signal(bool, str)       # ok, error message

    def __init__(self, ffmpeg: str, plan: T.Plan, parent=None):
        super().__init__(parent)
        self.ffmpeg, self.plan = ffmpeg, plan
        self.proc: QProcess | None = None
        self.i = -1
        self._buf = b""
        self._tail: list[str] = []
        self._t0 = 0.0
        self._stopped = False
        self._last_output = 0.0
        self._watchdog = QTimer(self, interval=5000)
        self._watchdog.timeout.connect(self._check_stall)

    STALL_SECONDS = 180

    def start(self):
        self._t0 = time.monotonic()
        self._last_output = self._t0
        self._watchdog.start()
        self._next()

    def _check_stall(self):
        """ffmpeg alive but silent for minutes = hung (seen with a crashed encoder thread)."""
        if self.proc and self.proc.state() != QProcess.NotRunning and \
                time.monotonic() - self._last_output > self.STALL_SECONDS:
            self._watchdog.stop()
            self._stopped = True
            kill_tree(int(self.proc.processId()))
            self.proc.kill()
            self.finished.emit(False, f"ffmpeg stopped responding for {self.STALL_SECONDS // 60} minutes and was stopped.")

    def stop(self):
        self._stopped = True
        self._watchdog.stop()
        if self.proc and self.proc.state() != QProcess.NotRunning:
            kill_tree(int(self.proc.processId()))
            self.proc.kill()
            self.proc.waitForFinished(3000)

    @property
    def log(self) -> str:
        return "\n".join(self._tail)

    def _next(self):
        self.i += 1
        if self.i >= len(self.plan.passes):
            self._watchdog.stop()
            self.finished.emit(True, "")
            return
        p = QProcess(self)
        env = p.processEnvironment()
        for k, v in child_env().items():
            env.insert(k, v)
        p.setProcessEnvironment(env)
        p.setProcessChannelMode(QProcess.MergedChannels)
        if self.plan.passlog:
            p.setWorkingDirectory(str(Path(self.plan.passlog).parent))  # stray 2-pass logs stay in the temp dir
        p.readyReadStandardOutput.connect(self._read)
        p.finished.connect(self._done)
        p.errorOccurred.connect(lambda e: e == QProcess.FailedToStart and self.finished.emit(False, "Could not start ffmpeg."))
        self.proc = p
        p.start(self.ffmpeg, self.plan.passes[self.i])

    def _read(self):
        if not self.proc:
            return
        self._buf += bytes(self.proc.readAllStandardOutput())
        self._last_output = time.monotonic()
        *lines, self._buf = self._buf.split(b"\n")
        n = len(self.plan.passes)
        for raw in lines:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith(("out_time_us=", "out_time_ms=")):
                try:
                    us = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                part = max(0.0, min(1.0, us / 1e6 / max(self.plan.duration, 0.1)))
                pct = min(99.5, (self.i + part) / n * 100)
                el = time.monotonic() - self._t0
                eta = int(el * (100 - pct) / pct) if pct > 1 else None
                self.progress.emit(pct, eta)
            elif line and not _PROGRESS_KEY.match(line):
                self._tail = (self._tail + [line])[-30:]

    def _done(self, code, status):
        if self._stopped:
            return
        if code != 0 or status == QProcess.CrashExit:
            self._watchdog.stop()
            err = pick_error(self._tail)
            if status == QProcess.CrashExit or code in (139, -11, 3221225477):
                err = "ffmpeg crashed. " + err
            self.finished.emit(False, err or f"ffmpeg exited with code {code}")
            return
        self._next()


class ConvertQueue(QObject):
    item_added = Signal(object)
    item_changed = Signal(object)
    item_removed = Signal(str)
    all_done = Signal()

    def __init__(self, tools_getter, parent=None):
        super().__init__(parent)
        self.tools_getter = tools_getter
        self.items: dict[str, ConvertItem] = {}
        self.order: list[str] = []
        self.current: str | None = None
        self.runner: PlanRunner | None = None
        self.plan: T.Plan | None = None
        self.tmpdir: str | None = None
        self._gpu_retry = False

    # -- items ---------------------------------------------------------------
    def add(self, path: str) -> ConvertItem | None:
        path = os.path.abspath(path)
        if any(self.items[i].path == path and self.items[i].status not in FINISHED for i in self.order):
            return None
        it = ConvertItem(path=path, status=PROBING, stage="Reading file")
        try:
            it.size_before = os.path.getsize(path)
        except OSError:
            pass
        self.items[it.id] = it
        self.order.append(it.id)
        self.item_added.emit(it)
        return it

    def set_info(self, item_id: str, info: T.MediaInfo):
        it = self.items.get(item_id)
        if not it:
            return
        it.info = info
        if info.error:
            it.status, it.stage, it.error = FAILED, "Can't read", info.error
        else:
            it.status, it.stage = READY, ""
        self.item_changed.emit(it)

    def remove(self, item_id: str):
        if item_id == self.current:
            self.cancel(item_id)
        self.items.pop(item_id, None)
        if item_id in self.order:
            self.order.remove(item_id)
        self.item_removed.emit(item_id)

    def clear_finished(self):
        for i in [i for i in self.order if self.items[i].status in FINISHED]:
            self.remove(i)

    # -- running ---------------------------------------------------------------
    def start(self, options: T.Options, only: list[str] | None = None):
        """Queue every ready item (or just `only`) with these options."""
        for i in self.order:
            it = self.items[i]
            if only is not None and i not in only:
                continue
            if it.status in (READY, FAILED, CANCELED, DONE) and it.info and not it.info.error:
                if it.status == DONE and only is None:
                    continue
                it.options = replace(options)
                it.status, it.stage, it.percent, it.error, it.eta = WAITING, "Waiting", 0.0, "", None
                self.item_changed.emit(it)
        self._kick()

    def cancel(self, item_id: str):
        it = self.items.get(item_id)
        if not it:
            return
        if item_id == self.current and self.runner:
            self.runner.stop()
            self._cleanup(failed=True)
            it.status, it.stage, it.eta = CANCELED, "Canceled", None
            self.item_changed.emit(it)
            self.current, self.runner = None, None
            QTimer.singleShot(0, self._kick)
        elif it.status == WAITING and it.options:
            it.status, it.stage = CANCELED, "Canceled"
            self.item_changed.emit(it)

    def cancel_all(self):
        for i in list(self.order):
            self.cancel(i)

    def busy(self) -> bool:
        return self.current is not None

    def _kick(self):
        if self.current:
            return
        nxt = next((i for i in self.order if self.items[i].status == WAITING and self.items[i].options), None)
        if not nxt:
            self.all_done.emit()
            return
        self._run(self.items[nxt])

    def _run(self, it: ConvertItem, gpu_retry: bool = False):
        tools = self.tools_getter()
        if not tools.ffmpeg:
            it.status, it.stage, it.error = FAILED, "Failed", "ffmpeg is missing. Settings → Install / repair components."
            self.item_changed.emit(it)
            return
        self.tmpdir = tempfile.mkdtemp(prefix="zerolink-")
        try:
            self.plan = T.build_plan(it.info, it.options, self.tmpdir)
        except T.PlanError as e:
            shutil.rmtree(self.tmpdir, ignore_errors=True)
            it.status, it.stage, it.error = FAILED, "Can't convert", str(e)
            self.item_changed.emit(it)
            QTimer.singleShot(0, self._kick)
            return
        self._gpu_retry = gpu_retry
        self.current = it.id
        it.status, it.stage, it.percent = RUNNING, self.plan.description, 0.0
        it.output = self.plan.output
        self.item_changed.emit(it)
        r = PlanRunner(tools.ffmpeg, self.plan, self)
        r.progress.connect(lambda p, e, it=it: self._progress(it, p, e))
        r.finished.connect(lambda ok, msg, it=it: self._finished(it, ok, msg))
        self.runner = r
        r.start()

    def _progress(self, it, pct, eta):
        it.percent, it.eta = pct, eta
        self.item_changed.emit(it)

    def _finished(self, it: ConvertItem, ok: bool, msg: str):
        r, plan = self.runner, self.plan
        it.log = r.log if r else ""
        if r:
            r.deleteLater()
        self.runner = None
        if ok:
            try:
                if plan.temp_output != plan.output:
                    os.replace(plan.temp_output, plan.output)
                it.outputs = [plan.output] + plan.extra_outputs
                it.size_after = sum(os.path.getsize(p) for p in it.outputs if os.path.exists(p))
                it.status, it.stage, it.percent, it.eta = DONE, "Done", 100.0, None
            except OSError as e:
                it.status, it.stage, it.error = FAILED, "Failed", f"Couldn't save the result: {e}"
            self._cleanup(failed=it.status != DONE)
        else:
            self._cleanup(failed=True)
            if plan and plan.uses_gpu and not self._gpu_retry:
                # GPU encoder missing or unsupported: try once more on the CPU
                self.current = None
                it.options = replace(it.options, encoder="cpu")
                it.stage = "GPU encoder failed, retrying on CPU"
                self.item_changed.emit(it)
                self._run(it, gpu_retry=True)
                return
            it.status, it.stage, it.error, it.eta = FAILED, "Failed", msg, None
        self.current = None
        self.item_changed.emit(it)
        QTimer.singleShot(0, self._kick)

    def _cleanup(self, failed: bool):
        plan = self.plan
        if plan and failed:
            for p in [plan.temp_output] + ([plan.output] + plan.extra_outputs if plan.temp_output == plan.output else []):
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass
        if self.tmpdir:
            shutil.rmtree(self.tmpdir, ignore_errors=True)
            self.tmpdir = None
        # x264/vpx two-pass logs land next to passlog prefix (inside tmpdir) - removed above
