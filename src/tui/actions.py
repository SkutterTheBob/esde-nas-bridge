"""Builds the `work(progress_callback) -> str` callables RunScreen runs.

Each function here calls exactly the same underlying functions cli.py's
commands call (scan_system, import_skraper_export, scrape_system,
write_stubs_for_system, write_gamelist, write_all, generate_all,
write_custom_systems) and formats the same summary text the CLI prints --
this module only supplies the "which functions, in what order, with what
label" glue, never reimplements what those functions actually do.
"""
from __future__ import annotations

from typing import Callable

from ..config import Config
from ..db import connect, init_db
from ..es_systems_generator import write_custom_systems
from ..gamelist_writer import write_gamelist
from ..indexer import find_stale_roms, scan_all, scan_system
from ..rom_stubs import write_stubs_for_system
from ..scrapers.api_scraper import FatalScraperError, scrape_system
from ..scrapers.skraper_import import import_skraper_export

ProgressCallback = Callable[[int, "int | None", str], None]
Work = Callable[[ProgressCallback], str]


def scan_work(config: Config, system_name: "str | None", checksums: bool) -> Work:
    def work(progress_callback: ProgressCallback) -> str:
        init_db(config.db_path)
        if system_name:
            count = scan_system(config, system_name, compute_checksums=checksums,
                                 progress_callback=progress_callback)
            return f"{system_name}: indexed {count} ROMs"
        results = scan_all(config, compute_checksums=checksums, progress_callback=progress_callback)
        return "\n".join(f"{name}: indexed {count} ROMs" for name, count in results.items())
    return work


def import_skraper_work(config: Config, system_name: str, missing_only: bool) -> Work:
    def work(progress_callback: ProgressCallback) -> str:
        export_dir = config.skraper_imports.get(system_name)
        if not export_dir:
            return f"No skraper_imports entry for system '{system_name}' in config.yaml"

        only_filenames = None
        if missing_only:
            from ..db import rom_filenames_missing_metadata
            with connect(config.db_path) as conn:
                only_filenames = rom_filenames_missing_metadata(conn, system_name)
            if not only_filenames:
                return f"{system_name}: nothing missing metadata -- every indexed ROM already has some."

        try:
            result = import_skraper_export(
                config, system_name, export_dir, progress_callback=progress_callback,
                only_filenames=only_filenames,
            )
        except FileNotFoundError:
            return (
                f"No gamelist.xml found in {export_dir} -- run Skraper against that folder "
                f"first (or fix the skraper_imports entry for '{system_name}' in config.yaml)."
            )

        lines = [f"{system_name}: matched {result['matched']}, unmatched {result['unmatched']}"]
        not_in_export = result.get("not_in_export")
        if not_in_export:
            lines.append(
                "Not found in this Skraper export (check spelling, or it was never scraped): "
                + ", ".join(not_in_export)
            )
        return "\n".join(lines)
    return work


def scrape_work(config: Config, system_name: str, rescrape_all: bool) -> Work:
    def work(progress_callback: ProgressCallback) -> str:
        try:
            result = scrape_system(config, system_name, only_missing=not rescrape_all,
                                    progress_callback=progress_callback)
        except FatalScraperError as e:
            return str(e)

        line = (
            f"{system_name}: scraped {result['scraped']}, skipped {result['skipped']}, "
            f"failed {result['failed']}"
        )
        if result.get("abort_reason"):
            line += f"\nScrape run stopped early: {result['abort_reason']}"
        return line
    return work


def publish_work(config: Config, system_name: "str | None") -> Work:
    def work(progress_callback: ProgressCallback) -> str:
        systems = [system_name] if system_name else list(config.systems)
        lines = []
        for name in systems:
            stub_count = write_stubs_for_system(config, name, progress_callback=progress_callback)
            gamelist_path = write_gamelist(config, name)
            lines.append(f"{name}: {stub_count} stub files, wrote {gamelist_path}")
        return "\n".join(lines)
    return work


def sync_work(config: Config, system_name: "str | None", checksums: bool, skip_skraper: bool) -> Work:
    def work(progress_callback: ProgressCallback) -> str:
        init_db(config.db_path)
        systems = [system_name] if system_name else list(config.systems)
        all_lines = []
        for name in systems:
            lines = [f"=== {name} ==="]

            count = scan_system(config, name, compute_checksums=checksums, progress_callback=progress_callback)
            lines.append(f"  scan: indexed {count} ROMs")

            if not skip_skraper and name in config.skraper_imports:
                try:
                    result = import_skraper_export(
                        config, name, config.skraper_imports[name], progress_callback=progress_callback
                    )
                except FileNotFoundError:
                    lines.append(
                        f"  import-skraper: no gamelist.xml found in {config.skraper_imports[name]} "
                        f"-- run Skraper against that folder first. Skipping import for '{name}'."
                    )
                else:
                    lines.append(f"  import-skraper: matched {result['matched']}, unmatched {result['unmatched']}")

            stub_count = write_stubs_for_system(config, name, progress_callback=progress_callback)
            gamelist_path = write_gamelist(config, name)
            lines.append(f"  publish: {stub_count} stub files, wrote {gamelist_path}")

            stale_count = len(find_stale_roms(config, name))
            if stale_count:
                lines.append(
                    f"  note: {stale_count} previously-indexed ROM(s) weren't found on this scan "
                    f"(removed/renamed on the NAS?) -- run Prune Removed to review."
                )
            all_lines.append("\n".join(lines))
        return "\n\n".join(all_lines)
    return work


def generate_es_systems_work(config: Config) -> Work:
    def work(progress_callback: ProgressCallback) -> str:
        target = write_custom_systems(config)
        return (
            f"Wrote {len(config.systems)} system entries directly to {target}\n"
            "Restart ES-DE (fully close, not just back out of the menu) to pick these up."
        )
    return work
