"""Generic modal dialogs shared across screens (as opposed to
destructive_screen.py's ConfirmModal/TypeToConfirmModal, which are
specifically part of that dry-run/confirm/apply flow)."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class InfoModal(ModalScreen[None]):
    """A dismiss-only popup showing a title and body. For actions fast
    enough (no meaningful progress to report -- e.g. Generate ES-Systems,
    which just writes one XML file) that switching to a full RunScreen
    would be pure friction: no progress bar to watch, just a result."""

    DEFAULT_CSS = """
    InfoModal {
        align: center middle;
    }
    InfoModal > Vertical {
        width: 70%;
        max-height: 80%;
        border: round $primary;
        padding: 1 2;
        background: $surface;
    }
    InfoModal Static#body {
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Dismiss"),
        Binding("enter", "dismiss_modal", "OK"),
    ]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title, classes="screen-title")
            yield Static(self._body, id="body")
            with Horizontal(classes="button-row"):
                yield Button("OK", id="ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)
