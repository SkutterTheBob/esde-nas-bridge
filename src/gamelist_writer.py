"""Emits gamelist.xml (text metadata) per system, for ES-DE to read.

IMPORTANT, confirmed directly from ES-DE's own user guide: ES-DE reads
gamelist.xml for text metadata (name, desc, developer, genre, etc.) but
does NOT use any media tags inside it to find artwork. Media is instead
matched purely by filename against ES-DE's own downloaded_media/<system>/
<mediatype>/ folder structure. That's why our media cache (see api_scraper.py
and skraper_import.py) uses ES-DE's own folder-name vocabulary directly
("covers", "screenshots", "marquees", etc. -- confirmed against ES-DE's own
source code and a real downloaded_media listing) and files are named to
match each ROM's filename stem -- config.media_root should point directly
at your ES-DE installation's actual downloaded_media folder so this "just
works" with no separate publish/copy step.

The media tags below are still written into gamelist.xml despite ES-DE
ignoring them for its own rendering, because they're free to include and
keep the file usable if you ever export to a legacy-ES-flavor frontend
(RetroPie-ES, Batocera-ES) that does read them.

<path> here points at wherever config.roms_stub_root/<system> ends up
(see rom_stubs.py) -- gamelist.xml's per-game <path> values are written
relative to the system's <path> folder in es_systems.xml, which is
configured to point at the local stub tree, not the NAS.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import Config
from .db import connect

# Reverse of scrapers/skraper_import.py MEDIA_TAGS. Kind "marquees" round-trips
# through the legacy <marquee> tag, which is exactly what that tag has
# always meant (wheel/logo) -- see skraper_import.py for the historical note.
KIND_TO_TAG = {
    "covers": "image",
    "screenshots": "screenshot",
    "marquees": "marquee",
    "3dboxes": "thumbnail",
    "videos": "video",
    "fanart": "fanart",
    "manuals": "manual",
    "titlescreens": "titlescreen",
    "miximages": "miximage",
    "backcovers": "backcover",
}

# ScreenScraper tags a ROM it's identified as not a game with a
# "ZZZ(notgame):" prefixed title -- either a bare category code with no
# real name ("ZZZ(notgame):#NONGAME", "#BIOS", "#PROGRAMS", "#demo", ...)
# or an actual attempted name ("ZZZ(notgame):Geos 1.2"). Neither half is
# trustworthy: the category codes are obviously useless to show, and the
# named ones are frequently just wrong -- e.g. six different physical
# Minolta camera test-cart ROMs in this library, all with different
# filenames, all got the identical ScreenScraper title "Minolta 2085 Adj.
# Program" (confirmed by inspecting the local DB). Rather than show either
# half, both cases fall back to a title inferred from the ROM's OWN
# filename (region/revision/language tags stripped) -- the one thing
# that's actually specific to that ROM. Doesn't touch the ROM's own
# indexed/stub/media data -- only the <name> written out here.
_NONGAME_PREFIX_RE = re.compile(r"^ZZZ\(notgame\):")

# Trailing "(...)"/"[...]" tag groups in No-Intro/TOSEC/Redump-style
# filenames -- region, revision, language, dump-quality flags, etc., e.g.
# "Foo (USA) (Rev 1) [b].zip". Stripped repeatedly from the end so a
# filename with several tags (the common case) loses all of them, not just
# the last one.
_TRAILING_TAG_RE = re.compile(r"\s*[\(\[][^\(\)\[\]]*[\)\]]\s*$")


def _infer_title_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    title = stem
    while True:
        stripped = _TRAILING_TAG_RE.sub("", title)
        if stripped == title:
            break
        title = stripped
    title = title.strip()
    return title or stem


def write_gamelist(config: Config, system_name: str) -> Path:
    out_dir = config.gamelists_root / system_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "gamelist.xml"

    root = ET.Element("gameList")

    with connect(config.db_path) as conn:
        roms = conn.execute(
            "SELECT * FROM roms WHERE system = ?", (system_name,)
        ).fetchall()

        for rom in roms:
            meta = conn.execute(
                "SELECT * FROM metadata WHERE rom_id = ?", (rom["id"],)
            ).fetchone()
            # Fall back to filename if nothing's been scraped yet, so the
            # game still shows up in the frontend rather than being omitted.
            title = meta["title"] if meta and meta["title"] else rom["rom_filename"]
            if _NONGAME_PREFIX_RE.match(title):
                title = _infer_title_from_filename(rom["rom_filename"])

            game_el = ET.SubElement(root, "game")
            ET.SubElement(game_el, "path").text = f"./{rom['rel_path']}"
            ET.SubElement(game_el, "name").text = title
            if meta:
                _maybe_set(game_el, "desc", meta["description"])
                _maybe_set(game_el, "releasedate", meta["release_date"])
                _maybe_set(game_el, "developer", meta["developer"])
                _maybe_set(game_el, "publisher", meta["publisher"])
                _maybe_set(game_el, "genre", meta["genre"])
                _maybe_set(game_el, "players", meta["players"])
                if meta["rating"] is not None:
                    ET.SubElement(game_el, "rating").text = str(meta["rating"])

            media_rows = conn.execute(
                "SELECT * FROM media WHERE rom_id = ?", (rom["id"],)
            ).fetchall()
            for media in media_rows:
                tag = KIND_TO_TAG.get(media["kind"])
                if tag:
                    ET.SubElement(game_el, tag).text = media["local_path"]

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


def _maybe_set(parent: ET.Element, tag: str, value: str | None) -> None:
    if value:
        ET.SubElement(parent, tag).text = value


def write_all(config: Config) -> list[Path]:
    return [write_gamelist(config, system) for system in config.systems]
