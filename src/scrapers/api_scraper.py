"""ScreenScraper API v2 client.

Request/response shape reverse-engineered from the Skyscraper project's
C++ client (github.com/muldjord/skyscraper/blob/master/src/screenscraper.cpp),
which is the most reliable public reference available -- ScreenScraper's own
docs are a PHP-rendered parameter table with no example payloads.

Key facts baked in here:
  - Every piece of client software needs its OWN devid/devpassword, issued
    by ScreenScraper on request. This is separate from your personal
    ssid/sspassword login. Get one at:
    https://www.screenscraper.fr/forumsujets.php?frub=12
  - Matching is by crc32/md5/sha1 + filename + filesize against
    jeuInfos.php. Anonymous/unregistered users get a low daily quota and a
    single request thread; a personal login raises both.
  - The API responds with French error strings for known failure modes
    (quota exceeded, API closed, bad devid). Those are treated as fatal for
    the whole scrape run rather than a single-ROM failure, since retrying
    ROM-by-ROM into a closed API just burns your quota further.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import requests

from ..config import Config
from .screenscraper_ids import lookup_system_id

# (index, total, status_message) -- called once per ROM as it's considered,
# and again with the outcome (scraped/skipped/not found) once known.
ProgressCallback = Callable[[int, int, str], None]

API_BASE = "https://www.screenscraper.fr/api2/jeuInfos.php"
MIN_REQUEST_INTERVAL_SECONDS = 2.0  # conservative default; lower if you have a registered/donor account
REQUEST_TIMEOUT_SECONDS = 20

# Preference order used to pick a single value out of ScreenScraper's
# region/language-tagged arrays (noms, dates, synopsis, medias).
REGION_PRIORITY = ["us", "wor", "eu", "jp", "ss"]
LANGUAGE_PRIORITY = ["en", "wor"]

# jeu.medias[].type -> our internal media "kind". Kind names match ES-DE's
# own downloaded_media/<system>/<kind>/ folder names directly (confirmed
# against ES-DE's own source and a real downloaded_media listing) rather
# than an arbitrary vocabulary of our own. Note: ES-DE appears to only have
# a single "marquees" folder, not a separate "wheels" -- ScreenScraper's
# wheel/wheel-hd and screenmarquee media types are both folded into it here.
MEDIA_TYPE_MAP: dict[str, list[str]] = {
    "covers": ["box-2D", "box-3D"],
    "screenshots": ["ss", "sstitle"],
    "marquees": ["screenmarquee", "wheel-hd", "wheel"],
    "videos": ["video-normalized", "video"],
    "fanart": ["fanart"],
}


class ApiScraperError(RuntimeError):
    """Non-fatal: this one ROM failed, keep going."""


class FatalScraperError(RuntimeError):
    """Fatal: stop scraping the rest of the batch (quota/auth/API-down)."""


class _RateLimiter:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last_request: float = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.monotonic()


_limiter = _RateLimiter(MIN_REQUEST_INTERVAL_SECONDS)


def scrape_system(config: Config, system_name: str, only_missing: bool = True,
                   progress_callback: ProgressCallback | None = None) -> dict:
    from ..db import connect, roms_for_system, upsert_media, upsert_metadata  # avoid import cycle

    provider = config.scraper_api.provider
    if provider != "screenscraper":
        raise ApiScraperError(f"api_scraper only implements 'screenscraper', config says '{provider}'")

    devid = config.scraper_api.devid()
    devpassword = config.scraper_api.devpassword()
    if not devid or not devpassword:
        raise FatalScraperError(
            "Missing ScreenScraper devid/devpassword. Set devid_env/devpassword_env in "
            "config.yaml to point at environment variables holding your registered developer "
            "credentials (request one at https://www.screenscraper.fr/forumsujets.php?frub=12)."
        )

    system_id = lookup_system_id(system_name, config.systems[system_name].screenscraper_id)
    if system_id is None:
        raise FatalScraperError(
            f"No ScreenScraper systemeid known for system '{system_name}'. "
            f"Add `screenscraper_id: <id>` under this system in config.yaml."
        )

    media_root = config.media_root / system_name
    scraped, skipped, failed = 0, 0, 0
    abort_reason: str | None = None

    with connect(config.db_path) as conn:
        roms = roms_for_system(conn, system_name)
        total = len(roms)
        for idx, rom in enumerate(roms, start=1):
            if progress_callback:
                progress_callback(idx, total, rom["rom_filename"])

            has_metadata = conn.execute(
                "SELECT 1 FROM metadata WHERE rom_id = ?", (rom["id"],)
            ).fetchone()
            if only_missing and has_metadata:
                skipped += 1
                if progress_callback:
                    progress_callback(idx, total, f"{rom['rom_filename']} (already have metadata, skipped)")
                continue

            try:
                jeu = _fetch_jeu(config, system_id, rom)
            except FatalScraperError as e:
                abort_reason = str(e)
                break
            except ApiScraperError:
                failed += 1
                if progress_callback:
                    progress_callback(idx, total, f"{rom['rom_filename']} (request failed)")
                continue

            if jeu is None:
                failed += 1  # not found in ScreenScraper's database
                if progress_callback:
                    progress_callback(idx, total, f"{rom['rom_filename']} (not found in ScreenScraper)")
                continue

            metadata = _extract_metadata(jeu)
            upsert_metadata(conn, rom_id=rom["id"], source="api:screenscraper", **metadata)

            media = _download_media(jeu, media_root, rom["rom_filename"], config)
            for kind, local_path in media.items():
                upsert_media(conn, rom_id=rom["id"], kind=kind, local_path=local_path)

            scraped += 1
            if progress_callback:
                progress_callback(idx, total, f"{rom['rom_filename']} -> scraped ({len(media)} media files)")

    return {"scraped": scraped, "skipped": skipped, "failed": failed, "abort_reason": abort_reason}


def _fetch_jeu(config: Config, system_id: int, rom) -> dict | None:
    """Calls jeuInfos.php for one ROM. Returns the `jeu` object, or None if
    ScreenScraper has no match. Raises ApiScraperError for a request-level
    problem with this ROM, FatalScraperError for quota/auth/API-down.
    """
    api = config.scraper_api
    params = {
        "devid": api.devid(),
        "devpassword": api.devpassword(),
        "softname": api.softname,
        "output": "json",
        "systemeid": system_id,
        "romnom": rom["rom_filename"],
        "romtaille": rom["size_bytes"],
    }
    if api.username():
        params["ssid"] = api.username()
    if api.password():
        params["sspassword"] = api.password()
    if rom["crc32"]:
        params["crc"] = rom["crc32"]
    if rom["md5"]:
        params["md5"] = rom["md5"]
    if rom["sha1"]:
        params["sha1"] = rom["sha1"]

    _limiter.wait()
    try:
        resp = requests.get(API_BASE, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        raise ApiScraperError(f"request failed for {rom['rom_filename']}: {e}") from e

    text_head = resp.text[:1024]

    # ScreenScraper returns plain-text French error messages (not JSON) for
    # these conditions -- check before attempting to parse JSON.
    if "non trouvée" in text_head or "n'est pas dans la base" in text_head:
        return None  # genuinely not found, not an error
    if "API totalement fermé" in text_head:
        raise FatalScraperError("ScreenScraper API is currently closed (maintenance).")
    if "Votre quota de scrape est" in text_head:
        raise FatalScraperError("Daily ScreenScraper request quota reached.")
    if "Le logiciel de scrape utilisé a été blacklisté" in text_head:
        raise FatalScraperError("This software's devid has been blacklisted by ScreenScraper.")
    if "closed for non-registered members" in text_head or "fermé pour les non membres" in text_head:
        raise FatalScraperError(
            "ScreenScraper API is closed to unregistered/inactive users at this thread level. "
            "Set username_env/password_env to a personal ScreenScraper login."
        )

    try:
        data = resp.json()
    except ValueError:
        raise ApiScraperError(f"non-JSON response for {rom['rom_filename']}: {text_head[:200]}")

    header = data.get("header", {})
    if str(header.get("success", "true")).lower() == "false":
        raise ApiScraperError(f"API error for {rom['rom_filename']}: {header.get('error')}")

    return data.get("response", {}).get("jeu")


def _pick_text(items: list[dict], key: str, priority: list[str]) -> str | None:
    """Picks `text` from the first item matching a preferred key value
    (e.g. region='us'), falling back to the first item if none match.
    """
    if not items:
        return None
    for pref in priority:
        for item in items:
            if item.get(key) == pref:
                return item.get("text")
    return items[0].get("text")


def _pick_media_url(items: list[dict]) -> str | None:
    for pref in REGION_PRIORITY:
        for item in items:
            if item.get("region") == pref and item.get("url"):
                return item["url"]
    for item in items:
        if item.get("url"):
            return item["url"]
    return None


def _extract_metadata(jeu: dict) -> dict:
    title = _pick_text(jeu.get("noms", []), "region", REGION_PRIORITY)
    description = _pick_text(jeu.get("synopsis", []), "langue", LANGUAGE_PRIORITY)
    release_date = _pick_text(jeu.get("dates", []), "region", REGION_PRIORITY)
    developer = (jeu.get("developpeur") or {}).get("text")
    publisher = (jeu.get("editeur") or {}).get("text")
    players = (jeu.get("joueurs") or {}).get("text")

    genres = []
    for genre in jeu.get("genres", []):
        name = _pick_text(genre.get("noms", []), "langue", LANGUAGE_PRIORITY)
        if name:
            genres.append(name)

    rating_raw = (jeu.get("note") or {}).get("text")
    rating = None
    if rating_raw:
        try:
            # ScreenScraper rates 0-20; normalize to 0-1 (ES-DE's gamelist
            # <rating> convention: fraction of 5 stars).
            rating = float(rating_raw) / 20.0
        except ValueError:
            rating = None

    return {
        "title": title,
        "description": description,
        "release_date": release_date,
        "developer": developer,
        "publisher": publisher,
        "genre": ", ".join(genres) if genres else None,
        "players": players,
        "rating": rating,
    }


def _download_media(jeu: dict, media_root: Path, rom_filename: str, config: Config) -> dict[str, str]:
    medias = jeu.get("medias", [])
    result: dict[str, str] = {}

    for kind, type_prefs in MEDIA_TYPE_MAP.items():
        if not config.is_media_enabled(kind):
            continue
        url = None
        for type_name in type_prefs:
            candidates = [m for m in medias if m.get("type") == type_name]
            if candidates:
                url = _pick_media_url(candidates)
            if url:
                break
        if not url:
            continue

        ext = Path(url.split("?")[0]).suffix or (".mp4" if kind == "videos" else ".png")
        dest = media_root / kind / f"{Path(rom_filename).stem}{ext}"
        dest.parent.mkdir(parents=True, exist_ok=True)

        if _download_file(url, dest):
            result[kind] = str(dest)

    return result


def _download_file(url: str, dest: Path, min_size: int = 256) -> bool:
    _limiter.wait()
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.RequestException:
        return False
    if len(resp.content) < min_size:
        return False
    dest.write_bytes(resp.content)
    return True
