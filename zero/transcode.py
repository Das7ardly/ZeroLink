"""Convert and compress local media files with ffmpeg.

Pure logic only (no Qt): probing, output presets, the ffmpeg command plan and size estimates.
The runner that executes a plan lives in convert_queue.py.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path

from .binaries import NO_WINDOW, child_env

# --------------------------------------------------------------------------------------
# Output targets
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Target:
    key: str
    label: str
    kind: str          # video | audio | anim | subs
    ext: str


TARGETS: list[Target] = [
    Target("mp4", "MP4 video", "video", ".mp4"),
    Target("mkv", "MKV video", "video", ".mkv"),
    Target("webm", "WebM video", "video", ".webm"),
    Target("mov", "MOV video", "video", ".mov"),
    Target("avi", "AVI video", "video", ".avi"),
    Target("ts", "TS video (TV / set-top boxes)", "video", ".ts"),
    Target("mp3", "MP3 audio", "audio", ".mp3"),
    Target("m4a", "M4A audio (AAC)", "audio", ".m4a"),
    Target("opus", "Opus audio", "audio", ".opus"),
    Target("ogg", "OGG Vorbis audio", "audio", ".ogg"),
    Target("flac", "FLAC audio (lossless)", "audio", ".flac"),
    Target("alac", "ALAC audio (lossless, Apple)", "audio", ".m4a"),
    Target("wav", "WAV audio (uncompressed)", "audio", ".wav"),
    Target("ac3", "AC3 audio (Dolby Digital)", "audio", ".ac3"),
    Target("gif", "GIF animation", "anim", ".gif"),
    Target("webp", "Animated WebP", "anim", ".webp"),
    Target("srt", "Subtitles → SRT", "subs", ".srt"),
    Target("ass", "Subtitles → ASS", "subs", ".ass"),
    Target("vtt", "Subtitles → VTT", "subs", ".vtt"),
]
TARGET = {t.key: t for t in TARGETS}

VCODECS = [
    ("auto", "Auto (keep original if it fits, else H.264)"),
    ("copy", "Keep original (fastest, no quality loss)"),
    ("h264", "H.264 (plays everywhere)"),
    ("h265", "H.265 / HEVC (smaller)"),
    ("av1", "AV1 (smallest)"),
    ("vp9", "VP9"),
]
VCODEC_LABEL = {"copy": "original", "h264": "H.264", "h265": "H.265", "av1": "AV1", "vp9": "VP9"}

QUALITIES = [("smaller", "Smaller file"), ("balanced", "Balanced"), ("best", "Best quality")]

SIZE_PRESETS = [  # (key, label, MB)
    ("discord", "Discord (10 MB)", 10),
    ("whatsapp", "WhatsApp (16 MB)", 16),
    ("email", "Email (25 MB)", 25),
    ("viber", "Viber (200 MB)", 200),
    ("custom", "Custom size…", 0),
]

HEIGHTS = [(0, "Keep original"), (2160, "4K (2160p)"), (1440, "1440p"), (1080, "1080p"),
           (720, "720p"), (480, "480p"), (360, "360p")]

AUDIO_KBPS = [(0, "Auto"), (320, "320 kbps"), (256, "256 kbps"), (192, "192 kbps"),
              (160, "160 kbps"), (128, "128 kbps"), (96, "96 kbps"), (64, "64 kbps")]

# what each container can hold without re-encoding
COPY_VIDEO = {
    "mp4": {"h264", "hevc", "av1", "vp9", "mpeg4"},
    "mov": {"h264", "hevc", "mpeg4", "prores", "mjpeg"},
    "mkv": None,  # anything
    "webm": {"vp8", "vp9", "av1"},
    "avi": {"h264", "mpeg4", "mjpeg", "msmpeg4v3"},
    "ts": {"h264", "hevc", "mpeg2video"},
}
COPY_AUDIO = {
    "mp4": {"aac", "mp3", "ac3", "eac3", "alac"},
    "mov": {"aac", "mp3", "ac3", "alac", "pcm_s16le"},
    "mkv": None,
    "webm": {"opus", "vorbis"},
    "avi": {"mp3", "ac3", "pcm_s16le"},
    "ts": {"aac", "mp3", "ac3", "eac3"},
}
TEXT_SUBS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}
DEFAULT_VCODEC = {"mp4": "h264", "mov": "h264", "mkv": "h264", "webm": "vp9", "avi": "h264", "ts": "h264"}
ALLOWED_VCODECS = {"webm": {"vp9", "av1"}, "avi": {"h264"}, "ts": {"h264", "h265"}, "mov": {"h264", "h265"}}

CRF = {  # smaller, balanced, best
    "h264": (28, 23, 19), "h265": (30, 26, 22), "av1": (40, 34, 28), "vp9": (38, 32, 26),
}
GPU = {
    "nvidia": {"h264": "h264_nvenc", "h265": "hevc_nvenc", "av1": "av1_nvenc"},
    "intel": {"h264": "h264_qsv", "h265": "hevc_qsv", "av1": "av1_qsv"},
    "amd": {"h264": "h264_amf", "h265": "hevc_amf", "av1": "av1_amf"},
}


# --------------------------------------------------------------------------------------
# Probe
# --------------------------------------------------------------------------------------

@dataclass
class Stream:
    index: int
    kind: str          # video | audio | subtitle
    codec: str
    lang: str = ""
    title: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    channels: int = 0
    bitrate: int = 0   # bits/s, 0 if unknown
    cover: bool = False


@dataclass
class MediaInfo:
    path: str
    duration: float = 0.0
    size: int = 0
    bitrate: int = 0
    format: str = ""
    streams: list[Stream] = field(default_factory=list)
    error: str = ""

    @property
    def video(self) -> Stream | None:
        return next((s for s in self.streams if s.kind == "video" and not s.cover), None)

    @property
    def audio(self) -> list[Stream]:
        return [s for s in self.streams if s.kind == "audio"]

    @property
    def subs(self) -> list[Stream]:
        return [s for s in self.streams if s.kind == "subtitle"]

    def summary(self) -> str:
        parts = []
        v = self.video
        if v:
            parts.append(f"{v.width}×{v.height} {v.codec.upper()}" + (f" {v.fps:.0f}fps" if v.fps else ""))
        if self.audio:
            a = self.audio[0]
            parts.append(f"{a.codec.upper()}" + (f" {a.channels}ch" if a.channels else "")
                         + (f" +{len(self.audio) - 1}" if len(self.audio) > 1 else ""))
        if self.subs:
            parts.append(f"{len(self.subs)} sub")
        if self.duration:
            m, s = divmod(int(self.duration), 60)
            h, m = divmod(m, 60)
            parts.append(f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}")
        return " · ".join(parts)


def _fps(rate: str | None) -> float:
    try:
        n, d = (rate or "0/1").split("/")
        return float(n) / float(d) if float(d) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: str, ffprobe: str) -> MediaInfo:
    info = MediaInfo(path=path)
    try:
        p = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                           capture_output=True, timeout=60, env=child_env(), creationflags=NO_WINDOW)
        data = json.loads(p.stdout.decode("utf-8", "replace") or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        info.error = str(e)
        return info
    fmt = data.get("format") or {}
    if not fmt and not data.get("streams"):
        info.error = (p.stderr.decode("utf-8", "replace").strip().splitlines() or ["Not a media file ffmpeg can read."])[-1]
        return info
    info.duration = float(fmt.get("duration") or 0)
    info.size = int(fmt.get("size") or (os.path.getsize(path) if os.path.exists(path) else 0))
    info.bitrate = int(fmt.get("bit_rate") or 0)
    info.format = fmt.get("format_name", "")
    for s in data.get("streams") or []:
        kind = s.get("codec_type")
        if kind not in ("video", "audio", "subtitle"):
            continue
        tags = s.get("tags") or {}
        disp = s.get("disposition") or {}
        info.streams.append(Stream(
            index=int(s.get("index", 0)), kind=kind, codec=(s.get("codec_name") or "").lower(),
            lang=tags.get("language", ""), title=tags.get("title", ""),
            width=int(s.get("width") or 0), height=int(s.get("height") or 0),
            fps=_fps(s.get("avg_frame_rate") or s.get("r_frame_rate")),
            channels=int(s.get("channels") or 0), bitrate=int(s.get("bit_rate") or 0),
            cover=bool(disp.get("attached_pic")),
        ))
        if kind == "video" and not info.duration:
            info.duration = float(s.get("duration") or 0)
    if not info.streams:
        info.error = "No audio or video found in this file."
    return info


# --------------------------------------------------------------------------------------
# Options and plan
# --------------------------------------------------------------------------------------

@dataclass
class Options:
    target: str = "mp4"
    vcodec: str = "auto"
    quality: str = "balanced"
    size_mode: str = "quality"     # quality | target
    target_mb: float = 25.0
    height: int = 0
    audio_kbps: int = 0
    trim_start: float | None = None
    trim_end: float | None = None
    normalize: bool = False
    keep_subs: bool = True
    out_dir: str = ""              # "" = next to the source
    encoder: str = "cpu"           # cpu | nvidia | intel | amd
    anim_fps: int = 0              # 0 = by quality
    anim_width: int = 0


@dataclass
class Plan:
    passes: list[list[str]]        # ffmpeg argument lists (without the ffmpeg path)
    output: str                    # final file (written to temp first)
    temp_output: str
    duration: float                # seconds of output, for progress
    description: str               # human summary, e.g. "H.264 1080p, 2-pass 25 MB"
    extra_outputs: list[str] = field(default_factory=list)
    passlog: str = ""
    uses_gpu: bool = False


class PlanError(Exception):
    pass


def out_duration(info: MediaInfo, o: Options) -> float:
    start = o.trim_start or 0.0
    end = o.trim_end if o.trim_end and o.trim_end > start else info.duration
    return max(0.0, (end or 0) - start)


def output_path(info: MediaInfo, o: Options, suffix: str = "") -> str:
    src = Path(info.path)
    folder = Path(o.out_dir) if o.out_dir else src.parent
    ext = TARGET[o.target].ext
    stem = src.stem + suffix
    cand = folder / f"{stem}{ext}"
    n = 1
    while cand.exists() or cand.resolve() == src.resolve():
        cand = folder / f"{stem} (ZeroLink{'' if n == 1 else ' ' + str(n)}){ext}"
        n += 1
    return str(cand)


def resolve_vcodec(info: MediaInfo, o: Options) -> str:
    """'copy' or a codec key. 'auto' keeps the original when nothing forces a re-encode."""
    t = o.target
    v = info.video
    if not v:
        return "copy"
    allowed = COPY_VIDEO.get(t)
    can_copy = allowed is None or v.codec in allowed
    must_encode = o.size_mode == "target" or (o.height and v.height and o.height < v.height)
    if o.vcodec == "copy":
        if not can_copy:
            raise PlanError(f"{v.codec.upper()} video can't go into {t.upper()} without re-encoding. "
                            f"Pick a codec instead of 'Keep original'.")
        if must_encode:
            raise PlanError("Resizing or a target size needs re-encoding. Pick a codec instead of 'Keep original'.")
        return "copy"
    if o.vcodec == "auto":
        if can_copy and not must_encode and o.quality == "balanced":
            return "copy"
        return DEFAULT_VCODEC[t]
    if t in ALLOWED_VCODECS and o.vcodec not in ALLOWED_VCODECS[t]:
        raise PlanError(f"{TARGET[t].label} can't hold {VCODEC_LABEL[o.vcodec]}. "
                        f"Use {' or '.join(VCODEC_LABEL[c] for c in sorted(ALLOWED_VCODECS[t]))}.")
    return o.vcodec


def _scale_height(info: MediaInfo, o: Options, video_kbps: float | None, vcodec: str) -> int:
    v = info.video
    if not v or not v.height:
        return 0
    want = o.height if o.height and o.height < v.height else 0
    if video_kbps is not None and not o.height:
        # target size: drop resolution when the bitrate can't carry it
        k = video_kbps / (0.7 if vcodec in ("h265", "av1", "vp9") else 1.0)
        auto = 360 if k < 400 else 480 if k < 800 else 720 if k < 1600 else 1080 if k < 3500 else 0
        if auto and auto < v.height:
            want = auto
    return want


def _video_encode_args(vcodec: str, o: Options, kbps: float | None, pass_no: int, passlog: str,
                       container: str) -> tuple[list[str], bool]:
    """Encoder arguments; returns (args, uses_gpu)."""
    qi = {"smaller": 0, "balanced": 1, "best": 2}[o.quality]
    gpu = GPU.get(o.encoder, {}).get(vcodec) if kbps is None else None  # target size: CPU two-pass
    if gpu:
        q = CRF[vcodec][qi] + (0 if vcodec != "av1" else -6)
        if o.encoder == "nvidia":
            args = ["-c:v", gpu, "-preset", "p5", "-rc", "vbr", "-cq", str(q), "-b:v", "0"]
        elif o.encoder == "intel":
            args = ["-c:v", gpu, "-global_quality", str(q)]
        else:
            args = ["-c:v", gpu, "-rc", "cqp", "-qp_i", str(q), "-qp_p", str(q)]
        if vcodec == "h264":
            args += ["-pix_fmt", "yuv420p"]
        if vcodec == "h265" and container in ("mp4", "mov"):
            args += ["-tag:v", "hvc1"]
        return args, True

    if vcodec == "h264":
        args = ["-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p"]
        # mbtree off in 2-pass: some x264 builds crash in pass 2 with it ("MV out of thread range")
        args += ["-b:v", f"{int(kbps)}k", "-pass", str(pass_no), "-passlogfile", passlog,
                 "-x264-params", "mbtree=0"] if kbps else ["-crf", str(CRF["h264"][qi])]
    elif vcodec == "h265":
        args = ["-c:v", "libx265", "-preset", "medium"]
        if kbps:
            args += ["-b:v", f"{int(kbps)}k", "-x265-params", f"pass={pass_no}:stats={passlog}.log:log-level=error"]
        else:
            args += ["-crf", str(CRF["h265"][qi]), "-x265-params", "log-level=error"]
        if container in ("mp4", "mov"):
            args += ["-tag:v", "hvc1"]
    elif vcodec == "vp9":
        args = ["-c:v", "libvpx-vp9", "-row-mt", "1", "-deadline", "good", "-cpu-used", "4"]
        args += ["-b:v", f"{int(kbps)}k", "-pass", str(pass_no), "-passlogfile", passlog] if kbps else ["-crf", str(CRF["vp9"][qi]), "-b:v", "0"]
    elif vcodec == "av1":
        args = ["-c:v", "libsvtav1", "-preset", "8"]
        args += ["-b:v", f"{int(kbps)}k", "-svtav1-params", "rc=1"] if kbps else ["-crf", str(CRF["av1"][qi])]
    else:
        raise PlanError(f"Unknown video codec {vcodec}")
    return args, False


def _audio_codec_for(target: str) -> tuple[str, int]:
    """(encoder, default kbps) for audio-only targets and video containers."""
    return {
        "mp3": ("libmp3lame", 192), "m4a": ("aac", 192), "opus": ("libopus", 128), "ogg": ("libvorbis", 192),
        "flac": ("flac", 0), "alac": ("alac", 0), "wav": ("pcm_s16le", 0), "ac3": ("ac3", 448),
        "mp4": ("aac", 160), "mov": ("aac", 160), "mkv": ("aac", 160), "ts": ("aac", 160),
        "avi": ("libmp3lame", 192), "webm": ("libopus", 128),
    }[target]


def _already_fits(info: MediaInfo, o: Options) -> bool:
    """Target size requested but the (trimmed) file is already smaller: don't blow it up."""
    if o.size_mode != "target" or not info.size:
        return False
    frac = out_duration(info, o) / info.duration if info.duration else 1.0
    return info.size * frac <= o.target_mb * 1024 * 1024 * 0.98 or not info.video


def build_plan(info: MediaInfo, o: Options, temp_dir: str) -> Plan:
    if info.error:
        raise PlanError(info.error)
    if TARGET[o.target].kind == "video" and _already_fits(info, o):
        o = replace(o, size_mode="quality", quality="balanced", vcodec="auto")
        plan = build_plan(info, o, temp_dir)
        plan.description = f"Already under {o.target_mb:g} MB: " + plan.description[0].lower() + plan.description[1:]
        return plan
    t = TARGET[o.target]
    dur = out_duration(info, o)
    if dur <= 0 and t.kind != "subs":
        raise PlanError("The trim range is empty.")
    out = output_path(info, o)
    tmp = str(Path(out).with_name(Path(out).stem + ".zl-part" + Path(out).suffix))
    passlog = str(Path(temp_dir) / "pass")

    pre = ["-hide_banner", "-nostdin", "-y"]
    if o.trim_start:
        pre += ["-ss", f"{o.trim_start:.3f}"]
    pre += ["-i", info.path]
    if o.trim_end and o.trim_end > (o.trim_start or 0):
        pre += ["-t", f"{dur:.3f}"]
    post = ["-progress", "pipe:1", "-nostats"]
    af = ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"] if o.normalize else []

    # ---- subtitles only
    if t.kind == "subs":
        subs = info.subs
        if not subs:
            raise PlanError("This file has no subtitle tracks.")
        text = [s for s in subs if s.codec in TEXT_SUBS]
        if not text:
            raise PlanError("These subtitles are pictures (PGS/VobSub). Turning them into text needs OCR.")
        codec = {"srt": "srt", "ass": "ass", "vtt": "webvtt"}[o.target]
        passes, outs = [], []
        for i, s in enumerate(text):
            tag = ".".join(x for x in (s.lang, str(i + 1) if len(text) > 1 else "") if x)
            o2 = output_path(info, o, suffix=f".{tag}" if tag else "")
            outs.append(o2)
            passes.append(pre + ["-map", f"0:{s.index}", "-c:s", codec] + post + [o2])
        return Plan(passes, outs[0], outs[0], max(dur, 1), f"{len(outs)} subtitle file(s) as {o.target.upper()}",
                    extra_outputs=outs[1:])

    # ---- audio only
    if t.kind == "audio":
        if not info.audio:
            raise PlanError("This file has no audio.")
        enc, default_kbps = _audio_codec_for(o.target)
        a = info.audio[0]
        if enc == "ac3" and (a.channels or 2) <= 2:
            default_kbps = 192
        kbps = o.audio_kbps or default_kbps
        copy = (not o.normalize and not o.trim_start and not o.trim_end
                and ((o.target == "m4a" and a.codec == "aac") or (o.target == "mp3" and a.codec == "mp3")
                     or (o.target == "opus" and a.codec == "opus") or (o.target == "flac" and a.codec == "flac"))
                and not o.audio_kbps)
        args = pre + ["-map", f"0:{a.index}", "-vn", "-map_metadata", "0"]
        if copy:
            args += ["-c:a", "copy"]
            desc = f"{o.target.upper()} (audio copied, no quality loss)"
        else:
            args += ["-c:a", enc] + (["-b:a", f"{kbps}k"] if kbps and enc not in ("flac", "alac", "pcm_s16le") else [])
            if enc == "flac":
                args += ["-compression_level", "8"]
            args += af
            desc = f"{o.target.upper()}" + (f" {kbps} kbps" if kbps and enc not in ("flac", "alac", "pcm_s16le") else "")
        if o.target in ("m4a", "alac"):
            args += ["-movflags", "+faststart"]
        return Plan([args + post + [tmp]], out, tmp, dur, desc)

    # ---- animated image
    if t.kind == "anim":
        if not info.video:
            raise PlanError("This file has no video.")
        qi = {"smaller": 0, "balanced": 1, "best": 2}[o.quality]
        fps = o.anim_fps or (10, 15, 20)[qi]
        width = o.anim_width or (360, 480, 720)[qi]
        width = min(width, info.video.width or width)
        if o.target == "gif":
            vf = (f"[0:{info.video.index}]fps={fps},scale={width}:-1:flags=lanczos,split[a][b];"
                  f"[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5[out]")
            args = pre + ["-filter_complex", vf, "-map", "[out]", "-an", "-loop", "0"]
        else:
            args = pre + ["-map", f"0:{info.video.index}", "-an", "-vf", f"fps={fps},scale={width}:-1:flags=lanczos",
                          "-c:v", "libwebp_anim", "-loop", "0", "-q:v", str((50, 70, 85)[qi]), "-compression_level", "4"]
        return Plan([args + post + [tmp]], out, tmp, dur, f"{o.target.upper()} {width}px {fps} fps")

    # ---- video
    if not info.video and not info.audio:
        raise PlanError("Nothing to convert.")
    container = o.target
    vcodec = resolve_vcodec(info, o) if info.video else "copy"
    video_kbps = None
    audio_target_kbps = None
    if o.size_mode == "target":
        total_kbps = o.target_mb * 8 * 1024 * 1024 / max(dur, 0.1) / 1000 * 0.96
        audio_target_kbps = 128 if total_kbps >= 1000 else 96 if total_kbps >= 300 else 64
        if not info.audio:
            audio_target_kbps = 0
        video_kbps = total_kbps - audio_target_kbps * max(1, len(info.audio))
        if video_kbps < 60:
            min_mb = dur * (60 + audio_target_kbps * max(1, len(info.audio))) * 1000 / 8 / 1024 / 1024 / 0.96
            raise PlanError(f"{o.target_mb:g} MB is too small for {dur / 60:.1f} minutes of video. "
                            f"Trim it or pick at least {min_mb:.0f} MB.")
    height = _scale_height(info, o, video_kbps, vcodec) if vcodec != "copy" else 0

    maps = ["-map", f"0:{info.video.index}"] if info.video else []
    for a in info.audio:
        maps += ["-map", f"0:{a.index}"]
    sub_maps, sub_codec = [], []
    if o.keep_subs and info.subs and container not in ("avi", "ts"):
        keep = [s for s in info.subs if container == "mkv" or s.codec in TEXT_SUBS]
        for s in keep:
            sub_maps += ["-map", f"0:{s.index}"]
        if keep:
            sub_codec = ["-c:s", {"mkv": "copy", "mp4": "mov_text", "mov": "mov_text", "webm": "webvtt"}[container]]

    # audio codec per track
    acopy_ok = COPY_AUDIO.get(container)
    a_args = []
    enc, default_kbps = _audio_codec_for(container)
    for i, a in enumerate(info.audio):
        copy = (not o.normalize and o.size_mode != "target" and not o.audio_kbps
                and (acopy_ok is None or a.codec in acopy_ok))
        if copy:
            a_args += [f"-c:a:{i}", "copy"]
        else:
            kb = audio_target_kbps or o.audio_kbps or default_kbps
            a_args += [f"-c:a:{i}", enc, f"-b:a:{i}", f"{kb}k"]
    if o.normalize:
        a_args += af

    vf = ["-vf", f"scale=-2:{height}"] if height else []
    common_tail = ["-map_metadata", "0"] + (["-map_chapters", "0"] if container not in ("avi", "ts") else [])
    if container in ("mp4", "mov"):
        common_tail += ["-movflags", "+faststart"]

    uses_gpu = False
    if vcodec == "copy":
        v_args = ["-c:v", "copy"] if info.video else []
        if info.video and container == "avi" and info.video.codec in ("h264", "hevc"):
            v_args += ["-bsf:v", f"{info.video.codec}_mp4toannexb"]
        passes = [pre + maps + sub_maps + v_args + a_args + sub_codec + common_tail + post + [tmp]]
        what = "Remux: codecs kept, no quality loss" if info.video else "Audio"
    elif video_kbps and vcodec in ("h264", "h265", "vp9"):
        p1, _ = _video_encode_args(vcodec, o, video_kbps, 1, passlog, container)
        p2, _ = _video_encode_args(vcodec, o, video_kbps, 2, passlog, container)
        null = "NUL" if os.name == "nt" else "/dev/null"
        first = pre + ["-map", f"0:{info.video.index}"] + vf + p1 + ["-an", "-sn", "-f", "null"] + post + [null]
        second = pre + maps + sub_maps + vf + p2 + a_args + sub_codec + common_tail + post + [tmp]
        passes = [first, second]
        what = f"{VCODEC_LABEL[vcodec]}{f' {height}p' if height else ''}, 2-pass to {o.target_mb:g} MB"
    else:
        v_args, uses_gpu = _video_encode_args(vcodec, o, video_kbps, 1, passlog, container)
        passes = [pre + maps + sub_maps + vf + v_args + a_args + sub_codec + common_tail + post + [tmp]]
        what = f"{VCODEC_LABEL[vcodec]}{f' {height}p' if height else ''}" + (
            f", about {o.target_mb:g} MB" if video_kbps else f", {dict(QUALITIES)[o.quality].lower()}")
    return Plan(passes, out, tmp, dur, what, passlog=passlog, uses_gpu=uses_gpu)


# --------------------------------------------------------------------------------------
# Size estimate (shown before converting)
# --------------------------------------------------------------------------------------

_BPP = {"h264": 0.07, "h265": 0.042, "av1": 0.05, "vp9": 0.045}
_QMUL = {"smaller": 0.55, "balanced": 1.0, "best": 1.8}


def estimate(info: MediaInfo, o: Options) -> tuple[int, str]:
    """(bytes, short note). Rough for re-encodes, exact-ish for target size and lossless copies."""
    if info.error:
        return 0, ""
    t = TARGET[o.target]
    dur = out_duration(info, o)
    if not dur:
        return 0, ""
    frac = dur / info.duration if info.duration else 1.0
    if t.kind == "subs":
        return 0, ""
    if t.kind == "audio":
        enc, kb = _audio_codec_for(o.target)
        kb = o.audio_kbps or kb
        ch = (info.audio[0].channels if info.audio else 2) or 2
        if enc == "pcm_s16le":
            return int(dur * 44100 * 2 * ch), ""
        if enc in ("flac", "alac"):
            return int(dur * 44100 * 2 * ch * 0.58), "lossless"
        return int(dur * kb * 1000 / 8), ""
    if t.kind == "anim":
        v = info.video
        if not v:
            return 0, ""
        qi = {"smaller": 0, "balanced": 1, "best": 2}[o.quality]
        w = min(o.anim_width or (360, 480, 720)[qi], v.width or 480)
        h = w * (v.height or 270) / (v.width or 480)
        fps = o.anim_fps or (10, 15, 20)[qi]
        bits_per_px = 1.6 if o.target == "gif" else 0.35  # calibrated on test clips, varies with content
        return int(w * h * fps * dur * bits_per_px / 8), "rough"
    if o.size_mode == "target":
        if _already_fits(info, o):
            return estimate(info, replace(o, size_mode="quality", quality="balanced", vcodec="auto"))
        return int(o.target_mb * 1024 * 1024), "target"
    try:
        vcodec = resolve_vcodec(info, o) if info.video else "copy"
    except PlanError:
        return 0, ""
    if vcodec == "copy":
        return int(info.size * frac), "same quality"
    v = info.video
    h = min(o.height, v.height) if o.height and v.height else v.height
    w = (v.width * h / v.height) if v.height else v.width
    fps = v.fps or 30
    vbytes = w * h * fps * _BPP[vcodec] * _QMUL[o.quality] * dur / 8
    src_v = (v.bitrate or max(0, info.bitrate - sum(a.bitrate for a in info.audio))) * dur / 8
    if src_v:
        vbytes = min(vbytes, src_v * 1.3)
    enc, kb = _audio_codec_for(o.target)
    abytes = 0
    for a in info.audio:
        allowed = COPY_AUDIO.get(o.target)
        if not o.normalize and not o.audio_kbps and (allowed is None or a.codec in allowed) and a.bitrate:
            abytes += a.bitrate * dur / 8
        else:
            abytes += (o.audio_kbps or kb) * 1000 * dur / 8
    return int(vbytes + abytes), "estimate"
