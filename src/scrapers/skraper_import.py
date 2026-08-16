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
   CONFIRMED (pcenginecd, real NAS export): within one of those folders,
   media can ALSO be nested one level deeper under a per-game subfolder --
   media/screenshots/Loom (USA)/Loom (USA).png -- rather than flat directly
   under media/screenshots/, seen for every other system in the same
   install (snes/genesis/pcengine). Apparently tied to whether the ROM
   itself was scraped from inside its own subfolder (true for pcenginecd's
   cue-in-a-folder layout) -- checked as a second, lower-priority
   convention alongside the flat one, same "first found wins" approach as
   the folder-name fallback list above.

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
from ..db import connect, rom_by_filename, rom_filenames_missing_metadata, upsert_media, upsert_metadata

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

# Matches a trailing multi-disc marker, e.g. " (Disc 1)" or " [Disc 2]",
# so a per-disc filename's stem can be reduced to its shared base title --
# see the "multi-disc .m3u fallback" comment below in import_skraper_export
# for why this exists.
_DISC_SUFFIX_RE = re.compile(r"\s*[\(\[]disc\s*\d+[\)\]]\s*$", re.IGNORECASE)


def _disc_base_stem(stem: str) -> str:
    return _DISC_SUFFIX_RE.sub("", stem).strip().lower()


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
                           progress_callback: ProgressCallback | None = None,
                           only_filenames: set[str] | None = None) -> dict:
    """only_filenames, if given, restricts processing to just those ROM
    filenames instead of the whole export -- skipped entries cost only a
    cheap in-memory tag lookup, none of the DB writes or media file copies
    (the actual expensive part, since skraper_imports typically points
    straight at the NAS: each copy is a real SMB round trip). Useful for
    re-importing a single ROM (e.g. after `prune-removed` cascaded its
    cached metadata/media away, then it reappeared on the NAS) without
    re-touching every other already-cached ROM in the system.
    """
    export_path = Path(export_dir)
    gamelist_path = export_path / "gamelist.xml"
    if not gamelist_path.exists():
        raise FileNotFoundError(f"No gamelist.xml found in {export_dir}")

    tree = ET.parse(gamelist_path)
    root = tree.getroot()
    games = root.findall("game")
    total = len(games) if only_filenames is None else len(only_filenames)

    matched, unmatched = 0, 0
    seen_filenames: set[str] = set()
    media_root = config.media_root / system_name
    media_root.mkdir(parents=True, exist_ok=True)
    # Built lazily, one iterdir() per folder for the whole run -- not once
    # per ROM, which would mean thousands of redundant NAS directory listings.
    folder_index_cache: dict[Path, dict[str, Path]] = {}
    # base title (disc marker + extension stripped, lowercased) -> the first
    # per-disc <game> element seen for it. Populated as we go, consumed by
    # the .m3u fallback pass below.
    disc_matches: dict[str, ET.Element] = {}
    # extension-stripped filename (lowercased), unmodified otherwise -> the
    # first <game> element seen with that stem. Populated as we go, consumed
    # by the extension-mismatch fallback pass below.
    stem_matches: dict[str, ET.Element] = {}

    with connect(config.db_path) as conn:
        for game in games:
            rom_path = game.findtext("path", default="").lstrip("./")
            filename = Path(rom_path).name
            if not filename:
                continue

            # Always recorded, even for a game that only_filenames is about
            # to skip below -- a cheap in-memory tag lookup, not the DB
            # writes/media copies only_filenames exists to avoid, and the
            # .m3u fallback pass needs every disc's entry available even
            # when a --rom/--missing-only run targets just the .m3u itself
            # (never one of its individual disc files by name).
            stem = Path(filename).stem
            stem_matches.setdefault(stem.strip().lower(), game)
            base = _disc_base_stem(stem)
            if base != stem.strip().lower():
                # This filename actually had a " (Disc N)" marker -- remember
                # it (first disc wins) as a fallback match for a same-titled
                # .m3u playlist, which Skraper never scrapes as its own entry.
                disc_matches.setdefault(base, game)

            if only_filenames is not None:
                if filename not in only_filenames:
                    continue
                seen_filenames.add(filename)

            idx = len(seen_filenames) if only_filenames is not None else matched + unmatched + 1
            if progress_callback:
                progress_callback(idx, total, filename)

            rom = rom_by_filename(conn, system_name, filename)
            if rom is None:
                # ROM referenced by Skraper isn't in our index yet -- run
                # `scan` first, or it'll just be skipped on this pass.
                unmatched += 1
                continue

            _apply_game_to_rom(conn, config, game, rom, filename, export_path, media_root,
                                folder_index_cache, idx, total, progress_callback,
                                prefer_direct_probe=only_filenames is not None)
            matched += 1

        # Second pass: multi-disc games represented as .m3u playlists never
        # appear as their own <game> entry in Skraper's export -- Skraper
        # scrapes each disc file individually, matched by its own
        # name/checksum. An .m3u's base title (filename minus extension)
        # matches its disc files' base title once their " (Disc N)" marker
        # is stripped, so borrow the first disc's metadata/media for the
        # .m3u ROM ES-DE actually launches. Without this, the .m3u is left
        # with no <name> at all, and ES-DE falls back to showing the raw
        # filename -- extension, region tag, and all.
        if disc_matches:
            missing = rom_filenames_missing_metadata(conn, system_name)
            m3u_candidates = {f for f in missing if f.lower().endswith(".m3u")}
            if only_filenames is not None:
                m3u_candidates &= only_filenames

            for filename in sorted(m3u_candidates):
                game = disc_matches.get(_disc_base_stem(Path(filename).stem))
                if game is None:
                    continue
                rom = rom_by_filename(conn, system_name, filename)
                if rom is None:
                    continue

                disc_filename = Path(game.findtext("path", default="")).name
                seen_filenames.add(filename)  # matched via fallback -- don't report as not-in-export

                idx = matched + unmatched + 1
                if progress_callback:
                    progress_callback(idx, total, filename)
                _apply_game_to_rom(conn, config, game, rom, filename, export_path, media_root,
                                    folder_index_cache, idx, total, progress_callback,
                                    prefer_direct_probe=only_filenames is not None,
                                    source_filename=disc_filename)
                matched += 1

        # Third pass: a ROM re-encoded to a different container after being
        # scraped (most commonly .cue/.bin -> .chd, via chdman) keeps the
        # same base title but no longer matches Skraper's <path> filename
        # exactly -- e.g. our indexed "Loom (USA).chd" vs. Skraper's own
        # "Loom (USA).cue". Still-unmatched ROMs get one more attempt here,
        # purely by extension-stripped stem, before being left as-is.
        if stem_matches:
            missing = rom_filenames_missing_metadata(conn, system_name)
            if only_filenames is not None:
                missing &= only_filenames

            for filename in sorted(missing):
                game = stem_matches.get(Path(filename).stem.strip().lower())
                if game is None:
                    continue
                rom = rom_by_filename(conn, system_name, filename)
                if rom is None:
                    continue

                source_filename = Path(game.findtext("path", default="")).name
                seen_filenames.add(filename)  # matched via fallback -- don't report as not-in-export

                idx = matched + unmatched + 1
                if progress_callback:
                    progress_callback(idx, total, filename)
                _apply_game_to_rom(conn, config, game, rom, filename, export_path, media_root,
                                    folder_index_cache, idx, total, progress_callback,
                                    prefer_direct_probe=only_filenames is not None,
                                    source_filename=source_filename)
                matched += 1

    result = {"matched": matched, "unmatched": unmatched}
    if only_filenames is not None:
        result["not_in_export"] = sorted(only_filenames - seen_filenames)
    return result


def _apply_game_to_rom(conn, config: Config, game: ET.Element, rom, filename: str,
                        export_path: Path, media_root: Path,
                        folder_index_cache: dict[Path, dict[str, Path]],
                        idx: int, total: int, progress_callback: ProgressCallback | None,
                        prefer_direct_probe: bool, source_filename: str | None = None) -> None:
    """Writes one Skraper <game> element's metadata, and copies its media,
    onto one of our indexed ROMs. Shared between the main path-matched loop
    and the .m3u fallback pass in import_skraper_export -- `game` and `rom`
    don't have to share the same filename there: `filename` (the .m3u's own
    name) drives what the copied media gets renamed to, while
    `source_filename` (the matched disc file's name, defaulting to
    `filename` when not given) drives where the folder-convention sweep
    looks for it -- Skraper's media/ folder holds it under the disc's name,
    not the .m3u's.
    """
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

    def _on_copy(kind: str, dest_name: str) -> None:
        if progress_callback:
            progress_callback(idx, total, f"{filename} -> {kind}/{dest_name}")

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

    # Fallback: for any kind not already picked up from the XML, look for a
    # same-named file under media/<folder>/ directly.
    _scan_media_folders(conn, rom["id"], filename, export_path, media_root,
                         skip_kinds=found_kinds, folder_index_cache=folder_index_cache,
                         config=config, on_copy=_on_copy,
                         prefer_direct_probe=prefer_direct_probe,
                         source_filename=source_filename)


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

    Some Skraper exports (confirmed: pcenginecd, where each disc's own .cue
    lives in its own folder) nest media under a per-game subfolder mirroring
    the ROM's own folder structure -- "media/screenshots/Loom (USA)/Loom
    (USA).png" -- instead of a flat file directly under media/<kind>/, seen
    everywhere else in this same install (snes/genesis/pcengine all flat).
    A directory entry is checked for a same-named file one level in as a
    second, lower-priority convention.
    """
    index: dict[str, Path] = {}
    try:
        entries = list(folder_path.iterdir())
    except OSError:
        return index

    for entry in entries:
        if entry.is_file() and entry.suffix.lower() in extensions:
            normalized = _HASH_SUFFIX_RE.sub("", entry.stem)
            if normalized not in index or entry.stem == normalized:
                index[normalized] = entry
        elif entry.is_dir():
            for ext in extensions:
                nested = entry / f"{entry.name}{ext}"
                if nested.exists():
                    index.setdefault(_HASH_SUFFIX_RE.sub("", entry.name), nested)
                    break

    return index


def _probe_file(folder_path: Path, stem: str, extensions: tuple[str, ...]) -> Path | None:
    """Direct existence check for `<stem><ext>`, one extension at a time --
    a handful of stats instead of listing the whole folder. Cheap when only
    a few ROMs are being processed (see `only_filenames` in
    import_skraper_export), but WON'T find hash-suffixed filenames or the
    nested-subfolder convention (see _build_folder_index) -- callers should
    fall back to the full index on a miss.
    """
    for ext in extensions:
        candidate = folder_path / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    for ext in extensions:
        nested = folder_path / stem / f"{stem}{ext}"
        if nested.exists():
            return nested
    return None


def _scan_media_folders(conn, rom_id: int, rom_filename: str, export_path: Path,
                         media_root: Path, skip_kinds: set[str],
                         folder_index_cache: dict[Path, dict[str, Path]],
                         config: Config,
                         on_copy: Callable[[str, str], None] | None = None,
                         prefer_direct_probe: bool = False,
                         source_filename: str | None = None) -> None:
    """source_filename, if given, is searched for in media/<folder>/ instead
    of rom_filename -- used by the .m3u fallback pass, where the media
    actually sits under the matched disc file's name (e.g. "Foo (Disc
    1).png"), but the copy must still be named after rom_filename (the .m3u
    itself) for ES-DE to associate it with the right game.
    """
    stem = Path(source_filename if source_filename is not None else rom_filename).stem
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

        match: Path | None = None
        # Targeted (--rom) imports: a few stats beats listing a folder that
        # might have thousands of entries, just to look up one ROM. Only
        # falls back to the full index (which also catches hash-suffixed
        # filenames a direct probe can't) on a miss, or if this run is
        # already processing enough ROMs that indexing-once pays for itself.
        if prefer_direct_probe and folder_path not in folder_index_cache:
            match = _probe_file(folder_path, stem, extensions)

        if match is None:
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
