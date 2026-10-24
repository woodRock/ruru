"""Widgets and dialogs shared by the screens."""

from __future__ import annotations

from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    DataTable,
    Input,
    OptionList,
    Static,
)

POLL_SECONDS = 2.0
VIEW_SECONDS = 1.0  # resting on an inbox item this long marks it viewed (no longer bold)
DETAIL_SECONDS = 0.05  # the detail pane renders once the cursor settles, so holding j stays smooth


class Table(DataTable[Any]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("j", "cursor_down", "down", show=False),
        Binding("k", "cursor_up", "up", show=False),
        Binding("g", "scroll_top", "top", show=False),
        Binding("G", "scroll_bottom", "bottom", show=False),
    ]

    def current_key(self) -> str | None:
        if not self.row_count:
            return None
        return str(self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value)

    def refill(self, rows: list[tuple[str, list[str]]], bold: set[str] | None = None) -> None:
        """Replace the rows (those in `bold` in bold), keeping the cursor on the same key, or the same
        position if it is gone."""
        key, index = self.current_key(), self.cursor_row
        self.clear()
        for row_key, cells in rows:
            style = "bold" if bold and row_key in bold else ""
            self.add_row(*(Text(c, style=style) for c in cells), key=row_key)
        keys = [k for k, _ in rows]
        if keys:
            self.move_cursor(row=keys.index(key) if key in keys else min(index, len(keys) - 1))


class Prompt(ModalScreen[str | None]):
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "cancel")]

    def __init__(self, label: str, placeholder: str = "", value: str = "") -> None:
        super().__init__()
        self.label, self.placeholder, self.value = label, placeholder, value

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.label)
            yield Input(value=self.value, placeholder=self.placeholder)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class Choose(ModalScreen[str | None]):
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "cancel")]

    def __init__(self, label: str, choices: list[str]) -> None:
        super().__init__()
        self.label, self.choices = label, choices

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.label)
            yield OptionList(*self.choices)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.choices[event.option_index])

    def action_cancel(self) -> None:
        self.dismiss(None)
