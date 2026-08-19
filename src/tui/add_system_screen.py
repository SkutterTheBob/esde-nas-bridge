"""Form version of cli.py's `add-system` Q&A -- same fields, same
auto-suggestions (COMMON_EXTENSIONS/GLOBAL_ARCHIVE_EXTENSIONS for
extensions, COMMON_FULLNAMES for the display name), same underlying
config_editor.build_system_yaml_block/append_to_yaml_section calls, so the
two never drift apart on what actually lands in config.yaml.

Two fields additionally offer a picker instead of free-text entry, built
from real data already available: the system key (from
config.systems_reference_dir, if set -- a folder with one subfolder per
possible system code) and the RetroArch core (from
config.retroarch_core_dir, always set -- the actual installed .dll/.so
files). Both degrade to plain free-text entry if there's nothing to list.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Header, Input, Select, Static

from ..config import Config
from ..config_editor import append_to_yaml_section, build_system_yaml_block
from ..system_extensions import COMMON_EXTENSIONS, GLOBAL_ARCHIVE_EXTENSIONS
from ..system_names import COMMON_FULLNAMES

_OTHER = "__other__"


def _suggested_extensions(name: str) -> str:
    native = COMMON_EXTENSIONS.get(name, [])
    suggested = native + [e for e in GLOBAL_ARCHIVE_EXTENSIONS if e not in native]
    return ",".join(suggested)


def _discover_cores(core_dir: str) -> "list[str]":
    """Lists available libretro core base names (e.g. 'snes9x_libretro')
    under config.retroarch_core_dir, matching the same platform-extension
    convention launch_wrapper.py's _core_filename uses in reverse (.dll on
    Windows, .dylib on Mac, .so elsewhere). Returns an empty list if the
    directory doesn't exist or can't be read -- the form falls back to
    free-text entry in that case, same as before this existed."""
    ext = {"nt": ".dll", "posix": ".dylib" if sys.platform == "darwin" else ".so"}[os.name]
    try:
        return sorted(
            p.stem for p in Path(core_dir).iterdir()
            if p.is_file() and p.suffix.lower() == ext
        )
    except OSError:
        return []


def _discover_system_codes(reference_dir: "str | Path") -> "list[str]":
    """Lists subfolder names under a systems-reference folder (e.g. a
    folder mirroring ES-DE's own ROMs directory structure, one subfolder
    per possible system code) -- used only to populate the system-key
    picker with real, correctly-spelled options. Returns an empty list if
    the directory doesn't exist, isn't configured, or can't be read --
    falls back to free-text entry in that case."""
    try:
        return sorted(p.name for p in Path(reference_dir).iterdir() if p.is_dir())
    except OSError:
        return []


class AddSystemScreen(Screen):
    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self, config: Config, config_path: str) -> None:
        super().__init__()
        self._config = config
        self._config_path = config_path
        self._available_cores = _discover_cores(config.retroarch_core_dir)
        self._available_system_codes = (
            _discover_system_codes(config.systems_reference_dir)
            if config.systems_reference_dir else []
        )

    def _picker(self, select_id: str, manual_id: str, options: "list[str]",
                manual_placeholder: str, none_found_hint: str) -> ComposeResult:
        """Yields a Select-of-real-options + fallback manual Input, or just
        the manual Input alone if `options` is empty. Shared by the system
        key and RetroArch core fields, the two pickers built from real
        on-disk data. `on_mount`/`on_select_changed` below rely on the
        manual Input always existing under `manual_id` regardless of which
        branch rendered, so submit-time reading is uniform via
        `_picked_value`."""
        if options:
            select_options = [(o, o) for o in options] + [("Other (type manually)...", _OTHER)]
            yield Select(select_options, id=select_id)
            yield Input(id=manual_id, placeholder=manual_placeholder)
        else:
            yield Static(none_found_hint, classes="hint")
            yield Input(id=manual_id, placeholder=manual_placeholder)

    def _picked_value(self, select_id: str, manual_id: str, available: bool) -> str:
        """Reads a Select+manual-Input pair built by `_picker`: a real
        selected option wins; otherwise (no options were ever discovered,
        or 'Other' was chosen) falls back to the manual Input."""
        if available:
            selected = self.query_one(f"#{select_id}", Select).value
            if isinstance(selected, str) and selected != _OTHER:
                return selected
        return self.query_one(f"#{manual_id}", Input).value.strip()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Add a New System", classes="screen-title")
        with VerticalScroll():
            yield Static("System key (lowercase, e.g. 'psx', 'n64')", classes="field-label")
            yield from self._picker(
                "name_select", "name_manual", self._available_system_codes,
                manual_placeholder="e.g. psx",
                none_found_hint=(
                    "Set 'Systems reference folder' in Settings to pick from a list here "
                    "instead of typing it."
                ),
            )

            nas_names = list(self._config.nas_sources.keys())
            yield Static("NAS source", classes="field-label")
            if len(nas_names) == 1:
                yield Static(f"{nas_names[0]} (only one configured)")
                yield Select([(n, n) for n in nas_names], value=nas_names[0], id="nas_source", disabled=True)
            else:
                yield Select([(n, n) for n in nas_names], id="nas_source")

            yield Static("NAS subfolder name for this system (defaults to the system key)", classes="field-label")
            yield Input(placeholder="(same as system key)", id="subdir")

            yield Static("File extensions, comma-separated", classes="field-label")
            yield Input(id="extensions")

            yield Checkbox("Use a standalone emulator instead of a RetroArch core", id="standalone")

            with Vertical(id="retroarch-fields"):
                yield Static(
                    f"RetroArch core"
                    + (f" ({len(self._available_cores)} found under {self._config.retroarch_core_dir})"
                       if self._available_cores else " (e.g. 'snes9x_libretro')"),
                    classes="field-label",
                )
                yield from self._picker(
                    "retroarch_core_select", "retroarch_core_manual", self._available_cores,
                    manual_placeholder="Core name, e.g. 'snes9x_libretro'",
                    none_found_hint=f"Couldn't list any cores under {self._config.retroarch_core_dir} -- type the name manually.",
                )

            with Vertical(id="emulator-fields"):
                yield Static("Path to the standalone emulator's executable", classes="field-label")
                yield Input(id="emulator_binary")
                yield Static('Command-line arguments, with {rom} where the ROM path goes', classes="field-label")
                yield Input(value='"{rom}"', id="emulator_args")
                yield Checkbox(
                    "Launch fails with 'WinError 5 / Access is denied' (rare, some standalone emulators)",
                    id="emulator_use_shell",
                )

            yield Static("Display name (Enter to auto-detect)", classes="field-label")
            yield Input(id="fullname")

            yield Static("ScreenScraper system ID (Enter to auto-detect)", classes="field-label")
            yield Input(id="screenscraper_id")

            yield Static("Skraper NAS export path, e.g. 'Y:\\roms\\psx' (optional)", classes="field-label")
            yield Input(id="skraper_path")

            yield Static("", id="form-error", classes="error-box")
            with Horizontal(classes="button-row"):
                yield Button("Add System", id="submit", variant="primary")
                yield Button("Back", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#emulator-fields").display = False
        self.query_one("#form-error").display = False
        if self._available_system_codes:
            self.query_one("#name_manual").display = False
        if self._available_cores:
            self.query_one("#retroarch_core_manual").display = False

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "retroarch_core_select":
            self.query_one("#retroarch_core_manual").display = (event.value == _OTHER)
        elif event.select.id == "name_select":
            self.query_one("#name_manual").display = (event.value == _OTHER)
            if isinstance(event.value, str) and event.value != _OTHER:
                self._apply_name_suggestions(event.value)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "name_manual":
            self._apply_name_suggestions(event.value)

    def _apply_name_suggestions(self, name: str) -> None:
        name = name.strip().lower()
        ext_input = self.query_one("#extensions", Input)
        if not ext_input.value:
            ext_input.value = _suggested_extensions(name)
        fullname_input = self.query_one("#fullname", Input)
        if not fullname_input.value:
            fullname_input.placeholder = COMMON_FULLNAMES.get(name, name)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "standalone":
            self.query_one("#emulator-fields").display = event.value
            self.query_one("#retroarch-fields").display = not event.value
            if event.value:
                # The path field this reveals is easy to miss -- it appears
                # below the checkbox with nothing scrolling the form to show
                # it, so a user already scrolled down elsewhere in this long
                # form can check the box and see no visible change at all.
                # scroll_end() (an earlier attempt here) scrolls to the very
                # bottom of the WHOLE form, past the fields below the
                # emulator section (fullname/screenscraper_id/skraper_path/
                # buttons) -- on a short-enough terminal that tail alone can
                # fill the viewport, pushing the actual emulator fields back
                # OFF the top of the screen. Confirmed via a headless
                # Textual pilot test at 80x10: scroll_end() left the path
                # field's region at y=-4 (not in view) while pinning the
                # section itself to the TOP of the viewport (top=True) kept
                # it visible at every terminal size tested, since it never
                # scrolls further than needed to reveal this section.
                # Deferred to call_after_refresh so it runs once the layout
                # has actually re-run after the display change above --
                # calling it immediately can hit a zero-size region and
                # silently no-op.
                def _reveal() -> None:
                    self.query_one("#emulator-fields").scroll_visible(
                        top=True, animate=False, immediate=True
                    )
                    # Belt-and-suspenders against a stale partial repaint on
                    # terminals that don't cleanly redraw a region whose
                    # `display` just flipped True in the same tick as a
                    # scroll -- forces every cell to be redrawn, not just
                    # the ones Textual's diffing thinks changed.
                    self.screen.refresh(layout=True)

                self.call_after_refresh(_reveal)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()
        elif event.button.id == "submit":
            self._submit()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def _show_error(self, message: str) -> None:
        error = self.query_one("#form-error", Static)
        error.update(message)
        error.display = True

    def _submit(self) -> None:
        name = self._picked_value("name_select", "name_manual", bool(self._available_system_codes)).lower()
        if not name:
            self._show_error("System key is required.")
            return
        if name in self._config.systems:
            self._show_error(f"'{name}' is already configured -- edit config.yaml directly to change it.")
            return

        nas_source = self.query_one("#nas_source", Select).value
        if not nas_source:
            self._show_error("Choose a NAS source.")
            return

        subdir = self.query_one("#subdir", Input).value.strip() or name

        extensions_raw = self.query_one("#extensions", Input).value
        extensions = []
        for e in extensions_raw.split(","):
            e = e.strip()
            if not e:
                continue
            extensions.append(e if e.startswith(".") else f".{e}")
        if not extensions:
            self._show_error("At least one file extension is required.")
            return

        standalone = self.query_one("#standalone", Checkbox).value
        retroarch_core = None
        emulator_binary = None
        emulator_args = None
        emulator_use_shell = False
        if standalone:
            emulator_binary = self.query_one("#emulator_binary", Input).value.strip()
            if not emulator_binary:
                self._show_error("Standalone emulator path is required.")
                return
            emulator_args = self.query_one("#emulator_args", Input).value.strip() or "{rom}"
            emulator_use_shell = self.query_one("#emulator_use_shell", Checkbox).value
        else:
            retroarch_core = self._picked_value(
                "retroarch_core_select", "retroarch_core_manual", bool(self._available_cores)
            )
            if not retroarch_core:
                self._show_error("RetroArch core name is required.")
                return

        fullname = self.query_one("#fullname", Input).value.strip()
        screenscraper_id = self.query_one("#screenscraper_id", Input).value.strip()
        skraper_path = self.query_one("#skraper_path", Input).value.strip()

        block = build_system_yaml_block(
            name, nas_source, subdir, extensions,
            retroarch_core=retroarch_core,
            emulator_binary=emulator_binary, emulator_args=emulator_args,
            emulator_use_shell=emulator_use_shell,
            screenscraper_id=screenscraper_id or None, fullname=fullname or None,
        )

        if not append_to_yaml_section(self._config_path, "systems", block):
            self._show_error(
                "Couldn't safely auto-update config.yaml (unexpected file structure). "
                "Add this yourself under the `systems:` section:\n" + block
            )
            return

        if skraper_path:
            skraper_line = f"  {name}: '{skraper_path}'"
            append_to_yaml_section(self._config_path, "skraper_imports", skraper_line)

        self.app.reload_config()
        self.app.pop_screen()
        from .dashboard_screen import DashboardScreen
        if isinstance(self.app.screen, DashboardScreen):
            self.app.screen.refresh_systems()
        self.app.notify(f"Added '{name}' to config.yaml. Run Sync to pull in its ROMs.", title="System added")
