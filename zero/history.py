"""Download history in SQLite."""
from __future__ import annotations

import sqlite3
import time

from .paths import data_dir


class History:
    def __init__(self, path=None):
        self.path = path or (data_dir() / "history.db")
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT,
                quality TEXT,
                preset TEXT,
                container TEXT,
                filepath TEXT,
                size INTEGER,
                status TEXT,
                error TEXT,
                finished_at REAL
            )"""
        )
        for col in ("vcodec TEXT", "audio_quality TEXT"):  # added in 1.1
            try:
                self.db.execute(f"ALTER TABLE downloads ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        self.db.commit()

    def add(self, job) -> None:
        self.db.execute(
            "INSERT INTO downloads (url, title, quality, preset, container, vcodec, audio_quality,"
            " filepath, size, status, error, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (job.url, job.title, job.quality_label, job.preset, job.container, job.vcodec,
             job.audio_quality, job.filepath, job.total_bytes or None, job.status, job.error or None,
             time.time()),
        )
        self.db.commit()

    def search(self, text: str = "", limit: int = 2000) -> list[sqlite3.Row]:
        q = "SELECT * FROM downloads"
        args: list = []
        if text:
            q += " WHERE title LIKE ? OR url LIKE ?"
            args = [f"%{text}%", f"%{text}%"]
        q += " ORDER BY finished_at DESC LIMIT ?"
        args.append(limit)
        return list(self.db.execute(q, args))

    def delete(self, row_id: int) -> None:
        self.db.execute("DELETE FROM downloads WHERE id=?", (row_id,))
        self.db.commit()

    def clear(self) -> None:
        self.db.execute("DELETE FROM downloads")
        self.db.commit()
