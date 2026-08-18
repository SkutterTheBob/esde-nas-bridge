"""BridgeApp -- the Textual application object. Owns the loaded Config,
reloads it after any config-editing screen saves, and shows a friendly
startup screen instead of a raw traceback if config.yaml is missing or
malformed (the non-technical target user may well double-click tui.bat
before ever running setup.ps1)."""
from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

from ..config import Config, load_config


class StartupErrorScreen(Screen):
    """Shown instead of the dashboard when config.yaml can't be loaded."""

    def __init__(self, config_path: str, error: str) -> None:
        super().__init__()
        self._config_path = config_path
        self._error = error

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="error-box"):
            yield Static("Couldn't load config.yaml", classes="screen-title")
            yield Static(f"Path: {self._config_path}")
            yield Static(str(self._error))
            yield Static(
                "\nIf this is a fresh checkout, run setup.ps1 first (it copies "
                "config/config.example.yaml to config/config.yaml), then edit "
                "the machine-specific paths it points out. If config.yaml "
                "already exists, check it for a typo near the reported error.",
                classes="hint",
            )
            yield Button("Retry", id="retry", variant="primary")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "retry":
            self.app.reload_config()
            if self.app.config is not None:
                from .dashboard_screen import DashboardScreen
                self.app.pop_screen()
                self.app.push_screen(DashboardScreen())
            else:
                self.app.pop_screen()
                self.app.push_screen(StartupErrorScreen(self._config_path, self.app.config_error))


class BridgeApp(App):
    """esde-nas-bridge's terminal UI -- a presentation layer over the same
    src/ modules the CLI (src/cli.py) uses, so both stay in sync by
    construction rather than by discipline."""

    CSS_PATH = "app.tcss"
    TITLE = "esde-nas-bridge"

    def __init__(self, config_path: str = "config/config.yaml") -> None:
        super().__init__()
        self.config_path = config_path
        self.config: "Config | None" = None
        self.config_error: "str | None" = None
        self._load_config()

    def _load_config(self) -> None:
        try:
            self.config = load_config(self.config_path)
            self.config_error = None
        except Exception as exc:  # noqa: BLE001 -- config.yaml can fail in many ways (missing file, bad YAML, bad values); all of them should land on the same friendly startup screen, not crash the app
            self.config = None
            self.config_error = str(exc)

    def reload_config(self) -> None:
        """Re-reads config.yaml from disk. Call after any screen that edits
        it (Add System, Configure Media, Settings) saves successfully, so
        the rest of the app reflects the change without a restart."""
        self._load_config()

    def on_mount(self) -> None:
        if self.config is None:
            self.push_screen(StartupErrorScreen(self.config_path, self.config_error))
        else:
            from .dashboard_screen import DashboardScreen
            self.push_screen(DashboardScreen())
