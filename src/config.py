"""Loads and validates config.yaml into typed structures."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _strip_wrapping_quotes(value: str) -> str:
    """Defensive normalization for path-like config values: strips one
    matching pair of leading/trailing quote characters if present.

    A bare literal quote character at the very start AND end of a real
    path is never legitimate -- but it's an easy mistake to make copying a
    path from a terminal/example that displayed it quoted for readability
    (confirmed: caused a real launch failure -- a doubled-quote mess when
    the value was later embedded in a constructed command line). Safe to
    normalize away unconditionally. Only applied to whole-value fields
    (paths), never to command templates like `emulator.args`, where
    internal quotes are meaningful and must be preserved exactly.
    """
    if isinstance(value, str) and len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


@dataclass
class MountConfig:
    type: str
    share: str
    mount_point: str
    credentials_file: str | None = None   # Linux cifs-utils only
    username_env: str | None = None       # Windows net use only (see mount.py)
    password_env: str | None = None       # Windows net use only -- prefer Credential Manager instead
    timeout_seconds: int = 15

    def username(self) -> str | None:
        return os.environ.get(self.username_env) if self.username_env else None

    def password(self) -> str | None:
        return os.environ.get(self.password_env) if self.password_env else None


@dataclass
class NasSource:
    name: str
    mode: str  # "mapped" | "on_demand"
    root: str
    mount: MountConfig | None = None


@dataclass
class EmulatorConfig:
    """A standalone (non-RetroArch) emulator for one system, e.g. DuckStation
    for PSX. `args` is a command-line template; `{rom}` gets replaced with
    the resolved absolute ROM path (already quoted if it needs quoting --
    don't add your own quotes around {rom} in the template)."""
    binary: str
    args: str = "{rom}"  # default: just pass the rom path as the sole argument
    # Confirmed on real hardware (2026-08-13, DuckStation): some emulators
    # fail with PermissionError/WinError 5 when launched via Windows'
    # process-creation API directly, but launch fine when the immediate
    # parent process is cmd.exe instead (root cause not fully understood --
    # ruled out elevation, Defender, and wrong-binary theories; empirically
    # confirmed cmd.exe-mediated launch works). Set this to true if a
    # standalone emulator hits that specific error. Implemented safely --
    # still uses Python's list-based argument passing (each argument
    # properly quoted automatically), not a raw shell string, so this does
    # NOT introduce shell-metacharacter injection risk from ROM filenames.
    use_shell: bool = False


@dataclass
class SystemConfig:
    name: str
    nas_source: str
    subdir: str
    extensions: list[str]
    # Exactly one of these two must be set -- validated in load_config.
    # retroarch_core: use RetroArch with this libretro core.
    # emulator: launch a standalone emulator (e.g. DuckStation) instead.
    retroarch_core: str | None = None
    emulator: EmulatorConfig | None = None
    screenscraper_id: int | None = None  # overrides the built-in name->id lookup
    fullname: str | None = None          # display name for generated es_systems.xml (defaults to `name`)
    theme: str | None = None             # ES-DE theme id for generated es_systems.xml (defaults to `name`)


@dataclass
class ScraperApiConfig:
    provider: str
    softname: str = "esde-nas-bridge"
    # "Developer" credentials: ScreenScraper requires every piece of client
    # software to register for its own devid/devpassword pair (request one
    # at https://www.screenscraper.fr/forumsujets.php?frub=12). This is
    # separate from your personal login below and is NOT optional -- the
    # API rejects unregistered software.
    devid_env: str | None = None
    devpassword_env: str | None = None
    # Personal ScreenScraper account login (ssid/sspassword). Optional but
    # raises your daily quota and thread count vs anonymous "leecher" use.
    username_env: str | None = None
    password_env: str | None = None

    def devid(self) -> str | None:
        return os.environ.get(self.devid_env) if self.devid_env else None

    def devpassword(self) -> str | None:
        return os.environ.get(self.devpassword_env) if self.devpassword_env else None

    def username(self) -> str | None:
        return os.environ.get(self.username_env) if self.username_env else None

    def password(self) -> str | None:
        return os.environ.get(self.password_env) if self.password_env else None


@dataclass
class Config:
    db_path: Path
    media_root: Path
    gamelists_root: Path
    roms_stub_root: Path
    nas_sources: dict[str, NasSource]
    systems: dict[str, SystemConfig]
    skraper_imports: dict[str, str]
    scraper_priority: list[str]
    scraper_api: ScraperApiConfig
    retroarch_binary: str
    retroarch_core_dir: str
    # None = every media type is cached (default). A list (including an
    # empty one, for ROMs-only) restricts import-skraper/scrape to only
    # those kinds -- see media_types.py for the canonical vocabulary and
    # `configure-media` in cli.py for the interactive picker.
    enabled_media_types: list[str] | None = None
    # ES-DE's home/portable-data folder (same level as gamelists/,
    # downloaded_media/, es_settings.xml). Optional -- if not set, derived
    # as gamelists_root's parent, which holds as long as gamelists_root is
    # the standard "<home>/gamelists" (true for every config in this repo
    # so far). Used to find custom_systems/es_systems.xml -- ES-DE's own
    # sanctioned override location; never write to the bundled
    # resources/systems/<os>/es_systems.xml, which gets overwritten on
    # every ES-DE update.
    es_de_home: Path | None = None

    def nas_source_for_system(self, system_name: str) -> NasSource:
        sys_cfg = self.systems[system_name]
        return self.nas_sources[sys_cfg.nas_source]

    def resolved_es_de_home(self) -> Path:
        return self.es_de_home if self.es_de_home is not None else self.gamelists_root.parent

    def custom_systems_path(self) -> Path:
        return self.resolved_es_de_home() / "custom_systems" / "es_systems.xml"

    def is_media_enabled(self, kind: str) -> bool:
        return self.enabled_media_types is None or kind in self.enabled_media_types


def load_config(path: str | Path) -> Config:
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text())

    nas_sources = {}
    for entry in raw["nas"]:
        mount_cfg = None
        if entry.get("mode") == "on_demand":
            m = entry["mount"]
            mount_cfg = MountConfig(
                type=m["type"],
                share=_strip_wrapping_quotes(m["share"]),
                mount_point=_strip_wrapping_quotes(m["mount_point"]),
                credentials_file=_strip_wrapping_quotes(m.get("credentials_file")) if m.get("credentials_file") else None,
                username_env=m.get("username_env"),
                password_env=m.get("password_env"),
                timeout_seconds=m.get("timeout_seconds", 15),
            )
        nas_sources[entry["name"]] = NasSource(
            name=entry["name"], mode=entry["mode"],
            root=_strip_wrapping_quotes(entry["root"]), mount=mount_cfg,
        )

    systems = {}
    for name, s in (raw.get("systems") or {}).items():
        emulator_raw = s.get("emulator")
        emulator = None
        if emulator_raw:
            emulator = EmulatorConfig(
                binary=_strip_wrapping_quotes(emulator_raw["binary"]),
                args=emulator_raw.get("args", "{rom}"),  # NOT stripped -- internal quotes are meaningful here
                use_shell=bool(emulator_raw.get("use_shell", False)),
            )

        retroarch_core = s.get("retroarch_core")
        if bool(retroarch_core) == bool(emulator):
            # Both set or neither set -- ambiguous either way.
            raise ValueError(
                f"System '{name}': set exactly one of `retroarch_core` or "
                f"`emulator` (got retroarch_core={retroarch_core!r}, "
                f"emulator={'set' if emulator else None!r})."
            )

        systems[name] = SystemConfig(
            name=name,
            nas_source=s["nas_source"],
            subdir=_strip_wrapping_quotes(s["subdir"]),
            extensions=[e.lower() for e in s["extensions"]],
            retroarch_core=retroarch_core,
            emulator=emulator,
            screenscraper_id=s.get("screenscraper_id"),
            fullname=s.get("fullname"),
            theme=s.get("theme"),
        )

    cache = raw["cache"]
    scraper = raw.get("scraper", {})
    api = scraper.get("api", {})

    return Config(
        db_path=Path(_strip_wrapping_quotes(cache["db_path"])),
        media_root=Path(_strip_wrapping_quotes(cache["media_root"])),
        gamelists_root=Path(_strip_wrapping_quotes(cache["gamelists_root"])),
        roms_stub_root=Path(_strip_wrapping_quotes(cache["roms_stub_root"])),
        nas_sources=nas_sources,
        systems=systems,
        skraper_imports={k: _strip_wrapping_quotes(v) for k, v in (raw.get("skraper_imports") or {}).items()},
        scraper_priority=scraper.get("priority", ["skraper_import", "api"]),
        scraper_api=ScraperApiConfig(
            provider=api.get("provider", "screenscraper"),
            softname=api.get("softname", "esde-nas-bridge"),
            devid_env=api.get("devid_env"),
            devpassword_env=api.get("devpassword_env"),
            username_env=api.get("username_env"),
            password_env=api.get("password_env"),
        ),
        retroarch_binary=_strip_wrapping_quotes(raw["retroarch"]["binary"]),
        retroarch_core_dir=_strip_wrapping_quotes(raw["retroarch"]["core_dir"]),
        enabled_media_types=cache.get("enabled_media_types"),
        es_de_home=Path(_strip_wrapping_quotes(cache["es_de_home"])) if cache.get("es_de_home") else None,
    )
