"""Re-encode a finished download to H.264 or H.265 with ffmpeg, reporting progress."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

from .binaries import child_env, kill_tree

# (encoder, quality args) per hardware choice and target codec
ENCODERS = {
    "cpu": {
        "h264": ["libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"],
        "h265": ["libx265", "-preset", "medium", "-crf", "24", "-x265-params", "log-level=error"],
    },
    "nvidia": {
        "h264": ["h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0", "-pix_fmt", "yuv420p"],
        "h265": ["hevc_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "25", "-b:v", "0"],
    },
    "intel": {
        "h264": ["h264_qsv", "-global_quality", "21"],
        "h265": ["hevc_qsv", "-global_quality", "25"],
    },
    "amd": {
        "h264": ["h264_amf", "-rc", "cqp", "-qp_i", "21", "-qp_p", "21"],
        "h265": ["hevc_amf", "-rc", "cqp", "-qp_i", "25", "-qp_p", "25"],
    },
}
ENCODER_LABELS = [
    ("cpu", "CPU (best quality, slowest)"),
    ("nvidia", "NVIDIA GPU (NVENC)"),
    ("intel", "Intel GPU (Quick Sync)"),
    ("amd", "AMD GPU (AMF)"),
]
PROBE_CODEC = {"h264": "h264", "h265": "hevc"}
LABEL = {"h264": "H.264", "h265": "H.265"}


def _env(proc: QProcess):
    env = proc.processEnvironment()
    for k, v in child_env().items():
        env.insert(k, v)
    proc.setProcessEnvironment(env)


class Converter(QObject):
    progress = Signal(float, object)  # percent, eta seconds or None
    done = Signal(bool, str)          # ok, message

    def __init__(self, ffmpeg: str, ffprobe: str, parent=None):
        super().__init__(parent)
        self.ffmpeg, self.ffprobe = ffmpeg, ffprobe
        self.proc: QProcess | None = None
        self.path = ""
        self.target = ""
        self.encoder = "cpu"
        self.duration = 0.0
        self._tmp = ""
        self._buf = b""
        self._tail: list[str] = []
        self._started = 0.0
        self._stopped = False

    # -- public -------------------------------------------------------------
    def start(self, path: str, target: str, encoder: str = "cpu"):
        self.path, self.target, self.encoder = path, target, encoder if encoder in ENCODERS else "cpu"
        p = QProcess(self)
        _env(p)
        p.finished.connect(self._probed)
        p.errorOccurred.connect(lambda e: e == QProcess.FailedToStart and self._fail("Could not start ffprobe."))
        self.proc = p
        p.start(self.ffprobe, ["-v", "error", "-select_streams", "V:0",
                               "-show_entries", "stream=codec_name:format=duration",
                               "-of", "json", path])

    def stop(self):
        self._stopped = True
        if self.proc and self.proc.state() != QProcess.NotRunning:
            kill_tree(int(self.proc.processId()))
            self.proc.kill()
            self.proc.waitForFinished(3000)
        self._cleanup_tmp()

    @property
    def log(self) -> str:
        return "\n".join(self._tail)

    # -- steps --------------------------------------------------------------
    def _probed(self, code, _st):
        p, self.proc = self.proc, None
        if self._stopped or p is None:
            return
        out = bytes(p.readAllStandardOutput()).decode("utf-8", "replace")
        p.deleteLater()
        codec = ""
        try:
            data = json.loads(out or "{}")
            streams = data.get("streams") or []
            codec = (streams[0].get("codec_name") if streams else "") or ""
            self.duration = float((data.get("format") or {}).get("duration") or 0)
        except (ValueError, TypeError):
            pass
        if codec == PROBE_CODEC[self.target]:
            self.done.emit(True, f"Already {LABEL[self.target]}, no conversion needed")
            return
        self._encode()

    def _encode(self):
        src = Path(self.path)
        self._tmp = str(src.with_name(src.stem + ".converting" + src.suffix))
        enc = ENCODERS[self.encoder][self.target]
        args = ["-hide_banner", "-nostdin", "-y", "-i", self.path,
                "-map", "0", "-dn", "-map_metadata", "0",
                "-c", "copy", "-c:V", enc[0], *enc[1:]]
        if src.suffix.lower() == ".mp4":
            if self.target == "h265":
                args += ["-tag:V", "hvc1"]  # lets Windows/Apple players recognise HEVC in MP4
            args += ["-movflags", "+faststart"]
        args += ["-progress", "pipe:1", "-nostats", self._tmp]
        p = QProcess(self)
        _env(p)
        p.setProcessChannelMode(QProcess.MergedChannels)
        p.readyReadStandardOutput.connect(self._read)
        p.finished.connect(self._encoded)
        p.errorOccurred.connect(lambda e: e == QProcess.FailedToStart and self._fail("Could not start ffmpeg."))
        self.proc = p
        self._started = time.monotonic()
        self.progress.emit(0.0, None)
        p.start(self.ffmpeg, args)

    def _read(self):
        if not self.proc:
            return
        self._buf += bytes(self.proc.readAllStandardOutput())
        *lines, self._buf = self._buf.split(b"\n")
        for raw in lines:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith(("out_time_us=", "out_time_ms=")) and self.duration:
                try:
                    us = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                pct = max(0.0, min(99.9, us / 1e6 * 100 / self.duration))
                elapsed = time.monotonic() - self._started
                eta = int(elapsed * (100 - pct) / pct) if pct > 1 else None
                self.progress.emit(pct, eta)
            elif line and "=" not in line:
                self._tail = (self._tail + [line])[-20:]

    def _encoded(self, code, _st):
        p, self.proc = self.proc, None
        if p:
            p.deleteLater()
        if self._stopped:
            return
        if code == 0 and os.path.exists(self._tmp):
            try:
                os.replace(self._tmp, self.path)
            except OSError as e:
                self._fail(f"Converted, but could not replace the original: {e}")
                return
            self.done.emit(True, f"Converted to {LABEL[self.target]}")
            return
        self._cleanup_tmp()
        if self.encoder != "cpu":
            # GPU encoder missing or unsupported: fall back to the CPU once
            self._tail.append(f"{self.encoder} encoder failed, retrying on CPU")
            self.encoder = "cpu"
            self._encode()
            return
        err = next((l for l in reversed(self._tail) if l), f"ffmpeg exited with code {code}")
        self._fail(f"Conversion to {LABEL[self.target]} failed: {err}")

    def _fail(self, msg: str):
        self._cleanup_tmp()
        self.done.emit(False, msg)

    def _cleanup_tmp(self):
        if self._tmp and os.path.exists(self._tmp):
            try:
                os.remove(self._tmp)
            except OSError:
                pass
