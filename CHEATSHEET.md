# esde-nas-bridge Cheat Sheet

Quick reference for day-to-day use. For the full story on *why* things work
this way, see [README.md](README.md); for what's actually been verified on
real hardware vs. only fixture-tested, see [TESTING.md](TESTING.md).

## What this is, in one paragraph

A local caching layer between a NAS-hosted ROM library and ES-DE. It indexes
ROMs into SQLite, pulls metadata/art from a Skraper export or the
ScreenScraper API, and writes real `gamelist.xml` + zero-byte stub files +
`downloaded_media/` directly into ES-DE's own folders. ES-DE ends up
browsing 100% locally — the NAS is only touched during an occasional
`scan`/`import-skraper` run and at the exact moment a game launches, when
`launch_wrapper` resolves the stub's filename back to the real NAS path.

## How data flows

```
NAS (Y:\roms\<system>\...)                    Local cache (SQLite + disk)
        │                                              │
        │  scan (walk + checksum)                      │
        ├─────────────────────────────────────────────>│  roms table
        │                                              │
        │  import-skraper (gamelist.xml + media/)       │
        ├─────────────────────────────────────────────>│  metadata + media tables
        │                                              │  + files under media_root
        │  scrape (ScreenScraper API, gap-filler)       │
        ├─────────────────────────────────────────────>│
        │                                              │
        │                              publish (write-stubs + write-gamelists)
        │                                              ▼
        │                                    roms_stub_root/<system>/*.zip (0 bytes)
        │                                    gamelists_root/<system>/gamelist.xml
        │                                    media_root/<system>/<kind>/*
        │                                              │
        │                                              ▼
        │                                         ES-DE browses
        │                                       (never touches NAS)
        │                                              │
        │                                     game launched, calls
        │                                     launch_wrapper <system> <filename>
        │<─────────────────────────────────────────────┤  filename -> DB lookup ->
        │  RetroArch/emulator runs the real file        real NAS path -> exec
```

## Command quick reference

| Command | Purpose |
|---|---|
| `scan [--system NAME] [--checksums]` | Walk the NAS, index ROMs into SQLite |
| `import-skraper <system> [--rom FILE ...] [--missing-only]` | Merge a Skraper export's metadata/art into the cache |
| `scrape <system> [--all]` | Fill metadata/art gaps via the ScreenScraper API |
| `write-stubs [--system NAME]` | Write zero-byte placeholder ROM files |
| `write-gamelists [--system NAME]` | Write `gamelist.xml` |
| `publish [--system NAME]` | `write-stubs` + `write-gamelists` together |
| `sync [--system NAME] [--checksums] [--skip-skraper]` | `scan` + `import-skraper` + `publish`, one shot |
| `generate-es-systems [--output PATH]` | Write `<system>` entries into ES-DE's `custom_systems/es_systems.xml` |
| `configure-media` | Interactively choose which media types to cache |
| `clean-media [--system NAME] [--apply]` | Delete cached media no longer in your `enabled_media_types` selection |
| `add-system` | Interactively add a new system to `config.yaml` (auto-suggests file extensions for known system keys) |
| `prune-removed [--system NAME] [--apply] [--orphaned-media]` | Clean up entries for ROMs no longer found on the NAS (warns + confirms separately if any look stale only because an extension was removed from config, not because the file is actually gone). `--orphaned-media` additionally sweeps `media_root` for files no longer referenced by any tracked ROM (renamed files, old re-scrapes) — runs even if nothing's stale |
| `reset-system <system> [--apply]` | Wipe ALL local cache (DB rows, stubs, media, gamelist.xml) for one system, unconditionally |

All commands accept `--config PATH` (default `config/config.yaml`).
Destructive commands (`clean-media`, `prune-removed`, `reset-system`) are
**dry-run by default** — nothing is deleted until you pass `--apply`.

## Typical workflows

**Day-to-day update** (new ROMs added, Skraper re-scraped something):
```bash
python -m src.cli sync --system snes
python -m src.cli generate-es-systems   # only needed if systems: changed
```
Restart ES-DE (fully close, not just back out) if `generate-es-systems` ran.

**Adding a whole new system:**
```bash
python -m src.cli add-system
python -m src.cli sync --system <name>
python -m src.cli generate-es-systems
```

**A NAS-side removal, rename, or reorganization:**
```bash
python -m src.cli scan --system <name>       # sync also flags this automatically
python -m src.cli prune-removed --system <name>            # dry run first
python -m src.cli prune-removed --system <name> --apply
python -m src.cli publish --system <name>
```

**Cleanly re-adding a system after significant changes** (re-scraped
everything in Skraper, restructured its NAS folder, or you just want a
guaranteed-clean re-import instead of a merge on top of stale data):
```bash
python -m src.cli reset-system <system>            # dry run first
python -m src.cli reset-system <system> --apply
python -m src.cli sync --system <system>
```
Unlike `prune-removed`, this doesn't check what's still on the NAS -- it
wipes every indexed ROM (and cascaded metadata/media), stub files, cached
media, and `gamelist.xml` for that system unconditionally. `config.yaml` is
untouched, so the system stays configured and ready for the next `sync`.

**Restoring one ROM's metadata/media** (e.g. it just reappeared after being
pruned, or was freshly scanned) — don't re-run `import-skraper` for the
whole system, it's slow (measured ~17-18 min on a ~2200-ROM library for a
full pass):
```bash
python -m src.cli import-skraper <system> --missing-only
# or, to target a specific file by name:
python -m src.cli import-skraper <system> --rom "Chrono Trigger (USA).zip"
python -m src.cli publish --system <system>
```
Measured ~9s for one ROM vs. ~17-18 min for a full-system run.

**Trimming disk usage** (e.g. drop videos/manuals from a huge library):
```bash
python -m src.cli configure-media   # interactive picker, updates config.yaml
python -m src.cli clean-media                     # dry run — see what would go
python -m src.cli clean-media --apply
```
For leftover media files no longer tied to any tracked ROM (renamed files,
old re-scrapes) rather than a deliberate media-type change, use
`prune-removed --orphaned-media` instead — see below.

**New machine, same NAS/library:**
```powershell
.\setup.ps1
```
Then set the machine-specific paths in `config.yaml` (ES-DE/RetroArch
folders) — NAS/systems config is normally identical across machines and can
be copied from a working config. Then `sync` + `generate-es-systems`.

## `import-skraper` targeting flags in detail

| Flag | Behavior |
|---|---|
| *(none)* | Full system: every ROM in the export, DB write + media copy for each |
| `--rom "Filename.zip"` | Only that ROM. Repeatable (`--rom a.zip --rom b.zip`) |
| `--missing-only` | Auto-finds ROMs with no metadata row yet (freshly scanned, or reappeared after `prune-removed`) — no filename lookup needed |

Both flags skip DB writes and media copies for everything else, **and** use
direct file-existence checks instead of listing entire NAS media folders —
that second part is what makes a single-ROM restore fast rather than merely
"a bit faster." Requesting a filename that isn't actually in the export is
reported back (`Not found in this Skraper export`) rather than silently
ignored.

## `config.yaml` reference

```yaml
cache:
  db_path: '.\cache\library.db'
  media_root: '<ES-DE home>\downloaded_media'      # point at ES-DE's REAL folder
  gamelists_root: '<ES-DE home>\gamelists'          # point at ES-DE's REAL folder
  roms_stub_root: '.\cache\roms_stub'               # local only, not ES-DE's folder
  # es_de_home: '...'          # optional, only if gamelists_root isn't "<home>/gamelists"
  # enabled_media_types: [covers, screenshots, ...]  # omit = cache everything

nas:
  - name: main-nas
    mode: mapped            # "mapped": already-mounted drive, no mount code runs
    root: 'Y:\roms'
  # - name: backup-nas
  #   mode: on_demand        # mounts only around a launch, via `net use`/mount.cifs
  #   root: 'Z:\'
  #   mount:
  #     type: smb
  #     share: '\\host\share'
  #     mount_point: 'Z:'
  #     timeout_seconds: 15

systems:
  snes:
    nas_source: main-nas
    subdir: "snes"
    extensions: [".sfc", ".smc", ".zip"]
    retroarch_core: "snes9x_libretro"     # XOR with `emulator:` below, never both
    # screenscraper_id: 4                 # only if the built-in name table doesn't cover it
    # fullname: "Super Nintendo"          # display name in es_systems.xml
  psx:
    nas_source: main-nas
    subdir: "psx"
    extensions: [".chd", ".cue"]
    emulator:                             # standalone emulator instead of a RetroArch core
      binary: 'C:\Emulators\DuckStation\duckstation-qt-x64-ReleaseLTCG.exe'
      args: '-nogui -batch "{rom}"'       # {rom} = resolved absolute path, don't add your own quotes
      use_shell: true                     # only if you hit PermissionError/WinError 5 (see gotchas)

skraper_imports:
  snes: 'Y:\roms\snes'      # reads Skraper's output straight off the NAS

scraper:
  priority: [skraper_import, api]         # api only fills gaps skraper_import didn't cover
  api:
    provider: screenscraper
    softname: "esde-nas-bridge"
    devid_env: SCREENSCRAPER_DEVID        # required -- register at screenscraper.fr
    devpassword_env: SCREENSCRAPER_DEVPASSWORD
    username_env: SCREENSCRAPER_USER      # optional, raises quota above anonymous tier
    password_env: SCREENSCRAPER_PASS

retroarch:
  binary: 'C:\RetroArch-Win64\retroarch.exe'
  core_dir: 'C:\RetroArch-Win64\cores'
```

Path-like fields are automatically stripped of one matching pair of
wrapping quote characters at load time (a copy-paste mistake that's caused
real launch failures before) — everywhere **except** `emulator.args`, where
internal quotes are meaningful and preserved exactly as written.

## Media types

Folder names under `media_root/<system>/`, matching ES-DE's own vocabulary
exactly: `covers`, `screenshots`, `marquees`, `3dboxes`, `backcovers`,
`fanart`, `manuals`, `miximages`, `physicalmedia`, `titlescreens`, `videos`.
(Not `wheels` — that's not a real ES-DE folder, despite older docs
elsewhere suggesting it.) `manuals` and `videos` are usually the biggest
disk consumers on a large library — the first thing to drop via
`configure-media` if space is tight.

## Required ES-DE settings (`es_settings.xml`)

| Setting | Value | Why |
|---|---|---|
| `ParseGamelistOnly` | `true` | Trusts `gamelist.xml` completely instead of scanning the ROM folder — this is what actually makes browsing local |
| `LegacyGamelistFileLocation` | unset/`false` | Reads from ES-DE's own central `gamelists/`, not the NAS |
| `MediaDirectory` | unset/default | Falls back to ES-DE's own `downloaded_media/`, not a NAS path |

Edit directly or via ES-DE's own settings UI where exposed.

## Known gotchas (condensed — full details/root-causes in TESTING.md)

- **Edit `custom_systems/es_systems.xml`, never the bundled
  `resources/systems/<os>/es_systems.xml`** — the bundled file gets
  overwritten on every ES-DE update. `generate-es-systems` writes to the
  correct file automatically.
- **`es_systems.xml` must be trimmed to only your configured systems**, not
  the full stock list with your systems merged in — an untrimmed file makes
  ES-DE check every *other* system's default NAS path at startup, one SMB
  round trip at a time (measured: 16s startup vs. ~1.3s once fixed).
  `generate-es-systems` only ever touches the systems in your `config.yaml`.
- **Don't wrap `%ROM%` in extra quotes** in `es_systems.xml` — ES-DE already
  quotes it; doing so yourself produces a doubled `""..."" ` that corrupts
  Windows argument parsing.
- **`launch.bat`/`launch.sh` exist because ES-DE launches commands with its
  own working directory**, not this repo's — don't skip them for a bare
  `python -m src.launch_wrapper` call in `es_systems.xml`.
- **Non-elevated terminal only.** A mapped drive belongs to the logon
  session that created it — an elevated ("Run as Administrator") terminal
  often can't see it at all, showing as a plain "path not found" that looks
  like a connectivity issue but isn't.
- **RetroArch may not release audio after exiting a game, and ES-DE won't
  regain it.** Fix: set RetroArch's audio driver to `sdl2`.
- **Standalone emulators (DuckStation, etc.) may not respond to ES-DE's
  usual Start+Select exit combo under Steam Guide.** Set a dedicated
  exit/pause hotkey in the emulator's own settings, or run ES-DE with Steam
  closed entirely.
- **`PermissionError: WinError 5 Access is denied` launching a standalone
  emulator via `launch_wrapper`, even though it runs fine typed manually** —
  set `use_shell: true` on that system's `emulator:` config. Root cause not
  fully understood, but the fix is confirmed on real hardware (DuckStation).
- **Check `HKLM:\...\LanmanWorkstation\Parameters`'s
  `DirectoryCacheLifetime`/`FileNotFoundCacheLifetime`/`FileInfoCacheLifetime`
  on any new Windows machine.** A leftover "fix" from an old tutorial zeroing
  these makes *any* SMB metadata operation (including `scan`/
  `import-skraper`) look artificially slow — delete the values (reverting to
  Windows defaults) and reboot.
- **CHD-converted CD images (PS1/Saturn/Sega CD) won't reliably checksum-match
  ScreenScraper** (`scrape` uses crc32/md5/sha1, which won't match a `.chd`
  against the original cue/bin). Not an issue for `import-skraper`, which
  matches by filename instead.
- **Config values get one layer of wrapping quotes silently stripped** at
  load time — a real bug once, now normalized away automatically for every
  path-like field (not `emulator.args`).
- **Removing an extension from a system's `extensions:` makes `scan`
  silently stop seeing files with it** — not deleted, not renamed, just no
  longer matched — which looks identical to a real NAS removal to
  `prune-removed`. It now flags these separately (`[extension not in this
  system's current config...]`) and requires an extra explicit confirmation
  before `--apply` touches them, so a config edit can't accidentally wipe
  the cached metadata/art for ROMs that are still sitting right there on
  the NAS.
- **Multi-disc `.m3u` playlists showed up in ES-DE with their raw filename**
  (e.g. "Xenogears (USA).m3u") instead of the scraped title — Skraper never
  writes a `<game>` entry for the `.m3u` itself, only for each disc file.
  `import-skraper` now automatically borrows the matching disc's
  metadata/art for the `.m3u` (matched by base title once the "(Disc N)"
  marker is stripped) — just re-run `import-skraper <system>
  --missing-only` (or a full `import-skraper`/`sync`) followed by `publish`.
- **A ROM re-encoded to a different container after being scraped** (most
  commonly `.cue`/`.bin` → `.chd` via chdman) also showed its raw filename
  — Skraper's `<game>` entry still points at the original extension (e.g.
  `Loom (USA).cue`), which no longer matches the converted file's exact
  name. `import-skraper` now also falls back to an extension-agnostic
  stem match when the exact filename misses. Some Skraper exports also
  nest that ROM's media one level deeper (`media/screenshots/Loom
  (USA)/Loom (USA).png` instead of flat) — now checked as a second
  convention too. Same fix: `import-skraper <system> --missing-only` then
  `publish`.

## Troubleshooting quick hits

| Symptom | Likely cause |
|---|---|
| ES-DE startup takes 10+ seconds | Untrimmed `es_systems.xml` — re-run `generate-es-systems` |
| `Y:` "path not found" but works when typed manually | Terminal is elevated — use a non-elevated one |
| `scan`/`import-skraper` unexpectedly slow on a new PC | Check the SMB2 client-cache registry values (see gotchas) |
| Game launches but ES-DE loses audio after | Switch RetroArch's audio driver to `sdl2` |
| Multi-disc `.m3u` shows its raw filename, not the game title | `import-skraper <system> --missing-only` then `publish` — Skraper never scrapes the `.m3u` directly, now auto-matched to its disc files' title |
| A `.chd` (or other converted ROM) shows its raw filename, not the game title | `import-skraper <system> --missing-only` then `publish` — Skraper's export still references the pre-conversion extension, now auto-matched by stem regardless of extension |
| Standalone emulator: `WinError 5 Access is denied` | Set `use_shell: true` on that system's `emulator:` config |
| A ROM removed/renamed on the NAS still shows in ES-DE | `scan` then `prune-removed --apply` then `publish` |
| `prune-removed` warns a ROM's extension "not in this system's current config" | You likely removed that extension from `extensions:` — the file's probably still on the NAS; confirm before proceeding, or add the extension back and re-`scan` if not intended |
| ROM back on the NAS but still missing art/metadata in ES-DE | `scan`, then `import-skraper <system> --missing-only`, then `publish` |
| `add-system`/`configure-media` says it "couldn't safely auto-update config.yaml" | Your `config.yaml` doesn't match the expected structure for the surgical text edit — add the shown block by hand |
| `No such system 'X' in config.yaml. Configured systems: ...` | That system hasn't been added yet — run `add-system` first |
| `import-skraper`/`sync` says "No gamelist.xml found in ..." | Skraper hasn't been run against that NAS folder yet (or `skraper_imports:` points at the wrong path) — run Skraper there first, or fix the entry in `config.yaml`. `sync` skips just that system's import and continues with the rest |
