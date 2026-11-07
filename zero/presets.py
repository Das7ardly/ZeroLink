"""Quality presets and the yt-dlp command builder."""
from __future__ import annotations

import re
import shlex
import sys

PRESETS: list[tuple[str, str]] = [
    ("best", "Best available"),
    ("2160", "4K (2160p)"),
    ("1440", "1440p"),
    ("1080", "1080p"),
    ("720", "720p"),
    ("480", "480p"),
    ("360", "360p"),
    ("audio_mp3", "Audio only · MP3"),
    ("audio_m4a", "Audio only · M4A"),
    ("audio_best", "Audio only · original"),
]

# "Format" dropdown: video containers and audio-only outputs
FORMATS: list[tuple[str, str]] = [
    ("mp4", "MP4 video"),
    ("mkv", "MKV video"),
    ("audio_mp3", "MP3 audio"),
    ("audio_m4a", "M4A audio"),
    ("audio_best", "Original audio (no conversion)"),
]

# "Codec" dropdown for video
VCODECS: list[tuple[str, str]] = [
    ("auto", "Auto"),
    ("av1", "AV1"),
    ("vp9", "VP9"),
    ("h264", "H.264"),
    ("h265", "H.265 (HEVC)"),
]
VCODEC_SHORT = {"auto": "", "av1": "AV1", "vp9": "VP9", "h264": "H.264", "h265": "H.265"}
CONVERTIBLE = ("h264", "h265")  # codecs ZeroLink re-encodes to when the site doesn't offer them

# MP3 bitrate choices (value goes to --audio-quality)
MP3_QUALITY: list[tuple[str, str]] = [
    ("0", "Best (VBR, ~245 kbps)"),
    ("320K", "320 kbps"),
    ("256K", "256 kbps"),
    ("192K", "192 kbps"),
    ("128K", "128 kbps"),
]
MP3_QUALITY_SHORT = {"0": "VBR best", "320K": "320 kbps", "256K": "256 kbps", "192K": "192 kbps", "128K": "128 kbps"}

PP_NAMES = {
    "Merger": "Merging",
    "FFmpegMerger": "Merging",
    "ExtractAudio": "Converting audio",
    "FFmpegExtractAudio": "Converting audio",
    "EmbedSubtitle": "Embedding subtitles",
    "FFmpegEmbedSubtitle": "Embedding subtitles",
    "SubtitlesConvertor": "Converting subtitles",
    "FFmpegSubtitlesConvertor": "Converting subtitles",
    "EmbedThumbnail": "Embedding thumbnail",
    "FFmpegThumbnailsConvertor": "Converting thumbnail",
    "ThumbnailsConvertor": "Converting thumbnail",
    "Metadata": "Writing metadata",
    "FFmpegMetadata": "Writing metadata",
    "VideoRemuxer": "Remuxing",
    "FFmpegVideoRemuxer": "Remuxing",
    "FFmpegFixupM3u8": "Fixing stream",
    "FFmpegFixupM4a": "Fixing stream",
    "FFmpegFixupDuplicateMoov": "Fixing stream",
    "FixupM3u8": "Fixing stream",
    "MoveFiles": "Finishing",
}

MARK_PROGRESS = "[GB]"
MARK_PP = "[GBPP]"
MARK_FILE = "[GBFILE]"


def is_audio(preset: str) -> bool:
    return preset.startswith("audio")


def format_args(preset: str, container: str, vcodec: str = "auto", audio_quality: str = "0",
                convert: bool = True) -> list[str]:
    if preset == "audio_mp3":
        return ["-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", audio_quality or "0"]
    if preset == "audio_m4a":
        return ["-f", "ba[ext=m4a]/ba/b", "-x", "--audio-format", "m4a"]
    if preset == "audio_best":
        return ["-f", "ba/b", "-x"]

    merge = ["--merge-output-format", container, "--remux-video", container]
    if preset.startswith("fmt:"):
        # fmt:<format_id>:<has_audio 0|1>
        _, fid, has_audio = (preset.split(":") + ["1"])[:3]
        spec = fid if has_audio == "1" else f"{fid}+ba/{fid}"
        return ["-f", spec] + merge

    # keep in sync with estimate.sort_fields
    sort_codec = {"av1": "vcodec:av01", "vp9": "vcodec:vp9", "h264": "vcodec:h264", "h265": "vcodec:h265"}
    sort = []
    if vcodec in CONVERTIBLE and not convert:
        # no re-encoding allowed: the codec beats resolution (YouTube has no H.264 above 1080p)
        sort.append(sort_codec[vcodec])
    if preset.isdigit():
        sort.append(f"res:{preset}")
    elif vcodec != "auto" and not (vcodec in CONVERTIBLE and not convert):
        sort.append("res")  # "Best" keeps the highest resolution whatever the codec
    if vcodec in ("av1", "vp9") or (vcodec in CONVERTIBLE and convert):
        # keep the chosen resolution, prefer the codec at that resolution
        sort += ["fps", sort_codec[vcodec]]
    if container == "mp4":
        sort.append("ext:mp4:m4a")  # prefer formats that fit an MP4 without re-encoding
    args = ["-f", "bv*+ba/b"]
    if sort:
        args += ["-S", ",".join(sort)]
    return args + merge


def split_args(text: str) -> list[str]:
    """Split user-typed extra options; keeps Windows backslashes intact."""
    if sys.platform == "win32":
        return [a[1:-1] if len(a) > 1 and a[0] == a[-1] and a[0] in "'\"" else a
                for a in shlex.split(text, posix=False)]
    return shlex.split(text)


_CHANNEL = re.compile(r"^(https?://(?:www\.|m\.)?youtube\.com/(?:@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+))/?(?:[?#].*)?$")


def normalize_url(url: str) -> str:
    """A bare YouTube channel link lists its tabs; point it at the Videos tab instead."""
    m = _CHANNEL.match(url.strip())
    return f"{m.group(1)}/videos" if m else url.strip()


_WIN_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_folder(name: str) -> str:
    name = _WIN_BAD.sub("_", name).strip().rstrip(". ")
    return (name or "Playlist")[:120]


def output_template(job, settings) -> str:
    tmpl = settings.filename_template or "%(title)s [%(id)s].%(ext)s"
    if job.playlist_title:
        folder = safe_folder(job.playlist_title).replace("%", "%%")
        prefix = f"{job.playlist_index:03d} - " if job.playlist_index else ""
        return f"{folder}/{prefix}{tmpl}"
    return tmpl


def job_temp_dir(job, settings) -> str:
    """Per-job folder for partial files, so two jobs for the same video never collide.
    Lives inside the download folder so the final move is an instant rename."""
    import os
    return os.path.join(job.download_dir or settings.download_dir, ".zero-tmp", job.id)


COOKIE_ERRORS = ("cookie database", "failed to decrypt with dpapi", "app-bound", "could not find chrome cookies",
                 "could not find firefox cookies", "cookies database", "failed to load cookies")


def is_cookie_error(msg: str) -> bool:
    m = (msg or "").lower()
    return "cookie" in m and any(k in m for k in COOKIE_ERRORS)


def cookie_args(settings) -> list[str]:
    """Browser or file cookies, unless they failed earlier in this session."""
    if getattr(settings, "_cookies_broken", False):
        return []
    if settings.cookies_browser == "file":
        return ["--cookies", settings.cookies_file] if settings.cookies_file else []
    if settings.cookies_browser:
        return ["--cookies-from-browser", settings.cookies_browser]
    return []


def build_command(job, settings, tools) -> list[str]:
    """Full yt-dlp argv for a download job."""
    cmd = [tools.ytdlp] + tools.base_args()
    cmd += [
        "--newline", "--progress", "--quiet", "--no-simulate",
        "--progress-template", f"download:{MARK_PROGRESS}%(progress)j",
        "--progress-template", f"postprocess:{MARK_PP}%(progress.postprocessor)s|%(progress.status)s",
        "--print", f"after_move:{MARK_FILE}%(filepath)s",
        "--no-playlist", "--no-mtime", "--windows-filenames",
        "--concurrent-fragments", str(max(1, int(settings.concurrent_fragments))),
        "-P", job.download_dir or settings.download_dir,
        "-P", f"temp:{job_temp_dir(job, settings)}",
        "-o", output_template(job, settings),
    ]
    cmd += format_args(job.preset, job.container, job.vcodec, job.audio_quality, settings.convert_codecs)

    audio = is_audio(job.preset)
    if job.subs:
        cmd += ["--write-subs", "--sub-langs", settings.sub_langs or "en.*", "--convert-subs", "srt"]
        if settings.auto_subs:
            cmd.append("--write-auto-subs")
        if settings.embed_subs and not audio:
            cmd.append("--embed-subs")
    if settings.embed_metadata:
        cmd.append("--embed-metadata")
    if settings.embed_thumbnail:
        cmd.append("--embed-thumbnail")
    if settings.embed_chapters and not audio:
        cmd.append("--embed-chapters")
    if settings.use_archive and not job.force:
        from .paths import data_dir
        cmd += ["--download-archive", str(data_dir() / "archive.txt")]
    cmd += cookie_args(settings)
    if settings.rate_limit.strip():
        cmd += ["--limit-rate", settings.rate_limit.strip()]
    if settings.extra_args.strip():
        cmd += split_args(settings.extra_args)
    cmd += ["--", job.url]
    return cmd


def probe_command(url: str, whole_playlist: bool, settings, tools) -> list[str]:
    cmd = [tools.ytdlp] + tools.base_args() + ["-J", "--flat-playlist"]
    cmd.append("--yes-playlist" if whole_playlist else "--no-playlist")
    cmd += cookie_args(settings)
    return cmd + ["--", normalize_url(url)]
