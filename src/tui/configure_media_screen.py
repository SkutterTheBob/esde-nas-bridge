"""Form version of cli.py's `configure-media` command -- a checkbox grid
over ALL_MEDIA_TYPES instead of the CLI's numbered-list prompt, same
underlying config_editor.update_or_insert_scalar call and the same
`enabled_media_types: [...]` line format."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, SelectionList, Static

from ..config import Config
from ..config_editor import update_or_insert_scalar
from ..media_types import ALL_MEDIA_TYPES, LARGE_MEDIA_TYPES


class ConfigureMediaScreen(Screen):
    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self, config: Config, config_path: str) -> None:
        super().__init__()
        self._config = config
        self._config_path = config_path

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Configure Media", classes="screen-title")
        yield Static(
            "Choose which media types to cache locally. Deselect everything for "
            "ROMs-only mode (metadata still cached, no art/video/manuals at all). "
            "Doesn't delete media already on disk -- re-run Clean Media after "
            "narrowing your selection to remove what's no longer wanted.",
            classes="hint",
        )
        current = (
            set(self._config.enabled_media_types)
            if self._config.enabled_media_types is not None else set(ALL_MEDIA_TYPES)
        )
        selections = [
            (kind + ("  (often large)" if kind in LARGE_MEDIA_TYPES else ""), kind, kind in current)
            for kind in ALL_MEDIA_TYPES
        ]
        with VerticalScroll():
            yield SelectionList(*selections, id="media_types")
            yield Static("", id="form-error", classes="error-box")
            with Horizontal(classes="button-row"):
                yield Button("Save", id="save", variant="primary")
                yield Button("Back", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#form-error").display = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()
        elif event.button.id == "save":
            self._save()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def _save(self) -> None:
        selected = set(self.query_one("#media_types", SelectionList).selected)
        enabled = [k for k in ALL_MEDIA_TYPES if k in selected]  # keep canonical order
        yaml_line = f"enabled_media_types: [{', '.join(enabled)}]"

        ok = update_or_insert_scalar(
            self._config_path, "enabled_media_types", yaml_line, insert_after_key="roms_stub_root"
        )
        if not ok:
            error = self.query_one("#form-error", Static)
            error.update(
                "Couldn't safely auto-update config.yaml (unexpected file structure). "
                "Add this line yourself under the `cache:` section:\n" + yaml_line
            )
            error.display = True
            return

        self.app.reload_config()
        self.app.notify("Updated config.yaml.", title="Configure Media")
        self.app.pop_screen()
