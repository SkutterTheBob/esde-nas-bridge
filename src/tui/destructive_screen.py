"""The dry-run-preview -> confirm -> apply scaffold shared by the three
destructive commands (Clean Media, Prune Removed, Reset System), plus the
concrete screen for each. Confirmation friction scales with how destructive
the action is:

  - Clean Media: single Yes/No modal.
  - Prune Removed: Yes/No, plus a SECOND modal reproducing the CLI's
    config-drift warning if any flagged ROMs might still be on the NAS.
  - Reset System: a "type the system name to confirm" modal on top of
    Yes/No -- this one wipes ROMs still present on the NAS unconditionally,
    meaningfully more destructive than the other two's "only removes
    what's already gone/disabled" semantics.

None of these call media_cleanup.py's functions with apply_changes=True
until the user has cleared every required gate.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Input, Static

from .. import media_cleanup
from ..config import Config


class ConfirmModal(ModalScreen[bool]):
    """Plain Yes/No modal. Dismisses with True (confirmed) or False."""

    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
    }
    ConfirmModal > Vertical {
        width: 60%;
        border: round $warning;
        padding: 1 2;
        background: $surface;
    }
    """

    def __init__(self, message: str, confirm_label: str = "Yes, proceed") -> None:
        super().__init__()
        self._message = message
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._message)
            with Horizontal(classes="button-row"):
                yield Button(self._confirm_label, id="yes", variant="error")
                yield Button("Cancel", id="no", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class TypeToConfirmModal(ModalScreen[bool]):
    """Requires typing `required_text` exactly before Confirm enables.
    Dismisses with True (confirmed) or False."""

    DEFAULT_CSS = """
    TypeToConfirmModal {
        align: center middle;
    }
    TypeToConfirmModal > Vertical {
        width: 60%;
        border: round $error;
        padding: 1 2;
        background: $surface;
    }
    """

    def __init__(self, message: str, required_text: str) -> None:
        super().__init__()
        self._message = message
        self._required_text = required_text

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._message)
            yield Static(f"Type '{self._required_text}' below to confirm:", classes="hint")
            yield Input(id="confirm-input")
            with Horizontal(classes="button-row"):
                yield Button("Confirm", id="yes", variant="error", disabled=True)
                yield Button("Cancel", id="no", variant="primary")

    def on_input_changed(self, event: Input.Changed) -> None:
        self.query_one("#yes", Button).disabled = event.value != self._required_text

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class DestructiveScreen(Screen):
    """Base scaffold: load a dry-run preview via a worker thread, render
    it, gate an Apply button behind subclass-defined confirmation, then
    apply (also via a worker thread)."""

    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self, title: str) -> None:
        super().__init__()
        self._title = title
        self._preview = None
        self.preview_text = ""  # last text rendered into #preview; public for testability

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._title, classes="screen-title")
        with VerticalScroll(id="preview-container"):
            yield Static("Loading preview...", id="preview")
        with Horizontal(classes="button-row"):
            yield Button("Apply", id="apply", variant="error", disabled=True)
            yield Button("Back", id="back", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._do_load_preview, thread=True, exclusive=True)

    def _do_load_preview(self) -> None:
        preview = self._load_preview()
        self.app.call_from_thread(self._on_preview_loaded, preview)

    def _load_preview(self):
        raise NotImplementedError

    def _on_preview_loaded(self, preview) -> None:
        self._preview = preview
        text, has_work = self._render_preview(preview)
        self.preview_text = text
        self.query_one("#preview", Static).update(text)
        self.query_one("#apply", Button).disabled = not has_work
        self.query_one("#back", Button).disabled = False

    def _render_preview(self, preview) -> "tuple[str, bool]":
        raise NotImplementedError

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()
        elif event.button.id == "apply":
            self._confirm_and_apply()

    def _confirm_and_apply(self) -> None:
        raise NotImplementedError

    def _run_apply(self, apply_fn) -> None:
        """Shared by subclasses' confirm callbacks once every gate has
        passed: runs apply_fn() on a worker thread, then re-renders the
        result via _render_applied (subclass-defined) and disables Apply.
        Back is also disabled for the duration -- popping this screen
        mid-apply would leave the worker trying to update widgets that no
        longer exist once it calls back via call_from_thread."""
        self.query_one("#apply", Button).disabled = True
        self.query_one("#back", Button).disabled = True
        self.query_one("#preview", Static).update("Applying...")

        def _do() -> None:
            result = apply_fn()
            self.app.call_from_thread(self._on_applied, result)

        self.run_worker(_do, thread=True, exclusive=True)

    def _on_applied(self, result) -> None:
        self.preview_text = self._render_applied(result)
        self.query_one("#preview", Static).update(self.preview_text)
        self.query_one("#back", Button).disabled = False

    def _render_applied(self, result) -> str:
        raise NotImplementedError

    def action_go_back(self) -> None:
        if not self.query_one("#back", Button).disabled:
            self.app.pop_screen()


class CleanMediaScreen(DestructiveScreen):
    def __init__(self, config: Config, systems: "list[str]") -> None:
        super().__init__("Clean Media")
        self._config = config
        self._systems = systems

    def _load_preview(self) -> media_cleanup.CleanMediaResult:
        return media_cleanup.clean_media(self._config, self._systems, apply_changes=False)

    def _render_preview(self, preview: media_cleanup.CleanMediaResult) -> "tuple[str, bool]":
        if self._config.enabled_media_types is None:
            return (
                "enabled_media_types isn't restricted (all types currently allowed) -- "
                "nothing to clean.\nUse Configure Media first to narrow your selection.",
                False,
            )
        lines = []
        for name, kind_summary in preview.by_system.items():
            lines.append(f"=== {name} ===")
            for kind, (count, kind_bytes) in kind_summary.items():
                lines.append(f"  {kind}: {count} files, {media_cleanup.human_size(kind_bytes)}")
        if preview.total_files == 0:
            lines.append("Nothing to clean -- all cached media already matches your enabled_media_types selection.")
        else:
            lines.append(f"\nWould remove {preview.total_files} files ({media_cleanup.human_size(preview.total_bytes)}).")
        return "\n".join(lines), preview.total_files > 0

    def _confirm_and_apply(self) -> None:
        preview = self._preview
        message = (
            f"Remove {preview.total_files} file(s), freeing "
            f"{media_cleanup.human_size(preview.total_bytes)}?"
        )
        self.app.push_screen(ConfirmModal(message), self._on_confirmed)

    def _on_confirmed(self, confirmed: bool) -> None:
        if confirmed:
            self._run_apply(lambda: media_cleanup.clean_media(self._config, self._systems, apply_changes=True))

    def _render_applied(self, result: media_cleanup.CleanMediaResult) -> str:
        return f"Removed {result.total_files} files, freed {media_cleanup.human_size(result.total_bytes)}."


class PruneRemovedScreen(DestructiveScreen):
    def __init__(self, config: Config, systems: "list[str]", sweep_orphaned_media: bool) -> None:
        super().__init__("Prune Removed")
        self._config = config
        self._systems = systems
        self._sweep = sweep_orphaned_media

    def _load_preview(self) -> media_cleanup.PruneRemovedResult:
        return media_cleanup.prune_removed(
            self._config, self._systems, apply_changes=False, sweep_orphaned_media=self._sweep
        )

    def _render_preview(self, preview: media_cleanup.PruneRemovedResult) -> "tuple[str, bool]":
        lines = []
        entries = preview.entries
        for name in self._systems:
            system_entries = [e for e in entries if e.system == name]
            if not system_entries:
                continue
            lines.append(f"=== {name} ===")
            for e in system_entries:
                flag = "  [extension not in current config -- NOT confirmed missing from the NAS]" if e.is_drift else ""
                lines.append(f"  {e.rom['rel_path']}{flag}")

        if not entries:
            lines.append("Nothing stale -- every indexed ROM was found on the last scan.")
        else:
            drift_entries = [e for e in entries if e.is_drift]
            if drift_entries:
                lines.append(
                    f"\nWARNING: {len(drift_entries)} of {len(entries)} flagged ROM(s) have an "
                    "extension no longer in their system's configured extensions -- they may "
                    "still be on the NAS untouched."
                )
            lines.append(f"\nWould remove {len(entries)} stale ROM(s).")

        if self._sweep:
            if not preview.orphans:
                lines.append("\nNo orphaned media found.")
            else:
                lines.append(
                    f"\nOrphaned media: {len(preview.orphans)} file(s), "
                    f"{media_cleanup.human_size(preview.orphans_bytes)}"
                )
                for name, p in preview.orphans:
                    lines.append(f"  {name}: {p}")

        has_work = bool(entries) or (self._sweep and bool(preview.orphans))
        return "\n".join(lines), has_work

    def _confirm_and_apply(self) -> None:
        preview = self._preview
        parts = []
        if preview.entries:
            parts.append(f"{len(preview.entries)} stale ROM(s)")
        if self._sweep and preview.orphans:
            parts.append(f"{len(preview.orphans)} orphaned file(s)")
        message = f"Remove {' and '.join(parts)}?"
        self.app.push_screen(ConfirmModal(message), self._on_first_confirm)

    def _on_first_confirm(self, confirmed: bool) -> None:
        if not confirmed:
            return
        drift_entries = [e for e in self._preview.entries if e.is_drift]
        if drift_entries:
            message = (
                f"{len(drift_entries)} of {len(self._preview.entries)} flagged ROM(s) have an "
                "extension no longer in their system's configured extensions -- scan simply "
                "stopped looking for them, which looks identical to a real NAS removal from a "
                "timestamp alone. They may still be sitting right there on the NAS untouched. "
                "Proceed and remove them anyway?"
            )
            self.app.push_screen(ConfirmModal(message), self._on_drift_confirm)
        else:
            self._apply()

    def _on_drift_confirm(self, confirmed: bool) -> None:
        if confirmed:
            self._apply()

    def _apply(self) -> None:
        self._run_apply(lambda: media_cleanup.prune_removed(
            self._config, self._systems, apply_changes=True, sweep_orphaned_media=self._sweep
        ))

    def _render_applied(self, result: media_cleanup.PruneRemovedResult) -> str:
        lines = [f"Removed {result.removed_count} stale ROM(s) and their cached data."]
        if self._sweep:
            lines.append(
                f"Removed {result.orphans_removed_count} orphaned file(s), "
                f"freed {media_cleanup.human_size(result.orphans_bytes)}."
            )
        lines.append("Run Publish to update gamelist.xml so ES-DE stops showing these.")
        return "\n".join(lines)


class ResetSystemScreen(DestructiveScreen):
    def __init__(self, config: Config, system_name: str) -> None:
        super().__init__(f"Reset System: {system_name}")
        self._config = config
        self._system_name = system_name

    def _load_preview(self) -> media_cleanup.ResetSystemResult:
        return media_cleanup.reset_system(self._config, self._system_name, apply_changes=False)

    def _render_preview(self, preview: media_cleanup.ResetSystemResult) -> "tuple[str, bool]":
        has_work = bool(
            preview.rom_count or preview.stub_count or preview.media_count or preview.gamelist_present
        )
        if not has_work:
            return f"'{self._system_name}': nothing cached locally -- already clean.", False

        lines = [
            f"{preview.rom_count} indexed ROM(s) (DB row + cascaded metadata/media rows)",
            f"{preview.media_count} cached media file(s), {media_cleanup.human_size(preview.media_bytes)}",
            f"{preview.stub_count} stub file(s) under {preview.stub_dir}",
            f"gamelist.xml: {'present' if preview.gamelist_present else 'not present'} ({preview.gamelist_path})",
            "\nThis wipes ALL of the above unconditionally, including ROMs still present on "
            "the NAS -- unlike Prune Removed, this doesn't check what's still there.",
            "config.yaml is untouched -- this system stays configured for the next sync.",
        ]
        return "\n".join(lines), True

    def _confirm_and_apply(self) -> None:
        message = (
            f"This wipes ALL local data for '{self._system_name}' -- including ROMs still "
            "present on the NAS -- and cannot be undone (short of re-running sync)."
        )
        self.app.push_screen(TypeToConfirmModal(message, self._system_name), self._on_confirmed)

    def _on_confirmed(self, confirmed: bool) -> None:
        if confirmed:
            self._run_apply(lambda: media_cleanup.reset_system(self._config, self._system_name, apply_changes=True))

    def _render_applied(self, result: media_cleanup.ResetSystemResult) -> str:
        return (
            f"Wiped all local data for '{self._system_name}'.\n"
            f"Run Sync for this system to rebuild it."
        )
