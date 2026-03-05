from __future__ import annotations

import copy
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, time, timedelta
from pathlib import Path

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option
from rich.text import Text

from .db import Database
from .models import Area, SmartList, Task, TaskStatus
from .parsing import (
    format_deadline,
    normalize_repeat_rule,
    parse_attendees_csv,
    parse_deadline_input,
    parse_quick_capture,
    parse_tags_csv,
)
from .undo import TaskDeltaCommand, UndoManager

@dataclass(frozen=True)
class ThemeSpec:
    theme_id: str
    text: str
    text_muted: str
    accent: str
    border: str
    selected_bg: str
    selected_fg: str
    warning: str
    danger: str
    main_bg: str
    palette_colors: tuple[str, ...]


THEMES: dict[str, ThemeSpec] = {
    "catppuccin-mocha": ThemeSpec(
        "catppuccin-mocha",
        "#cdd6f4",
        "#a6adc8",
        "#cba6f7",
        "#74c7ec",
        "#313244",
        "#f9e2af",
        "#fab387",
        "#f38ba8",
        "#1e1e2e",
        ("#f38ba8", "#fab387", "#f9e2af", "#a6e3a1", "#89dceb", "#74c7ec", "#b4befe", "#cba6f7"),
    ),
    "catppuccin-macchiato": ThemeSpec(
        "catppuccin-macchiato",
        "#cad3f5",
        "#a5adcb",
        "#c6a0f6",
        "#8aadf4",
        "#363a4f",
        "#eed49f",
        "#f5a97f",
        "#ed8796",
        "#24273a",
        ("#ed8796", "#f5a97f", "#eed49f", "#a6da95", "#8bd5ca", "#91d7e3", "#8aadf4", "#c6a0f6"),
    ),
    "catppuccin-frappe": ThemeSpec(
        "catppuccin-frappe",
        "#c6d0f5",
        "#a5adce",
        "#ca9ee6",
        "#85c1dc",
        "#414559",
        "#e5c890",
        "#ef9f76",
        "#e78284",
        "#303446",
        ("#e78284", "#ef9f76", "#e5c890", "#a6d189", "#81c8be", "#85c1dc", "#8caaee", "#ca9ee6"),
    ),
    "catppuccin-latte": ThemeSpec(
        "catppuccin-latte",
        "#4c4f69",
        "#6c6f85",
        "#8839ef",
        "#209fb5",
        "#ccd0da",
        "#df8e1d",
        "#fe640b",
        "#d20f39",
        "#eff1f5",
        ("#d20f39", "#fe640b", "#df8e1d", "#40a02b", "#179299", "#04a5e5", "#1e66f5", "#8839ef"),
    ),
    "nord": ThemeSpec(
        "nord",
        "#ECEFF4",
        "#D8DEE9",
        "#81A1C1",
        "#88C0D0",
        "#434C5E",
        "#8FBCBB",
        "#EBCB8B",
        "#BF616A",
        "#2E3440",
        ("#BF616A", "#D08770", "#EBCB8B", "#A3BE8C", "#88C0D0", "#81A1C1", "#5E81AC", "#B48EAD"),
    ),
    "tokyo-night": ThemeSpec(
        "tokyo-night",
        "#c0caf5",
        "#9aa5ce",
        "#7aa2f7",
        "#bb9af7",
        "#292e42",
        "#e0af68",
        "#e0af68",
        "#f7768e",
        "#1a1b26",
        ("#f7768e", "#ff9e64", "#e0af68", "#9ece6a", "#73daca", "#7dcfff", "#7aa2f7", "#bb9af7"),
    ),
    "dracula": ThemeSpec(
        "dracula",
        "#F8F8F2",
        "#6272A4",
        "#BD93F9",
        "#8BE9FD",
        "#44475A",
        "#FFB86C",
        "#FFB86C",
        "#FF5555",
        "#282A36",
        ("#FF5555", "#FFB86C", "#F1FA8C", "#50FA7B", "#8BE9FD", "#6272A4", "#BD93F9", "#FF79C6"),
    ),
    "gruvbox": ThemeSpec(
        "gruvbox",
        "#EBDBB2",
        "#A89984",
        "#D3869B",
        "#83A598",
        "#3C3836",
        "#FABD2F",
        "#FABD2F",
        "#FB4934",
        "#282828",
        ("#FB4934", "#FE8019", "#FABD2F", "#B8BB26", "#8EC07C", "#83A598", "#D3869B", "#B16286"),
    ),
}
THEME_IDS = list(THEMES.keys())
THEME_CLASS_PREFIX = "theme-"
DEFAULT_THEME = "catppuccin-mocha"
THEME_CONFIG_PATH = Path("theme.toml")
ACTIVE_THEME: ThemeSpec = THEMES[DEFAULT_THEME]

ICON_CALENDAR = ""
ICON_INBOX = "󰇮"
ICON_TODAY = "󰃭"
ICON_UPCOMING = "󰃰"
ICON_ANYTIME = "󰋣"
ICON_SOMEDAY = "󰀦"
ICON_LOGBOOK = "󰄲"
ICON_TRASH = "󰩹"
ICON_FOLDER_CLOSED = "󰉋"
ICON_FOLDER_OPEN = "󰝰"
ICON_TASK_UNCHECKED = "󰄱"
ICON_TASK_CHECKED = "󰱒"
ICON_TASK_CANCELED = "󰅙"
ICON_IND_NOTES = "󰈚"
ICON_IND_DEADLINE = "󰥔"
ICON_IND_REPEAT = "󰑖"


def _set_active_theme(theme_id: str) -> None:
    global ACTIVE_THEME
    ACTIVE_THEME = THEMES.get(theme_id, THEMES[DEFAULT_THEME])


def _load_theme_selection(path: Path) -> str:
    if not path.exists():
        _save_theme_selection(path, DEFAULT_THEME)
        return DEFAULT_THEME
    raw = path.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'^\s*theme\s*=\s*"([^"]+)"\s*$', raw, flags=re.MULTILINE)
    if not m:
        return DEFAULT_THEME
    theme_id = m.group(1).strip()
    return theme_id if theme_id in THEMES else DEFAULT_THEME


def _save_theme_selection(path: Path, theme_id: str) -> None:
    path.write_text(f'theme = "{theme_id}"\n', encoding="utf-8")


def _apply_theme_class_to_screen(screen: object, theme_id: str) -> None:
    if not hasattr(screen, "set_class"):
        return
    for tid in THEME_IDS:
        screen.set_class(False, f"{THEME_CLASS_PREFIX}{tid}")
    screen.set_class(True, f"{THEME_CLASS_PREFIX}{theme_id}")


REPEAT_RULES = {
    "daily": "daily",
    "day": "daily",
    "every day": "daily",
    "weekly": "weekly",
    "week": "weekly",
    "every week": "weekly",
    "monthly": "monthly",
    "month": "monthly",
    "every month": "monthly",
    "yearly": "yearly",
    "year": "yearly",
    "annual": "yearly",
    "annually": "yearly",
    "every year": "yearly",
    "weekday": "weekdays",
    "weekdays": "weekdays",
    "every weekday": "weekdays",
    "every weekdays": "weekdays",
}


def _relative_day_label(day: date) -> str:
    today = date.today()
    if day == today:
        return "Today"
    if day == today + timedelta(days=1):
        return "Tomorrow"
    if day == today - timedelta(days=1):
        return "Yesterday"
    return day.isoformat()


def _recurrence_due_on(anchor: date, today: date, rule: str) -> bool:
    if today < anchor:
        return False
    days = (today - anchor).days
    if rule == "daily":
        return True
    if rule == "weekdays":
        return today.weekday() < 5
    if rule == "weekly":
        return days % 7 == 0
    if rule == "monthly":
        if anchor.day == today.day:
            return True
        # Month-end carry: Jan 31 repeats on Feb 28/29, Apr 30, etc.
        next_month = today.replace(day=28) + timedelta(days=4)
        last_day = (next_month - timedelta(days=next_month.day)).day
        return anchor.day > last_day and today.day == last_day
    if rule == "yearly":
        return anchor.month == today.month and anchor.day == today.day
    return False


def _last_day_of_month(year: int, month: int) -> int:
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day


def _advance_recurrence_once(anchor: date, rule: str) -> date:
    if rule == "daily":
        return anchor + timedelta(days=1)
    if rule == "weekdays":
        d = anchor + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d
    if rule == "weekly":
        return anchor + timedelta(days=7)
    if rule == "monthly":
        year = anchor.year + (1 if anchor.month == 12 else 0)
        month = 1 if anchor.month == 12 else anchor.month + 1
        return date(year, month, min(anchor.day, _last_day_of_month(year, month)))
    if rule == "yearly":
        year = anchor.year + 1
        return date(year, anchor.month, min(anchor.day, _last_day_of_month(year, anchor.month)))
    return anchor


def _normalize_repeat_rule(raw: str) -> str | None:
    return REPEAT_RULES.get(raw.strip().lower())


def _is_hashtag_token(token: str) -> bool:
    return re.fullmatch(r"#[A-Za-z0-9_-]+", token.strip()) is not None


def _token_spans(source: str, *, offset: int = 0) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for match in re.finditer(r"\S+", source):
        out.append((offset + match.start(), offset + match.end(), match.group(0)))
    return out


def _detect_recurrence_span_rule(buffer: str) -> tuple[int, int, str] | None:
    matches: list[tuple[int, int, str]] = []

    for m in re.finditer(
        r"\bevery\s+(day|week|month|year|weekday|weekdays)\b",
        buffer,
        flags=re.IGNORECASE,
    ):
        rule = _normalize_repeat_rule(m.group(0))
        if rule:
            matches.append((m.start(), m.end(), rule))

    for m in re.finditer(
        r"\b(daily|weekly|monthly|yearly|annual|annually|weekday|weekdays)\b",
        buffer,
        flags=re.IGNORECASE,
    ):
        # Prefer the full "every <rule>" phrase when present.
        if m.start() >= 6 and buffer[m.start() - 6 : m.start()].lower() == "every ":
            continue
        rule = _normalize_repeat_rule(m.group(0))
        if rule:
            matches.append((m.start(), m.end(), rule))

    if not matches:
        return None
    matches.sort(key=lambda x: x[0])
    return matches[-1]


def _strip_recurrence_suffix(buffer: str) -> tuple[str, str | None]:
    detected = _detect_recurrence_span_rule(buffer)
    if detected is None:
        return buffer, None
    start, end, rule = detected
    stripped = (buffer[:start] + " " + buffer[end:]).strip()
    stripped = re.sub(r"\s+", " ", stripped)
    return stripped, rule


def _find_due_span(buffer: str) -> tuple[int, int] | None:
    masked = buffer
    detected = _detect_recurrence_span_rule(buffer)
    if detected is not None:
        start, end, _ = detected
        masked = buffer[:start] + (" " * (end - start)) + buffer[end:]

    lower = masked.lower()

    marker_candidates = [lower.rfind(" due "), lower.rfind(" by ")]
    marker_idx = max(marker_candidates)
    if marker_idx >= 0:
        marker_len = 5 if lower[marker_idx : marker_idx + 5] == " due " else 4
        expr_start = marker_idx + marker_len
        tokens = _token_spans(masked[expr_start:], offset=expr_start)
        non_tag = [(s, e, t) for (s, e, t) in tokens if not _is_hashtag_token(t)]
        if non_tag:
            expr = " ".join(t for (_, _, t) in non_tag)
            if parse_deadline_input(expr) is not None:
                return (non_tag[0][0], non_tag[-1][1])

    tokens = _token_spans(masked)
    non_tag_idx = [i for i, (_, _, tok) in enumerate(tokens) if not _is_hashtag_token(tok)]
    if non_tag_idx:
        max_take = min(4, len(non_tag_idx))
        for take in range(max_take, 0, -1):
            picked = non_tag_idx[-take:]
            expr = " ".join(tokens[i][2] for i in picked)
            if parse_deadline_input(expr) is None:
                continue
            first_start = tokens[picked[0]][0]
            if not masked[:first_start].strip():
                continue
            last_end = tokens[picked[-1]][1]
            return (first_start, last_end)
    return None


def _highlight_quick_capture(buffer: str) -> Text:
    text = Text(buffer or "", style=ACTIVE_THEME.text)
    due = _find_due_span(buffer)
    if due:
        text.stylize(f"bold underline {ACTIVE_THEME.danger}", due[0], due[1])
    for match in re.finditer(r"#\w+", buffer):
        text.stylize(f"bold underline {ACTIVE_THEME.accent}", match.start(), match.end())
    for match in re.finditer(
        r"\bevery\s+(?:day|week|month|year|weekday|weekdays)\b|\b(?:daily|weekly|monthly|yearly|annual|annually|weekday|weekdays)\b",
        buffer,
        flags=re.IGNORECASE,
    ):
        text.stylize(f"bold underline {ACTIVE_THEME.warning}", match.start(), match.end())
    return text


def _detect_capture_tags(buffer: str) -> list[str]:
    tags: list[str] = []
    for _, _, token in _token_spans(buffer):
        if not _is_hashtag_token(token):
            continue
        tag = token[1:].lower()
        if tag not in [x.lower() for x in tags]:
            tags.append(tag)
    return tags


def _quick_capture_summary(buffer: str) -> Text:
    _, repeat_rule = _strip_recurrence_suffix(buffer)

    due_text = "-"
    due_span = _find_due_span(buffer)
    if due_span:
        parsed_due = parse_deadline_input(buffer[due_span[0] : due_span[1]].strip())
        if parsed_due and parsed_due[0]:
            due_text = _relative_day_label(parsed_due[0])
            if parsed_due[1]:
                due_text = f"{due_text} {parsed_due[1].strftime('%H:%M')}"

    tags = _detect_capture_tags(buffer)
    tags_text = ", ".join(tags) if tags else "-"
    summary = Text()
    summary.append(f"⏰ Due: {due_text}", style=ACTIVE_THEME.danger)
    summary.append("   ")
    summary.append(f"# Tags: {tags_text}", style=ACTIVE_THEME.accent)
    summary.append("   ")
    summary.append(f"↻ Repeat: {repeat_rule or '-'}", style=ACTIVE_THEME.warning)
    return summary


@dataclass(frozen=True)
class QuickFindTarget:
    kind: str
    payload: str | int
    nav_kind: str | None = None
    nav_payload: str | int | None = None
    force_smart: str | None = None


@dataclass(frozen=True)
class QuickFindCandidate:
    kind: str
    icon: str
    display: str
    searchable: str
    target: QuickFindTarget


@dataclass(frozen=True)
class QuickFindResult:
    kind: str
    icon: str
    display: str
    matched_indices: tuple[int, ...]
    score: int
    target: QuickFindTarget


def _compact(value: str, max_chars: int) -> str:
    clean = value.replace("\n", " ").strip()
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars] + "..."


def _default_kind_score(kind: str) -> int:
    if kind == "smart":
        return 10_000
    if kind == "task":
        return 9_000
    if kind == "project":
        return 8_000
    if kind == "area":
        return 7_000
    return 6_000


def _quick_find_smart_icon(list_name: str) -> str:
    if list_name == SmartList.CALENDAR.value:
        return ICON_CALENDAR
    if list_name == SmartList.INBOX.value:
        return ICON_INBOX
    if list_name == SmartList.TODAY.value:
        return ICON_TODAY
    if list_name == SmartList.UPCOMING.value:
        return ICON_UPCOMING
    if list_name == SmartList.ANYTIME.value:
        return ICON_ANYTIME
    if list_name == SmartList.SOMEDAY.value:
        return ICON_SOMEDAY
    if list_name == SmartList.LOGBOOK.value:
        return ICON_LOGBOOK
    return ICON_TRASH


def _smart_icon_color(list_name: str) -> str:
    if list_name == SmartList.CALENDAR.value:
        return "#80d8ff"
    if list_name == SmartList.INBOX.value:
        return "#82aaff"
    if list_name == SmartList.TODAY.value:
        return "#ffd54f"
    if list_name == SmartList.UPCOMING.value:
        return "#ff8a80"
    if list_name == SmartList.ANYTIME.value:
        return "#4dd0e1"
    if list_name == SmartList.SOMEDAY.value:
        return "#b8860b"
    if list_name == SmartList.LOGBOOK.value:
        return "#81c784"
    return "#808080"


def _parse_color_input(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip().lower()
    if not value:
        return None
    if value.startswith("#") and len(value) == 7 and re.fullmatch(r"#[0-9a-f]{6}", value):
        return value
    named: dict[str, str] = {
        "red": "#ff5555",
        "orange": "#ffa500",
        "yellow": "#ffff55",
        "green": "#55ff55",
        "blue": "#5555ff",
        "purple": "#ff55ff",
        "pink": "#ff69b4",
        "teal": "#00ffff",
    }
    return named.get(value)


def _rotl64(value: int, shift: int) -> int:
    return ((value << shift) & 0xFFFFFFFFFFFFFFFF) | (value >> (64 - shift))


def _siphash13_64(data: bytes, k0: int = 0, k1: int = 0) -> int:
    mask = 0xFFFFFFFFFFFFFFFF
    v0 = 0x736F6D6570736575 ^ k0
    v1 = 0x646F72616E646F6D ^ k1
    v2 = 0x6C7967656E657261 ^ k0
    v3 = 0x7465646279746573 ^ k1

    def sipround() -> None:
        nonlocal v0, v1, v2, v3
        v0 = (v0 + v1) & mask
        v1 = _rotl64(v1, 13)
        v1 ^= v0
        v0 = _rotl64(v0, 32)
        v2 = (v2 + v3) & mask
        v3 = _rotl64(v3, 16)
        v3 ^= v2
        v0 = (v0 + v3) & mask
        v3 = _rotl64(v3, 21)
        v3 ^= v0
        v2 = (v2 + v1) & mask
        v1 = _rotl64(v1, 17)
        v1 ^= v2
        v2 = _rotl64(v2, 32)

    full = len(data) // 8
    for i in range(full):
        m = int.from_bytes(data[i * 8 : (i + 1) * 8], "little")
        v3 ^= m
        sipround()
        v0 ^= m

    tail = data[full * 8 :]
    b = (len(data) & 0xFF) << 56
    for i, byte in enumerate(tail):
        b |= (byte & 0xFF) << (8 * i)
    v3 ^= b
    sipround()
    v0 ^= b
    v2 ^= 0xFF
    sipround()
    sipround()
    sipround()
    return (v0 ^ v1 ^ v2 ^ v3) & mask


def _generate_color_from_string(value: str, palette: tuple[str, ...]) -> str:
    if not palette:
        return "#5555ff"
    # Rust's `Hash for str` writes bytes + 0xFF sentinel; mirror that for parity.
    hashed = _siphash13_64(value.lower().encode("utf-8") + b"\xff")
    return palette[hashed % len(palette)]


def _display_color(name: str, stored: str | None) -> str:
    parsed = _parse_color_input(stored)
    if parsed:
        return parsed
    return _generate_color_from_string(name, ACTIVE_THEME.palette_colors)


def _fuzzy_match_indices(haystack: str, needle: str) -> tuple[int, tuple[int, ...]] | None:
    if not needle:
        return 0, ()

    h = haystack.lower()
    n = needle.lower()
    indices: list[int] = []
    start = 0
    for ch in n:
        found = h.find(ch, start)
        if found < 0:
            return None
        indices.append(found)
        start = found + 1

    span = indices[-1] - indices[0] + 1
    consecutive = sum(1 for i in range(1, len(indices)) if indices[i] == indices[i - 1] + 1)
    score = (len(indices) * 100) + (consecutive * 15) - span - indices[0]
    return score, tuple(indices)


def _quick_find_results(candidates: list[QuickFindCandidate], query: str) -> list[QuickFindResult]:
    q = query.strip().lower()
    out: list[QuickFindResult] = []
    for candidate in candidates:
        if not q:
            out.append(
                QuickFindResult(
                    kind=candidate.kind,
                    icon=candidate.icon,
                    display=candidate.display,
                    matched_indices=(),
                    score=_default_kind_score(candidate.kind),
                    target=candidate.target,
                )
            )
            continue

        matched = _fuzzy_match_indices(candidate.searchable, q)
        if matched is None:
            continue
        score, _ = matched
        display_matched = _fuzzy_match_indices(candidate.display, q)
        display_indices = display_matched[1] if display_matched is not None else ()
        out.append(
            QuickFindResult(
                kind=candidate.kind,
                icon=candidate.icon,
                display=candidate.display,
                matched_indices=display_indices,
                score=score,
                target=candidate.target,
            )
        )

    out.sort(key=lambda x: x.score, reverse=True)
    return out[:40]


class PromptScreen(ModalScreen[str | None]):
    def __init__(self, title: str, placeholder: str = "", value: str = "") -> None:
        super().__init__()
        self.title_text = title
        self.placeholder = placeholder
        self.value = value

    def compose(self) -> ComposeResult:
        with Container(id="prompt-modal"):
            yield Static(self.title_text, classes="prompt-title")
            yield Input(value=self.value, placeholder=self.placeholder, id="prompt-input")
            yield Static("Enter to confirm, Esc to cancel", classes="prompt-help")

    def on_mount(self) -> None:
        app = self.app
        if isinstance(app, JustDoItTextual):
            _apply_theme_class_to_screen(self, app.active_theme_id)
        self.query_one("#prompt-input", Input).focus()

    @on(Input.Submitted, "#prompt-input")
    def submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def key_escape(self, event: events.Key) -> None:
        event.stop()
        self.dismiss(None)


@dataclass(frozen=True)
class CaptureDestination:
    kind: str  # inbox | area | project
    payload: int | None = None


@dataclass(frozen=True)
class NewTaskResult:
    raw: str
    destination: CaptureDestination


class NewTaskScreen(ModalScreen[NewTaskResult | None]):
    def __init__(
        self,
        candidates: list[tuple[CaptureDestination, str]],
        selected_destination: CaptureDestination,
        value: str = "",
    ) -> None:
        super().__init__()
        self.value = value
        self.cursor = len(value)
        self.destination_candidates = candidates
        self.selected_destination = selected_destination
        self.destination_query: str | None = None
        self.destination_selected: int = 0

    def compose(self) -> ComposeResult:
        with Container(id="prompt-modal"):
            yield Static("New Task (Enter to save, Esc to cancel)", classes="prompt-title")
            yield Static("", id="capture-input-line")
            yield Static("", id="capture-summary")
            yield Static("Deadlines red, #tags accent, recurrence yellow", classes="prompt-help")
            yield Static("Type '/' for smart destination selector (Up/Down).", classes="prompt-help")
            yield Static("", id="capture-destination-line")
            yield OptionList(id="capture-destination-list")

    def on_mount(self) -> None:
        app = self.app
        if isinstance(app, JustDoItTextual):
            _apply_theme_class_to_screen(self, app.active_theme_id)
        self._refresh(self.value)

    @on(events.Key)
    def key_input(self, event: events.Key) -> None:
        key = event.key
        if self.destination_query is not None:
            if key == "enter":
                event.stop()
                self._confirm_destination_selection()
                self._refresh(self.value)
                return
            if key == "escape":
                event.stop()
                self.dismiss(None)
                return
            if key == "up":
                event.stop()
                self._move_destination_selection(-1)
                self._refresh(self.value)
                return
            if key == "down":
                event.stop()
                self._move_destination_selection(1)
                self._refresh(self.value)
                return
            if key == "backspace":
                event.stop()
                if self.destination_query:
                    self.destination_query = self.destination_query[:-1]
                    self.destination_selected = 0
                else:
                    self.destination_query = None
                self._refresh(self.value)
                return
            if event.character and event.character >= " ":
                event.stop()
                self.destination_query += event.character
                self.destination_selected = 0
                self._refresh(self.value)
                return
            return

        if key == "enter":
            event.stop()
            self.dismiss(NewTaskResult(raw=self.value, destination=self.selected_destination))
            return
        if key == "escape":
            event.stop()
            self.dismiss(None)
            return
        if event.character == "/":
            event.stop()
            self.destination_query = ""
            self.destination_selected = 0
            self._refresh(self.value)
            return
        if key == "left":
            event.stop()
            self.cursor = max(0, self.cursor - 1)
            self._refresh(self.value)
            return
        if key == "right":
            event.stop()
            self.cursor = min(len(self.value), self.cursor + 1)
            self._refresh(self.value)
            return
        if key == "home":
            event.stop()
            self.cursor = 0
            self._refresh(self.value)
            return
        if key == "end":
            event.stop()
            self.cursor = len(self.value)
            self._refresh(self.value)
            return
        if key == "backspace":
            event.stop()
            if self.cursor > 0:
                self.value = self.value[: self.cursor - 1] + self.value[self.cursor :]
                self.cursor -= 1
                self._refresh(self.value)
            return
        if key == "delete":
            event.stop()
            if self.cursor < len(self.value):
                self.value = self.value[: self.cursor] + self.value[self.cursor + 1 :]
                self._refresh(self.value)
            return

        if event.character and event.character >= " ":
            event.stop()
            self.value = self.value[: self.cursor] + event.character + self.value[self.cursor :]
            self.cursor += 1
            self._refresh(self.value)

    def _filtered_destination_candidates(self) -> list[tuple[CaptureDestination, str]]:
        query = (self.destination_query or "").strip().lower()
        if not query:
            return self.destination_candidates
        scored: list[tuple[tuple[CaptureDestination, str], int]] = []
        for candidate in self.destination_candidates:
            _, label = candidate
            matched = _fuzzy_match_indices(label.lower(), query)
            if matched is None:
                continue
            scored.append((candidate, matched[0]))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [item for item, _ in scored]

    def _move_destination_selection(self, delta: int) -> None:
        candidates = self._filtered_destination_candidates()
        if not candidates:
            self.destination_selected = 0
            return
        self.destination_selected = max(0, min(len(candidates) - 1, self.destination_selected + delta))

    def _confirm_destination_selection(self) -> None:
        candidates = self._filtered_destination_candidates()
        if candidates:
            idx = min(self.destination_selected, len(candidates) - 1)
            self.selected_destination = candidates[idx][0]
        self.destination_query = None
        self.destination_selected = 0

    def _destination_label(self, destination: CaptureDestination) -> str:
        for dest, label in self.destination_candidates:
            if dest == destination:
                return label
        return "Inbox"

    def _refresh(self, raw: str) -> None:
        text = _highlight_quick_capture(raw)
        if self.destination_query is None:
            if self.cursor < len(raw):
                text.stylize("reverse", self.cursor, self.cursor + 1)
            else:
                text.append(" ", style="reverse")
        self.query_one("#capture-input-line", Static).update(text)
        self.query_one("#capture-summary", Static).update(_quick_capture_summary(raw))

        destination_line = self.query_one("#capture-destination-line", Static)
        destination_list = self.query_one("#capture-destination-list", OptionList)
        destination_list.clear_options()

        if self.destination_query is None:
            destination_list.set_class(True, "hidden")
            destination_line.update(
                Text(
                    f"{ICON_FOLDER_CLOSED} Destination: {self._destination_label(self.selected_destination)} (type '/' to change)",
                    style=ACTIVE_THEME.accent,
                )
            )
            return

        destination_list.set_class(False, "hidden")
        destination_line.update(Text(f"{ICON_FOLDER_CLOSED} Filter: {self.destination_query}", style=ACTIVE_THEME.accent))
        filtered = self._filtered_destination_candidates()
        if not filtered:
            destination_list.add_option(Option("No matching destination"))
            return
        for destination, label in filtered:
            icon = ICON_INBOX if destination.kind == "inbox" else ICON_FOLDER_CLOSED if destination.kind == "project" else ICON_FOLDER_OPEN
            destination_list.add_option(Option(f"{icon} {label}"))
        destination_list.highlighted = min(self.destination_selected, len(filtered) - 1)


@dataclass
class EditTaskDraft:
    title: str
    location: str
    tags: str
    deadline: str
    recurrence: str
    status: str


class EditTaskScreen(ModalScreen[EditTaskDraft | None]):
    FIELD_ORDER = ("title", "location", "tags", "deadline", "recurrence", "status")
    FIELD_LABELS = {
        "title": "Title",
        "location": "Location",
        "tags": "Tags",
        "deadline": "Deadline",
        "recurrence": "Repeat",
        "status": "Status",
    }

    def __init__(self, task: Task, location: str, destination_candidates: list[tuple[CaptureDestination, str]]) -> None:
        super().__init__()
        self.draft = EditTaskDraft(
            title=task.title,
            location=location,
            tags=",".join(task.tags),
            deadline=format_deadline(task.deadline, task.deadline_time),
            recurrence=task.recurrence_rule or "",
            status=(
                "done"
                if task.status is TaskStatus.COMPLETED
                else "canceled" if task.status is TaskStatus.CANCELED else "todo"
            ),
        )
        self.active_idx = 0
        self.cursor = len(self._active_value())
        self.destination_candidates = destination_candidates
        self.destination_query: str | None = None
        self.destination_selected: int = 0

    def compose(self) -> ComposeResult:
        with Container(id="prompt-modal"):
            yield Static("Edit Task", classes="prompt-title")
            yield Static("", id="edit-fields")
            yield Static("Up/Down switch field, type to edit, Enter to save all", classes="prompt-help")
            yield Static(
                "Status: todo/done/canceled | Deadline: date or date+time",
                classes="prompt-help",
            )
            yield Static(
                "Repeat: daily/weekly/monthly/yearly/weekdays or every ...",
                classes="prompt-help",
            )
            yield Static(
                "Location: Inbox or Area / Project",
                classes="prompt-help",
            )
            yield Static("", id="edit-destination-line")
            yield OptionList(id="edit-destination-list")

    def on_mount(self) -> None:
        app = self.app
        if isinstance(app, JustDoItTextual):
            _apply_theme_class_to_screen(self, app.active_theme_id)
        self._refresh()

    @on(events.Key)
    def key_input(self, event: events.Key) -> None:
        key = event.key
        if self.destination_query is not None:
            if key == "enter":
                event.stop()
                self._confirm_destination_selection()
                self._refresh()
                return
            if key == "escape":
                event.stop()
                self.destination_query = None
                self.destination_selected = 0
                self._refresh()
                return
            if key == "up":
                event.stop()
                self._move_destination_selection(-1)
                self._refresh()
                return
            if key == "down":
                event.stop()
                self._move_destination_selection(1)
                self._refresh()
                return
            if key == "backspace":
                event.stop()
                if self.destination_query:
                    self.destination_query = self.destination_query[:-1]
                    self.destination_selected = 0
                else:
                    self.destination_query = None
                self._refresh()
                return
            if event.character and event.character >= " ":
                event.stop()
                self.destination_query += event.character
                self.destination_selected = 0
                self._refresh()
                return
            return

        if key == "enter":
            event.stop()
            self.dismiss(self.draft)
            return
        if key == "escape":
            event.stop()
            self.dismiss(None)
            return
        if key == "up":
            event.stop()
            self.active_idx = max(0, self.active_idx - 1)
            self.cursor = min(self.cursor, len(self._active_value()))
            self._refresh()
            return
        if key == "down":
            event.stop()
            self.active_idx = min(len(self.FIELD_ORDER) - 1, self.active_idx + 1)
            self.cursor = min(self.cursor, len(self._active_value()))
            self._refresh()
            return
        if key == "left":
            event.stop()
            self.cursor = max(0, self.cursor - 1)
            self._refresh()
            return
        if key == "right":
            event.stop()
            self.cursor = min(len(self._active_value()), self.cursor + 1)
            self._refresh()
            return
        if key == "home":
            event.stop()
            self.cursor = 0
            self._refresh()
            return
        if key == "end":
            event.stop()
            self.cursor = len(self._active_value())
            self._refresh()
            return
        if event.character == "/" and self._active_key() == "location":
            event.stop()
            self.destination_query = ""
            self.destination_selected = 0
            self._refresh()
            return
        if key == "backspace":
            event.stop()
            value = self._active_value()
            if self.cursor > 0:
                value = value[: self.cursor - 1] + value[self.cursor :]
                self.cursor -= 1
                self._set_active_value(value)
                self._refresh()
            return
        if key == "delete":
            event.stop()
            value = self._active_value()
            if self.cursor < len(value):
                value = value[: self.cursor] + value[self.cursor + 1 :]
                self._set_active_value(value)
                self._refresh()
            return
        if event.character and event.character >= " ":
            event.stop()
            value = self._active_value()
            value = value[: self.cursor] + event.character + value[self.cursor :]
            self.cursor += 1
            self._set_active_value(value)
            self._refresh()

    def _active_key(self) -> str:
        return self.FIELD_ORDER[self.active_idx]

    def _active_value(self) -> str:
        return getattr(self.draft, self._active_key())

    def _set_active_value(self, value: str) -> None:
        setattr(self.draft, self._active_key(), value)

    def _refresh(self) -> None:
        out = Text()
        for idx, key in enumerate(self.FIELD_ORDER):
            label = self.FIELD_LABELS[key]
            value = getattr(self.draft, key) or " "
            line = Text(f"{label:<9}: ", style=ACTIVE_THEME.text_muted)
            if idx == self.active_idx:
                line = Text(f"{label:<9}: ", style=f"bold {ACTIVE_THEME.selected_fg} on {ACTIVE_THEME.selected_bg}")
                value_text = Text(value, style=f"bold {ACTIVE_THEME.selected_fg} on {ACTIVE_THEME.selected_bg}")
                if self.cursor < len(value):
                    value_text.stylize("reverse", self.cursor, self.cursor + 1)
                else:
                    value_text.append(" ", style="reverse")
            else:
                value_text = Text(value, style=ACTIVE_THEME.text)
            line.append_text(value_text)
            out.append_text(line)
            if idx < len(self.FIELD_ORDER) - 1:
                out.append("\n")
        self.query_one("#edit-fields", Static).update(out)
        self._refresh_destination_widgets()

    def _filtered_destination_candidates(self) -> list[tuple[CaptureDestination, str]]:
        query = (self.destination_query or "").strip().lower()
        if not query:
            return self.destination_candidates
        scored: list[tuple[tuple[CaptureDestination, str], int]] = []
        for candidate in self.destination_candidates:
            _, label = candidate
            matched = _fuzzy_match_indices(label.lower(), query)
            if matched is None:
                continue
            scored.append((candidate, matched[0]))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [item for item, _ in scored]

    def _move_destination_selection(self, delta: int) -> None:
        candidates = self._filtered_destination_candidates()
        if not candidates:
            self.destination_selected = 0
            return
        self.destination_selected = max(0, min(len(candidates) - 1, self.destination_selected + delta))

    def _confirm_destination_selection(self) -> None:
        candidates = self._filtered_destination_candidates()
        if candidates:
            idx = min(self.destination_selected, len(candidates) - 1)
            self.draft.location = candidates[idx][1]
            self.cursor = min(self.cursor, len(self._active_value()))
        self.destination_query = None
        self.destination_selected = 0

    def _refresh_destination_widgets(self) -> None:
        line_widget = self.query_one("#edit-destination-line", Static)
        list_widget = self.query_one("#edit-destination-list", OptionList)
        list_widget.clear_options()

        if self.destination_query is None:
            list_widget.set_class(True, "hidden")
            line_widget.update(Text(f"{ICON_FOLDER_CLOSED} Location: {self.draft.location} (type '/' to search)", style=ACTIVE_THEME.accent))
            return

        list_widget.set_class(False, "hidden")
        line_widget.update(Text(f"{ICON_FOLDER_CLOSED} Filter: {self.destination_query}", style=ACTIVE_THEME.accent))
        filtered = self._filtered_destination_candidates()
        if not filtered:
            list_widget.add_option(Option("No matching destination"))
            return
        for destination, label in filtered:
            icon = ICON_INBOX if destination.kind == "inbox" else ICON_FOLDER_CLOSED if destination.kind == "project" else ICON_FOLDER_OPEN
            list_widget.add_option(Option(f"{icon} {label}"))
        list_widget.highlighted = min(self.destination_selected, len(filtered) - 1)


class ConfirmDeleteScreen(ModalScreen[bool]):
    def __init__(self, title: str, detail: str) -> None:
        super().__init__()
        self.title_text = title
        self.detail_text = detail

    def compose(self) -> ComposeResult:
        with Container(id="prompt-modal"):
            yield Static(self.title_text, classes="prompt-title")
            yield Static(self.detail_text)
            yield Static("Enter to confirm, Esc to cancel", classes="prompt-help")

    def on_mount(self) -> None:
        app = self.app
        if isinstance(app, JustDoItTextual):
            _apply_theme_class_to_screen(self, app.active_theme_id)

    def key_enter(self, event: events.Key) -> None:
        event.stop()
        self.dismiss(True)

    def key_escape(self, event: events.Key) -> None:
        event.stop()
        self.dismiss(False)


class RecurringEditScopeScreen(ModalScreen[str | None]):
    def __init__(self, title: str = "Recurring Task Edit Scope") -> None:
        super().__init__()
        self.title = title

    def compose(self) -> ComposeResult:
        with Container(id="prompt-modal"):
            yield Static(self.title, classes="prompt-title")
            yield OptionList(
                Option("This occurrence only"),
                Option("All future occurrences"),
                id="recurring-scope-list",
            )
            yield Static("Up/Down to select, Enter to confirm, Esc to cancel", classes="prompt-help")

    def on_mount(self) -> None:
        app = self.app
        if isinstance(app, JustDoItTextual):
            _apply_theme_class_to_screen(self, app.active_theme_id)
        lst = self.query_one("#recurring-scope-list", OptionList)
        lst.highlighted = 0
        lst.focus()

    @on(OptionList.OptionSelected, "#recurring-scope-list")
    def select_option(self) -> None:
        idx = self.query_one("#recurring-scope-list", OptionList).highlighted or 0
        self.dismiss("this" if idx == 0 else "future")

    def key_enter(self, event: events.Key) -> None:
        event.stop()
        idx = self.query_one("#recurring-scope-list", OptionList).highlighted or 0
        self.dismiss("this" if idx == 0 else "future")

    def key_escape(self, event: events.Key) -> None:
        event.stop()
        self.dismiss(None)


class QuickFindScreen(ModalScreen[QuickFindTarget | None]):
    def __init__(self, candidates: list[QuickFindCandidate]) -> None:
        super().__init__()
        self.query_text = ""
        self.cursor = 0
        self.selected = 0
        self.candidates = candidates
        self.results: list[QuickFindResult] = []

    def compose(self) -> ComposeResult:
        with Container(id="prompt-modal"):
            yield Static("Search", classes="prompt-title")
            yield Static("", id="quick-find-input")
            yield OptionList(id="quick-find-results")
            yield Static("Type to search, Up/Down to select, Enter to jump, Esc to cancel", classes="prompt-help")

    def on_mount(self) -> None:
        app = self.app
        if isinstance(app, JustDoItTextual):
            _apply_theme_class_to_screen(self, app.active_theme_id)
        self._refresh()

    @on(events.Key)
    def key_input(self, event: events.Key) -> None:
        key = event.key
        if key == "enter":
            event.stop()
            target = self.results[self.selected].target if self.results else None
            self.dismiss(target)
            return
        if key == "escape":
            event.stop()
            self.dismiss(None)
            return
        if key == "up":
            event.stop()
            if self.results:
                self.selected = max(0, self.selected - 1)
                self._sync_highlight()
            return
        if key == "down":
            event.stop()
            if self.results:
                self.selected = min(len(self.results) - 1, self.selected + 1)
                self._sync_highlight()
            return
        if key == "left":
            event.stop()
            self.cursor = max(0, self.cursor - 1)
            self._refresh()
            return
        if key == "right":
            event.stop()
            self.cursor = min(len(self.query_text), self.cursor + 1)
            self._refresh()
            return
        if key == "home":
            event.stop()
            self.cursor = 0
            self._refresh()
            return
        if key == "end":
            event.stop()
            self.cursor = len(self.query_text)
            self._refresh()
            return
        if key == "backspace":
            event.stop()
            if self.cursor > 0:
                self.query_text = self.query_text[: self.cursor - 1] + self.query_text[self.cursor :]
                self.cursor -= 1
                self._refresh()
            return
        if key == "delete":
            event.stop()
            if self.cursor < len(self.query_text):
                self.query_text = self.query_text[: self.cursor] + self.query_text[self.cursor + 1 :]
                self._refresh()
            return
        if event.character and event.character >= " ":
            event.stop()
            self.query_text = self.query_text[: self.cursor] + event.character + self.query_text[self.cursor :]
            self.cursor += 1
            self._refresh()

    @on(OptionList.OptionHighlighted, "#quick-find-results")
    def result_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if not self.results:
            return
        self.selected = max(0, min(event.option_index, len(self.results) - 1))

    def _sync_highlight(self) -> None:
        widget = self.query_one("#quick-find-results", OptionList)
        if self.results:
            widget.highlighted = self.selected

    def _refresh(self) -> None:
        input_text = Text(self.query_text, style=ACTIVE_THEME.text)
        if self.cursor < len(self.query_text):
            input_text.stylize("reverse", self.cursor, self.cursor + 1)
        else:
            input_text.append(" ", style="reverse")
        self.query_one("#quick-find-input", Static).update(input_text)

        self.results = _quick_find_results(self.candidates, self.query_text)
        if self.results:
            self.selected = min(self.selected, len(self.results) - 1)
        else:
            self.selected = 0

        result_list = self.query_one("#quick-find-results", OptionList)
        result_list.clear_options()
        for item in self.results:
            line = Text(f"{item.icon} {item.display}", style=ACTIVE_THEME.text)
            prefix = len(item.icon) + 1
            for idx in item.matched_indices:
                if idx < len(item.display):
                    line.stylize(f"bold {ACTIVE_THEME.accent}", prefix + idx, prefix + idx + 1)
            result_list.add_option(Option(line))
        self._sync_highlight()


class ShortcutHelpScreen(ModalScreen[None]):
    def compose(self) -> ComposeResult:
        with Container(id="prompt-modal"):
            yield Static("Shortcuts", classes="prompt-title")
            yield Static(
                "q/Esc quit | Tab/Shift+Tab/Left/Right focus | arrows move\n"
                "Enter open/collapse | i/n new task | a new area | p new project\n"
                "r rename | e edit task | d/s deadline | Space toggle | x/Backspace delete\n"
                "u restore | / search | f filter by tags | ? help | Ctrl+t theme selector"
            )
            yield Static("Enter or Esc to close", classes="prompt-help")

    def on_mount(self) -> None:
        app = self.app
        if isinstance(app, JustDoItTextual):
            _apply_theme_class_to_screen(self, app.active_theme_id)

    def key_enter(self, event: events.Key) -> None:
        event.stop()
        self.dismiss(None)

    def key_escape(self, event: events.Key) -> None:
        event.stop()
        self.dismiss(None)


def _month_first(day: date) -> date:
    return date(day.year, day.month, 1)


def _month_weeks(month_start: date) -> list[list[date | None]]:
    days_in_month = _last_day_of_month(month_start.year, month_start.month)
    first_weekday = month_start.weekday()  # Monday=0
    weeks: list[list[date | None]] = []
    current_day = 1
    for week_idx in range(6):
        row: list[date | None] = []
        for weekday in range(7):
            if week_idx == 0 and weekday < first_weekday:
                row.append(None)
            elif current_day > days_in_month:
                row.append(None)
            else:
                row.append(date(month_start.year, month_start.month, current_day))
                current_day += 1
        weeks.append(row)
        if current_day > days_in_month:
            break
    return weeks


def _calendar_deadline_counts(tasks: list[Task]) -> dict[date, tuple[int, int]]:
    counts: dict[date, tuple[int, int]] = {}
    for task in tasks:
        if task.deadline is None or task.trashed or task.status is TaskStatus.CANCELED or task.is_repeating_master:
            continue
        todo_count, done_count = counts.get(task.deadline, (0, 0))
        if task.status is TaskStatus.COMPLETED:
            done_count += 1
        else:
            todo_count += 1
        counts[task.deadline] = (todo_count, done_count)
    return counts


def _calendar_tasks_for_day(tasks: list[Task], on_day: date) -> list[Task]:
    rows = [
        task
        for task in tasks
        if task.deadline == on_day
        and not task.trashed
        and task.status is not TaskStatus.CANCELED
        and not task.is_repeating_master
    ]
    return sorted(
        rows,
        key=lambda t: (
            t.status is TaskStatus.COMPLETED,
            t.deadline_time is None,
            t.deadline_time or time.min,
            t.order_index,
            t.id,
        ),
    )


class CalendarScreen(Screen[None]):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
        Binding("left", "day_left", "Prev Day"),
        Binding("right", "day_right", "Next Day"),
        Binding("up", "week_up", "Prev Week"),
        Binding("down", "week_down", "Next Week"),
        Binding("n", "next_month", "Next Month"),
        Binding("p", "prev_month", "Prev Month"),
        Binding("t", "today", "Today"),
    ]

    def __init__(self, tasks: list[Task], initial_day: date | None = None) -> None:
        super().__init__()
        self.tasks = tasks
        self.selected_day = initial_day or date.today()
        self.current_month = _month_first(self.selected_day)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="calendar-root"):
            with Container(id="calendar-month-pane", classes="calendar-pane"):
                yield Static("Calendar", classes="pane-title")
                yield Static("", id="calendar-month-title")
                yield Static("", id="calendar-grid")
                yield Static("Arrows: day/week, n/p: month, t: today, Esc: back", classes="prompt-help")
            with Container(id="calendar-details-pane", classes="calendar-pane"):
                yield Static("Day Details", classes="pane-title")
                yield Static("", id="calendar-selected-day")
                yield OptionList(id="calendar-day-tasks")
                yield Static("", id="calendar-day-summary", classes="prompt-help")
        yield Footer()

    def on_mount(self) -> None:
        app = self.app
        if isinstance(app, JustDoItTextual):
            _apply_theme_class_to_screen(self, app.active_theme_id)
        self._refresh()

    @on(events.ScreenResume)
    def _screen_resumed(self) -> None:
        app = self.app
        if isinstance(app, JustDoItTextual):
            _apply_theme_class_to_screen(self, app.active_theme_id)
        self._refresh()

    def action_close(self) -> None:
        self.app.pop_screen()

    def action_day_left(self) -> None:
        self._set_selected_day(self.selected_day - timedelta(days=1))

    def action_day_right(self) -> None:
        self._set_selected_day(self.selected_day + timedelta(days=1))

    def action_week_up(self) -> None:
        self._set_selected_day(self.selected_day - timedelta(days=7))

    def action_week_down(self) -> None:
        self._set_selected_day(self.selected_day + timedelta(days=7))

    def action_prev_month(self) -> None:
        month = self.current_month.month - 1
        year = self.current_month.year
        if month == 0:
            month = 12
            year -= 1
        day = min(self.selected_day.day, _last_day_of_month(year, month))
        self._set_selected_day(date(year, month, day))

    def action_next_month(self) -> None:
        month = self.current_month.month + 1
        year = self.current_month.year
        if month == 13:
            month = 1
            year += 1
        day = min(self.selected_day.day, _last_day_of_month(year, month))
        self._set_selected_day(date(year, month, day))

    def action_today(self) -> None:
        self._set_selected_day(date.today())

    def _set_selected_day(self, day: date) -> None:
        self.selected_day = day
        self.current_month = _month_first(day)
        self._refresh()

    def _refresh(self) -> None:
        self.query_one("#calendar-month-title", Static).update(
            Text(self.current_month.strftime("%B %Y"), style=f"bold {ACTIVE_THEME.accent}")
        )
        self.query_one("#calendar-grid", Static).update(self._render_month_grid())
        self.query_one("#calendar-selected-day", Static).update(self._render_selected_day_header())
        self._render_day_tasks()

    def _render_month_grid(self) -> Text:
        counts = _calendar_deadline_counts(self.tasks)
        today = date.today()
        text = Text()
        text.append(" Mo    Tu    We    Th    Fr    Sa    Su  ", style=f"bold {ACTIVE_THEME.text_muted}")
        text.append("\n")
        for week in _month_weeks(self.current_month):
            for day in week:
                if day is None:
                    text.append("      ")
                    continue
                todo_count, done_count = counts.get(day, (0, 0))
                total_count = todo_count + done_count
                marker = "•" if todo_count > 0 else "·" if done_count > 0 else " "
                count_str = str(total_count) if total_count < 10 else "+"
                cell = f"{day.day:>2}{marker}{count_str}".ljust(6)
                style = ACTIVE_THEME.text
                if total_count > 0:
                    style = ACTIVE_THEME.accent if todo_count > 0 else ACTIVE_THEME.text_muted
                if day == today:
                    style = f"underline {style}"
                if day == self.selected_day:
                    style = f"bold {ACTIVE_THEME.selected_fg} on {ACTIVE_THEME.selected_bg}"
                text.append(cell, style=style)
            text.append("\n")
        text.append(
            "Legend: • open deadlines, · completed-only, number = tasks",
            style=ACTIVE_THEME.text_muted,
        )
        return text

    def _render_selected_day_header(self) -> Text:
        label = _relative_day_label(self.selected_day)
        heading = Text(f"{ICON_CALENDAR} {self.selected_day.isoformat()} ({label})", style=f"bold {ACTIVE_THEME.text}")
        return heading

    def _render_day_tasks(self) -> None:
        rows = _calendar_tasks_for_day(self.tasks, self.selected_day)
        day_list = self.query_one("#calendar-day-tasks", OptionList)
        day_list.clear_options()
        if not rows:
            day_list.add_option(Option(Text("No tasks due", style=ACTIVE_THEME.text_muted)))
            day_list.highlighted = 0
            self.query_one("#calendar-day-summary", Static).update(
                Text("0 open, 0 completed", style=ACTIVE_THEME.text_muted)
            )
            return

        open_count = 0
        done_count = 0
        for task in rows:
            if task.status is TaskStatus.COMPLETED:
                done_count += 1
                icon = ICON_TASK_CHECKED
                title_style = f"{ACTIVE_THEME.text_muted} strike"
            else:
                open_count += 1
                icon = ICON_TASK_UNCHECKED
                title_style = ACTIVE_THEME.text
            line = Text()
            line.append(f"{icon} ", style=ACTIVE_THEME.accent)
            line.append(task.title, style=title_style)
            if task.deadline_time:
                line.append(f"  {task.deadline_time.strftime('%H:%M')}", style=ACTIVE_THEME.text_muted)
            if task.template_id is not None:
                line.append(f"  {ICON_IND_REPEAT}", style=ACTIVE_THEME.warning)
            day_list.add_option(Option(line))
        day_list.highlighted = 0
        self.query_one("#calendar-day-summary", Static).update(
            Text(f"{open_count} open, {done_count} completed", style=ACTIVE_THEME.text_muted)
        )


class JustDoItTextual(App[None]):
    CSS_PATH = "styles.tcss"
    TITLE = "JustDoIt Textual"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("tab", "focus_next", "Focus"),
        Binding("shift+tab", "focus_prev", "Focus"),
        Binding("right", "focus_next", "Focus"),
        Binding("left", "focus_prev", "Focus"),
        Binding("down", "cursor_down", "Down"),
        Binding("up", "cursor_up", "Up"),
        Binding("enter", "open_details", "Open"),
        Binding("i", "new_task", "New Task"),
        Binding("n", "new_task", "New Task"),
        Binding("e", "edit_task", "Edit Task"),
        Binding("d", "edit_deadline", "Deadline"),
        Binding("s", "edit_deadline", "Deadline"),
        Binding("space", "toggle_task", "Toggle"),
        Binding("x", "delete_selected", "Delete"),
        Binding("backspace", "delete_selected", "Delete"),
        Binding("u", "restore_task", "Restore"),
        Binding("a", "new_area", "New Area"),
        Binding("p", "new_project", "New Project"),
        Binding("r", "rename_entity", "Rename"),
        Binding("/", "search_prompt", "Search"),
        Binding("f", "filter_prompt", "Filter"),
        Binding("question_mark", "shortcut_help", "Help"),
        Binding("ctrl+t", "theme_selector_prompt", "Theme"),
        Binding("ctrl+z", "undo", "Undo"),
        Binding("ctrl+y", "redo", "Redo"),
        Binding("[", "calendar_prev_month", "Prev Month"),
        Binding("]", "calendar_next_month", "Next Month"),
        Binding("t", "calendar_today", "Today"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.db = Database.open()
        self.active_theme_id = _load_theme_selection(THEME_CONFIG_PATH)
        _set_active_theme(self.active_theme_id)
        self.inbox: list[Task] = []
        self.areas: list[Area] = []
        self.sidebar_targets: list[tuple[str, object]] = []
        self.visible_tasks: list[Task] = []
        self.collapsed_area_ids: set[int] = set()
        self.active_tag_filters: set[str] = set()
        self.undo_manager = UndoManager()
        self.calendar_selected_day = date.today()
        self._last_app_size: tuple[int, int] = (0, 0)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="root"):
            with Container(id="sidebar-pane"):
                yield Static("Lists", classes="pane-title")
                yield OptionList(id="sidebar")
            with Container(id="main-pane"):
                yield Static("Tasks", classes="pane-title")
                yield OptionList(id="tasks")
                yield Static("", id="calendar-inline", classes="hidden")
                yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self._apply_theme_class()
        self.reload_state()
        self._last_app_size = (self.size.width, self.size.height)
        self.set_interval(0.2, self._poll_resize_reflow)
        self.query_one("#sidebar", OptionList).focus()

    def on_unmount(self) -> None:
        self.db.close()

    @on(events.ScreenResume)
    def _screen_resumed(self) -> None:
        self._apply_theme_class()

    def on_resize(self, event: events.Resize) -> None:
        # Re-render after resize so inline calendar/task layouts reflow when terminal
        # geometry changes (e.g. splitting panes).
        self.set_timer(0.02, self._render_tasks)
        self._last_app_size = (self.size.width, self.size.height)

    def _poll_resize_reflow(self) -> None:
        current = (self.size.width, self.size.height)
        if current == self._last_app_size:
            return
        self._last_app_size = current
        self._render_tasks()

    def _apply_theme_class(self) -> None:
        _apply_theme_class_to_screen(self.screen, self.active_theme_id)

    def _set_theme(self, theme_id: str, *, persist: bool = True) -> None:
        if theme_id not in THEMES:
            return
        self.active_theme_id = theme_id
        _set_active_theme(theme_id)
        self._apply_theme_class()
        if persist:
            _save_theme_selection(THEME_CONFIG_PATH, theme_id)

    def reload_state(self) -> None:
        self.db.migrate_legacy_repeating_masters()
        self.db.materialize_recurrence_instances(date.today())
        self.inbox, self.areas = self.db.load_state()
        if self._sync_repeating_instances(date.today()):
            self.inbox, self.areas = self.db.load_state()
        self._render_sidebar()
        self._render_tasks()

    def _snapshot_task_state(self) -> dict[int, Task]:
        state: dict[int, Task] = {}
        for task in self._all_tasks_flat():
            state[task.id] = copy.deepcopy(task)
        return state

    def _snapshot_template_state(self) -> dict[int, tuple]:
        return self.db.load_recurrence_templates_state()

    def _finalize_undoable_change(
        self,
        description: str,
        before: dict[int, Task],
        before_templates: dict[int, tuple],
    ) -> None:
        self.inbox, self.areas = self.db.load_state()
        if self._sync_repeating_instances(date.today()):
            self.inbox, self.areas = self.db.load_state()
        after = self._snapshot_task_state()
        after_templates = self._snapshot_template_state()
        cmd = TaskDeltaCommand.from_states(
            self.db,
            description,
            before,
            after,
            before_templates=before_templates,
            after_templates=after_templates,
        )
        if cmd.has_changes():
            self.undo_manager.record(cmd)
        self._render_sidebar()
        self._render_tasks()

    def action_undo(self) -> None:
        cmd = self.undo_manager.undo()
        if cmd is None:
            self._status("Nothing to undo")
            return
        self.reload_state()
        self._status(f"Undid: {cmd.description}")

    def action_redo(self) -> None:
        cmd = self.undo_manager.redo()
        if cmd is None:
            self._status("Nothing to redo")
            return
        self.reload_state()
        self._status(f"Redid: {cmd.description}")

    def _sidebar(self) -> OptionList:
        return self.query_one("#sidebar", OptionList)

    def _tasks(self) -> OptionList:
        return self.query_one("#tasks", OptionList)

    def _calendar_inline(self) -> Static:
        return self.query_one("#calendar-inline", Static)

    def _status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    def _calendar_active(self) -> bool:
        return self._current_sidebar_target() == ("smart", SmartList.CALENDAR.value)

    def _render_sidebar(self) -> None:
        sidebar = self._sidebar()
        old = sidebar.highlighted or 0
        sidebar.clear_options()
        self.sidebar_targets = []

        for smart in SmartList:
            self.sidebar_targets.append(("smart", smart.value))
            icon = _quick_find_smart_icon(smart.value)
            line = Text()
            line.append(f"{icon} ", style=_smart_icon_color(smart.value))
            line.append(smart.value, style=ACTIVE_THEME.text)
            sidebar.add_option(Option(line))

        for area in self.areas:
            self.sidebar_targets.append(("area", area.id))
            collapsed = area.id in self.collapsed_area_ids
            color = _display_color(area.title, area.color)
            area_icon = ICON_FOLDER_CLOSED if collapsed else ICON_FOLDER_OPEN
            area_line = Text()
            area_line.append("  ")
            area_line.append(f"{area_icon} ", style=color)
            area_line.append(area.title, style=f"bold {color}")
            sidebar.add_option(Option(area_line))
            if not collapsed:
                for project in area.projects:
                    self.sidebar_targets.append(("project", project.id))
                    project_color = _display_color(project.title, project.color)
                    project_line = Text()
                    project_line.append("    ")
                    project_line.append(f"{ICON_FOLDER_CLOSED} ", style=project_color)
                    project_line.append(project.title, style=project_color)
                    sidebar.add_option(Option(project_line))

        if self.sidebar_targets:
            sidebar.highlighted = min(old, len(self.sidebar_targets) - 1)

    def _current_sidebar_target(self) -> tuple[str, object] | None:
        idx = self._sidebar().highlighted
        if idx is None:
            return None
        if idx < 0 or idx >= len(self.sidebar_targets):
            return None
        return self.sidebar_targets[idx]

    def _render_tasks(self) -> None:
        tasks = self._tasks()
        old = tasks.highlighted or 0
        tasks.clear_options()

        target = self._current_sidebar_target()
        if target == ("smart", SmartList.CALENDAR.value):
            self.visible_tasks = []
            tasks.set_class(True, "hidden")
            cal = self._calendar_inline()
            cal.set_class(False, "hidden")
            self._render_calendar_inline(cal)
            # Re-render after layout settles so mode selection uses final pane size.
            self.set_timer(0, lambda: self._render_calendar_inline(cal))
            return
        tasks.set_class(False, "hidden")
        self._calendar_inline().set_class(True, "hidden")
        in_upcoming_smart = target == ("smart", SmartList.UPCOMING.value)
        if target is None:
            self.visible_tasks = []
        else:
            self.visible_tasks = self.db.list_tasks_for_sidebar(
                self.inbox,
                self.areas,
                target,
                today=date.today(),
            )
        if self.active_tag_filters:
            self.visible_tasks = [
                t for t in self.visible_tasks if any(tag.lower() in self.active_tag_filters for tag in [x.lower() for x in t.tags])
            ]

        for task in self.visible_tasks:
            if task.trashed or task.status is TaskStatus.CANCELED:
                icon = ICON_TASK_CANCELED
            elif task.status is TaskStatus.COMPLETED:
                icon = ICON_TASK_CHECKED
            else:
                icon = ICON_TASK_UNCHECKED

            line = Text()
            line.append(f"{icon} ", style=ACTIVE_THEME.accent)

            title_style = ACTIVE_THEME.text
            if task.status is TaskStatus.COMPLETED:
                title_style = f"{ACTIVE_THEME.text_muted} strike"
            elif task.status is TaskStatus.CANCELED:
                title_style = f"{ACTIVE_THEME.danger} strike"
            elif task.trashed:
                title_style = f"{ACTIVE_THEME.warning} strike"
            line.append(task.title, style=title_style)

            right_meta: list[str] = []
            if task.notes_markdown.strip():
                right_meta.append(ICON_IND_NOTES)
            if task.is_repeating_master or task.template_id is not None:
                right_meta.append(ICON_IND_REPEAT)
            if right_meta:
                line.append("  ")
                line.append(" ".join(right_meta), style=ACTIVE_THEME.text_muted)

            if task.tags:
                line.append("  ")
                for idx, tag in enumerate(task.tags):
                    if idx > 0:
                        line.append(" ")
                    line.append(f"[ {tag} ]", style=f"bold {ACTIVE_THEME.accent}")

            display_deadline = task.deadline
            if in_upcoming_smart and task.is_repeating_master:
                next_occurrence = self.db._next_occurrence_for_master(task, date.today())
                if next_occurrence is not None:
                    display_deadline = next_occurrence

            if display_deadline:
                line.append("  ")
                label = _relative_day_label(display_deadline)
                if task.deadline_time:
                    label = f"{label} {task.deadline_time.strftime('%H:%M')}"
                overdue = display_deadline < date.today()
                deadline_style = f"bold {ACTIVE_THEME.danger}" if overdue else ACTIVE_THEME.text_muted
                line.append(f"{ICON_IND_DEADLINE} {label}", style=deadline_style)

            tasks.add_option(Option(line))

        if self.visible_tasks:
            tasks.highlighted = min(old, len(self.visible_tasks) - 1)
        extras = []
        if self.active_tag_filters:
            extras.append("tags=" + ",".join(sorted(self.active_tag_filters)))
        suffix = f"  ({' | '.join(extras)})" if extras else ""
        self._status(f"{len(self.visible_tasks)} task(s){suffix}")

    def _render_calendar_inline(self, widget: Static) -> None:
        main_pane = self.query_one("#main-pane", Container)
        render_width = max(widget.size.width, main_pane.size.width - 4)
        render_height = max(widget.size.height, main_pane.size.height - 6)
        day = self.calendar_selected_day
        month_start = _month_first(day)
        month_end = date(month_start.year, month_start.month, _last_day_of_month(month_start.year, month_start.month))
        all_tasks = self._all_tasks_flat()
        tasks_by_day: dict[date, list[Task]] = {}
        for task in all_tasks:
            if (
                task.deadline is None
                or task.trashed
                or task.status is TaskStatus.CANCELED
                or task.is_repeating_master
            ):
                continue
            tasks_by_day.setdefault(task.deadline, []).append(task)

        # Calendar-only recurrence projection: show future occurrences in-grid even when
        # storage keeps only a single active concrete instance per template.
        existing_template_dates: set[tuple[int, date]] = set()
        for task in all_tasks:
            if task.template_id is not None and task.deadline is not None:
                existing_template_dates.add((int(task.template_id), task.deadline))

        for row in self.db.load_recurrence_templates_state().values():
            # Tuple layout:
            # id, legacy_task_id, area_id, project_id, title, notes, tags, attendees,
            # anchor_date, deadline_time, recurrence_rule, timezone, enabled, last_generated_at
            template_id = int(row[0])
            area_id = row[2]
            project_id = row[3]
            title = str(row[4] or "Untitled")
            tags = parse_tags_csv(str(row[6] or ""))
            anchor_raw = row[8]
            time_raw = row[9]
            rule = str(row[10] or "")
            enabled = bool(row[12])
            if not enabled or not anchor_raw or not rule:
                continue
            try:
                anchor = date.fromisoformat(str(anchor_raw))
            except ValueError:
                continue
            deadline_time = None
            if time_raw:
                try:
                    deadline_time = time.fromisoformat(str(time_raw))
                except ValueError:
                    deadline_time = None
            for due in self.db._iter_occurrences(anchor, rule, start=month_start, end=month_end):
                if (template_id, due) in existing_template_dates:
                    continue
                synthetic = Task(
                    id=-(template_id * 100000 + due.toordinal()),
                    order_index=0,
                    project_id=project_id,
                    area_id=area_id,
                    template_id=template_id,
                    title=title,
                    notes_markdown="",
                    tags=[t for t in tags if t.lower() != "someday"],
                    attendees=[],
                    deadline=due,
                    deadline_time=deadline_time,
                    recurrence_rule=rule,
                    status=TaskStatus.TODO,
                )
                tasks_by_day.setdefault(due, []).append(synthetic)

        rows = sorted(
            tasks_by_day.get(day, []),
            key=lambda t: (t.status is TaskStatus.COMPLETED, t.deadline_time is None, t.deadline_time or time.min, t.order_index, t.id),
        )

        cell_w = max(11, min(17, max(11, (render_width - 8) // 7)))
        weeks = _month_weeks(month_start)
        # Keep all rows visible by adapting card height to available space.
        available_h = max(8, render_height - 6)  # title + weekday line + details/footer slack
        # 3 = border + header + border. 4/5 adds task preview rows when there is room.
        cell_h = max(3, min(5, available_h // max(1, len(weeks))))
        # Compact mode only when pane is truly narrow; otherwise prefer box mode.
        narrow_mode = render_width < 95

        def trunc(value: str, limit: int) -> str:
            return value if len(value) <= limit else value[: limit - 1] + "…"

        def cell_lines(d: date | None) -> list[Text]:
            if d is None:
                empty = " " * cell_w
                return [Text(empty) for _ in range(cell_h)]
            day_tasks = sorted(
                tasks_by_day.get(d, []),
                key=lambda t: (t.status is TaskStatus.COMPLETED, t.deadline_time or time.min, t.order_index, t.id),
            )
            open_count = len([t for t in day_tasks if t.status is TaskStatus.TODO])
            done_count = len([t for t in day_tasks if t.status is TaskStatus.COMPLETED])
            summary_parts = [f"{d.day:>2}"]
            if open_count > 0:
                summary_parts.append(f"•{open_count}")
            if done_count > 0:
                summary_parts.append(f"✓{done_count}")
            if d == date.today():
                summary_parts.append("Today")
            header = " ".join(summary_parts)

            task_lines: list[str] = []
            preview_slots = 2 if cell_h >= 5 else 1
            for idx in range(min(preview_slots, len(day_tasks))):
                lead = "☐ " if day_tasks[idx].status is TaskStatus.TODO else "☑ "
                task_lines.append(trunc(lead + day_tasks[idx].title, cell_w - 4))
            if len(day_tasks) > preview_slots:
                remaining = len(day_tasks) - preview_slots
                if task_lines:
                    task_lines[-1] = trunc(f"+{remaining} more", cell_w - 4)
                else:
                    task_lines.append(trunc(f"+{remaining} more", cell_w - 4))

            border_style = ACTIVE_THEME.text_muted
            body_style = ACTIVE_THEME.text
            if d == day:
                border_style = f"bold {ACTIVE_THEME.warning}"
                body_style = f"bold {ACTIVE_THEME.warning}"
            elif d == date.today():
                border_style = f"bold {ACTIVE_THEME.accent}"
                body_style = ACTIVE_THEME.accent if day_tasks else ACTIVE_THEME.text
            lines = [Text("┌" + ("─" * (cell_w - 2)) + "┐", style=border_style)]
            lines.append(Text("│" + trunc(header, cell_w - 2).ljust(cell_w - 2) + "│", style=body_style))
            body_rows = cell_h - 3
            for idx in range(body_rows):
                content = task_lines[idx] if idx < len(task_lines) else ""
                lines.append(Text("│" + content.ljust(cell_w - 2) + "│", style=body_style))
            lines.append(Text("└" + ("─" * (cell_w - 2)) + "┘", style=border_style))
            return lines

        out = Text()
        out.append(f"{month_start.strftime('%B %Y')}\n", style=f"bold {ACTIVE_THEME.accent}")
        if narrow_mode:
            out.append("Mo Tu We Th Fr Sa Su\n", style=f"bold {ACTIVE_THEME.text_muted}")
            for week in weeks:
                line = Text()
                for idx, d in enumerate(week):
                    if d is None:
                        token = "  "
                        style = ACTIVE_THEME.text_muted
                    else:
                        day_tasks = tasks_by_day.get(d, [])
                        open_count = len([t for t in day_tasks if t.status is TaskStatus.TODO])
                        done_count = len([t for t in day_tasks if t.status is TaskStatus.COMPLETED])
                        mark = "•" if open_count > 0 else "✓" if done_count > 0 else " "
                        token = f"{d.day:>2}{mark}"
                        style = ACTIVE_THEME.text
                        if d == day:
                            style = f"bold {ACTIVE_THEME.warning}"
                        elif d == date.today():
                            style = f"bold {ACTIVE_THEME.accent}"
                    line.append(token, style=style)
                    if idx < 6:
                        line.append(" ", style=ACTIVE_THEME.text_muted)
                out.append_text(line)
                out.append("\n")
            out.append("Legend: • open  ✓ completed\n", style=ACTIVE_THEME.text_muted)
        else:
            out.append("Mon".center(cell_w) + " " + "Tue".center(cell_w) + " " + "Wed".center(cell_w) + " " + "Thu".center(cell_w) + " " + "Fri".center(cell_w) + " " + "Sat".center(cell_w) + " " + "Sun".center(cell_w) + "\n", style=ACTIVE_THEME.text_muted)
            for week in weeks:
                block = [cell_lines(d) for d in week]
                for line_idx in range(cell_h):
                    line = Text()
                    for col in range(7):
                        line.append_text(block[col][line_idx])
                        if col < 6:
                            line.append(" ")
                    out.append_text(line)
                    out.append("\n")
        out.append(
            f"\n{ICON_CALENDAR} {day.isoformat()} ({_relative_day_label(day)})  |  arrows move day/week, [ ] month, t today\n",
            style=f"bold {ACTIVE_THEME.text}",
        )
        if not rows:
            out.append("No tasks due\n", style=ACTIVE_THEME.text_muted)
        else:
            for task in rows[:10]:
                icon = ICON_TASK_CHECKED if task.status is TaskStatus.COMPLETED else ICON_TASK_UNCHECKED
                title_style = f"{ACTIVE_THEME.text_muted} strike" if task.status is TaskStatus.COMPLETED else ACTIVE_THEME.text
                due_label = _relative_day_label(task.deadline) if task.deadline else "-"
                if task.deadline_time:
                    due_label = f"{due_label} {task.deadline_time.strftime('%H:%M')}"
                location = self._task_location_label(task)
                line = Text()
                line.append(f"{icon} {task.title}", style=title_style)
                line.append("  |  ", style=ACTIVE_THEME.text_muted)
                line.append(f"{ICON_IND_DEADLINE} {due_label}", style=ACTIVE_THEME.text_muted)
                line.append("  |  ", style=ACTIVE_THEME.text_muted)
                line.append(f"{ICON_FOLDER_CLOSED} {location}", style=ACTIVE_THEME.accent)
                if task.tags:
                    line.append("  |  ", style=ACTIVE_THEME.text_muted)
                    line.append("#" + ",#".join(task.tags), style=ACTIVE_THEME.accent)
                if task.template_id is not None:
                    line.append("  |  ", style=ACTIVE_THEME.text_muted)
                    line.append(ICON_IND_REPEAT, style=ACTIVE_THEME.warning)
                out.append_text(line)
                out.append("\n")
        widget.update(out)
        open_count = len([t for t in rows if t.status is TaskStatus.TODO])
        done_count = len([t for t in rows if t.status is TaskStatus.COMPLETED])
        self._status(f"Calendar {month_start.strftime('%B %Y')}  |  {open_count} open, {done_count} completed on {day.isoformat()}")

    @on(OptionList.OptionHighlighted, "#sidebar")
    def sidebar_highlighted(self) -> None:
        self._render_tasks()

    def action_focus_next(self) -> None:
        sidebar = self._sidebar()
        tasks = self._tasks()
        if self._calendar_active() and not sidebar.has_focus:
            self.calendar_selected_day = self.calendar_selected_day + timedelta(days=1)
            self._render_tasks()
            return
        if sidebar.has_focus:
            tasks.focus()
        else:
            sidebar.focus()

    def action_focus_prev(self) -> None:
        sidebar = self._sidebar()
        if self._calendar_active() and not sidebar.has_focus:
            self.calendar_selected_day = self.calendar_selected_day - timedelta(days=1)
            self._render_tasks()
            return
        self.action_focus_next()

    @on(events.Key)
    def handle_escape_key(self, event: events.Key) -> None:
        if event.key != "escape":
            return
        if isinstance(self.screen, ModalScreen):
            return
        event.stop()
        self.push_screen(
            ConfirmDeleteScreen("Quit JustDoIt", "Are you sure you want to quit?"),
            callback=lambda confirmed: self.exit() if confirmed else None,
        )

    def action_cursor_down(self) -> None:
        if self._calendar_active() and not self._sidebar().has_focus:
            self.calendar_selected_day = self.calendar_selected_day + timedelta(days=7)
            self._render_tasks()
            return
        widget = self.focused
        if isinstance(widget, OptionList):
            widget.action_cursor_down()

    def action_cursor_up(self) -> None:
        if self._calendar_active() and not self._sidebar().has_focus:
            self.calendar_selected_day = self.calendar_selected_day - timedelta(days=7)
            self._render_tasks()
            return
        widget = self.focused
        if isinstance(widget, OptionList):
            widget.action_cursor_up()

    def action_calendar_prev_month(self) -> None:
        if not self._calendar_active():
            return
        m = self.calendar_selected_day.month - 1
        y = self.calendar_selected_day.year
        if m == 0:
            m = 12
            y -= 1
        d = min(self.calendar_selected_day.day, _last_day_of_month(y, m))
        self.calendar_selected_day = date(y, m, d)
        self._render_tasks()

    def action_calendar_next_month(self) -> None:
        if not self._calendar_active():
            return
        m = self.calendar_selected_day.month + 1
        y = self.calendar_selected_day.year
        if m == 13:
            m = 1
            y += 1
        d = min(self.calendar_selected_day.day, _last_day_of_month(y, m))
        self.calendar_selected_day = date(y, m, d)
        self._render_tasks()

    def action_calendar_today(self) -> None:
        if not self._calendar_active():
            return
        self.calendar_selected_day = date.today()
        self._render_tasks()

    def action_open_details(self) -> None:
        if self._sidebar().has_focus:
            self._open_sidebar_target()
            return
        self.action_edit_task()

    def _open_sidebar_target(self) -> None:
        target = self._current_sidebar_target()
        if target == ("smart", SmartList.CALENDAR.value):
            self._render_tasks()
            self._tasks().focus()
            return
        if target and target[0] == "area":
            area_id = int(target[1])
            if area_id in self.collapsed_area_ids:
                self.collapsed_area_ids.remove(area_id)
            else:
                self.collapsed_area_ids.add(area_id)
            self._render_sidebar()
            self._render_tasks()

    def action_open_calendar(self) -> None:
        self.push_screen(CalendarScreen(self._all_tasks_flat(), date.today()))

    @on(OptionList.OptionSelected, "#sidebar")
    def sidebar_selected(self) -> None:
        if self._sidebar().has_focus:
            self._open_sidebar_target()

    def _move_task(self, delta: int) -> None:
        if not self._tasks().has_focus:
            return
        idx = self._tasks().highlighted
        if idx is None:
            return
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(self.visible_tasks):
            return
        current = self.visible_tasks[idx]
        other = self.visible_tasks[new_idx]
        self.db.swap_task_order(current.id, other.id)
        self.reload_state()
        self._tasks().highlighted = new_idx

    def action_move_task_up(self) -> None:
        self._move_task(-1)

    def action_move_task_down(self) -> None:
        self._move_task(1)

    def _selected_task(self) -> Task | None:
        idx = self._tasks().highlighted
        if idx is None or idx < 0 or idx >= len(self.visible_tasks):
            return None
        return self.visible_tasks[idx]

    def _default_capture_destination(self) -> CaptureDestination:
        target = self._current_sidebar_target()
        if target is None:
            return CaptureDestination("inbox")
        kind, payload = target
        if kind == "area":
            return CaptureDestination("area", int(payload))
        if kind == "project":
            return CaptureDestination("project", int(payload))
        return CaptureDestination("inbox")

    def _capture_destination_label(self, destination: CaptureDestination) -> str:
        if destination.kind == "inbox":
            return "Inbox"
        if destination.kind == "area" and destination.payload is not None:
            area = next((a for a in self.areas if a.id == int(destination.payload)), None)
            return area.title if area else "Area"
        if destination.kind == "project" and destination.payload is not None:
            for area in self.areas:
                project = next((p for p in area.projects if p.id == int(destination.payload)), None)
                if project:
                    return f"{area.title} / {project.title}"
            return "Project"
        return "Inbox"

    def _task_location_label(self, task: Task) -> str:
        if task.project_id is not None:
            for area in self.areas:
                project = next((p for p in area.projects if p.id == task.project_id), None)
                if project is not None:
                    return f"{area.title} / {project.title}"
        if task.area_id is not None:
            area = next((a for a in self.areas if a.id == task.area_id), None)
            if area is not None:
                return area.title
        return "Inbox"

    def _capture_destination_candidates(self, query: str) -> list[tuple[CaptureDestination, str]]:
        out: list[tuple[CaptureDestination, str]] = [(CaptureDestination("inbox"), "Inbox")]
        for area in self.areas:
            out.append((CaptureDestination("area", area.id), area.title))
            for project in area.projects:
                out.append((CaptureDestination("project", project.id), f"{area.title} / {project.title}"))
        q = query.strip().lower()
        if not q:
            return out
        scored: list[tuple[tuple[CaptureDestination, str], int]] = []
        for item in out:
            _, label = item
            matched = _fuzzy_match_indices(label.lower(), q)
            if matched is None:
                continue
            scored.append((item, matched[0]))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [item for item, _ in scored]

    def _task_capture_destination(self, task: Task) -> CaptureDestination:
        if task.project_id is not None:
            return CaptureDestination("project", task.project_id)
        if task.area_id is not None:
            return CaptureDestination("area", task.area_id)
        return CaptureDestination("inbox")

    def _parse_capture_destination_input(self, raw: str) -> CaptureDestination | None:
        value = raw.strip()
        if not value:
            return None
        if value.lower() == "inbox":
            return CaptureDestination("inbox")
        for destination, label in self._capture_destination_candidates(""):
            if label.lower() == value.lower():
                return destination
        return None

    def _all_tasks_flat(self) -> list[Task]:
        out = list(self.inbox)
        for area in self.areas:
            out.extend(area.tasks)
            for project in area.projects:
                out.extend(project.tasks)
        return out

    def _sync_repeating_instances(self, today: date) -> bool:
        inserted = self.db.materialize_recurrence_instances(today)
        return inserted > 0

    def _template_anchor_for_task(self, task: Task) -> date | None:
        if task.template_id is None:
            return None
        row = self.db.get_recurrence_template(task.template_id)
        if row is None:
            return None
        raw = row["anchor_date"] if isinstance(row, sqlite3.Row) else row[8]
        try:
            return date.fromisoformat(str(raw))
        except Exception:
            return None

    @work(exclusive=True, group="modal")
    async def action_new_task(self) -> None:
        default_destination = self._default_capture_destination()
        result = await self.push_screen_wait(
            NewTaskScreen(
                candidates=self._capture_destination_candidates(""),
                selected_destination=default_destination,
            )
        )
        if result is None or not result.raw.strip():
            return

        parsed = parse_quick_capture(result.raw)
        area_id: int | None = None
        project_id: int | None = None

        destination = result.destination
        if destination.kind == "area" and destination.payload is not None:
            area_id = int(destination.payload)
        elif destination.kind == "project" and destination.payload is not None:
            project_id = int(destination.payload)
            for area in self.areas:
                if any(p.id == project_id for p in area.projects):
                    area_id = area.id
                    break

        target = self._current_sidebar_target()
        if target is not None and destination.kind == "inbox":
            kind, payload = target
            if kind == "smart":
                if payload == SmartList.TODAY.value and parsed.deadline is None:
                    parsed.deadline = date.today()
                if payload == SmartList.UPCOMING.value and parsed.deadline is None:
                    parsed.deadline = date.today() + timedelta(days=1)
                if payload == SmartList.SOMEDAY.value and "someday" not in [x.lower() for x in parsed.tags]:
                    parsed.tags.append("someday")

        before = self._snapshot_task_state()
        before_templates = self._snapshot_template_state()
        if parsed.recurrence_rule:
            anchor = parsed.deadline or date.today()
            self.db.insert_recurrence_template(
                title=parsed.title,
                notes="",
                tags=parsed.tags,
                attendees=parsed.attendees,
                anchor_date=anchor,
                deadline_time=parsed.deadline_time,
                recurrence_rule=parsed.recurrence_rule,
                area_id=area_id,
                project_id=project_id,
            )
            self.db.materialize_recurrence_instances(date.today())
        else:
            self.db.insert_task(
                title=parsed.title,
                notes="",
                tags=parsed.tags,
                attendees=parsed.attendees,
                deadline=parsed.deadline,
                deadline_time=parsed.deadline_time,
                recurrence_rule=None,
                area_id=area_id,
                project_id=project_id,
            )
        self._finalize_undoable_change("Create task", before, before_templates)

    @work(exclusive=True, group="modal")
    async def action_edit_task(self) -> None:
        task = self._selected_task()
        if not task:
            return
        before = self._snapshot_task_state()
        before_templates = self._snapshot_template_state()
        current_destination = self._task_capture_destination(task)
        draft = await self.push_screen_wait(
            EditTaskScreen(
                task,
                self._capture_destination_label(current_destination),
                self._capture_destination_candidates(""),
            )
        )
        if draft is None:
            return
        task.title = draft.title.strip() or "Untitled"
        task.tags = parse_tags_csv(draft.tags)

        destination = self._parse_capture_destination_input(draft.location)
        if destination is None:
            self._status("Invalid location (Inbox or Area / Project)")
            return
        if destination.kind == "inbox":
            task.area_id = None
            task.project_id = None
        elif destination.kind == "area" and destination.payload is not None:
            task.area_id = int(destination.payload)
            task.project_id = None
        elif destination.kind == "project" and destination.payload is not None:
            task.project_id = int(destination.payload)
            area_match = next(
                (area.id for area in self.areas if any(p.id == task.project_id for p in area.projects)),
                None,
            )
            if area_match is None:
                self._status("Invalid project location")
                return
            task.area_id = int(area_match)
        else:
            self._status("Invalid location")
            return

        parsed_deadline = parse_deadline_input(draft.deadline)
        if parsed_deadline is None:
            self._status("Invalid deadline format")
            return
        task.deadline, task.deadline_time = parsed_deadline
        repeat_value = draft.recurrence.strip().lower()
        normalized_repeat: str | None = None
        if repeat_value:
            normalized_repeat = normalize_repeat_rule(repeat_value)
            if normalized_repeat is None:
                self._status("Invalid repeat rule")
                return
        task.recurrence_rule = None if task.template_id is not None else normalized_repeat
        task.is_repeating_master = bool(task.recurrence_rule)

        raw_status = draft.status.strip().lower()
        if raw_status in {"todo", "to-do"}:
            task.status = TaskStatus.TODO
        elif raw_status in {"done", "completed", "complete"}:
            task.status = TaskStatus.COMPLETED
        elif raw_status in {"canceled", "cancelled"}:
            task.status = TaskStatus.CANCELED
        else:
            self._status("Invalid status (todo/done/canceled)")
            return

        if task.template_id is not None:
            scope = await self.push_screen_wait(RecurringEditScopeScreen("Recurring Task Edit Scope"))
            if scope is None:
                return
            if scope == "future":
                row = self.db.get_recurrence_template(task.template_id)
                if row is None:
                    self._status("Missing recurring template")
                    return
                anchor = task.deadline or self._template_anchor_for_task(task)
                if anchor is None:
                    self._status("Recurring template requires a due date")
                    return
                recurrence = normalized_repeat or str(row["recurrence_rule"])
                self.db.update_recurrence_template(
                    task.template_id,
                    title=task.title,
                    notes=task.notes_markdown,
                    tags=list(task.tags),
                    attendees=list(task.attendees),
                    anchor_date=anchor,
                    deadline_time=task.deadline_time,
                    recurrence_rule=recurrence,
                    area_id=task.area_id,
                    project_id=task.project_id,
                    enabled=True,
                )
                self.db.materialize_recurrence_instances(date.today())
            else:
                if repeat_value:
                    self._status("Repeat rule is managed by recurring template (instance edit only)")

        self.db.update_task(task)
        self._finalize_undoable_change("Edit task", before, before_templates)

    @work(exclusive=True, group="modal")
    async def action_edit_deadline(self) -> None:
        task = self._selected_task()
        if not task:
            return
        before = self._snapshot_task_state()
        before_templates = self._snapshot_template_state()
        scope: str = "this"
        if task.template_id is not None:
            selected_scope = await self.push_screen_wait(RecurringEditScopeScreen("Recurring Deadline Scope"))
            if selected_scope is None:
                return
            scope = selected_scope
        value = await self.push_screen_wait(
            PromptScreen("Set Deadline", "tomorrow 14:30", format_deadline(task.deadline, task.deadline_time))
        )
        if value is None:
            return
        parsed = parse_deadline_input(value)
        if parsed is None:
            self._status("Invalid deadline format")
            return
        task.deadline, task.deadline_time = parsed
        if task.template_id is not None and scope == "future":
            row = self.db.get_recurrence_template(task.template_id)
            if row is None:
                self._status("Missing recurring template")
                return
            anchor = task.deadline or self._template_anchor_for_task(task)
            if anchor is None:
                self._status("Recurring template requires a due date")
                return
            self.db.update_recurrence_template(
                task.template_id,
                title=str(row["title"]),
                notes=str(row["notes"] or ""),
                tags=parse_tags_csv(str(row["tags"] or "")),
                attendees=parse_attendees_csv(str(row["attendees"] or "")),
                anchor_date=anchor,
                deadline_time=task.deadline_time,
                recurrence_rule=str(row["recurrence_rule"]),
                area_id=row["area_id"],
                project_id=row["project_id"],
                enabled=bool(row["enabled"]),
            )
            self.db.materialize_recurrence_instances(date.today())
        self.db.update_task(task)
        self._finalize_undoable_change("Edit deadline", before, before_templates)

    @work(exclusive=True, group="modal")
    async def action_toggle_task(self) -> None:
        task = self._selected_task()
        if not task:
            return
        before = self._snapshot_task_state()
        before_templates = self._snapshot_template_state()

        scope: str = "this"
        if task.template_id is not None:
            selected_scope = await self.push_screen_wait(RecurringEditScopeScreen("Recurring Completion Scope"))
            if selected_scope is None:
                return
            scope = selected_scope
        if task.template_id is not None and scope == "future":
            new_status = TaskStatus.COMPLETED if task.status is TaskStatus.TODO else TaskStatus.TODO
            task.status = new_status
            self.db.set_recurrence_template_enabled(task.template_id, new_status is TaskStatus.TODO)
            self.db.update_task(task)
            if new_status is TaskStatus.TODO:
                self.db.materialize_recurrence_instances(date.today())
            self._finalize_undoable_change("Update recurring series scope", before, before_templates)
            if new_status is TaskStatus.COMPLETED:
                self._status("Completed this occurrence and paused all future occurrences")
            else:
                self._status("Reopened this occurrence and resumed the recurring series")
            return
        was_status = task.status
        if task.template_id is not None and was_status is TaskStatus.COMPLETED:
            # Undo completion for a single recurring occurrence.
            task.status = TaskStatus.TODO
            if task.deadline and task.deadline < date.today():
                task.deadline = date.today()
            self.db.update_task(task)
            self._finalize_undoable_change("Recreate recurring occurrence", before, before_templates)
            self._status("Recreated this recurring occurrence")
            return
        self.db.toggle_task(task)
        self._finalize_undoable_change("Toggle task", before, before_templates)
        if task.template_id is not None:
            if was_status is TaskStatus.TODO:
                self._status("Completed this occurrence only")
            else:
                self._status("Reopened this occurrence only")

    @work(exclusive=True, group="modal")
    async def action_delete_selected(self) -> None:
        if self.active_tag_filters and self._tasks().has_focus:
            self.active_tag_filters.clear()
            self._render_tasks()
            return

        sidebar = self._sidebar()
        if sidebar.has_focus:
            target = self._current_sidebar_target()
            if target is None:
                return
            kind, payload = target
            if kind == "project":
                project = None
                for area in self.areas:
                    project = next((p for p in area.projects if p.id == int(payload)), None)
                    if project:
                        break
                if not project:
                    return
                confirmed = await self.push_screen_wait(
                    ConfirmDeleteScreen(
                        "Delete Project",
                        f"Delete '{project.title}' and move its todo tasks to Trash?",
                    )
                )
                if confirmed:
                    self.db.delete_project_to_trash(project.id)
                    self.reload_state()
                return
            if kind == "area":
                area = next((a for a in self.areas if a.id == int(payload)), None)
                if not area:
                    return
                confirmed = await self.push_screen_wait(
                    ConfirmDeleteScreen(
                        "Delete Area",
                        f"Delete '{area.title}' and move its todo tasks to Trash?",
                    )
                )
                if confirmed:
                    self.db.delete_area_to_trash(area.id)
                    self.reload_state()
                return
            return

        task = self._selected_task()
        if not task:
            return
        before = self._snapshot_task_state()
        before_templates = self._snapshot_template_state()
        if task.template_id is not None and not task.trashed:
            scope = await self.push_screen_wait(RecurringEditScopeScreen("Recurring Delete Scope"))
            if scope is None:
                return
            if scope == "future":
                self.db.set_recurrence_template_enabled(task.template_id, False)
                self.db.set_tasks_trashed_for_template(task.template_id, True)
                self._finalize_undoable_change("Delete recurring series", before, before_templates)
                return
        if task.trashed:
            self.db.delete_task_permanently(task.id)
        else:
            self.db.set_task_trashed(task, True)
        self._finalize_undoable_change("Delete task", before, before_templates)

    def action_restore_task(self) -> None:
        task = self._selected_task()
        if not task or not task.trashed:
            return
        before = self._snapshot_task_state()
        before_templates = self._snapshot_template_state()
        self.db.set_task_trashed(task, False)
        self._finalize_undoable_change("Restore task", before, before_templates)

    @work(exclusive=True, group="modal")
    async def action_new_area(self) -> None:
        value = await self.push_screen_wait(PromptScreen("New Area", "Area name"))
        if value is None or not value.strip():
            return
        try:
            self.db.insert_area(value.strip())
        except sqlite3.IntegrityError:
            self._status("Area name already exists")
            return
        self.reload_state()

    @work(exclusive=True, group="modal")
    async def action_new_project(self) -> None:
        target = self._current_sidebar_target()
        if target is None:
            return

        area_id: int | None = None
        if target[0] == "area":
            area_id = int(target[1])
        elif target[0] == "project":
            project_id = int(target[1])
            for area in self.areas:
                if any(p.id == project_id for p in area.projects):
                    area_id = area.id
                    break

        if area_id is None:
            self._status("Select an area (or project under area) first")
            return

        value = await self.push_screen_wait(PromptScreen("New Project", "Project name"))
        if value is None or not value.strip():
            return
        self.db.insert_project(area_id, value.strip())
        self.reload_state()

    @work(exclusive=True, group="modal")
    async def action_rename_entity(self) -> None:
        target = self._current_sidebar_target()
        if target is None:
            return
        kind, payload = target
        if kind == "area":
            area = next((a for a in self.areas if a.id == int(payload)), None)
            if not area:
                return
            value = await self.push_screen_wait(PromptScreen("Rename Area", value=area.title))
            if value and value.strip():
                try:
                    self.db.rename_area(area.id, value.strip())
                except sqlite3.IntegrityError:
                    self._status("Area name already exists")
                    return
                self.reload_state()
        elif kind == "project":
            project = None
            for area in self.areas:
                project = next((p for p in area.projects if p.id == int(payload)), None)
                if project:
                    break
            if not project:
                return
            value = await self.push_screen_wait(PromptScreen("Rename Project", value=project.title))
            if value and value.strip():
                self.db.rename_project(project.id, value.strip())
                self.reload_state()

    @work(exclusive=True, group="modal")
    async def action_search_prompt(self) -> None:
        target = await self.push_screen_wait(QuickFindScreen(self._quick_find_candidates()))
        if target is None:
            return
        self._navigate_quick_find_target(target)

    @work(exclusive=True, group="modal")
    async def action_filter_prompt(self) -> None:
        existing = ",".join(sorted(self.active_tag_filters))
        value = await self.push_screen_wait(PromptScreen("Filter Tags", "tag1,tag2", existing))
        if value is None:
            return
        tags = [t.strip().lower().lstrip("#") for t in value.split(",") if t.strip()]
        self.active_tag_filters = set(tags)
        self._render_tasks()

    @work(exclusive=True, group="modal")
    async def action_shortcut_help(self) -> None:
        await self.push_screen_wait(ShortcutHelpScreen())

    @work(exclusive=True, group="modal")
    async def action_theme_selector_prompt(self) -> None:
        selected = await self.push_screen_wait(ThemeSelectorScreen(self.active_theme_id))
        if selected is None:
            return
        self._set_theme(selected)

    def _quick_find_candidates(self) -> list[QuickFindCandidate]:
        candidates: list[QuickFindCandidate] = []

        for smart in SmartList:
            candidates.append(
                QuickFindCandidate(
                    kind="smart",
                    icon=_quick_find_smart_icon(smart.value),
                    target=QuickFindTarget(kind="smart", payload=smart.value),
                    display=smart.value,
                    searchable=f"smart list {smart.value}",
                )
            )

        for area in self.areas:
            candidates.append(
                QuickFindCandidate(
                    kind="area",
                    icon=ICON_FOLDER_OPEN,
                    target=QuickFindTarget(kind="area", payload=area.id),
                    display=area.title,
                    searchable=f"area {area.title}",
                )
            )
            for project in area.projects:
                candidates.append(
                    QuickFindCandidate(
                        kind="project",
                        icon=ICON_FOLDER_CLOSED,
                        target=QuickFindTarget(kind="project", payload=project.id),
                        display=project.title,
                        searchable=f"project {project.title} {project.notes} {area.title}",
                    )
                )

        seen_tags: dict[str, tuple[str, QuickFindTarget]] = {}
        for task, nav_kind, nav_payload, trashed in self._all_tasks_with_context():
            preview = f" - {_compact(task.notes_markdown, 48)}" if task.notes_markdown else ""
            tags = f" [{','.join(task.tags)}]" if task.tags else ""
            force_smart = SmartList.TRASH.value if trashed else None
            target = QuickFindTarget(
                kind="task",
                payload=task.id,
                nav_kind=nav_kind,
                nav_payload=nav_payload,
                force_smart=force_smart,
            )
            candidates.append(
                QuickFindCandidate(
                    kind="task",
                    icon="✖" if task.trashed or task.status is TaskStatus.CANCELED else "☑" if task.status is TaskStatus.COMPLETED else "☐",
                    target=target,
                    display=f"{task.title}{preview}{tags}",
                    searchable=f"task {task.title} {task.notes_markdown} {' '.join(task.tags)}",
                )
            )

            for tag in task.tags:
                key = tag.lower()
                if key in seen_tags:
                    continue
                seen_tags[key] = (
                    tag,
                    QuickFindTarget(
                        kind="tag",
                        payload=task.id,
                        nav_kind=nav_kind,
                        nav_payload=nav_payload,
                        force_smart=force_smart,
                    ),
                )

        for tag, target in seen_tags.values():
            candidates.append(
                QuickFindCandidate(
                    kind="tag",
                    icon="#",
                    target=target,
                    display=f"#{tag}",
                    searchable=f"tag {tag}",
                )
            )

        return candidates

    def _all_tasks_with_context(self) -> list[tuple[Task, str, str | int, bool]]:
        out: list[tuple[Task, str, str | int, bool]] = []
        for task in self.inbox:
            if task.is_repeating_master:
                continue
            out.append((task, "smart", SmartList.INBOX.value, task.trashed))
        for area in self.areas:
            for task in area.tasks:
                if task.is_repeating_master:
                    continue
                out.append((task, "area", area.id, task.trashed))
            for project in area.projects:
                for task in project.tasks:
                    if task.is_repeating_master:
                        continue
                    out.append((task, "project", project.id, task.trashed))
        return out

    def _select_sidebar_target(self, kind: str, payload: str | int) -> bool:
        sidebar = self._sidebar()
        for idx, entry in enumerate(self.sidebar_targets):
            if entry == (kind, payload):
                sidebar.highlighted = idx
                return True
        return False

    def _navigate_quick_find_target(self, target: QuickFindTarget) -> None:
        if target.kind == "smart":
            self._select_sidebar_target("smart", str(target.payload))
            self._render_tasks()
            if self.visible_tasks:
                self._tasks().highlighted = 0
            self._tasks().focus()
            return

        if target.kind == "area":
            self._select_sidebar_target("area", int(target.payload))
            self._render_tasks()
            if self.visible_tasks:
                self._tasks().highlighted = 0
            self._tasks().focus()
            return

        if target.kind == "project":
            project_id = int(target.payload)
            for area in self.areas:
                if any(project.id == project_id for project in area.projects):
                    self.collapsed_area_ids.discard(area.id)
                    break
            self._render_sidebar()
            self._select_sidebar_target("project", project_id)
            self._render_tasks()
            if self.visible_tasks:
                self._tasks().highlighted = 0
            self._tasks().focus()
            return

        if target.force_smart:
            self._select_sidebar_target("smart", target.force_smart)
        elif target.nav_kind and target.nav_payload is not None:
            if target.nav_kind == "project":
                for area in self.areas:
                    if any(project.id == int(target.nav_payload) for project in area.projects):
                        self.collapsed_area_ids.discard(area.id)
                        break
                self._render_sidebar()
            self._select_sidebar_target(target.nav_kind, target.nav_payload)
        self._render_tasks()
        if self.visible_tasks:
            found = next((i for i, task in enumerate(self.visible_tasks) if task.id == int(target.payload)), None)
            self._tasks().highlighted = 0 if found is None else found
        self._tasks().focus()


class ThemeSelectorScreen(ModalScreen[str | None]):
    def __init__(self, active_theme_id: str) -> None:
        super().__init__()
        self.active_theme_id = active_theme_id

    def compose(self) -> ComposeResult:
        with Container(id="prompt-modal"):
            yield Static("Runtime Theme (Ctrl+t)", classes="prompt-title")
            yield OptionList(*[Option(tid) for tid in THEME_IDS], id="theme-list")
            yield Static("Up/Down to select, Enter to apply, Esc to cancel", classes="prompt-help")

    def on_mount(self) -> None:
        app = self.app
        if isinstance(app, JustDoItTextual):
            _apply_theme_class_to_screen(self, app.active_theme_id)
        lst = self.query_one("#theme-list", OptionList)
        idx = THEME_IDS.index(self.active_theme_id) if self.active_theme_id in THEME_IDS else 0
        lst.highlighted = idx
        lst.focus()

    @on(OptionList.OptionSelected, "#theme-list")
    def select_option(self) -> None:
        idx = self.query_one("#theme-list", OptionList).highlighted or 0
        self.dismiss(THEME_IDS[max(0, min(idx, len(THEME_IDS) - 1))])

    def key_enter(self, event: events.Key) -> None:
        event.stop()
        idx = self.query_one("#theme-list", OptionList).highlighted or 0
        self.dismiss(THEME_IDS[max(0, min(idx, len(THEME_IDS) - 1))])

    def key_escape(self, event: events.Key) -> None:
        event.stop()
        self.dismiss(None)
