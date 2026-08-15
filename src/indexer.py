"""Walks a system's NAS source and populates the local ROM index.

By design this only touches the NAS filesystem during an explicit `scan`
(or `scan --system X`) run -- never during normal browsing/launching.
Checksums are optional and off by default (size+mtime is usually enough to
detect changes; checksumming a full NAS library over SMB can be very slow).
"""
from __future__ import annotations

import hashlib
import time
import zlib
from pathlib import Path
from typing import Callable

from .config import Config, SystemConfig
from .db import connect, upsert_rom
from .mount import ensure_mounted, elevation_hint

# (count_so_far, total_or_None, current_relative_path) -- total is None here
# since knowing it upfront would mean walking the NAS tree twice (expensive
# over SMB, exactly the cost this project exists to avoid doubling).
ProgressCallback = Callable[[int, "int | None", str], None]


def _hash_file(path: Path, chunk_size: int = 1 << 20) -> tuple[str, str, str]:
    """Computes crc32, md5, sha1 in a single read pass (ScreenScraper wants
    crc32 first, falls back to md5/sha1 -- cheaper to compute all three now
    than to re-read the file later for each one).
    """
    crc = 0
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            crc = zlib.crc32(chunk, crc)
            md5.update(chunk)
            sha1.update(chunk)
    return f"{crc & 0xFFFFFFFF:08x}", md5.hexdigest(), sha1.hexdigest()


def scan_system(config: Config, system_name: str, compute_checksums: bool = False,
                 progress_callback: ProgressCallback | None = None) -> int:
    """Indexes one system's ROMs. Returns the number of ROMs seen."""
    sys_cfg: SystemConfig = config.systems[system_name]
    nas_source = config.nas_sources[sys_cfg.nas_source]

    # This is one of the two moments we're allowed to touch the network.
    root = ensure_mounted(nas_source)
    system_root = Path(root) / sys_cfg.subdir

    if not system_root.exists():
        raise FileNotFoundError(f"System root not found: {system_root}{elevation_hint()}")

    count = 0
    now = time.time()
    with connect(config.db_path) as conn:
        for path in system_root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in sys_cfg.extensions:
                continue

            rel_path_obj = path.relative_to(system_root)
            # Skip anything under a dot-prefixed folder (e.g. ".duplicates"
            # from rom-cleanup tools, ".media" per the community convention
            # for hiding a folder from directory scanners). Matches the
            # hidden-folder convention already established in this project's
            # own notes -- ES-DE itself follows the same convention.
            if any(part.startswith(".") for part in rel_path_obj.parts):
                continue
            rel_path = str(rel_path_obj)
            stat = path.stat()
            crc32 = md5 = sha1 = None
            if compute_checksums:
                crc32, md5, sha1 = _hash_file(path)

            upsert_rom(
                conn,
                system=system_name,
                rel_path=rel_path,
                filename=path.name,
                size_bytes=stat.st_size,
                mtime=stat.st_mtime,
                seen_at=now,
                crc32=crc32,
                md5=md5,
                sha1=sha1,
            )
            count += 1
            if progress_callback:
                progress_callback(count, None, rel_path)

    return count


def scan_all(config: Config, compute_checksums: bool = False,
             progress_callback: ProgressCallback | None = None) -> dict[str, int]:
    results = {}
    for system_name in config.systems:
        results[system_name] = scan_system(config, system_name, compute_checksums, progress_callback)
    return results


def find_stale_roms(config: Config, system_name: str):
    """ROMs in `system_name` not touched by the most recent `scan` run --
    i.e. no longer found on the NAS. Compares each rom's last_seen_at
    against the max(last_seen_at) for that system, which reflects the most
    recent scan (every rom actually found gets stamped with the same `now`
    value on a given run -- see scan_system). Assumes `scan` has just been
    run for this system; if the scan itself was interrupted or partial
    (e.g. a network hiccup mid-walk), this would incorrectly treat
    still-present files as stale -- that's why the caller (prune-removed)
    is dry-run by default rather than ever deleting automatically.
    """
    with connect(config.db_path) as conn:
        row = conn.execute(
            "SELECT MAX(last_seen_at) as latest FROM roms WHERE system = ?", (system_name,)
        ).fetchone()
        latest = row["latest"]
        if latest is None:
            return []
        return conn.execute(
            "SELECT * FROM roms WHERE system = ? AND last_seen_at < ?", (system_name, latest)
        ).fetchall()
