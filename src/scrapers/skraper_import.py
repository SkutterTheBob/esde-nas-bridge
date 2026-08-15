"""Imports an existing Skraper export (gamelist.xml + media/) into the cache.

Skraper (https://skraper.net) is a standalone Windows/Linux GUI app that
scrapes ScreenScraper.fr and writes EmulationStation-style output -- it's
a different codebase from the "Skyscraper" CLI tool, despite the similar
name and shared underlying database.

export_dir can point anywhere readable, including directly at a NAS-mapped
drive letter (e.g. the Windows path "Y:" mapped to a share, then joined
with "roms/snes" or similar) if that's where Skraper already writes its
output -- there's no requirement to copy the export locally first. Doing
this NAS-direct read is itself a deliberate, occasional network touch (like
`scan`), separate from normal ES-DE browsing which reads only the local
copy this importer produces.

Two things worth knowing about Skraper's output, since they shape how this
importer works:

1. The exact gamelist.xml media tag names vary a bit by which frontend
   flavor you picked in Skraper's setup wizard (Recalbox / RetroPie /
   Batocera-ES / LaunchBox export). The tag map below covers the known
   set, but isn't guaranteed exhaustive for every flavor.

2. The `media/` folder layout is CONFIRMED (verified twice against a real
   NAS export: a folder screenshot and a direct `dir` listing) to include:
       media/3dboxes/, backcovers/, fanart/, manuals/, marquees/,
             miximages/, physicalmedia/, screenshots/, titlescreens/, videos/
   i.e. this version of Skraper writes ES-DE's own native folder names
   directly, NOT the older box2d/box3d/manual/mixed/screenshot/snap/title/
   wheel naming some third-party docs describe (that older naming is kept
   as a lower-priority fallback below in case a different Skraper
   version/config produces it instead). "covers" is the 10th folder in
   this same native naming scheme -- it just happened to be absent from
   the specific library used to confirm this list (no front box art was
   scraped for that particular collection), not evidence that Skraper
   itself omits it. Treated here as an equally legitimate possible folder,
   checked the same way as the other nine, for anyone whose library does
   have it populated.
   Because the folder layout is on firmer ground than the XML tag names,
   this importer treats gamelist.xml as the primary source for metadata +
   media, and ALSO does a filename-convention sweep of media/ as a
   fallback/supplement for any media a game's XML entry didn't reference.

ES-DE's own downloaded_media vocabulary also includes a "custom" media type
that neither Skraper nor ScreenScraper are currently known to populate here
-- not wired up since nothing produces it, but noted in case a future
source does. "physicalmedia" IS wired up below, confirmed as an actively
populated folder in a real Skraper export.

Historical gotcha carried over correctly here: in EmulationStation-style
gamelist.xml, the <marquee> tag holds the WHEEL/LOGO image, not an arcade
marquee -- this is a long-documented quirk (see Skyscraper's own docs),
not a bug in this importer.
"""
from __future__ import annotations

import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

from ..config import Config
from ..db import connect, rom_by_filename, upsert_media, upsert_metadata

# (game_index, total_games, status_message) -- called once per game as it
# starts processing, and again each time a media file actually gets copied.
# The CLI layer decides how to render this (overwriting terminal line,
# throttled log lines, or nothing at all).
ProgressCallback = Callable[[int, int, str], None]

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXTENSIONS = (".mp4", ".avi", ".mkv", ".webm")
MANUAL_EXTENSIONS = (".pdf", ".cbz", ".cbr")  # manual scans are virtually always PDF; comic-archive
                                                # formats included as a rarer alternative

# Some Skraper exports (confirmed: this install's manuals folder) append a
# 32-hex-char hash after the title, e.g.
#   "Hungry Dinosaurs (Europe) DF2CDEE6CB32823769F9446706F27DDD.pdf"
# instead of a plain "Hungry Dinosaurs (Europe).pdf". An exact stem match
# against the ROM's filename silently misses every one of these. Stripping
# this suffix when indexing a folder handles both naming styles uniformly
# with the same lookup -- harmless for folders that don't use it, since the
# pattern just won't match anything there.
_HASH_SUFFIX_RE = re.compile(r" [0-9A-Fa-f]{32}$")

# gamelist.xml tag -> our internal media "kind". Kind names match ES-DE's
# own downloaded_media/<system>/<kind>/ folder names (see gamelist_writer.py
# header). Best-effort superset across known Skraper/Recalbox/Batocera-ES/
# LaunchBox export flavors -- tags that don't appear in a given export are
# simply skipped.
MEDIA_TAGS: dict[str, str] = {
    "image": "covers",           # box2d / front box art
    "marquee": "marquees",       # legacy ES gamelist convention: marquee tag = wheel/logo; ES-DE
                                  # appears to only have one "marquees" folder for this concept
    "thumbnail": "3dboxes",      # common Recalbox/Batocera-ES convention: 3D box render
    "box3d": "3dboxes",          # some newer flavors write this tag literally instead of <thumbnail>
    "video": "videos",
    "fanart": "fanart",
    "manual": "manuals",
    "screenshot": "screenshots",
    "titlescreen": "titlescreens",
    "title_screen": "titlescreens",
    "miximage": "miximages",
    "mix": "miximages",
    "backcover": "backcovers",
    "boxback": "backcovers",
    # Plural, ES-DE-style tag name guesses -- NOT directly confirmed (unlike
    # the folder names below), but this Skraper install's folder layout
    # strongly suggests it may write matching plural tag names too. Harmless
    # if wrong: unmatched tags are simply skipped.
    "covers": "covers",
    "screenshots": "screenshots",
    "titlescreens": "titlescreens",
    "manuals": "manuals",
    "miximages": "miximages",
    "backcovers": "backcovers",
    "marquees": "marquees",
    "videos": "videos",
    "3dboxes": "3dboxes",
    "physicalmedia": "physicalmedia",
}

# media/<folder> -> our internal media "kind". CONFIRMED (verified twice
# against a real NAS export) as ES-DE's own native folder names -- these
# take priority. Older third-party-documented naming (box2d, box3d, manual,
# mixed, screenshot, snap, title, wheel) is kept as a lower-priority
# fallback in case a different Skraper version/config produces that
# instead. Checked in this order; first existing file for a given kind wins.
MEDIA_FOLDER_FALLBACK: list[tuple[str, str]] = [
    # ES-DE-native naming (this Skraper install/version) -- checked first.
    # All ten folders are treated as equally legitimate possibilities;
    # "covers" wasn't populated in the specific library used to confirm
    # this list, but that's a fact about that library's scrape, not
    # evidence Skraper omits the folder.
    ("3dboxes", "3dboxes"),
    ("backcovers", "backcovers"),
    ("covers", "covers"),
    ("fanart", "fanart"),
    ("manuals", "manuals"),
    ("marquees", "marquees"),
    ("miximages", "miximages"),
    ("physicalmedia", "physicalmedia"),
    ("screenshots", "screenshots"),
    ("titlescreens", "titlescreens"),
    ("videos", "videos"),
    # Older third-party-documented naming, lower priority.
    ("box2d", "covers"),
    ("box3d", "3dboxes"),
    ("manual", "manuals"),
    ("mixed", "miximages"),
    ("screenshot", "screenshots"),
    ("snap", "screenshots"),
    ("title", "titlescreens"),
    ("wheel", "marquees"),
    ("video", "videos"),
]


def import_skraper_export(config: Config, system_name: str, export_dir: str,
                           progress_callback: ProgressCallback | None = None) -> dict:
    export_path = Path(export_dir)
    gamelist_path = export_path / "gamelist.xml"
    if not gamelist_path.exists():
        raise FileNotFoundError(f"No gamelist.xml found in {export_dir}")

    tree = ET.parse(gamelist_path)
    root = tree.getroot()
    games = root.findall("game")
    total = len(games)

    matched, unmatched = 0, 0
    media_root = config.media_root / system_name
    media_root.mkdir(parents=True, exist_ok=True)
    # Built lazily, one iterdir() per folder for the whole run -- not once
    # per ROM, which would mean thousands of redundant NAS directory listings.
    folder_index_cache: dict[Path, dict[str, Path]] = {}

    with connect(config.db_path) as conn:
        for idx, game in enumerate(games, start=1):
            rom_path = game.findtext("path", default="").lstrip("./")
            filename = Path(rom_path).name
            if not filename:
                continue

            if progress_callback:
                progress_callback(idx, total, filename)

            rom = rom_by_filename(conn, system_name, filename)
            if rom is None:
                # ROM referenced by Skraper isn't in our index yet -- run
                # `scan` first, or it'll just be skipped on this pass.
                unmatched += 1
                continue

            upsert_metadata(
                conn,
                rom_id=rom["id"],
                source="skraper_import",
                title=game.findtext("name"),
                description=game.findtext("desc"),
                release_date=game.findtext("releasedate"),
                developer=game.findtext("developer"),
                publisher=game.findtext("publisher"),
                genre=game.findtext("genre"),
                players=game.findtext("players"),
                rating=_safe_float(game.findtext("rating")),
            )

            def _on_copy(kind: str, dest_name: str, _idx=idx, _filename=filename) -> None:
                if progress_callback:
                    progress_callback(_idx, total, f"{_filename} -> {kind}/{dest_name}")

            found_kinds: set[str] = set()
            for tag, kind in MEDIA_TAGS.items():
                if not config.is_media_enabled(kind):
                    continue
                rel = game.findtext(tag)
                if not rel:
                    continue
                src = export_path / rel.lstrip("./")
                if not src.exists():
                    continue
                if _copy_media(conn, rom["id"], kind, src, media_root, filename, on_copy=_on_copy):
                    found_kinds.add(kind)

            # Fallback: for any kind not already picked up from the XML,
            # look for a same-named file under media/<folder>/ directly.
            _scan_media_folders(conn, rom["id"], filename, export_path, media_root,
                                 skip_kinds=found_kinds, folder_index_cache=folder_index_cache,
                                 config=config, on_copy=_on_copy)

            matched += 1

    return {"matched": matched, "unmatched": unmatched}


def _copy_media(conn, rom_id: int, kind: str, src: Path, media_root: Path, rom_filename: str,
                 on_copy: Callable[[str, str], None] | None = None) -> bool:
    # Always name the destination by the ROM's own stem (not whatever
    # Skraper happened to call the source file) -- ES-DE finds media purely
    # by matching filenames against the ROM, so this filename is what
    # actually makes the artwork show up, regardless of source naming.
    dest = media_root / kind / f"{Path(rom_filename).stem}{src.suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    upsert_media(conn, rom_id=rom_id, kind=kind, local_path=str(dest))
    if on_copy:
        on_copy(kind, dest.name)
    return True


def _build_folder_index(folder_path: Path, extensions: tuple[str, ...]) -> dict[str, Path]:
    """Lists a media folder ONCE and returns {normalized_stem: Path}.

    normalized_stem strips an optional trailing " <32-hex-char-hash>" suffix
    so both hash-suffixed filenames (e.g. Skraper's manuals naming) and
    plain filenames resolve to the same key, matched against the ROM's own
    plain stem either way. If a hash-suffixed and non-suffixed file somehow
    collide on the same normalized stem, the non-suffixed (exact) one wins.
    """
    index: dict[str, Path] = {}
    try:
        entries = list(folder_path.iterdir())
    except OSError:
        return index

    for entry in entries:
        if not entry.is_file() or entry.suffix.lower() not in extensions:
            continue
        normalized = _HASH_SUFFIX_RE.sub("", entry.stem)
        if normalized not in index or entry.stem == normalized:
            index[normalized] = entry

    return index


def _scan_media_folders(conn, rom_id: int, rom_filename: str, export_path: Path,
                         media_root: Path, skip_kinds: set[str],
                         folder_index_cache: dict[Path, dict[str, Path]],
                         config: Config,
                         on_copy: Callable[[str, str], None] | None = None) -> None:
    stem = Path(rom_filename).stem
    media_dir = export_path / "media"
    if not media_dir.exists():
        return

    for folder, kind in MEDIA_FOLDER_FALLBACK:
        if kind in skip_kinds or not config.is_media_enabled(kind):
            continue
        folder_path = media_dir / folder
        if not folder_path.exists():
            continue

        if kind == "videos":
            extensions = VIDEO_EXTENSIONS
        elif kind == "manuals":
            extensions = MANUAL_EXTENSIONS
        else:
            extensions = IMAGE_EXTENSIONS

        if folder_path not in folder_index_cache:
            folder_index_cache[folder_path] = _build_folder_index(folder_path, extensions)

        match = folder_index_cache[folder_path].get(stem)
        if match is not None:
            _copy_media(conn, rom_id, kind, match, media_root, rom_filename, on_copy=on_copy)
            skip_kinds.add(kind)


def _safe_float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None
