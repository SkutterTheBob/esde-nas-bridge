"""The only piece ES-DE actually calls to launch a game.

Usage (as configured in es_systems.xml, see systems/es_systems.snippet.xml):

    python -m src.launch_wrapper <system> <path-to-stub-rom-file>

The second argument is whatever ES-DE substitutes for %ROM% -- which will
be the path to the local placeholder file under roms_stub_root (see
rom_stubs.py), NOT a real ROM. We deliberately don't trust that path beyond
its filename: we look the filename up in the local database to get the
ROM's real rel_path, then resolve that against the actual NAS source. This
decouples us from exactly how ES-DE formats %ROM% (absolute vs relative)
and means a stale/moved stub file can never cause the wrong file to launch.

This is deliberately the ONLY place in normal frontend usage where the NAS
path gets resolved/mounted (the other is the explicit `scan` command). It:

  1. Looks up the ROM by filename in the local index to get its real rel_path.
  2. Resolves the system's NAS source to a local root via ensure_mounted()
     -- a no-op for "mapped" sources, an actual mount/net-use for "on_demand".
  3. Verifies the ROM file exists at that path (fail fast with a clear
     error rather than letting RetroArch throw a confusing one).
  4. Runs RetroArch with the resolved absolute path and configured core,
     and exits with RetroArch's own exit code.

Note on process replacement: this uses subprocess.run rather than
os.execv. execv is documented as not reliably replacing the process on
Windows (it's emulated via spawn), so subprocess + propagating the exit
code is the more predictable choice cross-platform.

Note on working directory: ES-DE launches this command with ITS OWN working
directory, not the repo root -- so anything resolved relative to the
current directory (a bare "config/config.yaml", or `python -m src...`
needing the repo root on sys.path) breaks silently: a console window
flashes and closes, ES-DE just sees a failed launch and returns to its
menu. See systems/launch.bat for the actual fix (sets cwd explicitly
before invoking Python) -- the config path below is also made
cwd-independent as defense in depth, resolved relative to this file's own
location instead of the current directory.
"""
from __future__ import annotations

import os
import sys
import shlex
import subprocess
import traceback
from pathlib import Path

from .config import load_config
from .db import connect, rom_by_filename
from .mount import MountError, ensure_mounted, elevation_hint

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = os.environ.get("RA_NAS_CONFIG", str(_REPO_ROOT / "config" / "config.yaml"))
# ES-DE's console window closes the instant this process exits, giving no
# chance to read a traceback -- write failures here too so they're still
# diagnosable after the fact.
ERROR_LOG_PATH = _REPO_ROOT / "launch_wrapper_errors.log"


def resolve_rom_path(config, system_name: str, rom_arg: str) -> Path:
    sys_cfg = config.systems[system_name]
    nas_source = config.nas_sources[sys_cfg.nas_source]
    filename = Path(rom_arg).name

    with connect(config.db_path) as conn:
        rom = rom_by_filename(conn, system_name, filename)

    if rom is None:
        raise FileNotFoundError(
            f"'{filename}' isn't in the local index for system '{system_name}'. "
            f"Run `scan` (and `write-stubs`) again if this is a new file."
        )

    root = ensure_mounted(nas_source)  # <-- network touched here, and only here
    rom_path = Path(root) / sys_cfg.subdir / rom["rel_path"]

    if not rom_path.exists():
        raise FileNotFoundError(
            f"ROM not found at resolved path: {rom_path}\n"
            f"(NAS source '{nas_source.name}', mode={nas_source.mode}). "
            f"If the file was recently moved or deleted, run `scan` again."
            f"{elevation_hint()}"
        )
    return rom_path


def launch(system_name: str, rom_arg: str, config_path: str = DEFAULT_CONFIG_PATH) -> None:
    config = load_config(config_path)
    sys_cfg = config.systems[system_name]

    try:
        rom_path = resolve_rom_path(config, system_name, rom_arg)
    except (MountError, FileNotFoundError) as e:
        print(f"[launch_wrapper] {e}", file=sys.stderr)
        sys.exit(1)

    argv = _build_argv(config, sys_cfg, rom_path)
    # Show what's ACTUALLY going to run, not a naive space-join -- a plain
    # join doesn't reflect real quoting (e.g. never shows spaces-in-a-path
    # as quoted even when they will be), which made a real cmd.exe quoting
    # bug harder to diagnose than it should have been. list2cmdline is the
    # same logic subprocess itself uses to build the real command line.
    display = argv if isinstance(argv, str) else subprocess.list2cmdline(argv)
    print(f"[launch_wrapper] launching: {display}", file=sys.stderr)

    try:
        result = subprocess.run(argv)
    except OSError as e:
        # Distinct from a crash in our own code -- this means Windows/the OS
        # itself refused to start the target process (e.g. PermissionError /
        # WinError 5, file-not-found for the emulator binary, etc). Log the
        # actual command that was attempted, not just launch_wrapper's own
        # arguments, since that's what's actually needed to diagnose it.
        msg = f"Failed to start process.\nCommand attempted: {display}\n{e}"
        print(f"[launch_wrapper] {msg}", file=sys.stderr)
        _log_error(f"argv={sys.argv}", f"{msg}\n{traceback.format_exc()}")
        sys.exit(1)

    sys.exit(result.returncode)


def _build_argv(config, sys_cfg, rom_path: Path) -> "list[str] | str":
    """Builds the command to run for this system: either a standalone
    emulator (sys_cfg.emulator set -- e.g. DuckStation for PSX) or
    RetroArch with a libretro core (sys_cfg.retroarch_core set). Exactly
    one is set, enforced at config load time.

    Returns a list (the common case) EXCEPT for the emulator.use_shell
    case, where it returns a pre-built STRING instead (see that branch's
    comment for why) -- subprocess.run accepts either on Windows, and
    passing a string means Python won't re-quote something we've already
    deliberately quoted ourselves.
    """
    if sys_cfg.emulator is not None:
        # Split the TEMPLATE first, while {rom} is still a plain token with
        # no spaces, so shlex splits predictably regardless of what the
        # real ROM path looks like -- then substitute the real path in
        # after splitting. This means a rom path containing spaces can
        # never confuse the split, since it's never present during it.
        template_parts = shlex.split(sys_cfg.emulator.args)
        emulator_argv = [sys_cfg.emulator.binary] + [
            part.replace("{rom}", str(rom_path)) for part in template_parts
        ]
        if sys_cfg.emulator.use_shell:
            # cmd.exe /c has a well-documented, notorious quirk (confirmed
            # in Microsoft's own docs): it only preserves quoting cleanly
            # when the remaining command line has EXACTLY TWO quote
            # characters total. With more than two -- e.g. a quoted binary
            # path AND a quoted rom path together, four quote characters --
            # it falls back to legacy behavior: strip only the very first
            # and very last quote character of the WHOLE line, leaving
            # everything between them unquoted. That silently un-protects
            # spaces in the binary path, breaking the launch (confirmed on
            # real hardware, see TESTING.md). Standard, documented
            # workaround: wrap the ENTIRE inner command in one more pair of
            # quotes, so cmd.exe's quote-stripping consumes those instead.
            # Built as a plain string (not a list) so Python's own
            # automatic list2cmdline quoting doesn't get applied a second
            # time on top of this deliberate, hand-built quoting.
            inner = subprocess.list2cmdline(emulator_argv)
            return f'cmd.exe /c "{inner}"'
        return emulator_argv

    core_path = str(Path(config.retroarch_core_dir) / _core_filename(sys_cfg.retroarch_core))
    return [config.retroarch_binary, "-L", core_path, str(rom_path)]


def _core_filename(core_name: str) -> str:
    """Turns a bare core name (e.g. "snes9x_libretro") into the platform's
    actual core filename (e.g. "snes9x_libretro.so" or "snes9x_libretro.dll").
    If the config already includes an extension, it's used as-is.
    """
    if core_name.endswith((".so", ".dll", ".dylib")):
        return core_name
    ext = {"nt": ".dll", "posix": ".dylib" if sys.platform == "darwin" else ".so"}[os.name]
    return core_name + ext


def _log_error(context: str, body: str) -> None:
    try:
        with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n--- {context} ---\n{body}")
    except OSError:
        pass  # logging failed too -- nothing more we can do here


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m src.launch_wrapper <system> <rom-path-or-filename>", file=sys.stderr)
        sys.exit(2)
    try:
        launch(system_name=sys.argv[1], rom_arg=sys.argv[2])
    except SystemExit:
        raise  # sys.exit() calls from launch() itself -- not an unexpected crash
    except Exception:
        # Catch-all: ES-DE's console window closes instantly on exit, so
        # without this an unexpected crash is invisible. Log it somewhere
        # that survives.
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        _log_error(f"argv={sys.argv}", tb)
        sys.exit(1)
