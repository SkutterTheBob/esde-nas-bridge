"""Resolves a NasSource to a usable local root path.

For "mapped" sources this is a no-op on every OS: the drive/share is
assumed to always be available (Windows "Map Network Drive" with
reconnect-at-sign-in, a Linux fstab entry, etc.) -- we simply return the
configured root and never issue mount/unmount commands. Windows itself
lazily reconnects a mapped drive letter the moment a path under it is
touched, which already satisfies "only hit the network at launch" as long
as nothing else reads from the drive during normal browsing (it doesn't,
since browsing is served from the local cache).

For "on_demand" sources this is the ONLY place network mount commands are
issued, so callers (indexer, launch_wrapper) don't need to know or care
how a given source is actually attached. Windows uses `net use` against a
drive letter; Linux/macOS use `mount.cifs`/`mount.nfs` via sudo.

Credential handling: on Windows, the strongly preferred approach is to
pre-register the share's credentials in Windows Credential Manager
(`cmdkey /add:<server> /user:<user> /pass:<password>`, run once, outside
this codebase) so `net use` succeeds with no credentials on the command
line at all. username_env/password_env are supported as a fallback, but
note the password is briefly visible on the command line to anything
inspecting the process list while the command runs.
"""
from __future__ import annotations

import platform
import re
import subprocess
import time
from pathlib import Path

from .config import NasSource


class MountError(RuntimeError):
    pass


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _is_elevated_windows() -> bool:
    """Best-effort check for an elevated ("Run as Administrator") process."""
    if not _is_windows():
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevation_hint() -> str:
    """A one-line hint to append to "path not found" errors when running
    elevated on Windows. Mapped network drives belong to the specific
    logon session that created them, so an elevated terminal frequently
    can't see a drive that resolves fine in a normal one -- this has
    already been hit twice in testing, showing up as a generic "path not
    found" that looks like a connectivity problem but isn't one. Empty
    string when not applicable, so callers can always safely append this.
    """
    if _is_elevated_windows():
        return (
            " (This terminal is running as Administrator -- mapped network "
            "drives from a normal session are often not visible here. Try "
            "a non-elevated terminal.)"
        )
    return ""


def _normalize_drive(mount_point: str) -> str:
    """Accepts "Z", "Z:", or "Z:\\" and returns the "Z:" form net use expects."""
    letter = mount_point.rstrip("\\").rstrip(":")
    return f"{letter}:"


def _is_mounted(mount_point: str) -> bool:
    """Best-effort check that something is actually attached at mount_point."""
    if _is_windows():
        drive = _normalize_drive(mount_point)
        p = Path(drive + "\\")
        try:
            next(p.iterdir(), None)
            return True
        except OSError:
            return False

    p = Path(mount_point)
    if not p.exists():
        return False
    # POSIX: compare device id of mount_point vs its parent.
    try:
        return p.stat().st_dev != p.parent.stat().st_dev
    except OSError:
        return False


def _mount_cifs(source: NasSource) -> None:
    m = source.mount
    Path(m.mount_point).mkdir(parents=True, exist_ok=True)
    cmd = ["sudo", "mount", "-t", "cifs", m.share, m.mount_point]
    if m.credentials_file:
        cmd += ["-o", f"credentials={m.credentials_file}"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=m.timeout_seconds)
    if result.returncode != 0:
        raise MountError(f"mount.cifs failed for {source.name}: {result.stderr.strip()}")


def _mount_nfs(source: NasSource) -> None:
    m = source.mount
    Path(m.mount_point).mkdir(parents=True, exist_ok=True)
    cmd = ["sudo", "mount", "-t", "nfs", m.share, m.mount_point]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=m.timeout_seconds)
    if result.returncode != 0:
        raise MountError(f"mount.nfs failed for {source.name}: {result.stderr.strip()}")


def _mount_windows_smb(source: NasSource) -> None:
    m = source.mount
    drive = _normalize_drive(m.mount_point)

    # Clean up any stale/disconnected mapping at this drive letter first --
    # `net use` errors out rather than replacing an existing (even broken)
    # mapping.
    subprocess.run(["net", "use", drive, "/delete", "/y"], capture_output=True, text=True)

    cmd = ["net", "use", drive, m.share]
    username = m.username()
    password = m.password()
    if password:
        cmd.append(password)
    if username:
        cmd.append(f"/user:{username}")
    cmd.append("/persistent:no")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=m.timeout_seconds)
    if result.returncode != 0:
        # Strip anything password-like out of the error before raising, in
        # case net use echoed the command back.
        stderr = re.sub(re.escape(password), "****", result.stderr) if password else result.stderr
        raise MountError(f"net use failed for {source.name}: {stderr.strip()}")


def ensure_mounted(source: NasSource, retries: int = 2, retry_delay: float = 1.5) -> str:
    """Returns a local filesystem root usable right now.

    For mapped sources, returns source.root immediately with no network call.
    For on_demand sources, mounts (if not already mounted) then returns
    source.mount.mount_point.
    """
    if source.mode == "mapped":
        return source.root

    if source.mode != "on_demand":
        raise ValueError(f"Unknown NAS mode: {source.mode}")

    m = source.mount
    if _is_mounted(m.mount_point):
        return _normalize_drive(m.mount_point) + "\\" if _is_windows() else m.mount_point

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if _is_windows():
                _mount_windows_smb(source)
            elif m.type in ("cifs", "smb"):
                _mount_cifs(source)
            elif m.type == "nfs":
                _mount_nfs(source)
            else:
                raise MountError(f"Unsupported mount type: {m.type}")

            if _is_mounted(m.mount_point):
                return _normalize_drive(m.mount_point) + "\\" if _is_windows() else m.mount_point
        except (subprocess.TimeoutExpired, MountError) as e:
            last_err = e
            time.sleep(retry_delay)

    raise MountError(
        f"Could not mount NAS source '{source.name}' after {retries + 1} attempts: {last_err}"
    )


def maybe_unmount(source: NasSource) -> None:
    """Optional: unmount/disconnect an on_demand source after launch to avoid
    idle connections.

    Not called automatically -- wire this into launch_wrapper if you want
    "connected only for the duration of the game" behavior instead of
    leaving it mounted until next reboot/logoff.
    """
    if source.mode != "on_demand":
        return
    m = source.mount
    if _is_windows():
        drive = _normalize_drive(m.mount_point)
        subprocess.run(["net", "use", drive, "/delete", "/y"], capture_output=True, text=True)
    else:
        subprocess.run(["sudo", "umount", m.mount_point], capture_output=True, text=True)
