# Test Runbook

Living document. Steps get added/revised as we build more; results go in the
Test Log at the bottom so we can track what's actually been verified on your
setup (Windows 11 Pro, NAS `little-dipper` over SMB, `Y:\roms`) vs. what's
only been fixture-tested here.

## Known context

**es_systems.xml: use custom_systems, never the bundled resources file.**
Confirmed directly from ES-DE's own USERGUIDE.md/FAQ.md: the bundled
`resources/systems/<os>/es_systems.xml` must never be hand-edited (gets
overwritten on every ES-DE update) -- `custom_systems/es_systems.xml` is
the sanctioned override location. `generate-es-systems` now writes
directly there via a safe merge (only replaces entries for systems
configured in config.yaml; anything else in that file is left untouched),
eliminating the manual copy-paste step and the "which file, replace or
append" confusion that caused the Office PC startup slowdown.
**Confirmed resolved 2026-08-13:** once the correct file (custom_systems,
not the bundled one) was actually being edited, the ~193-other-stock-systems
NAS checks disappeared from the log entirely -- was never a "some systems
still get checked regardless" problem, purely a wrong-file problem. Office
PC now startup-instant, confirming it's genuinely the faster machine as
suspected.

**es_systems.xml must be TRIMMED to only your configured systems, not the
stock 195-system default with your entries merged in.** Confirmed via
Office PC's es_log.txt: 16074 ms startup vs. the Basement PC's ~1328 ms,
caused by `Parsed configuration for 195 systems, loaded 2 systems` --
ES-DE checked each of the ~193 *other*, unconfigured systems' NAS path
(`Y:\roms\<system>`, ES-DE's default) at startup for a stale gamelist.xml
warning, one SMB round trip at a time (visible as multi-second gaps
between consecutive `Warn: Found a gamelist.xml file in "Y:\..."` log
lines). Fix: back up es_systems.xml, then replace its <system> entries
with exactly what `generate-es-systems` produces -- nothing else.

**Standalone emulators (e.g. DuckStation for PSX) are now supported**
alongside RetroArch cores -- set `emulator: {binary, args}` instead of
`retroarch_core` on a system (exactly one of the two, validated at config
load). `add-system` prompts for this interactively. **Confirmed working
2026-08-13:** DuckStation's `-nogui -batch "{rom}"` launches directly into
the game (no launcher screen) and exits cleanly with no lingering process
on game exit -- verified manually before wiring it into ES-DE.
**Real issue found and fixed the same day:** launching via `launch_wrapper`
(not manually) failed with `PermissionError: [WinError 5] Access is denied`
for this specific DuckStation binary, even though it launched fine typed
directly into PowerShell. Root cause not fully pinned down -- ruled out
ES-DE elevation, DuckStation's own elevation manifest (Compatibility tab
unchecked), Windows Defender (clean protection history), and testing the
wrong binary (confirmed same exact exe both times). Empirically confirmed
fix: routing the launch through `cmd.exe /c` as the immediate parent
process resolves it. Added as an opt-in `use_shell: true` on `emulator:`
(default false, so RetroArch and unaffected standalone emulators are
untouched) -- implemented safely via list-based argument passing (not
Python's shell=True string mode), so ROM filenames with parentheses/spaces
can't trigger cmd.exe metacharacter issues. `add-system` asks about this
directly when setting up a standalone emulator.

**DuckStation (and likely other standalone emulators) doesn't play well
with Steam Guide button chords.** The usual Start+Select combo that exits
back to ES-DE via RetroArch's hotkey system doesn't work the same way here.
**Workaround:** set a dedicated pause-menu/exit hotkey combo directly in
DuckStation's own controller/hotkey settings and exit through that,
instead of relying on ES-DE's usual exit-to-frontend combo. Alternative:
run ES-DE outside of Steam entirely (possibly with Steam closed
altogether) to avoid input conflicts.

**ES-DE doesn't regain audio after exiting RetroArch.** Not a bug in
launch_wrapper/launch.bat -- it's a RetroArch/ES-DE audio-driver
interaction (RetroArch's default driver, WASAPI on Windows, appears to
hold onto the audio device in a way ES-DE doesn't recover from on return).
**Fix:** in RetroArch's settings, switch the audio driver to `sdl2`.

**2026-08-10: NAS hardware/OS migration.** `skag` (Ubuntu 16.04, EOL) was
replaced by `little-dipper` (i7-8700K, 32GB RAM, Ubuntu 24.04 LTS Server).
The `wd-reds` ZFS mirror was exported/imported intact -- same pool, same
data, no path changes, so `Y:\roms` should be unaffected. Samba runs native
(not Docker). This also resolves the "Samba slower than NFS, cause unknown"
open item from earlier notes -- a modern Samba build on current Ubuntu was
one of the two suspected causes (the other, the SMB2 client-cache registry
issue below, turned out to be the larger factor). `skag` stays on briefly
as a fallback before decommissioning.

**Tailscale is now fully working** (verified with 4K Plex streaming over
cellular, no wifi). This makes the on-demand mount path (`mount.py`'s
`net use` branch, written early on but never actually exercised since `Y:`
has been a persistent LAN mapped drive) worth genuinely testing for the
first time -- a laptop connecting via Tailscale when away from home is a
real scenario now, not a hypothetical one. See step 11.

**Firewall is default-deny, scoped to LAN + Tailscale.** Shouldn't affect
anything on-LAN, but worth knowing if an on-demand mount from a Tailscale
client ever fails to connect -- check Samba's own access controls
(`smb.conf` host allow/deny, valid users) aren't separately blocking it.

**Resolved 2026-08-10:** Basement PC's 15-20min ES-DE load time was caused
by `DirectoryCacheLifetime`/`FileNotFoundCacheLifetime`/`FileInfoCacheLifetime`
zeroed under `HKLM:\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters`
(a harmful leftover "fix" from an old tutorial), not Samba/NAS performance.
Fixed by deleting those values (reverting to Windows defaults) and rebooting.
This affects *any* tool doing SMB metadata operations, including this one's
`scan`/`import-skraper` steps -- worth checking on any new machine before
assuming something here is slow.

**Two-PC setup:** Office PC and Basement PC both point at the same NAS.
Each needs its own local cache (separate `config.yaml`, separate
`media_root`/`gamelists_root`/`roms_stub_root`, separate SQLite DB) -- the
architecture already supports this naturally since each machine just runs
its own `scan`/`import-skraper`/`publish` cycle independently against the
same NAS source, but it's not automatic; each machine needs to be set up.

**CHD conversion caveat:** if CD-based systems (PS1/Saturn/Sega CD) get
converted to CHD per your archive-format notes, be aware the `scrape`
command's ScreenScraper matching (crc32/md5/sha1) won't reliably match a
`.chd` file's checksum against ScreenScraper's database, which expects the
original cue/bin or zip. Not an issue for `import-skraper` (matches by
filename, not checksum), only relevant if `scrape` is ever used as a
gap-filler for those systems.

## Current known-good config values for your setup

```yaml
cache:
  db_path: '.\cache\library.db'
  media_root: 'C:\Users\YOURNAME\ES-DE\downloaded_media'
  gamelists_root: 'C:\Users\YOURNAME\ES-DE\gamelists'
  roms_stub_root: '.\cache\roms_stub'

nas:
  - name: main-nas
    mode: mapped
    root: 'Y:\roms'

skraper_imports:
  snes: 'Y:\roms\snes'   # reads Skraper's output straight off the NAS -- one entry per system
```

Required `es_settings.xml` changes (edit directly, or via ES-DE's UI where exposed):
- `ParseGamelistOnly = true` -- lets ES-DE trust the local gamelist.xml without scanning for files
- `LegacyGamelistFileLocation` -- back to **false/default** (if you'd set it true for the old NAS-read setup)
- `MediaDirectory` -- **unset/default** (if you'd pointed it at `%ROMPATH%/_media` for the old setup)

Your NAS-side `_media` symlinks can stay in place; they're just unused now.

---

## 0. Check SMB2 client cache state on whichever PC you're testing from

- [ ] `Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters"`
- [ ] Confirm `DirectoryCacheLifetime`/`FileNotFoundCacheLifetime`/`FileInfoCacheLifetime` are either absent (defaults) or 10/5/10 -- not 0
- [ ] If zeroed, fix this first regardless of anything else being tested -- it'll otherwise make `scan`/`import-skraper` look artificially slow and muddy any comparison in step 10

## 0.5. Use a non-elevated terminal

Mapped drives belong to the specific Windows logon session that created
them. A terminal opened via "Run as Administrator" gets a different logon
session, so `Y:` (mapped normally, not elevated) won't resolve there at all
-- not an access-denied error, a flat "cannot find the path", which looks
like a connectivity problem but isn't one.

- [ ] Confirm this terminal was NOT opened with "Run as Administrator"
- [ ] `dir Y:\` succeeds before doing anything else in this session

## 1. Environment setup

- [ ] Extract the repo somewhere permanent, not Downloads
- [ ] `python -m venv .venv`
- [ ] `.venv\Scripts\activate`
- [ ] `pip install -r requirements.txt`
- [ ] `python -m src.cli --help` runs without error

## 2. Configure config.yaml

- [ ] Copy `config.example.yaml` to `config.yaml`
- [ ] Set `nas.main-nas.root` to `Y:\roms`
- [ ] Find your real ES-DE home folder (check `es_log.txt`) and set `media_root` / `gamelists_root` to match exactly
- [ ] Set `retroarch.binary` and `core_dir` to your real RetroArch paths
- [ ] Point `skraper_imports.<system>` at `Y:\roms\<system>` directly for each system you're testing

## 3. Trim to one small test system first

- [ ] Comment out every system except one (`snes` recommended) in `config.yaml`
- [ ] Ideally test against a small subfolder first, not the full 10,000+ game library

## 4. Index and inspect

- [ ] `python -m src.cli scan --system snes --checksums`
- [ ] Printed count matches expectations
- [ ] Open `cache\library.db` (DB Browser for SQLite) and spot-check a few `roms` rows -- filenames, sizes, crc32/md5/sha1 look right

## 5. Import from your existing Skraper export

- [ ] `python -m src.cli import-skraper snes` (reads directly off `Y:\roms\snes`)
- [ ] Check `matched`/`unmatched` counts -- `unmatched` should be 0 or explainable (files not yet in the index)
- [ ] Spot-check media kinds landed correctly: **`covers`**, **`screenshots`**, **`marquees`**, **`3dboxes`**, **`manuals`**, **`miximages`**, **`titlescreens`**, **`backcovers`**, **`fanart`**, **`videos`** -- NOT `wheels` (confirmed not a real ES-DE folder)

## 6. Publish and verify locally

- [ ] `python -m src.cli publish --system snes`
- [ ] `roms_stub_root\snes` has zero-byte files matching real ROM names
- [ ] `<ES-DE>\downloaded_media\snes\` has the expected subfolders populated with images named after your ROMs
- [ ] `<ES-DE>\gamelists\snes\gamelist.xml` has real entries with titles filled in

## 7. Test the launch wrapper directly (before touching ES-DE)

- [ ] `python -m src.launch_wrapper snes "Chrono Trigger.sfc"` (use a real filename from your library)
- [ ] stderr output shows the resolved path under `Y:\roms\snes\`, not the stub folder
- [ ] RetroArch actually launches the game

## 8. Apply the es_settings.xml changes

- [ ] Back up `es_settings.xml` and `es_systems.xml` first
- [ ] Set `ParseGamelistOnly = true`
- [ ] Revert `LegacyGamelistFileLocation` to false/default
- [ ] Revert/unset `MediaDirectory`

## 9. Wire up es_systems.xml and verify inside ES-DE

- [ ] Edit the `snes` entry per `systems/es_systems.snippet.xml` -- `<path>` at `roms_stub_root\snes`, `<command>` calling `launch_wrapper.py`
- [ ] Fully close and restart ES-DE (not just back out of the menu)
- [ ] Games show up with correct titles and art
- [ ] Launch a game from inside ES-DE itself -- confirms the full chain

## 10. Compare against your old setup

- [ ] With the SMB2 registry values confirmed sane (step 0), does browsing still feel meaningfully faster with local caching, or is NAS-direct now "fast enough"?
- [ ] This comparison means something different than it used to: the original Basement-PC slowness had a specific, now-fixed client-side cause, not a Samba/NAS problem -- so a small or unnoticeable difference here doesn't mean this project failed, it means the registry fix already solved the speed problem. The remaining case for local caching is resilience (works even if that registry setting regresses on some machine, or the NAS is down/slow for unrelated reasons), not necessarily raw speed.

## 11. (New, first real test) On-demand mount over Tailscale

This is the first chance to actually exercise `mount.py`'s `net use` branch
rather than the always-on `mapped` mode `Y:` has used so far.

- [ ] On a laptop, connect to Tailscale but NOT the home LAN/wifi
- [ ] Add a second `nas` entry in `config.yaml` with `mode: on_demand`, `share: '\\little-dipper\roms'` (or its Tailscale hostname/IP), a free drive letter as `mount_point`
- [ ] Either register credentials in Windows Credential Manager first (`cmdkey /add:little-dipper /user:... /pass:...`), or set `username_env`/`password_env`
- [ ] Confirm the drive is NOT connected yet: `Get-PSDrive` shouldn't show it
- [ ] Run `python -m src.launch_wrapper <system> "<filename>"` for a game already in the local cache
- [ ] Confirm it connects only at that moment (watch stderr for the mount attempt), launches correctly, and -- if using `maybe_unmount` -- disconnects after
- [ ] Confirm browsing (ES-DE itself) never touched Tailscale/the NAS at all during this, since it's reading the local cache same as always

## 12. Standalone emulator (e.g. DuckStation for PSX)

- [x] Manually confirmed DuckStation's `-nogui -batch "{rom}"` launches directly into the game and exits cleanly, no lingering process
- [ ] `add-system`, answer yes to "use a standalone emulator", point at the real DuckStation binary
- [ ] `sync --system psx`, `generate-es-systems`, restart ES-DE
- [ ] Launch a game from ES-DE -- confirm DuckStation opens (not RetroArch) and the correct file loads
- [ ] Exit the game -- confirm control returns to ES-DE cleanly, same as the RetroArch path

---

## Test Log

| Date | Step | Result | Notes |
|------|------|--------|-------|
| 2026-08-11 | 4 (scan) | Pass | 2000+ SNES ROMs indexed with checksums |
| 2026-08-11 | 5 (import-skraper) | Pass | All 10 media kinds populated with healthy counts (3dboxes/backcovers 2151, manuals 1897/1897 -- exact match, fanart 1724, others 2144-2182). Required two fixes along the way: media folder names corrected to confirmed real structure, and manuals' hash-suffixed filenames needed stripping to match. `ZZZ(notgame):#NONGAME` titles for BIOS/test-cartridge entries are expected (Skraper/ScreenScraper convention), not a bug -- worth filtering later when polishing. |
| 2026-08-12 | 6 (publish + verify) | Pass | Stub files confirmed 0-byte, downloaded_media/gamelist.xml landed correctly in real ES-DE folders. Found scan was indexing a `.duplicates` folder (rom-cleanup tool output) as real ROMs -- fixed by excluding dot-prefixed folders from the walk, matching the project's own established hidden-folder convention. |
| 2026-08-12 | 7 (launch wrapper direct) | Pass | `launch_wrapper snes "Chrono Trigger (USA).zip"` resolved the real NAS path and launched RetroArch successfully. Core mechanism (filename lookup -> NAS resolve -> exec) fully proven on real hardware/data, not just fixtures. |
| 2026-08-12 | 9 (ES-DE launch) | Pass | Root cause: es_systems.xml double-quoted %ROM% on top of ES-DE's own auto-quoting. Fixed by removing the extra quotes. Full pipeline now proven end-to-end on real hardware: ES-DE browses 2180 games entirely locally (`ParseGamelistOnly` confirmed via es_log.txt -- "Only parsing the gamelist.xml files, not scanning system directories"), with real metadata/art, and launching from inside ES-DE itself correctly resolves and plays the real NAS file. |
| 2026-08-12 | Rollout: genesis | Pass | Second system added via the simplified `sync` + `generate-es-systems` workflow. Confirms the deployment tooling generalizes beyond the original snes setup, not just in theory. |
| 2026-08-13 | Rollout: Office PC | Found real issue | ES-DE startup 16074 ms vs. Basement PC's ~1328 ms. Root cause via es_log.txt: Office PC's es_systems.xml was the stock 195-system file with our 2 entries added in, not replaced -- every other system's default NAS <path> got checked at startup (SMB round trip each, visible as multi-second gaps between consecutive log warnings). Fix: trim es_systems.xml to only configured systems. Updated generate-es-systems' own output/header messaging so this can't recur silently on a future machine. |
| 2026-08-13 | Deployment overhaul | Pass -- confirmed on real hardware | custom_systems direct-write and add-system verified end-to-end on the Office PC: switching to the correct file (custom_systems, not the bundled/stock one) eliminated the ~193-other-systems NAS checks entirely -- was purely a wrong-file problem, not a "some systems still get checked regardless" limitation. ES-DE startup now effectively instant on the Office PC, confirming it genuinely is the faster machine once the config was actually correct. |
| 2026-08-13 | 12 (standalone emulator, manual check) | Pass | DuckStation `-nogui -batch "{rom}"` confirmed manually: launches directly into the game (no launcher screen), exits cleanly, no lingering process after exit. Flags were an educated guess pending this test -- now confirmed correct. Full ES-DE integration (add-system -> sync -> generate-es-systems -> launch from ES-DE) still to be tested. |
| 2026-08-13 | 12 (via launch_wrapper) | Found real issue, fixed | `PermissionError: WinError 5 Access is denied` when launched via launch_wrapper (not manually) for the exact same DuckStation binary. Multi-step diagnosis: ruled out ES-DE elevation, DuckStation's own elevation manifest, Windows Defender (clean history), wrong-binary confusion. Confirmed empirically: cmd.exe-mediated launch (shell=True test) works where direct process creation doesn't. Root cause not fully understood, but fix confirmed working -- added `use_shell: true` opt-in on `emulator:` config. Not yet retested end-to-end via ES-DE with the fix applied. |
| 2026-08-13 | 12 (still failing after use_shell fix) | Found real issue, fixed | New error after adding use_shell: "The filename, directory name, or volume label syntax is incorrect" from cmd.exe. Root cause: config.yaml's `binary:` value had literal doubled quote characters baked into the string itself (`'"D:\...\exe"'` -- quotes inside the YAML quotes), most likely from copying a path that had been shown quoted for terminal-display purposes. Same class of bug as the earlier %ROM% double-quoting issue, different location. Fixed at the source: added `_strip_wrapping_quotes()` normalization applied to every path-like config field at load time (NOT applied to `emulator.args`, where internal quotes are meaningful) -- this exact mistake can no longer cause a launch failure regardless of how the value got into config.yaml. |
| 2026-08-13 | 12 (still failing after quote-strip fix) | Found real issue, fixed | Third issue on the same DuckStation launch chain: "'D:\Games\Emulation' is not recognized" -- cmd.exe splitting the binary path at its first space. Root cause: a genuinely well-documented cmd.exe /c quirk (confirmed in Microsoft's own docs) -- it only preserves quoting cleanly with exactly two quote characters in the remaining command line; with four (a quoted binary path AND a quoted rom path together), it falls back to stripping only the very first and last quote of the WHOLE line, unprotecting everything between. Fixed with the standard documented workaround: wrap the entire inner command in one extra pair of quotes, built as a pre-constructed string (not a list, so Python doesn't re-quote on top of it) using subprocess.list2cmdline for the inner quoting. Also fixed launch_wrapper's debug print, which was showing a naive space-join that never reflected real quoting -- now uses list2cmdline for an accurate display, which would have made all three of today's cmd.exe-adjacent bugs easier to diagnose from the start. Confirmed working on real hardware -- DuckStation launches successfully via launch_wrapper. |
| 2026-08-13 | 12 (standalone emulator, full chain) | Pass -- confirmed on real hardware | Standalone emulator support (EmulatorConfig, use_shell, config value quote-stripping, cmd.exe /c double-quote workaround) all confirmed working end-to-end for DuckStation/PSX. Three real bugs found and fixed in this single chain: WinError 5 from direct process creation (fixed with use_shell), a config value with literal quote characters baked in (fixed with load-time normalization), and a cmd.exe /c multi-quote quirk (fixed with the doubled-outer-quote workaround). ES-DE-side integration (es_systems.xml entry, launching from inside ES-DE itself rather than the command line) still to be confirmed. |
| 2026-08-13 | Removed-ROM cleanup | Pass (fixture-tested) | Found a real gap: `scan` only adds/updates entries, never notices when a ROM disappears from the NAS -- would have left stale DB rows, stub files, gamelist entries, and orphaned media forever. Added `prune-removed` (dry-run by default, `--apply` to act) using the already-present `last_seen_at` timestamp to detect staleness, plus a non-destructive nudge in `sync`'s output when it finds something stale. Verified end-to-end with fixtures: dry run touches nothing, `--apply` correctly removes the DB row (with metadata/media cascading), the stub file, AND the cached media file; `publish` afterward correctly drops the entry from gamelist.xml; running twice is safely idempotent. Not yet tested against real hardware/a real NAS removal. |
| 2026-08-14 | Removed-ROM cleanup (real hardware) | Pass | Found the feature had never actually reached the real Office PC working copy (`D:\Games\Emulation and Misc\retroarch-nas-frontend`) -- it only existed in a separate `C:\Users\...\Downloads\retroarch-nas-frontend` copy from an earlier session. Diffed `cli.py`/`indexer.py` between the two (purely additive, nothing to lose) and copied them into the real copy before testing. Full real-NAS cycle against `snes`: moved `Chrono Trigger (USA).zip` out of `Y:\roms\snes` into a holding folder, re-scanned (2180 -> 2179), dry-run `prune-removed` correctly listed exactly that one ROM and nothing else, `--apply` removed the DB row/stub file/all 10 cached media files (verified each directly), `publish` correctly dropped it from `gamelist.xml` (0 filename matches, confirmed the only remaining "Chrono Trigger" hit was unrelated plot text in a different game's description), and a second `prune-removed` run correctly reported "Nothing stale" (idempotent). Restored the file, re-scanned (back to 2180), then re-ran `import-skraper` + `publish` to bring back its metadata/media too, since pruning had cascaded that away for real and `scan` alone doesn't restore it -- confirmed fully back to original state (2180 matched/0 unmatched, entry and all 10 media files back). Confirms the fixture tests weren't hiding anything: real NAS I/O, real SMB paths with spaces/parens, real cascade deletes all behaved as designed. |
| 2026-08-14 | Targeted import-skraper (--rom / --missing-only) | Pass -- confirmed on real hardware | Prompted by the previous test: restoring one ROM's metadata/media required a full `import-skraper snes` re-run (~2200 ROMs, 17m40s measured), even though only 1 ROM actually needed it. Added `--rom FILENAME` (repeatable, explicit target) and `--missing-only` (auto-detects ROMs with no metadata row -- same "missing" definition as `scrape`'s `only_missing`, via new `rom_filenames_missing_metadata` DB helper) to `import_skraper_export`/the CLI. First pass only trimmed DB writes/media copies for skipped ROMs but left the media-folder-fallback scan (`_scan_media_folders`) doing a full NAS `iterdir()` per folder regardless -- measured 1m41s for 1 ROM, barely better, because 3 of this export's 10 media kinds (screenshots/titlescreens/videos) aren't tagged in gamelist.xml and only get found via that folder scan. Fixed by adding a direct-probe-first path (`_probe_file`, a few `.exists()` stats instead of listing a folder that can hold thousands of files) used only in targeted mode (`prefer_direct_probe`, gated on `only_filenames is not None`) -- full-system runs keep the original index-once-and-reuse approach since that's genuinely cheaper at scale. Re-measured: 1 ROM via `--rom` now ~9.1-9.4s (~10-18x faster than the first-pass fix, ~180x faster than a full run), all 10 media types still landed correctly. Verified: multiple `--rom` flags together, a nonexistent filename correctly reported under "not found in this export" (`not_in_export` in the result dict) without failing the whole command, `--missing-only` against a DB-simulated missing-metadata ROM correctly found and re-imported exactly that one ROM (~9.1s) and correctly reported "nothing missing" on a clean re-run, and a full `import-skraper snes` (no filter) re-run afterward still matched all 2180/2180 -- confirms the targeted-mode changes didn't regress the untargeted path. |
| 2026-08-15 | Multi-disc .m3u titles | Found real issue, fixed -- confirmed on real hardware | Reported: ES-DE showed multi-disc PSX games as their raw filename ("Xenogears (USA).m3u") instead of the scraped title. Root cause, confirmed directly against the real DB and Skraper's real gamelist.xml on `Y:\roms\psx`: Skraper scrapes each disc file individually (`Xenogears (USA) (Disc 1).chd`, etc.) and never writes a `<game>` entry for the `.m3u` playlist itself -- all 9 of this library's `.m3u` ROMs had zero metadata rows, so `gamelist_writer` fell back to the raw filename exactly as reported. (The individual `.chd` discs themselves are correctly hidden from indexing already -- this library keeps them under a dot-prefixed `.chd/` subfolder, which `scan` already excludes by convention -- so this was purely a title-matching gap, not a duplicate-entry problem.) Fixed in `skraper_import.py`: a new fallback pass matches each metadata-less `.m3u` ROM to its disc files' shared base title (stripping the " (Disc N)" marker) and borrows that entry's metadata + media, naming the copied art after the `.m3u` (not the disc) so ES-DE associates it correctly. Two follow-on bugs found and fixed during testing: (1) `--rom`-targeted mode originally only recorded a disc's entry for fallback-matching if that exact disc filename was itself in the targeted set -- meant targeting just the `.m3u` by name found no match at all; fixed by always recording disc entries (cheap, in-memory only) regardless of targeting. (2) the media-folder-convention sweep was searching for art under the `.m3u`'s own stem, but Skraper's `media/` folder holds it under the disc's stem (e.g. "Xenogears (USA) (Disc 1).png") -- fixed by decoupling the sweep's search stem from the destination filename. Verified against the real library: all 9 affected PSX titles (Xenogears, Metal Gear Solid, Final Fantasy VIII, Parasite Eve, Driver 2, Countdown Vampires, D, Alone in the Dark: The New Nightmare, Resident Evil 2) now show their correct scraped title and full media set (covers/screenshots/videos/etc., all 9 media kinds landed for Xenogears) in the real `gamelist.xml`; `--missing-only` correctly reports "nothing missing" afterward; a full untargeted `import-skraper psx` re-run left all 9 titles unchanged and correct (no regression, no duplication). |
| 2026-08-15 | Converted-ROM (.chd) titles + nested media | Found real issue, fixed -- confirmed on real hardware | Reported same symptom as the .m3u fix, this time for `pcenginecd`: "Loom (USA).chd" showed its raw filename. Different root cause: Skraper's export references the pre-conversion filename ("Loom (USA).cue", inside its own subfolder) -- the ROM was later converted to `.chd` via chdman, so the exact-filename match missed for all 7 games in this system (confirmed: all 7 had zero metadata rows, and all 7 matched by extension-stripped stem against Skraper's export). Added a third fallback pass in `skraper_import.py` (`stem_matches`, extension-agnostic) for exactly this case, reusing the same `_apply_game_to_rom` machinery as the .m3u fix. While verifying media landed correctly, found a second, separate gap: this system's Skraper export nests media one level deeper than every other system in the same install (`media/screenshots/Loom (USA)/Loom (USA).png` vs. the flat `media/screenshots/Loom (USA).png` confirmed for snes/genesis/pcengine) -- apparently tied to the ROM itself having been scraped from inside its own subfolder. `_build_folder_index`/`_probe_file` only checked the flat convention, so even with the title fixed, box art would still have been missing. Fixed by checking a same-named file one level inside any directory entry, as a second lower-priority convention. Verified against the real library (DB backed up first): all 7 pcenginecd titles (Akumajou Dracula X - Chi no Rondo, Loom, Lords of Thunder, Valis II/III/IV, Ys Book I & II) now show correct titles in the real `gamelist.xml`, and all 9 media kinds landed for Loom (63 files total across all 7 games) -- confirmed via direct filesystem check, not just DB rows. Re-ran `--missing-only` against snes/genesis/psx/pcengine afterward -- all four correctly reported "nothing missing," confirming this was isolated to pcenginecd and the new fallback passes didn't introduce false matches elsewhere. |
|      |      |        |       |
|      |      |        |       |
