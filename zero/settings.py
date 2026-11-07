"""User settings, stored as JSON in the app data folder."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields

from .paths import data_dir, default_download_dir


@dataclass
class Settings:
    download_dir: str = str(default_download_dir())
    filename_template: str = "%(title)s [%(id)s].%(ext)s"
    max_concurrent: int = 2
    concurrent_fragments: int = 4
    default_preset: str = "best"
    container: str = "mp4"
    vcodec: str = "auto"
    mp3_quality: str = "0"
    convert_codecs: bool = True   # re-encode to H.264/H.265 when the site doesn't offer it
    encoder: str = "cpu"          # cpu | nvidia | intel | amd
    convert_options: dict = field(default_factory=dict)  # last choices on the Convert page
    subs_enabled: bool = False
    sub_langs: str = "en.*,bg"
    auto_subs: bool = True
    embed_subs: bool = True
    embed_thumbnail: bool = True
    embed_metadata: bool = True
    embed_chapters: bool = True
    use_archive: bool = True
    cookies_browser: str = ""  # "", "firefox", "chrome", "edge", "brave", "opera", "vivaldi", "file"
    cookies_file: str = ""     # cookies.txt (Netscape format) when cookies_browser == "file"
    rate_limit: str = ""  # e.g. "5M"
    files_dir: str = ""           # Files page folder; "" = download_dir
    files_concurrent: int = 3     # file downloads at once
    files_connections: int = 8    # connections per file
    files_sort: bool = False      # sort into Programs / Compressed / Documents ... subfolders
    extra_args: str = ""
    watch_clipboard: bool = False
    notify_on_finish: bool = True

    @property
    def path(self):
        return data_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        try:
            raw = json.loads(s.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return s
        known = {f.name: f.type for f in fields(cls)}
        for k, v in raw.items():
            if k in known:
                setattr(s, k, v)
        return s

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
