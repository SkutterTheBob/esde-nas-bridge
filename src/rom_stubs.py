"""Writes local placeholder ("stub") files matching each indexed ROM's
filename, so ES-DE has something to physically scan without ever touching
the NAS during startup or browsing.

Why this exists: ES-DE requires at least one real file present at a
system's configured <path> to populate that system at all -- it isn't a
pure gamelist-only frontend. These stubs satisfy that scan. They are never
executed: launch_wrapper.py resolves the real NAS path by looking up the
ROM's filename in the local database, ignoring whatever path ES-DE actually
hands back for the stub.

Stubs mirror each ROM's rel_path (including subfolders) so gamelist.xml's
existing <path> values -- already written relative to the system root --
line up without any translation.
"""
from __future__ import annotations

from typing import Callable

from .config import Config
from .db import connect, roms_for_system

# (index, total, current_rel_path)
ProgressCallback = Callable[[int, int, str], None]


def write_stubs_for_system(config: Config, system_name: str,
                            progress_callback: ProgressCallback | None = None) -> int:
    stub_root = config.roms_stub_root / system_name
    count = 0

    with connect(config.db_path) as conn:
        roms = roms_for_system(conn, system_name)
        total = len(roms)
        for idx, rom in enumerate(roms, start=1):
            dest = stub_root / rom["rel_path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                dest.touch()
            count += 1
            if progress_callback:
                progress_callback(idx, total, rom["rel_path"])

    return count


def write_all_stubs(config: Config, progress_callback: ProgressCallback | None = None) -> dict[str, int]:
    return {system: write_stubs_for_system(config, system, progress_callback) for system in config.systems}
