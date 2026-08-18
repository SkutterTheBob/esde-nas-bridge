"""Generic 'run a background job and show live progress' screen -- reused
for Scan/Import Skraper/Scrape/Publish/Sync/Generate ES-Systems. Not reused
for the destructive commands (Clean Media/Prune Removed/Reset System),
which need a dry-run preview and confirmation step first -- see
destructive_screen.py.
"""
from __future__ import annotations

from typing import Callable

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

from .widgets import ProgressLog
from .workers import make_ui_progress_callback


class RunScreen(Screen):
    """`work` is a callable taking one argument -- a progress_callback
    matching the (count, total, message) convention used throughout src/ --
    and returning a summary string to display on completion. It's built by
    one of the functions in actions.py, already bound to config/system/
    flags via a closure, so RunScreen itself never needs to know which
    underlying command it's running.

    No Cancel button: none of the wrapped functions accept a cancellation
    token, and Python threads can't be force-killed, so once started a job
    runs to completion (or its own exception). See the design plan for why
    this is a deliberate v1 gap, not an oversight.
    """

    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self, title: str, work: "Callable[[Callable], str]") -> None:
        super().__init__()
        self._title = title
        self._work = work
        self.result_text = ""  # last text shown in #result; public for testability

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._title, classes="screen-title")
        yield ProgressLog()
        yield Static("", id="result")
        with_back = Button("Back", id="back", disabled=True, variant="primary")
        yield with_back
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._do_work, thread=True, exclusive=True)

    def _do_work(self) -> None:
        progress_log = self.query_one(ProgressLog)
        progress_callback = make_ui_progress_callback(self.app, progress_log.report)
        try:
            summary = self._work(progress_callback)
        except Exception as exc:  # noqa: BLE001 -- any failure from a wrapped command (NAS unreachable, bad Skraper export, API error, etc.) should land here as readable text, never a raw traceback in the UI
            self.app.call_from_thread(self._on_error, exc)
            return
        self.app.call_from_thread(self._on_done, summary)

    def _on_error(self, exc: Exception) -> None:
        self.result_text = f"Something went wrong: {exc}"
        self.query_one("#result", Static).update(f"[bold red]Something went wrong:[/bold red]\n{exc}")
        self.query_one("#back", Button).disabled = False

    def _on_done(self, summary: str) -> None:
        self.result_text = summary
        self.query_one("#result", Static).update(summary)
        self.query_one("#back", Button).disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()

    def action_go_back(self) -> None:
        if not self.query_one("#back", Button).disabled:
            self.app.pop_screen()
