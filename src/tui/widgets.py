"""Shared widgets used across multiple TUI screens."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Log, ProgressBar, Static

from .workers import ProgressUpdate


class ProgressLog(Widget):
    """A progress bar (indeterminate until a total is known -- Textual's
    own default behavior for ProgressBar(total=None)) stacked over a
    scrolling log of progress messages. Fed directly by ProgressUpdate
    objects from make_ui_progress_callback's on_update."""

    DEFAULT_CSS = """
    ProgressLog {
        height: 1fr;
    }
    ProgressLog > ProgressBar {
        width: 1fr;
        margin-bottom: 1;
    }
    ProgressLog > Log {
        height: 1fr;
        border: round $primary;
    }
    """

    def compose(self) -> ComposeResult:
        yield ProgressBar(show_eta=False)
        yield Log()

    def report(self, update: ProgressUpdate) -> None:
        bar = self.query_one(ProgressBar)
        if update.total is not None:
            bar.update(total=update.total, progress=update.count)
        else:
            bar.update(progress=update.count)
        self.query_one(Log).write_line(
            f"[{update.count}/{update.total}] {update.message}"
            if update.total is not None else f"[{update.count}] {update.message}"
        )

    def write_line(self, line: str) -> None:
        self.query_one(Log).write_line(line)
