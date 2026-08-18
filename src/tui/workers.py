"""Bridges the blocking progress_callback(count, total, message) convention
used throughout src/ (scan_system, import_skraper_export, scrape_system,
write_stubs_for_system all call it synchronously, from whatever thread is
running them) onto Textual's UI thread.

Textual widgets may only be mutated from the app's own event-loop thread.
The functions above run on a worker thread (started via
App.run_worker(..., thread=True)), so their progress_callback needs to hop
back onto the UI thread for every update -- that's what
make_ui_progress_callback does, via App.call_from_thread.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from textual.app import App


@dataclass
class ProgressUpdate:
    count: int
    total: "int | None"
    message: str


def make_ui_progress_callback(
    app: App,
    on_update: "Callable[[ProgressUpdate], None]",
    min_interval: float = 0.05,
) -> "Callable[[int, int | None, str], None]":
    """Returns a progress_callback(count, total, message) safe to pass
    directly into scan_system/import_skraper_export/scrape_system/
    write_stubs_for_system.

    Called synchronously on the WORKER thread (by whichever blocking
    function it was handed to); marshals onto the UI thread via
    App.call_from_thread, which blocks the calling worker thread until
    on_update finishes running on the event loop -- keep on_update cheap
    (widget mutation only, never I/O).

    Time-throttled at min_interval seconds rather than count-throttled:
    scan without --checksums can call back well over 100 times/second at
    NAS-walk speed, and forwarding every single one through
    call_from_thread's round-trip would make the UI thread the bottleneck.
    The final update (count == total, when total is known) always goes
    through regardless of the throttle, so the bar visibly reaches 100%.
    """
    last_sent = [0.0]

    def _callback(count: int, total: "int | None", message: str) -> None:
        now = time.monotonic()
        is_last = total is not None and count == total
        if is_last or now - last_sent[0] >= min_interval:
            last_sent[0] = now
            app.call_from_thread(on_update, ProgressUpdate(count, total, message))

    return _callback
