"""Command-line entrypoint.

    python -m src.cli scan [--system NAME] [--checksums]
    python -m src.cli import-skraper <system> [--rom FILENAME ...] [--missing-only]
                                                   # omit both to import the whole export
    python -m src.cli scrape <system> [--all]
    python -m src.cli write-stubs [--system NAME]
    python -m src.cli write-gamelists [--system NAME]
    python -m src.cli publish [--system NAME]     # write-stubs + write-gamelists together
    python -m src.cli sync [--system NAME] [--checksums] [--skip-skraper]
                                                   # scan + import-skraper + publish, one shot
    python -m src.cli generate-es-systems [--output PATH]
                                                   # emit es_systems.xml <system> entries
    python -m src.cli configure-media             # interactively choose which media types to cache
    python -m src.cli clean-media [--system NAME] [--apply]
                                                   # remove already-cached media no longer enabled
    python -m src.cli add-system                  # interactively add a new system to config.yaml
    python -m src.cli prune-removed [--system NAME] [--apply]
                                                   # clean up entries for ROMs no longer on the NAS
    python -m src.cli reset-system <system> [--apply]
                                                   # wipe ALL local cache for one system, unconditionally
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import click

from .config import load_config
from .db import connect, init_db, rom_filenames_missing_metadata
from .es_systems_generator import generate_all, write_custom_systems
from .gamelist_writer import write_all, write_gamelist
from .indexer import find_stale_roms, scan_all, scan_system
from .media_types import ALL_MEDIA_TYPES, LARGE_MEDIA_TYPES
from .rom_stubs import write_stubs_for_system
from .scrapers.api_scraper import FatalScraperError, scrape_system
from .scrapers.skraper_import import import_skraper_export
from .system_names import COMMON_FULLNAMES

CONFIG_OPTION = click.option(
    "--config", "config_path", default="config/config.yaml", show_default=True,
    help="Path to config.yaml",
)


def _make_progress_printer(throttle: int = 100):
    """Returns a progress_callback(count, total, message) matching the
    signature used by scan/import-skraper/scrape/write-stubs.

    In a real terminal: overwrites a single line in place, throttled to
    every 5th update so very fast operations (e.g. scan without
    --checksums) aren't slowed down by excessive terminal I/O, while still
    updating quickly enough to look live.

    Redirected to a file/log (not a tty): throttled to one line per
    `throttle` items instead, so logs stay readable rather than flooded.

    total=None (scan doesn't know the total upfront, since counting first
    would mean walking the NAS tree twice) is rendered as "[count]" instead
    of "[count/total]".
    """
    is_tty = sys.stdout.isatty()
    last_logged = [0]

    def _progress(count: int, total: "int | None", message: str) -> None:
        prefix = f"[{count}/{total}]" if total is not None else f"[{count}]"
        line = f"{prefix} {message}"
        is_last = total is not None and count == total

        if is_tty:
            if count - last_logged[0] >= 5 or is_last or count <= 1:
                click.echo(f"\r{line:<100.100}", nl=False)
                last_logged[0] = count
        elif count - last_logged[0] >= throttle or is_last:
            click.echo(line)
            last_logged[0] = count

    return _progress, (lambda: is_tty)


def _validate_system(config, system_name: str) -> None:
    """Raises a clean error for an unconfigured system name instead of
    letting a raw KeyError (from config.systems[name] deep in indexer.py/
    api_scraper.py/etc.) surface as a Python traceback. Every command that
    accepts a system name from the user should call this right after
    loading config, before doing anything else with that name.
    """
    if system_name not in config.systems:
        available = ", ".join(sorted(config.systems)) or "(none configured yet -- run `add-system`)"
        raise click.ClickException(
            f"No such system '{system_name}' in config.yaml. Configured systems: {available}"
        )


@click.group()
def cli():
    pass


@cli.command()
@CONFIG_OPTION
@click.option("--system", default=None, help="Only scan this system (default: all)")
@click.option("--checksums", is_flag=True, help="Compute checksums (slower, needed for ScreenScraper)")
def scan(config_path, system, checksums):
    """Index ROMs from the NAS into the local cache."""
    config = load_config(config_path)
    if system:
        _validate_system(config, system)
    init_db(config.db_path)

    progress, is_tty = _make_progress_printer()

    if system:
        count = scan_system(config, system, compute_checksums=checksums, progress_callback=progress)
        if is_tty():
            click.echo()
        click.echo(f"{system}: indexed {count} ROMs")
    else:
        results = scan_all(config, compute_checksums=checksums, progress_callback=progress)
        if is_tty():
            click.echo()
        for name, count in results.items():
            click.echo(f"{name}: indexed {count} ROMs")


@cli.command("import-skraper")
@CONFIG_OPTION
@click.argument("system")
@click.option("--rom", "roms", multiple=True,
              help="Only (re-)import this ROM filename, e.g. --rom \"Chrono Trigger (USA).zip\". "
                   "Repeatable. Default: the whole system's export.")
@click.option("--missing-only", is_flag=True,
              help="Only (re-)import ROMs that have no metadata yet in the local cache "
                   "(e.g. freshly scanned, or reappeared after prune-removed cascaded their "
                   "old metadata/media away) -- no need to know filenames. Combines with --rom.")
def import_skraper(config_path, system, roms, missing_only):
    """Merge an existing Skraper export into the cache for one system."""
    config = load_config(config_path)
    _validate_system(config, system)
    export_dir = config.skraper_imports.get(system)
    if not export_dir:
        raise click.ClickException(
            f"No skraper_imports entry for system '{system}' in config.yaml"
        )

    progress, is_tty = _make_progress_printer()
    only_filenames = set(roms) if roms else None
    if missing_only:
        with connect(config.db_path) as conn:
            missing = rom_filenames_missing_metadata(conn, system)
        only_filenames = missing if only_filenames is None else only_filenames | missing
        if not only_filenames:
            click.echo(f"{system}: nothing missing metadata -- every indexed ROM already has some.")
            return
    result = import_skraper_export(
        config, system, export_dir, progress_callback=progress, only_filenames=only_filenames
    )
    if is_tty():
        click.echo()
    click.echo(f"{system}: matched {result['matched']}, unmatched {result['unmatched']}")
    not_in_export = result.get("not_in_export")
    if not_in_export:
        click.echo(
            "Not found in this Skraper export (check spelling, or it was never scraped): "
            + ", ".join(not_in_export)
        )


@cli.command()
@CONFIG_OPTION
@click.argument("system")
@click.option("--all", "rescrape_all", is_flag=True, help="Re-scrape even ROMs that already have metadata")
def scrape(config_path, system, rescrape_all):
    """Fill in metadata/media gaps via the configured API scraper."""
    config = load_config(config_path)
    _validate_system(config, system)
    progress, is_tty = _make_progress_printer()

    try:
        result = scrape_system(config, system, only_missing=not rescrape_all, progress_callback=progress)
    except FatalScraperError as e:
        if is_tty():
            click.echo()
        raise click.ClickException(str(e))

    if is_tty():
        click.echo()
    click.echo(
        f"{system}: scraped {result['scraped']}, skipped {result['skipped']}, failed {result['failed']}"
    )
    if result.get("abort_reason"):
        raise click.ClickException(f"Scrape run stopped early: {result['abort_reason']}")


@cli.command("write-stubs")
@CONFIG_OPTION
@click.option("--system", default=None, help="Only write this system (default: all)")
def write_stubs(config_path, system):
    """Write local zero-byte placeholder ROM files so ES-DE can enumerate
    the system without touching the NAS. Never executed -- launch_wrapper
    resolves the real file by filename lookup."""
    config = load_config(config_path)
    if system:
        _validate_system(config, system)
    progress, is_tty = _make_progress_printer()

    if system:
        count = write_stubs_for_system(config, system, progress_callback=progress)
        if is_tty():
            click.echo()
        click.echo(f"{system}: wrote {count} stub files")
    else:
        for name in config.systems:
            count = write_stubs_for_system(config, name, progress_callback=progress)
            if is_tty():
                click.echo()
            click.echo(f"{name}: wrote {count} stub files")


@cli.command("write-gamelists")
@CONFIG_OPTION
@click.option("--system", default=None, help="Only write this system (default: all)")
def write_gamelists(config_path, system):
    """Emit gamelist.xml (text metadata) for ES-DE to read. Point
    gamelists_root/media_root at ES-DE's real folders in config.yaml so
    this needs no separate publish step."""
    config = load_config(config_path)
    if system:
        _validate_system(config, system)
        path = write_gamelist(config, system)
        click.echo(f"{system}: wrote {path}")
    else:
        for path in write_all(config):
            click.echo(f"wrote {path}")


@cli.command()
@CONFIG_OPTION
@click.option("--system", default=None, help="Only publish this system (default: all)")
def publish(config_path, system):
    """Convenience: write-stubs + write-gamelists together. Run this after
    scan/import-skraper/scrape to make new/updated ROMs show up in ES-DE."""
    config = load_config(config_path)
    if system:
        _validate_system(config, system)
    progress, is_tty = _make_progress_printer()
    systems = [system] if system else list(config.systems)
    for name in systems:
        stub_count = write_stubs_for_system(config, name, progress_callback=progress)
        if is_tty():
            click.echo()
        gamelist_path = write_gamelist(config, name)
        click.echo(f"{name}: {stub_count} stub files, wrote {gamelist_path}")


@cli.command()
@CONFIG_OPTION
@click.option("--system", default=None, help="Only sync this system (default: all)")
@click.option("--checksums", is_flag=True, help="Compute checksums during scan (needed for ScreenScraper)")
@click.option("--skip-skraper", is_flag=True, help="Skip the Skraper import step even if configured")
def sync(config_path, system, checksums, skip_skraper):
    """One-shot: scan + import-skraper (if configured) + publish, in order,
    for one or all systems. This is the command to run whenever your NAS
    library or Skraper export changes -- no need to remember the individual
    steps or their order."""
    config = load_config(config_path)
    if system:
        _validate_system(config, system)
    init_db(config.db_path)
    progress, is_tty = _make_progress_printer()
    systems = [system] if system else list(config.systems)

    for name in systems:
        click.echo(f"=== {name} ===")

        count = scan_system(config, name, compute_checksums=checksums, progress_callback=progress)
        if is_tty():
            click.echo()
        click.echo(f"  scan: indexed {count} ROMs")

        if not skip_skraper and name in config.skraper_imports:
            result = import_skraper_export(
                config, name, config.skraper_imports[name], progress_callback=progress
            )
            if is_tty():
                click.echo()
            click.echo(f"  import-skraper: matched {result['matched']}, unmatched {result['unmatched']}")

        stub_count = write_stubs_for_system(config, name, progress_callback=progress)
        if is_tty():
            click.echo()
        gamelist_path = write_gamelist(config, name)
        click.echo(f"  publish: {stub_count} stub files, wrote {gamelist_path}")

        stale_count = len(find_stale_roms(config, name))
        if stale_count:
            click.echo(
                f"  note: {stale_count} previously-indexed ROM(s) weren't found on this scan "
                f"(removed/renamed on the NAS?) -- run `prune-removed --system {name}` to review."
            )


@cli.command("generate-es-systems")
@CONFIG_OPTION
@click.option("--output", default=None,
              help="Review-only: write to this path instead of ES-DE's real "
                   "custom_systems/es_systems.xml (no merge, just a fresh dump).")
def generate_es_systems(config_path, output):
    """Writes <system> entries for every configured system directly into
    ES-DE's own custom_systems/es_systems.xml -- the location ES-DE's docs
    specify for exactly this purpose, safe across ES-DE updates (unlike
    the bundled resources/systems/<os>/es_systems.xml, which gets
    overwritten on every update -- never edit that file directly).
    Existing entries for OTHER systems (added by hand, or by another tool)
    are left untouched; only systems configured here get replaced, so
    re-running this is always safe. Pass --output to instead write a
    standalone file for review, without touching ES-DE's real config."""
    config = load_config(config_path)

    if output:
        xml = generate_all(config)
        Path(output).write_text(xml, encoding="utf-8")
        click.echo(f"Wrote {len(config.systems)} system entries to {output} (review-only, not applied).")
        return

    target = write_custom_systems(config)
    click.echo(f"Wrote {len(config.systems)} system entries directly to {target}")
    click.echo(
        "Restart ES-DE (fully close, not just back out of the menu) to pick these up."
    )
    click.echo(
        "Note: this doesn't touch ES-DE's bundled default systems -- if any of "
        "those still have real content on your NAS with a leftover gamelist.xml "
        "in it, ES-DE may still check that system's default NAS path at startup. "
        "Configure it here too (or clean up the stale gamelist.xml on the NAS) "
        "if that system's startup checks are adding noticeable time."
    )
    click.echo(
        "Reminder -- es_settings.xml also needs: ParseGamelistOnly=true, "
        "LegacyGamelistFileLocation unset/false, MediaDirectory unset (see TESTING.md)."
    )


@cli.command("configure-media")
@CONFIG_OPTION
def configure_media(config_path):
    """Interactively choose which media types to cache locally. Videos and
    manuals are usually the biggest disk-space users for a large library --
    deselect everything for ROMs-only mode (metadata still cached, no art
    at all)."""
    config = load_config(config_path)
    current = set(config.enabled_media_types) if config.enabled_media_types is not None else set(ALL_MEDIA_TYPES)

    while True:
        click.echo("\nMedia types to cache locally:")
        for i, kind in enumerate(ALL_MEDIA_TYPES, start=1):
            mark = "x" if kind in current else " "
            hint = "  (often large)" if kind in LARGE_MEDIA_TYPES else ""
            click.echo(f"  {i:2}. [{mark}] {kind}{hint}")

        click.echo(
            "\nEnter numbers to toggle (e.g. \"7,11\"), \"all\", \"none\", "
            "or press Enter to confirm as shown:"
        )
        choice = click.prompt("> ", default="", show_default=False).strip().lower()

        if choice == "":
            break
        elif choice == "all":
            current = set(ALL_MEDIA_TYPES)
        elif choice == "none":
            current = set()
        else:
            try:
                indices = [int(x.strip()) for x in choice.split(",") if x.strip()]
            except ValueError:
                click.echo("Couldn't parse that -- use comma-separated numbers, \"all\", or \"none\".")
                continue
            for i in indices:
                if not (1 <= i <= len(ALL_MEDIA_TYPES)):
                    click.echo(f"'{i}' isn't a valid option, ignoring it.")
                    continue
                kind = ALL_MEDIA_TYPES[i - 1]
                if kind in current:
                    current.discard(kind)
                else:
                    current.add(kind)

    enabled = [k for k in ALL_MEDIA_TYPES if k in current]  # keep canonical order

    click.echo()
    if not enabled:
        click.echo("Confirmed: no media types selected -- ROMs only, no local art/video/manual caching.")
    elif len(enabled) == len(ALL_MEDIA_TYPES):
        click.echo("Confirmed: all media types selected (same as leaving this unset).")
    else:
        click.echo(f"Confirmed: {', '.join(enabled)}")

    yaml_line = f"enabled_media_types: [{', '.join(enabled)}]"
    if _try_update_config_file(config_path, yaml_line):
        click.echo(f"\nUpdated {config_path}.")
    else:
        click.echo(f"\nCouldn't safely auto-update {config_path} (unexpected file structure).")
        click.echo("Add this line yourself under the `cache:` section:")
        click.echo(f"  {yaml_line}")

    click.echo(
        "\nThis only affects what future import-skraper/scrape runs cache -- "
        "it doesn't delete media already on disk. Re-run import-skraper/scrape "
        "to apply the new selection to your existing library."
    )


def _try_update_config_file(config_path: str, new_line: str) -> bool:
    """Surgically updates (or inserts) a single `enabled_media_types:` line
    in config.yaml via text search-and-replace, rather than a full
    YAML parse+re-dump -- PyYAML doesn't preserve comments/formatting on
    round-trip, which would silently strip all the explanatory comments
    from the user's config.yaml. Returns False (making no changes) if the
    file doesn't look like the expected shape, rather than guessing.
    """
    path = Path(config_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False

    existing_pattern = re.compile(r"^[ \t]*enabled_media_types[ \t]*:.*$", re.MULTILINE)
    if existing_pattern.search(text):
        text = existing_pattern.sub(f"  {new_line}", text, count=1)
    else:
        anchor_pattern = re.compile(r"^([ \t]*roms_stub_root[ \t]*:.*)$", re.MULTILINE)
        if not anchor_pattern.search(text):
            return False
        text = anchor_pattern.sub(lambda m: f"{m.group(1)}\n  {new_line}", text, count=1)

    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        return False
    return True


def _append_to_yaml_section(config_path: str, section_name: str, block: str) -> bool:
    """Appends `block` (already-indented YAML lines) as the last entry
    under a top-level `section_name:` mapping, via text manipulation
    rather than a full YAML parse+re-dump (see _try_update_config_file's
    docstring for why). Finds the section header, then the next top-level
    (non-indented, non-blank, non-comment) line or end of file, and
    inserts just before that boundary. Returns False without changing
    anything if the section header isn't found.
    """
    path = Path(config_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False

    header_pattern = re.compile(rf"^{re.escape(section_name)}[ \t]*:[ \t]*$", re.MULTILINE)
    header_match = header_pattern.search(text)
    if not header_match:
        return False

    # Find the next top-level key after the section header: a line with no
    # leading whitespace, ignoring blank lines and comments (both of which
    # can legitimately appear between entries).
    boundary_pattern = re.compile(r"^\S", re.MULTILINE)
    search_start = header_match.end()
    boundary_match = None
    for m in boundary_pattern.finditer(text, search_start):
        line_start = m.start()
        line_end = text.find("\n", line_start)
        line = text[line_start: line_end if line_end != -1 else len(text)]
        if line.strip().startswith("#"):
            continue
        boundary_match = m
        break

    insert_at = boundary_match.start() if boundary_match else len(text)
    # Ensure exactly one blank line's worth of separation before our block.
    prefix = text[:insert_at].rstrip("\n") + "\n"
    suffix = text[insert_at:]
    text = prefix + block.rstrip("\n") + "\n" + suffix

    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        return False
    return True


@cli.command("add-system")
@CONFIG_OPTION
def add_system(config_path):
    """Interactively add a new system to config.yaml -- prompts for the
    fields you'd otherwise hand-edit (NAS source, subfolder, extensions,
    RetroArch core, optional Skraper import path), and writes them in via
    targeted text insertion so the rest of config.yaml's comments and
    formatting survive untouched."""
    config = load_config(config_path)

    click.echo("Add a new system\n")
    name = click.prompt("System key (lowercase, e.g. 'psx', 'n64')").strip().lower()
    if not name:
        click.echo("No name given, aborting.")
        return
    if name in config.systems:
        click.echo(f"'{name}' is already configured -- edit config.yaml directly to change it.")
        return

    nas_source_names = list(config.nas_sources.keys())
    if len(nas_source_names) == 1:
        nas_source = nas_source_names[0]
        click.echo(f"NAS source: {nas_source} (only one configured)")
    else:
        click.echo(f"Available NAS sources: {', '.join(nas_source_names)}")
        nas_source = click.prompt("NAS source", default=nas_source_names[0])

    subdir = click.prompt("NAS subfolder name for this system", default=name)

    extensions_raw = click.prompt("File extensions, comma-separated (e.g. '.zip,.chd')")
    extensions = []
    for e in extensions_raw.split(","):
        e = e.strip()
        if not e:
            continue
        extensions.append(e if e.startswith(".") else f".{e}")
    if not extensions:
        click.echo("No extensions given, aborting.")
        return

    use_standalone = click.confirm(
        "Use a standalone emulator instead of a RetroArch core "
        "(e.g. DuckStation for PSX)?", default=False,
    )
    if use_standalone:
        emulator_binary = click.prompt("Path to the standalone emulator's executable").strip()
        emulator_args = click.prompt(
            "Command-line arguments, with {rom} where the ROM path goes "
            "(e.g. '-nogui -batch \"{rom}\"')",
            default='"{rom}"',
        ).strip()
        emulator_use_shell = click.confirm(
            "Has this exact command already failed with 'PermissionError: "
            "WinError 5 / Access is denied' when launched by a script (even "
            "though it runs fine typed manually)? If unsure, say no -- you "
            "can add this later if you hit that specific error.",
            default=False,
        )
        retroarch_core = None
    else:
        retroarch_core = click.prompt("RetroArch core name (e.g. 'snes9x_libretro')").strip()
        emulator_binary = None
        emulator_args = None
        emulator_use_shell = False

    suggested_fullname = COMMON_FULLNAMES.get(name, name)
    fullname = click.prompt(
        f"Display name (Enter for auto-detected '{suggested_fullname}')", default="", show_default=False
    ).strip()

    screenscraper_id = click.prompt(
        "ScreenScraper system ID (Enter to auto-detect from the built-in table)",
        default="", show_default=False,
    ).strip()

    skraper_path = click.prompt(
        "Skraper NAS export path for this system, e.g. 'Y:\\roms\\psx' (Enter to skip)",
        default="", show_default=False,
    ).strip()

    lines = [f"  {name}:"]
    lines.append(f"    nas_source: {nas_source}")
    lines.append(f'    subdir: "{subdir}"')
    ext_list = ", ".join(f'"{e}"' for e in extensions)
    lines.append(f"    extensions: [{ext_list}]")
    if use_standalone:
        lines.append("    emulator:")
        lines.append(f"      binary: '{emulator_binary}'")
        lines.append(f"      args: '{emulator_args}'")
        if emulator_use_shell:
            lines.append("      use_shell: true")
    else:
        lines.append(f'    retroarch_core: "{retroarch_core}"')
    if screenscraper_id:
        lines.append(f"    screenscraper_id: {screenscraper_id}")
    if fullname:
        lines.append(f'    fullname: "{fullname}"')
    system_block = "\n".join(lines)

    if not _append_to_yaml_section(config_path, "systems", system_block):
        click.echo("\nCouldn't safely auto-update config.yaml (unexpected file structure).")
        click.echo("Add this yourself under the `systems:` section:")
        click.echo(system_block)
        return

    click.echo(f"\nAdded '{name}' under systems: in {config_path}.")

    if skraper_path:
        skraper_line = f"  {name}: '{skraper_path}'"
        if _append_to_yaml_section(config_path, "skraper_imports", skraper_line):
            click.echo(f"Added '{name}' under skraper_imports: too.")
        else:
            click.echo("\nCouldn't auto-add the skraper_imports entry -- add it yourself:")
            click.echo(skraper_line)

    click.echo(f"\nNext steps:")
    click.echo(f"  python -m src.cli sync --system {name}")
    click.echo(f"  python -m src.cli generate-es-systems")


def _human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


@cli.command("clean-media")
@CONFIG_OPTION
@click.option("--system", default=None, help="Only clean this system (default: all)")
@click.option("--apply", "apply_changes", is_flag=True,
              help="Actually delete files. Without this, only shows what would be removed.")
def clean_media(config_path, system, apply_changes):
    """Removes locally cached media files (and their DB rows) for kinds no
    longer in enabled_media_types -- the cleanup pass for after narrowing
    your selection with `configure-media`. Dry run by default, matching
    this project's other destructive-operation convention (see
    rom_cleanup.py's --apply flag) -- pass --apply to actually delete."""
    config = load_config(config_path)
    if system:
        _validate_system(config, system)
    if config.enabled_media_types is None:
        click.echo("enabled_media_types isn't restricted (all types currently allowed) -- nothing to clean.")
        click.echo("Run `configure-media` first to narrow your selection, then re-run this.")
        return

    systems = [system] if system else list(config.systems)
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

            click.echo(f"=== {name} ===")
            by_kind: dict[str, list] = {}
            for row in to_remove:
                by_kind.setdefault(row["kind"], []).append(row)

            for kind in sorted(by_kind):
                kind_rows = by_kind[kind]
                kind_bytes = sum(
                    Path(r["local_path"]).stat().st_size
                    for r in kind_rows if Path(r["local_path"]).exists()
                )
                click.echo(f"  {kind}: {len(kind_rows)} files, {_human_size(kind_bytes)}")
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

    click.echo()
    if total_files == 0:
        click.echo("Nothing to clean -- all cached media already matches your enabled_media_types selection.")
    elif apply_changes:
        click.echo(f"Removed {total_files} files, freed {_human_size(total_bytes)}.")
    else:
        click.echo(
            f"Would remove {total_files} files ({_human_size(total_bytes)}) -- re-run with --apply to actually delete."
        )


@cli.command("prune-removed")
@CONFIG_OPTION
@click.option("--system", default=None, help="Only prune this system (default: all)")
@click.option("--apply", "apply_changes", is_flag=True,
              help="Actually delete. Without this, only shows what would be removed.")
def prune_removed(config_path, system, apply_changes):
    """Cleans up local entries (DB row, stub file, cached media) for ROMs
    that a recent `scan` no longer found on the NAS -- e.g. after removing,
    renaming, or reorganizing files there. Run `scan` first so this has
    fresh data; dry run by default, matching clean-media's convention,
    since this assumes the scan it's comparing against actually completed
    (an interrupted/partial scan could make still-present files look
    stale). Run `publish` afterward to update gamelist.xml."""
    config = load_config(config_path)
    if system:
        _validate_system(config, system)
    systems = [system] if system else list(config.systems)
    total = 0

    for name in systems:
        stale = find_stale_roms(config, name)
        if not stale:
            continue

        click.echo(f"=== {name} ===")
        for rom in stale:
            click.echo(f"  {rom['rel_path']}")

            if apply_changes:
                with connect(config.db_path) as conn:
                    media_rows = conn.execute(
                        "SELECT local_path FROM media WHERE rom_id = ?", (rom["id"],)
                    ).fetchall()

                stub_path = config.roms_stub_root / name / rom["rel_path"]
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

        total += len(stale)

    click.echo()
    if total == 0:
        click.echo("Nothing stale -- every indexed ROM was found on the last scan.")
    elif apply_changes:
        click.echo(f"Removed {total} stale ROM(s) and their cached data.")
        click.echo("Run `publish` to update gamelist.xml so ES-DE stops showing these.")
    else:
        click.echo(f"Would remove {total} stale ROM(s) -- re-run with --apply to actually delete.")


@cli.command("reset-system")
@CONFIG_OPTION
@click.argument("system")
@click.option("--apply", "apply_changes", is_flag=True,
              help="Actually delete. Without this, only shows what would be removed.")
def reset_system(config_path, system, apply_changes):
    """Wipes ALL locally cached data for one system -- every indexed ROM
    (DB row, cascading to its metadata + media rows), every stub file,
    every cached media file, and gamelist.xml -- unconditionally, without
    checking what's still on the NAS. For when you've made significant
    changes (re-scraped the whole system in Skraper, restructured its NAS
    folder, etc.) and want a guaranteed-clean re-import instead of a merge
    on top of stale data. Unlike prune-removed, this doesn't require a
    fresh scan first, and it removes ROMs still present on the NAS too.
    config.yaml is left untouched -- the system stays configured, ready
    for the next scan/sync. Dry run by default, matching this project's
    other destructive-operation convention -- pass --apply to actually
    delete."""
    config = load_config(config_path)
    _validate_system(config, system)

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

    if rom_count == 0 and stub_count == 0 and not media_rows and not gamelist_path.exists():
        click.echo(f"'{system}': nothing cached locally -- already clean.")
        return

    click.echo(f"=== {system} ===")
    click.echo(f"  {rom_count} indexed ROM(s) (DB row + cascaded metadata/media rows)")
    click.echo(f"  {len(media_rows)} cached media file(s), {_human_size(media_bytes)}")
    click.echo(f"  {stub_count} stub file(s) under {stub_dir}")
    click.echo(f"  gamelist.xml: {'present' if gamelist_path.exists() else 'not present'} ({gamelist_path})")

    if not apply_changes:
        click.echo(f"\nWould wipe all of the above -- re-run with --apply to actually delete.")
        click.echo(f"config.yaml is untouched -- '{system}' stays configured for the next sync.")
        return

    with connect(config.db_path) as conn:
        conn.execute("DELETE FROM roms WHERE system = ?", (system,))  # cascades to metadata/media rows

    if stub_dir.exists():
        shutil.rmtree(stub_dir)
    if media_dir.exists():
        shutil.rmtree(media_dir)
    if gamelist_path.exists():
        gamelist_path.unlink()

    click.echo(f"\nWiped all local data for '{system}'.")
    click.echo(f"Run `sync --system {system}` (or scan + import-skraper + publish) to rebuild it.")


if __name__ == "__main__":
    cli()
