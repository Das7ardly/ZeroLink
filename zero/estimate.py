"""Predict which streams yt-dlp will pick for a given choice, and how big the file will be.

This mirrors the format-sort rules ZeroLink passes to yt-dlp (see presets.format_args) closely
enough to give a useful size next to every option. Sizes come from the site's own numbers
(filesize / filesize_approx) or bitrate x duration when those are missing.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import presets

# yt-dlp's default codec order, best first
_RANK = {"av1": 0, "vp9.2": 1, "vp9": 2, "h265": 3, "h264": 4, "vp8": 5, "other": 6}
_LIMIT_NAME = {"av1": "av1", "vp9": "vp9", "h264": "h264", "h265": "h265"}
_ACODEC_RANK = {"flac": 0, "alac": 1, "opus": 2, "vorbis": 3, "aac": 4, "mp3": 5, "other": 6}

# rough size factor when re-encoding from one codec to another at ZeroLink's quality settings
_CONVERT_FACTOR = {
    ("av1", "h264"): 1.9, ("vp9", "h264"): 1.5, ("h265", "h264"): 1.6, ("other", "h264"): 1.2,
    ("vp9.2", "h264"): 1.5, ("vp9.2", "h265"): 1.0,
    ("av1", "h265"): 1.25, ("vp9", "h265"): 1.0, ("h264", "h265"): 0.65, ("other", "h265"): 0.8,
}


def codec_family(vcodec: str | None) -> str:
    v = (vcodec or "").lower()
    if v.startswith(("av01", "av1")):
        return "av1"
    if v.startswith(("vp09.02", "vp9.2")):
        return "vp9.2"
    if v.startswith(("vp09", "vp9")):
        return "vp9"
    if v.startswith(("hev1", "hvc1", "h265", "hevc")):
        return "h265"
    if v.startswith(("avc", "h264")):
        return "h264"
    if v.startswith(("vp08", "vp8")):
        return "vp8"
    return "other"


def acodec_family(acodec: str | None) -> str:
    a = (acodec or "").lower()
    if a.startswith("mp4a") or a.startswith("aac"):
        return "aac"
    for k in ("flac", "alac", "opus", "vorbis", "mp3"):
        if a.startswith(k):
            return k
    return "other"


def fmt_size(f: dict, duration: float | None) -> float:
    s = f.get("filesize") or f.get("filesize_approx")
    if not s and f.get("tbr") and duration:
        s = f["tbr"] * 1000 / 8 * duration
    if not s and f.get("vbr") and duration:
        s = f["vbr"] * 1000 / 8 * duration
    if not s and f.get("abr") and duration:
        s = f["abr"] * 1000 / 8 * duration
    return float(s or 0)


def _hdr_score(f):
    dr = (f.get("dynamic_range") or "SDR").upper()
    if dr == "SDR":
        return 0
    if dr == "DV":
        return -1
    return 1


def _codec_key(fam: str, limit: str | None):
    r = _RANK.get(fam, 6)
    if not limit:
        return -r
    lr = _RANK[_LIMIT_NAME[limit]]
    # yt-dlp: codecs at or below the limit first (best of them first), then better ones (closest first)
    return (1, -r) if r >= lr else (0, r)


def sort_fields(preset: str, container: str, vcodec: str, convert: bool) -> list[tuple]:
    """The -S fields ZeroLink sends, as (name, arg) pairs, followed by yt-dlp's defaults."""
    height = int(preset) if preset.isdigit() else None
    fields: list[tuple] = []
    if vcodec in ("h264", "h265") and not convert:
        fields.append(("vcodec", vcodec))
    if height:
        fields.append(("res", height))
    elif vcodec != "auto" and not (vcodec in ("h264", "h265") and not convert):
        fields.append(("res", None))
    if vcodec in ("av1", "vp9") or (vcodec in ("h264", "h265") and convert):
        fields += [("fps", None), ("vcodec", vcodec)]
    if container == "mp4":
        fields.append(("ext", None))
    fields += [("quality", None), ("res", None), ("fps", None), ("hdr", None), ("vcodec", None),
               ("size", None)]
    return fields


def _video_key(f: dict, fields: list[tuple], duration):
    key = []
    h = f.get("height") or 0
    for name, arg in fields:
        if name == "res":
            key.append((1, h) if arg is None or h <= arg else (0, -h))
        elif name == "fps":
            key.append(f.get("fps") or 0)
        elif name == "vcodec":
            key.append(_codec_key(codec_family(f.get("vcodec")), arg))
        elif name == "ext":
            key.append(1 if f.get("ext") == "mp4" else 0)
        elif name == "quality":
            key.append(f.get("quality") or 0)
        elif name == "hdr":
            key.append(_hdr_score(f))
        elif name == "size":
            key.append(fmt_size(f, duration))
    return tuple(key)


def best_audio(formats: list[dict], container: str, duration, prefer_m4a: bool | None = None):
    aud = [f for f in formats if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    if not aud:
        return None
    m4a_first = container == "mp4" if prefer_m4a is None else prefer_m4a

    def key(f):
        return (
            (1 if f.get("ext") == "m4a" else 0) if m4a_first else 0,
            f.get("language_preference") or 0,
            f.get("quality") or 0,
            f.get("audio_channels") or 0,
            -_ACODEC_RANK.get(acodec_family(f.get("acodec")), 6),
            f.get("abr") or f.get("tbr") or 0,
        )
    return max(aud, key=key)


@dataclass
class Estimate:
    size: float               # bytes, 0 if unknown
    video: dict | None = None
    audio: dict | None = None
    converts: bool = False    # ffmpeg will re-encode to the requested codec
    source_codec: str = ""
    height: int = 0

    @property
    def known(self) -> bool:
        return self.size > 0


def estimate(info: dict, preset: str, container: str = "mp4", vcodec: str = "auto",
             audio_quality: str = "0", convert: bool = True) -> Estimate:
    """Predict the download for one choice. `preset` may be a resolution, best, fmt:..., or audio_*."""
    formats = [f for f in (info.get("formats") or []) if f.get("format_id")]
    duration = info.get("duration")
    if not formats:
        return Estimate(0)

    if preset == "audio_mp3":
        kbps = 245 if audio_quality in ("0", "") else int(audio_quality.rstrip("Kk"))
        a = best_audio(formats, "mkv", duration)
        return Estimate(kbps * 1000 / 8 * (duration or 0), audio=a)
    if preset == "audio_m4a":
        a = best_audio(formats, "mp4", duration, prefer_m4a=True)
        return Estimate(fmt_size(a, duration) if a else 0, audio=a)
    if preset == "audio_best":
        a = best_audio(formats, "mkv", duration, prefer_m4a=False)
        return Estimate(fmt_size(a, duration) if a else 0, audio=a)

    if preset.startswith("fmt:"):
        _, fid, has_audio = (preset.split(":") + ["1"])[:3]
        v = next((f for f in formats if f["format_id"] == fid), None)
        if not v:
            return Estimate(0)
        a = None if has_audio == "1" else best_audio(formats, container, duration)
        size = fmt_size(v, duration) + (fmt_size(a, duration) if a else 0)
        return Estimate(size, v, a, source_codec=codec_family(v.get("vcodec")), height=v.get("height") or 0)

    videos = [f for f in formats if f.get("vcodec") not in (None, "none") and f.get("height")]
    if not videos:
        # audio+video single file (many non-YouTube sites)
        best = max(formats, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
        return Estimate(fmt_size(best, duration), best, None, height=best.get("height") or 0)

    fields = sort_fields(preset, container, vcodec, convert)
    v = max(videos, key=lambda f: _video_key(f, fields, duration))
    has_audio = v.get("acodec") not in (None, "none")
    a = None if has_audio else best_audio(formats, container, duration)
    vsize, asize = fmt_size(v, duration), (fmt_size(a, duration) if a else 0)
    fam = codec_family(v.get("vcodec"))
    converts = convert and vcodec in ("h264", "h265") and fam != vcodec
    if converts:
        vsize *= _CONVERT_FACTOR.get((fam, vcodec), 1.0)
    return Estimate(vsize + asize if vsize else 0, v, a, converts, fam, v.get("height") or 0)


def delta_text(size: float, base: float) -> str:
    """'+65%' / '-27%' / '' compared to the Auto choice."""
    if not size or not base:
        return ""
    pct = (size - base) * 100 / base
    if abs(pct) < 3:
        return ""
    return f"{'+' if pct > 0 else '−'}{abs(pct):.0f}%"


def codec_label(fam: str) -> str:
    return {"av1": "AV1", "vp9": "VP9", "vp9.2": "VP9 HDR", "h265": "H.265", "h264": "H.264", "vp8": "VP8"}.get(fam, fam.upper())


__all__ = ["estimate", "Estimate", "delta_text", "codec_family", "codec_label", "presets"]
