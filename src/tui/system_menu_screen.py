"""Per-system action menu, pushed from a DashboardScreen row. Buttons for
every per-system CLI command, each with its 0-2 relevant options as inline
checkboxes right above it -- keeps RunScreen itself option-free and generic.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Header, Static

from ..config import Config
from ..system_names import COMMON_FULLNAMES
from . import actions
from .destructive_screen import CleanMediaScreen, PruneRemovedScreen, ResetSystemScreen
from .run_screen import RunScreen


class SystemMenuScreen(Screen):
    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self, config: Config, config_path: str, system_name: str) -> None:
        super().__init__()
        self._config = config
        self._config_path = config_path
        self._system_name = system_name

    def compose(self) -> ComposeResult:
        sys_cfg = self._config.systems[self._system_name]
        fullname = sys_cfg.fullname or COMMON_FULLNAMES.get(self._system_name.lower(), self._system_name)
        has_skraper = self._system_name in self._config.skraper_imports

        yield Header()
        yield Static(f"{self._system_name} ({fullname})", classes="screen-title")

        with VerticalScroll():
            yield Checkbox("Compute checksums (slower, needed for ScreenScraper)", id="checksums")
            yield Button("Scan", id="scan", variant="primary")

            yield Checkbox("Missing metadata only", id="missing_only", value=True)
            yield Button("Import Skraper", id="import_skraper", variant="primary", disabled=not has_skraper)
            if not has_skraper:
                yield Static("No skraper_imports entry configured for this system.", classes="hint")

            yield Checkbox("Re-scrape everything (not just missing)", id="rescrape_all")
            yield Button("Scrape", id="scrape", variant="primary")

            yield Button("Publish (write stubs + gamelist.xml)", id="publish", variant="primary")

            yield Checkbox("Compute checksums during scan", id="sync_checksums")
            yield Checkbox("Skip Skraper import", id="sync_skip_skraper")
            yield Button("Sync (scan + import-skraper + publish)", id="sync", variant="success")

            yield Button("Clean Media", id="clean_media")

            yield Checkbox("Also sweep orphaned media", id="sweep_orphaned")
            yield Button("Prune Removed", id="prune_removed")

            yield Button("Reset System (wipe everything)", id="reset_system", classes="warning-button")

            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
        yield Footer()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        name = self._system_name

        if bid == "back":
            self.app.pop_screen()

        elif bid == "scan":
            checksums = self.query_one("#checksums", Checkbox).value
            work = actions.scan_work(self._config, name, checksums)
            self.app.push_screen(RunScreen(f"Scan: {name}", work))

        elif bid == "import_skraper":
            missing_only = self.query_one("#missing_only", Checkbox).value
            work = actions.import_skraper_work(self._config, name, missing_only)
            self.app.push_screen(RunScreen(f"Import Skraper: {name}", work))

        elif bid == "scrape":
            rescrape_all = self.query_one("#rescrape_all", Checkbox).value
            work = actions.scrape_work(self._config, name, rescrape_all)
            self.app.push_screen(RunScreen(f"Scrape: {name}", work))

        elif bid == "publish":
            work = actions.publish_work(self._config, name)
            self.app.push_screen(RunScreen(f"Publish: {name}", work))

        elif bid == "sync":
            checksums = self.query_one("#sync_checksums", Checkbox).value
            skip_skraper = self.query_one("#sync_skip_skraper", Checkbox).value
            work = actions.sync_work(self._config, name, checksums, skip_skraper)
            self.app.push_screen(RunScreen(f"Sync: {name}", work))

        elif bid == "clean_media":
            self.app.push_screen(CleanMediaScreen(self._config, [name]))

        elif bid == "prune_removed":
            sweep = self.query_one("#sweep_orphaned", Checkbox).value
            self.app.push_screen(PruneRemovedScreen(self._config, [name], sweep))

        elif bid == "reset_system":
            self.app.push_screen(ResetSystemScreen(self._config, name))
