"""Business logic for the destructive cleanup commands (clean-media,
prune-removed, reset-system) -- extracted out of cli.py's click callbacks so
the CLI and the TUI can both call the same functions instead of one reaching
into the other's private helpers or duplicating this logic.

Every function here is pure computation plus an optional destructive action
(apply_changes=False is always a dry-run preview, never a side effect) --
none of them print anything or prompt for confirmation. Callers (cli.py's
commands, or TUI screens) own presenting the returned data and obtaining any
needed confirmation before calling again with apply_changes=True.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .db import connect
from .indexer import find_stale_roms


def human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def find_orphaned_media(config: Config, systems: list[str]) -> list[tuple[str, Path]]:
    """Walks cache.media_root/<system>/** for each given system and returns
    every file there that isn't referenced by any current `media` row for
    that system -- leftovers from a renamed ROM, a re-scrape that produced
    a differently-named file, or a manual copy. Scoped strictly to
    media_root/<system> per system (never anything outside it, e.g. other
    systems ES-DE manages independently of this tool) and compared against
    the DB as it stands when called -- if this runs after prune_removed's
    own stale-ROM deletions in the same call, those already-handled files
    are naturally excluded (their media rows are gone, but so are the
    files themselves, deleted a few lines up)."""
    orphans = []
    with connect(config.db_path) as conn:
        for name in systems:
            system_media_root = config.media_root / name
            if not system_media_root.is_dir():
                continue
            tracked = {
                Path(row["local_path"]).resolve()
                for row in conn.execute(
                    "SELECT media.local_path FROM media JOIN roms ON roms.id = media.rom_id "
                    "WHERE roms.system = ?",
                    (name,),
                ).fetchall()
            }
            for path in system_media_root.rglob("*"):
                if path.is_file() and path.resolve() not in tracked:
                    orphans.append((name, path))
    return orphans


@dataclass
class CleanMediaResult:
    by_system: dict[str, dict[str, tuple[int, int]]]  # system -> kind -> (count, bytes)
    total_files: int
    total_bytes: int
    applied: bool


def clean_media(config: Config, systems: list[str], apply_changes: bool = False) -> CleanMediaResult:
    """Removes locally cached media files (and their DB rows) for kinds no
    longer in config.enabled_media_types. Only actually deletes when
    apply_changes=True -- always returns the full breakdown either way."""
    by_system: dict[str, dict[str, tuple[int, int]]] = {}
    total_files, total_bytes = 0, 0

    with connect(config.db_path) as conn:
        for name in systems:
            rows = conn.execute(
                "SELECT media.id, media.kind, media.local_path FROM media "
                "JOIN roms ON roms.id = media.rom_id WHERE roms.system = ?",
                (name,),
            ).fetchall()
            to_remove = [r for r in rows if not config.is_media_enabled(r["kind"])]
            if not to_remove:
                continue

            by_kind: dict[str, list] = {}
            for row in to_remove:
                by_kind.setdefault(row["kind"], []).append(row)

            kind_summary: dict[str, tuple[int, int]] = {}
            for kind in sorted(by_kind):
                kind_rows = by_kind[kind]
                kind_bytes = sum(
                    Path(r["local_path"]).stat().st_size
                    for r in kind_rows if Path(r["local_path"]).exists()
                )
                kind_summary[kind] = (len(kind_rows), kind_bytes)
                total_files += len(kind_rows)
                total_bytes += kind_bytes

                if apply_changes:
                    for row in kind_rows:
                        p = Path(row["local_path"])
                        if p.exists():
                            p.unlink()
                        conn.execute("DELETE FROM media WHERE id = ?", (row["id"],))
                    kind_dir = config.media_root / name / kind
                    if kind_dir.exists() and not any(kind_dir.iterdir()):
                        kind_dir.rmdir()

            by_system[name] = kind_summary

    return CleanMediaResult(
        by_system=by_system, total_files=total_files, total_bytes=total_bytes, applied=apply_changes
    )


@dataclass
class StaleRomEntry:
    system: str
    rom: object  # sqlite3.Row
    is_drift: bool


def classify_stale_roms(config: Config, systems: list[str]) -> list[StaleRomEntry]:
    """ROMs indexed in the DB but not seen on the given systems' last scan,
    flagged with whether each one looks stale only because an extension was
    removed from that system's `extensions:` in config.yaml (a false
    positive from scan's perspective, not necessarily a real NAS removal)
    rather than confirmed missing from the NAS."""
    entries: list[StaleRomEntry] = []
    for name in systems:
        stale = find_stale_roms(config, name)
        if not stale:
            continue
        configured_extensions = set(config.systems[name].extensions)
        for rom in stale:
            is_drift = Path(rom["rel_path"]).suffix.lower() not in configured_extensions
            entries.append(StaleRomEntry(system=name, rom=rom, is_drift=is_drift))
    return entries


@dataclass
class PruneRemovedResult:
    entries: list[StaleRomEntry]
    removed_count: int
    orphans: list[tuple[str, Path]]
    orphans_removed_count: int
    orphans_bytes: int
    applied: bool


def prune_removed(config: Config, systems: list[str], apply_changes: bool = False,
                   sweep_orphaned_media: bool = False) -> PruneRemovedResult:
    """Computes (and, if apply_changes, deletes) stale ROM entries and,
    when requested, orphaned media for the given systems. Never prompts --
    callers own any confirmation flow (e.g. cli.py's click.confirm() for
    the config-drift warning) before calling again with apply_changes=True.
    When both are requested, stale-ROM deletion always runs before the
    orphan sweep so a file removed by the first pass isn't double-counted
    by the second."""
    entries = classify_stale_roms(config, systems)
    removed_count = 0

    if apply_changes and entries:
        for e in entries:
            rom = e.rom
            with connect(config.db_path) as conn:
                media_rows = conn.execute(
                    "SELECT local_path FROM media WHERE rom_id = ?", (rom["id"],)
                ).fetchall()

            stub_path = config.roms_stub_root / e.system / rom["rel_path"]
            if stub_path.exists():
                stub_path.unlink()
            for m in media_rows:
                p = Path(m["local_path"])
                if p.exists():
                    p.unlink()

            with connect(config.db_path) as conn:
                # Cascades to metadata + media DB rows automatically
                # (ON DELETE CASCADE, see db.py's schema).
                conn.execute("DELETE FROM roms WHERE id = ?", (rom["id"],))
            removed_count += 1

    orphans: list[tuple[str, Path]] = []
    orphans_removed_count = 0
    orphans_bytes = 0
    if sweep_orphaned_media:
        orphans = find_orphaned_media(config, systems)
        orphans_bytes = sum(p.stat().st_size for _, p in orphans if p.exists())
        if apply_changes and orphans:
            kind_dirs = set()
            for _, p in orphans:
                if p.exists():
                    p.unlink()
                    orphans_removed_count += 1
                kind_dirs.add(p.parent)
            for d in kind_dirs:
                if d.exists() and not any(d.iterdir()):
                    d.rmdir()

    return PruneRemovedResult(
        entries=entries, removed_count=removed_count,
        orphans=orphans, orphans_removed_count=orphans_removed_count,
        orphans_bytes=orphans_bytes, applied=apply_changes,
    )


@dataclass
class ResetSystemResult:
    rom_count: int
    media_count: int
    media_bytes: int
    stub_count: int
    gamelist_present: bool
    gamelist_path: Path
    stub_dir: Path
    applied: bool


def reset_system(config: Config, system: str, apply_changes: bool = False) -> ResetSystemResult:
    """Wipes ALL locally cached data for one system -- unconditionally,
    without checking what's still on the NAS -- when apply_changes=True.
    config.yaml is left untouched either way. Always returns the full
    preview numbers, computed before any deletion."""
    with connect(config.db_path) as conn:
        rom_count = conn.execute(
            "SELECT COUNT(*) FROM roms WHERE system = ?", (system,)
        ).fetchone()[0]
        media_rows = conn.execute(
            "SELECT media.local_path FROM media "
            "JOIN roms ON roms.id = media.rom_id WHERE roms.system = ?",
            (system,),
        ).fetchall()

    media_bytes = sum(
        Path(r["local_path"]).stat().st_size
        for r in media_rows if Path(r["local_path"]).exists()
    )

    stub_dir = config.roms_stub_root / system
    media_dir = config.media_root / system
    gamelist_path = config.gamelists_root / system / "gamelist.xml"
    stub_count = sum(1 for p in stub_dir.rglob("*") if p.is_file()) if stub_dir.exists() else 0
    gamelist_present = gamelist_path.exists()

    if apply_changes and (rom_count or stub_count or media_rows or gamelist_present):
        with connect(config.db_path) as conn:
            conn.execute("DELETE FROM roms WHERE system = ?", (system,))  # cascades to metadata/media rows
        if stub_dir.exists():
            shutil.rmtree(stub_dir)
        if media_dir.exists():
            shutil.rmtree(media_dir)
        if gamelist_path.exists():
            gamelist_path.unlink()

    return ResetSystemResult(
        rom_count=rom_count, media_count=len(media_rows), media_bytes=media_bytes,
        stub_count=stub_count, gamelist_present=gamelist_present,
        gamelist_path=gamelist_path, stub_dir=stub_dir, applied=apply_changes,
    )
