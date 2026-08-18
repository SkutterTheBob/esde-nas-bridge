"""Home screen: a table of every configured system (the same data
`list-systems` computes: fullname, ROM count, extensions, emulator,
Skraper import path) plus global action buttons. Row selection is the
system picker -- no separate picker widget needed."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from ..db import connect, init_db
from ..system_names import COMMON_FULLNAMES
from . import actions
from .add_system_screen import AddSystemScreen
from .configure_media_screen import ConfigureMediaScreen
from .modals import InfoModal
from .run_screen import RunScreen
from .settings_screen import SettingsScreen
from .system_menu_screen import SystemMenuScreen


class DashboardScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("esde-nas-bridge", classes="screen-title")
        if not self.app.config.systems:
            yield Static(
                "No systems configured yet -- use Add System below to add one.",
                classes="hint",
            )
        with VerticalScroll():
            yield DataTable(id="systems", cursor_type="row")
            with Horizontal(classes="button-row"):
                yield Button("Sync All", id="sync_all", variant="success")
                yield Button("Scan All", id="scan_all")
                yield Button("Publish All", id="publish_all")
                yield Button("Generate ES-Systems", id="generate_es_systems")
            with Horizontal(classes="button-row"):
                yield Button("Add System", id="add_system", variant="primary")
                yield Button("Configure Media", id="configure_media")
                yield Button("Settings", id="settings")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#systems", DataTable)
        table.add_columns("System", "Full Name", "ROMs", "Extensions", "Emulator", "Skraper Import")
        self.refresh_systems()

    def on_screen_resume(self) -> None:
        # Fires when returning here after a pushed screen (Scan, Sync,
        # Prune Removed, Reset System, ...) is popped -- keeps ROM counts
        # and any config.yaml changes visible without a manual refresh.
        self.refresh_systems()

    def refresh_systems(self) -> None:
        config = self.app.config
        table = self.query_one("#systems", DataTable)
        table.clear()
        if not config.systems:
            return

        init_db(config.db_path)
        with connect(config.db_path) as conn:
            for name, sys_cfg in config.systems.items():
                fullname = sys_cfg.fullname or COMMON_FULLNAMES.get(name.lower(), name)
                if sys_cfg.emulator:
                    emulator_desc = f"Standalone: {sys_cfg.emulator.binary}"
                else:
                    emulator_desc = f"RetroArch: {sys_cfg.retroarch_core}"
                skraper_path = config.skraper_imports.get(name, "(not configured)")
                rom_count = conn.execute(
                    "SELECT COUNT(*) c FROM roms WHERE system = ?", (name,)
                ).fetchone()["c"]
                table.add_row(
                    name, fullname, str(rom_count), " ".join(sys_cfg.extensions),
                    emulator_desc, skraper_path, key=name,
                )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        system_name = event.row_key.value
        if system_name:
            self.app.push_screen(SystemMenuScreen(self.app.config, self.app.config_path, system_name))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        config = self.app.config
        bid = event.button.id

        if bid == "sync_all":
            work = actions.sync_work(config, None, checksums=False, skip_skraper=False)
            self.app.push_screen(RunScreen("Sync All", work))
        elif bid == "scan_all":
            work = actions.scan_work(config, None, checksums=False)
            self.app.push_screen(RunScreen("Scan All", work))
        elif bid == "publish_all":
            work = actions.publish_work(config, None)
            self.app.push_screen(RunScreen("Publish All", work))
        elif bid == "generate_es_systems":
            work = actions.generate_es_systems_work(config)
            self._run_quick("Generate ES-Systems", work)
        elif bid == "add_system":
            self.app.push_screen(AddSystemScreen(config, self.app.config_path))
        elif bid == "configure_media":
            self.app.push_screen(ConfigureMediaScreen(config, self.app.config_path))
        elif bid == "settings":
            self.app.push_screen(SettingsScreen(config, self.app.config_path))

    def _run_quick(self, title: str, work) -> None:
        """Runs a RunScreen-style work callable off the UI thread and
        shows the result as a dismiss-only InfoModal instead of pushing a
        full RunScreen -- for actions with nothing meaningful to show a
        progress bar for (currently just Generate ES-Systems, which writes
        one XML file and returns near-instantly)."""
        def _do() -> None:
            try:
                result = work(lambda *_args: None)
            except Exception as exc:  # noqa: BLE001 -- same broad-catch rationale as RunScreen: never let a wrapped command's failure crash the app
                self.app.call_from_thread(self._show_quick_result, title, f"Something went wrong: {exc}")
                return
            self.app.call_from_thread(self._show_quick_result, title, result)

        self.run_worker(_do, thread=True)

    def _show_quick_result(self, title: str, body: str) -> None:
        self.app.push_screen(InfoModal(title, body))
