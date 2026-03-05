from __future__ import annotations

from datetime import date

from justdoit_textual.parsing import (
    format_deadline,
    normalize_repeat_rule,
    parse_deadline_input,
    parse_natural_date,
    parse_quick_capture,
    parse_tags_csv,
)


def test_parse_natural_date_relative() -> None:
    today = date(2026, 3, 4)
    assert parse_natural_date("today", today=today) == date(2026, 3, 4)
    assert parse_natural_date("tomorrow", today=today) == date(2026, 3, 5)
    assert parse_natural_date("in 2 days", today=today) == date(2026, 3, 6)
    assert parse_natural_date("next friday", today=today) == date(2026, 3, 6)


def test_parse_deadline_input_cases() -> None:
    assert parse_deadline_input("") == (None, None)
    parsed = parse_deadline_input("tomorrow 09:30")
    assert parsed is not None
    assert parsed[0] is not None and parsed[1] is not None and parsed[1].hour == 9
    assert parse_deadline_input("2026-03-12 08:00") is not None
    assert parse_deadline_input("invalid") is None


def test_parse_quick_capture_inline_due_repeat_tags() -> None:
    captured = parse_quick_capture("review #work by next friday 15:00 weekly")
    assert captured.title == "review"
    assert captured.deadline is not None
    assert captured.deadline_time is not None
    assert captured.recurrence_rule == "weekly"
    assert "work" in [t.lower() for t in captured.tags]


def test_parse_quick_capture_directives_due_repeat() -> None:
    captured = parse_quick_capture("call mom ; due=tomorrow ; repeat=weekly")
    assert captured.title == "call mom"
    assert captured.deadline is not None
    assert captured.recurrence_rule == "weekly"


def test_parse_quick_capture_due_repeat_order_variants() -> None:
    one = parse_quick_capture("standup due tomorrow 09:30 every weekday")
    two = parse_quick_capture("standup every weekday due tomorrow 09:30")
    for captured in (one, two):
        assert captured.title == "standup"
        assert captured.deadline is not None
        assert captured.deadline_time is not None
        assert captured.recurrence_rule == "weekdays"


def test_parse_quick_capture_today_monthly() -> None:
    captured = parse_quick_capture("test today monthly")
    assert captured.title == "test"
    assert captured.deadline == date.today()
    assert captured.recurrence_rule == "monthly"


def test_normalize_repeat_rule_aliases() -> None:
    assert normalize_repeat_rule("every day") == "daily"
    assert normalize_repeat_rule("annually") == "yearly"
    assert normalize_repeat_rule("weekday") == "weekdays"
    assert normalize_repeat_rule("nonsense") is None


def test_parse_tags_csv_deduplicates_case_insensitive() -> None:
    assert parse_tags_csv("#Work,work,Home") == ["Work", "Home"]


def test_format_deadline_formats_time_when_present() -> None:
    parsed = parse_deadline_input("2026-03-12 08:00")
    assert parsed is not None
    deadline, deadline_time = parsed
    assert format_deadline(deadline, deadline_time) == "2026-03-12 08:00"
