# esde-nas-bridge

A local-first caching layer that sits between a NAS-hosted ROM collection and
ES-DE. It indexes your ROMs, pulls in metadata/art from Skraper exports or
the ScreenScraper API, and writes everything into ES-DE's own real
`gamelists/` and `downloaded_media/` folders — all stored locally in SQLite
and on disk. The NAS is only touched for two things: the periodic re-index,
and the moment a game is actually launched.

## Why, and a real constraint this works around

Browsing a frontend over SMB/NFS is slow and flaky. This project makes ES-DE's
browsing 100% local: paths, metadata, and art all live in a local cache. The
network path is resolved lazily, only when RetroArch is about to run.

One thing worth knowing: **ES-DE needs at least one real file present at a
system's configured ROM folder to populate that system at all** — it isn't a
pure gamelist-only frontend. So instead of pointing ES-DE at the NAS (which
would mean a network directory scan every time it starts) or at nothing (which
means "no games installed"), the indexer also writes local **zero-byte
placeholder files** mirroring each real ROM's filename. ES-DE scans those
locally, finds metadata in `gamelist.xml` and art in `downloaded_media/` by
filename convention — also both entirely local — and only when you actually
launch a game does `launch_wrapper` look up the real NAS path by filename and
resolve it.

## Components

- **indexer** — walks a NAS path (mapped drive or on-demand mount) and
  builds a SQLite index of ROMs (path, size, mtime, crc32/md5/sha1).
- **skraper importer** — parses an existing Skraper `gamelist.xml` + media
  export and merges it into the local cache.
- **api scraper** — a real ScreenScraper v2 client (see `src/scrapers/
  api_scraper.py`); IGDB/TheGamesDB aren't implemented.
- **rom stubs** — writes the local zero-byte placeholder files described
  above, so ES-DE can enumerate systems without touching the NAS.
- **gamelist writer** — writes `gamelist.xml` (text metadata) directly into
  ES-DE's real `gamelists/` folder. Media is written directly into ES-DE's
  real `downloaded_media/<system>/<type>/` folders by the scraper/importer
  themselves, matched by ROM filename — this is how ES-DE actually finds
  artwork; it does **not** read media paths from `gamelist.xml`.
- **launch wrapper** — the command ES-DE actually calls to start a game.
  Ignores whatever path ES-DE passes beyond its filename, looks the real
  ROM up in the local index, resolves the NAS source (mapped drive or
  on-demand mount/`net use`), and runs RetroArch against the real file.

## Quickstart

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt

cp config/config.example.yaml config/config.yaml
# Edit config.yaml: NAS source(s), and point media_root/gamelists_root at
# your actual ES-DE installation's folders. Leave systems: empty for now.

python -m src.cli add-system              # interactive -- adds a system to config.yaml
python -m src.cli sync --system <name>    # scan + import-skraper + publish
python -m src.cli generate-es-systems     # writes directly into ES-DE's own
                                           # custom_systems/es_systems.xml
```

Restart ES-DE and you're done — no manual XML editing, no copy-pasting into
the wrong file.

### Terminal UI (recommended for day-to-day use)

The commands above are still the full picture, but once `.venv` exists and
`config.yaml` has at least been copied from the example, day-to-day work
(scanning, syncing, adding a system, cleaning up) is friendlier through the
terminal UI: double-click `tui.bat` (Windows) or run `./tui.sh`
(Linux/Mac) — no venv activation needed, same trick `launch.bat`/`launch.sh`
already use. It's a menu/form layer over the exact same code the CLI calls,
so both stay in sync by construction. Covers scan/import-skraper/scrape/
publish/sync (per-system or all), add-system as a guided form, configure-media
as a checklist, clean-media/prune-removed/reset-system with the same
dry-run-first safety, and a Settings screen for the machine-specific paths
mentioned below. The CLI remains fully available underneath for scripting.

 `generate-es-systems` writes straight to ES-DE's own
sanctioned override location (confirmed from ES-DE's docs: the bundled
`resources/systems/<os>/es_systems.xml` must never be hand-edited, since
it's overwritten on every ES-DE update; `custom_systems/es_systems.xml` is
what it's for). It's a safe merge, not a blind overwrite — re-running it
only touches the systems configured in `config.yaml`; anything else
already in that file (added by hand, or by another tool) is left alone.

(`sync` and `generate-es-systems` are shortcuts — see `python -m src.cli --help`
for the individual `scan`/`import-skraper`/`scrape`/`publish` commands if you
want more control over when each runs.)

Caching every media type for a large library adds up fast (videos and
manuals especially). Run `python -m src.cli configure-media` for an
interactive picker — toggle individual types, select "all"/"none", or leave
some out entirely for ROMs-only mode. Takes effect on the next
`import-skraper`/`scrape`/`sync` run; doesn't delete anything already cached.
To actually reclaim disk space from a narrower selection, run
`python -m src.cli clean-media` (dry run by default — shows what would be
removed; add `--apply` to actually delete the files and their DB rows).

See `systems/es_systems.snippet.xml` for what a generated entry looks like
annotated, including two real bugs already hit and fixed here: ES-DE
launches commands with its own working directory, not this repo's (fixed
by `launch.bat`/`launch.sh`), and `%ROM%` must NOT be wrapped in extra
quotes since ES-DE already quotes it (doing so produces a doubled
`""..."" ` that corrupts Windows' argument parsing).

Also required in ES-DE's `es_settings.xml`: `ParseGamelistOnly = true`,
`LegacyGamelistFileLocation` unset/false, `MediaDirectory` unset/default.

**Known RetroArch/ES-DE interaction, not a bug in this project:** ES-DE may
not regain audio after exiting a game. Fix is on RetroArch's side — switch
its audio driver to `sdl2` (Settings → Audio → Driver).

**Standalone emulators and Steam input:** DuckStation (and likely other
standalone emulators) doesn't respond to the usual Start+Select exit combo
when Steam Guide button chords are active. Set a dedicated exit/pause-menu
hotkey directly in the emulator's own controller settings instead, or run
ES-DE outside of Steam (Steam closed entirely, if needed) to avoid input
conflicts.

Re-run `sync` (for one system: `sync --system snes`) whenever your ROM
collection or Skraper export changes -- it's safe to run repeatedly. `sync`
picks up new/changed files automatically, but it doesn't delete anything
by itself: if you remove, rename, or reorganize ROMs on the NAS, `sync`
will flag it ("N previously-indexed ROM(s) weren't found on this scan")
without acting on it. Run `python -m src.cli prune-removed --system <name>`
to review what that means (dry run by default, like `clean-media`), then
add `--apply` to actually remove the stale entries — their DB row, stub
file, and cached media — followed by `publish` to update `gamelist.xml` so
ES-DE stops showing them.

If a ROM reappears after being pruned (or is otherwise missing metadata --
e.g. freshly scanned), you don't need a full `import-skraper` re-run to get
its art/metadata back: `import-skraper <system> --missing-only` finds and
re-imports only ROMs with no cached metadata yet, or target specific files
directly with `--rom "Exact Filename.zip"` (repeatable). Both skip the full
system pass entirely -- on a ~2200-ROM library, a full `import-skraper` run
took ~18 minutes end-to-end, while a `--missing-only`/`--rom` run targeting
one ROM took ~9 seconds.

Made significant changes upstream instead (re-scraped a whole system in
Skraper, restructured its NAS folder) and want a guaranteed-clean re-import
rather than a merge on top of whatever's already cached? `python -m src.cli
reset-system <system>` wipes every indexed ROM (DB row + cascaded
metadata/media), stub file, cached media file, and `gamelist.xml` for that
one system — dry run by default, `--apply` to actually delete. Unlike
`prune-removed`, it doesn't check what's still on the NAS first; it clears
the system unconditionally, `config.yaml` untouched, ready for the next
`sync`.

### Adding a new machine (e.g. a second PC on the same NAS)

Copy the repo over, then from the repo root on Windows: `.\setup.ps1` — it
creates the venv, installs dependencies, and tells you exactly what in
`config.yaml` needs to be machine-specific (ES-DE and RetroArch paths;
NAS/systems config is normally identical across machines pointed at the
same library, so it's usually fine to copy those sections from a working
config). Then `sync` + `generate-es-systems` as above.

## Status

Indexer, Skraper importer, rom stubs, gamelist writer, and launch wrapper are
functional and confirmed working end-to-end on real hardware (see
`TESTING.md`'s Test Log) -- not just fixture-tested. The ScreenScraper client
is implemented but untested against the live API. IGDB/TheGamesDB are not
implemented.
