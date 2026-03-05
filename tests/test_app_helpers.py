from __future__ import annotations

from datetime import date

from justdoit_textual.app import (
    QuickFindCandidate,
    QuickFindTarget,
    _calendar_deadline_counts,
    _calendar_tasks_for_day,
    _compact,
    _fuzzy_match_indices,
    _generate_color_from_string,
    _highlight_quick_capture,
    _month_weeks,
    _parse_color_input,
    _quick_capture_summary,
    _quick_find_results,
    _relative_day_label,
    _strip_recurrence_suffix,
)
from justdoit_textual.models import Task, TaskStatus


def test_relative_day_label() -> None:
    today = date.today()
    assert _relative_day_label(today) == "Today"
    assert _relative_day_label(date(1999, 12, 31)) == "1999-12-31"


def test_compact_truncates() -> None:
    assert _compact("abc", 10) == "abc"
    assert _compact("x" * 20, 10) == ("x" * 10) + "..."


def test_fuzzy_match_indices_positive_and_negative() -> None:
    matched = _fuzzy_match_indices("hello world", "hwd")
    assert matched is not None
    assert _fuzzy_match_indices("hello", "xyz") is None


def test_quick_find_results_orders_by_score() -> None:
    candidates = [
        QuickFindCandidate("task", "t", "alpha task", "alpha task", QuickFindTarget("task", 1)),
        QuickFindCandidate("task", "t", "alpine note", "alpine note", QuickFindTarget("task", 2)),
    ]
    results = _quick_find_results(candidates, "alp")
    assert len(results) == 2
    assert results[0].score >= results[1].score


def test_color_parsing_and_generation() -> None:
    assert _parse_color_input("#aabbcc") == "#aabbcc"
    assert _parse_color_input("red") == "#ff5555"
    assert _parse_color_input("unknown") is None
    palette = ("#111111", "#222222", "#333333")
    first = _generate_color_from_string("Work", palette)
    second = _generate_color_from_string("Work", palette)
    assert first == second
    assert first in palette


def test_strip_recurrence_suffix_detects_rule() -> None:
    text, rule = _strip_recurrence_suffix("pay bills every month")
    assert text == "pay bills"
    assert rule == "monthly"


def test_quick_capture_highlight_and_summary_do_not_crash() -> None:
    raw = "standup due tomorrow 09:30 every weekday #team"
    highlighted = _highlight_quick_capture(raw)
    summary = _quick_capture_summary(raw)
    assert highlighted.plain == raw
    assert "Due:" in summary.plain
    assert "Repeat:" in summary.plain


def test_month_weeks_builds_calendar_rows() -> None:
    weeks = _month_weeks(date(2026, 3, 1))
    assert len(weeks) >= 5
    assert weeks[0][0] is None  # March 2026 starts on Sunday.
    assert weeks[0][6] == date(2026, 3, 1)


def test_calendar_counts_and_day_sorting() -> None:
    d = date(2026, 3, 4)
    a = Task(id=1, order_index=2, title="a", notes_markdown="", deadline=d, deadline_time=None, status=TaskStatus.TODO)
    b = Task(id=2, order_index=1, title="b", notes_markdown="", deadline=d, deadline_time=None, status=TaskStatus.COMPLETED)
    c = Task(id=3, order_index=3, title="c", notes_markdown="", deadline=d, deadline_time=None, status=TaskStatus.TODO, trashed=True)
    master = Task(
        id=4,
        order_index=4,
        title="master",
        notes_markdown="",
        deadline=d,
        deadline_time=None,
        status=TaskStatus.TODO,
        is_repeating_master=True,
        recurrence_rule="weekly",
    )
    counts = _calendar_deadline_counts([a, b, c, master])
    assert counts[d] == (1, 1)
    rows = _calendar_tasks_for_day([a, b, c, master], d)
    assert [t.id for t in rows] == [1, 2]
