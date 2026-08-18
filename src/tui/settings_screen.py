"""A bounded settings form -- NOT a general YAML editor. Covers exactly
the machine-specific fields setup.ps1 already tells a new machine's user
to edit by hand: cache.media_root, cache.gamelists_root, each configured
NAS source's root, retroarch.binary, retroarch.core_dir. Deliberately
excludes db_path, roms_stub_root, es_de_home, scraper credential env-var
names, per-system screenscraper_id/theme overrides, and the on_demand NAS
mount block -- none of those are things setup.ps1 asks people to hand-edit.

Also covers cache.systems_reference_dir, a newer optional field with a
different shape from the rest (it may not exist in config.yaml yet, so it
needs insert-or-replace rather than replace-only) -- see its own comment
below.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Static

from ..config import Config
from ..config_editor import replace_scalar_in_section, update_nas_source_root, update_or_insert_scalar


def _quote(value: str) -> str:
    return f"'{value}'"


class SettingsScreen(Screen):
    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self, config: Config, config_path: str) -> None:
        super().__init__()
        self._config = config
        self._config_path = config_path

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Settings", classes="screen-title")
        yield Static(
            "Machine-specific paths -- usually only need editing once per machine, "
            "after copying config.yaml to a new one.",
            classes="hint",
        )
        with VerticalScroll():
            yield Static("ES-DE downloaded_media folder", classes="field-label")
            yield Input(value=str(self._config.media_root), id="media_root")

            yield Static("ES-DE gamelists folder", classes="field-label")
            yield Input(value=str(self._config.gamelists_root), id="gamelists_root")

            for name, source in self._config.nas_sources.items():
                yield Static(f"NAS source '{name}' root", classes="field-label")
                yield Input(value=source.root, id=f"nas_root__{name}")

            yield Static("RetroArch executable", classes="field-label")
            yield Input(value=self._config.retroarch_binary, id="retroarch_binary")

            yield Static("RetroArch cores folder", classes="field-label")
            yield Input(value=self._config.retroarch_core_dir, id="retroarch_core_dir")

            yield Static(
                "Systems reference folder (optional) -- a folder with one subfolder per "
                "possible system code (e.g. ES-DE's own ROMs directory structure). Used only "
                "to offer a picker for Add System's system-key field instead of free typing.",
                classes="field-label",
            )
            yield Input(
                value=str(self._config.systems_reference_dir) if self._config.systems_reference_dir else "",
                placeholder="(optional, leave blank to type system keys manually)",
                id="systems_reference_dir",
            )

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
        failures = []

        media_root = self.query_one("#media_root", Input).value.strip()
        if media_root != str(self._config.media_root):
            if not replace_scalar_in_section(self._config_path, "cache", "media_root", _quote(media_root)):
                failures.append("cache.media_root")

        gamelists_root = self.query_one("#gamelists_root", Input).value.strip()
        if gamelists_root != str(self._config.gamelists_root):
            if not replace_scalar_in_section(self._config_path, "cache", "gamelists_root", _quote(gamelists_root)):
                failures.append("cache.gamelists_root")

        for name, source in self._config.nas_sources.items():
            new_root = self.query_one(f"#nas_root__{name}", Input).value.strip()
            if new_root != source.root:
                if not update_nas_source_root(self._config_path, name, _quote(new_root)):
                    failures.append(f"nas.{name}.root")

        retroarch_binary = self.query_one("#retroarch_binary", Input).value.strip()
        if retroarch_binary != self._config.retroarch_binary:
            if not replace_scalar_in_section(self._config_path, "retroarch", "binary", _quote(retroarch_binary)):
                failures.append("retroarch.binary")

        retroarch_core_dir = self.query_one("#retroarch_core_dir", Input).value.strip()
        if retroarch_core_dir != self._config.retroarch_core_dir:
            if not replace_scalar_in_section(self._config_path, "retroarch", "core_dir", _quote(retroarch_core_dir)):
                failures.append("retroarch.core_dir")

        systems_reference_dir = self.query_one("#systems_reference_dir", Input).value.strip()
        current_reference_dir = str(self._config.systems_reference_dir) if self._config.systems_reference_dir else ""
        if systems_reference_dir and systems_reference_dir != current_reference_dir:
            # May not exist in config.yaml yet (it's a newer, optional
            # field) -- insert-or-replace, not replace-only, unlike the
            # fields above which load_config already requires.
            if not update_or_insert_scalar(
                self._config_path, "systems_reference_dir",
                f"systems_reference_dir: {_quote(systems_reference_dir)}",
                insert_after_key="roms_stub_root",
            ):
                failures.append("cache.systems_reference_dir")

        self.app.reload_config()
        if self.app.config is not None:
            self._config = self.app.config

        if failures:
            error = self.query_one("#form-error", Static)
            error.update(
                "Couldn't safely auto-update these fields in config.yaml (unexpected file "
                "structure) -- edit them yourself: " + ", ".join(failures)
            )
            error.display = True
        else:
            self.app.notify("Updated config.yaml.", title="Settings")
            self.app.pop_screen()
