"""SQLite-backed local cache of ROMs and their metadata/media."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS roms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    system TEXT NOT NULL,
    rom_filename TEXT NOT NULL,       -- filename only, e.g. "Chrono Trigger.sfc"
    rel_path TEXT NOT NULL,           -- path relative to the system's NAS subdir
    size_bytes INTEGER,
    mtime REAL,
    crc32 TEXT,                       -- optional, populated by `scan --checksums`
    md5 TEXT,
    sha1 TEXT,
    last_seen_at REAL NOT NULL,
    UNIQUE(system, rel_path)
);

CREATE TABLE IF NOT EXISTS metadata (
    rom_id INTEGER PRIMARY KEY REFERENCES roms(id) ON DELETE CASCADE,
    title TEXT,
    description TEXT,
    release_date TEXT,
    developer TEXT,
    publisher TEXT,
    genre TEXT,
    players TEXT,
    rating REAL,
    source TEXT                       -- "skraper_import" | "api:<provider>" | "manual"
);

CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rom_id INTEGER NOT NULL REFERENCES roms(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,               -- "cover" | "screenshot" | "marquee" | "video" | ...
    local_path TEXT NOT NULL,         -- path under cache.media_root
    UNIQUE(rom_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_roms_system ON roms(system);
"""


@contextmanager
def connect(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


def upsert_rom(conn: sqlite3.Connection, system: str, rel_path: str, filename: str,
                size_bytes: int, mtime: float, seen_at: float,
                crc32: str | None = None, md5: str | None = None, sha1: str | None = None) -> int:
    conn.execute(
        """
        INSERT INTO roms (system, rom_filename, rel_path, size_bytes, mtime, crc32, md5, sha1, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(system, rel_path) DO UPDATE SET
            size_bytes=excluded.size_bytes,
            mtime=excluded.mtime,
            crc32=COALESCE(excluded.crc32, roms.crc32),
            md5=COALESCE(excluded.md5, roms.md5),
            sha1=COALESCE(excluded.sha1, roms.sha1),
            last_seen_at=excluded.last_seen_at
        """,
        (system, filename, rel_path, size_bytes, mtime, crc32, md5, sha1, seen_at),
    )
    row = conn.execute(
        "SELECT id FROM roms WHERE system = ? AND rel_path = ?", (system, rel_path)
    ).fetchone()
    return row["id"]


def upsert_metadata(conn: sqlite3.Connection, rom_id: int, source: str, **fields) -> None:
    fields.setdefault("title", None)
    fields.setdefault("description", None)
    fields.setdefault("release_date", None)
    fields.setdefault("developer", None)
    fields.setdefault("publisher", None)
    fields.setdefault("genre", None)
    fields.setdefault("players", None)
    fields.setdefault("rating", None)
    conn.execute(
        """
        INSERT INTO metadata (rom_id, title, description, release_date, developer,
                               publisher, genre, players, rating, source)
        VALUES (:rom_id, :title, :description, :release_date, :developer,
                :publisher, :genre, :players, :rating, :source)
        ON CONFLICT(rom_id) DO UPDATE SET
            title=excluded.title, description=excluded.description,
            release_date=excluded.release_date, developer=excluded.developer,
            publisher=excluded.publisher, genre=excluded.genre,
            players=excluded.players, rating=excluded.rating, source=excluded.source
        """,
        {"rom_id": rom_id, "source": source, **fields},
    )


def upsert_media(conn: sqlite3.Connection, rom_id: int, kind: str, local_path: str) -> None:
    conn.execute(
        """
        INSERT INTO media (rom_id, kind, local_path) VALUES (?, ?, ?)
        ON CONFLICT(rom_id, kind) DO UPDATE SET local_path=excluded.local_path
        """,
        (rom_id, kind, local_path),
    )


def find_rom(conn: sqlite3.Connection, system: str, rel_path: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM roms WHERE system = ? AND rel_path = ?", (system, rel_path)
    ).fetchone()


def roms_for_system(conn: sqlite3.Connection, system: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM roms WHERE system = ?", (system,)).fetchall()


def rom_by_filename(conn: sqlite3.Connection, system: str, filename: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM roms WHERE system = ? AND rom_filename = ?", (system, filename)
    ).fetchone()


def rom_filenames_missing_metadata(conn: sqlite3.Connection, system: str) -> set[str]:
    """ROMs in `system` with no metadata row at all -- e.g. freshly scanned,
    or previously pruned by `prune-removed` (which cascades metadata away)
    and since reappeared on the NAS. Same "missing" definition as
    scrape_system's only_missing, so a ROM flagged missing here would also
    be picked up by a plain `scrape` run.
    """
    rows = conn.execute(
        """
        SELECT r.rom_filename FROM roms r
        LEFT JOIN metadata m ON m.rom_id = r.id
        WHERE r.system = ? AND m.rom_id IS NULL
        """,
        (system,),
    ).fetchall()
    return {row["rom_filename"] for row in rows}
