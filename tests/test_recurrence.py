from __future__ import annotations

from datetime import date

from justdoit_textual.app import _recurrence_due_on
from justdoit_textual.models import SmartList, Task, TaskStatus


def _master(anchor: date, rule: str) -> Task:
    return Task(
        id=999,
        order_index=0,
        title="m",
        notes_markdown="",
        deadline=anchor,
        recurrence_rule=rule,
        is_repeating_master=True,
        status=TaskStatus.TODO,
    )


def test_recurrence_due_on_daily_weekly_yearly() -> None:
    anchor = date(2026, 3, 4)
    assert _recurrence_due_on(anchor, date(2026, 3, 4), "daily")
    assert _recurrence_due_on(anchor, date(2026, 3, 11), "weekly")
    assert not _recurrence_due_on(anchor, date(2026, 3, 12), "weekly")
    assert _recurrence_due_on(date(2025, 7, 1), date(2026, 7, 1), "yearly")


def test_recurrence_due_on_month_end_carry() -> None:
    anchor = date(2026, 1, 31)
    assert _recurrence_due_on(anchor, date(2026, 2, 28), "monthly")
    assert _recurrence_due_on(anchor, date(2026, 4, 30), "monthly")
    assert not _recurrence_due_on(anchor, date(2026, 4, 29), "monthly")


def test_next_occurrence_for_upcoming_daily_weekly(db) -> None:
    today = date(2026, 3, 4)
    assert db._next_occurrence_for_master(_master(today, "daily"), today) == date(2026, 3, 5)
    assert db._next_occurrence_for_master(_master(today, "weekly"), today) == date(2026, 3, 11)


def test_next_occurrence_for_upcoming_monthly_and_yearly(db) -> None:
    today = date(2026, 3, 4)
    assert db._next_occurrence_for_master(_master(date(2026, 3, 4), "monthly"), today) == date(2026, 4, 4)
    assert db._next_occurrence_for_master(_master(date(2026, 1, 31), "monthly"), today) == date(2026, 3, 31)
    assert db._next_occurrence_for_master(_master(date(2025, 7, 1), "yearly"), today) == date(2026, 7, 1)


def test_next_occurrence_for_upcoming_leap_day_carry(db) -> None:
    today = date(2026, 3, 4)
    assert db._next_occurrence_for_master(_master(date(2024, 2, 29), "yearly"), today) == date(2027, 2, 28)


def test_iter_occurrences_weekdays_skips_weekends(db) -> None:
    got = db._iter_occurrences(
        date(2026, 3, 2),  # Monday
        "weekdays",
        start=date(2026, 3, 2),
        end=date(2026, 3, 8),
    )
    assert got == [
        date(2026, 3, 2),
        date(2026, 3, 3),
        date(2026, 3, 4),
        date(2026, 3, 5),
        date(2026, 3, 6),
    ]


def test_iter_occurrences_monthly_anchor_31_carries_month_end(db) -> None:
    got = db._iter_occurrences(
        date(2026, 1, 31),
        "monthly",
        start=date(2026, 1, 1),
        end=date(2026, 4, 30),
    )
    assert got == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]


def test_monthly_completion_early_keeps_next_month_due_instance(db) -> None:
    today = date(2026, 3, 4)
    template_id = db.insert_recurrence_template(
        title="monthly",
        notes="",
        tags=[],
        attendees=[],
        anchor_date=date(2026, 4, 4),
        deadline_time=None,
        recurrence_rule="monthly",
        area_id=None,
        project_id=None,
    )
    db.materialize_recurrence_instances(today, horizon_days=70)
    inbox, _ = db.load_state()
    april = next(t for t in inbox if t.template_id == template_id and t.deadline == date(2026, 4, 4))
    april.status = TaskStatus.COMPLETED
    db.update_task(april)
    db.materialize_recurrence_instances(today, horizon_days=90)

    # Completing an upcoming occurrence early should not remove later generated occurrences.
    inbox, areas = db.load_state()
    upcoming = db.list_tasks_for_sidebar(inbox, areas, ("smart", SmartList.UPCOMING.value), today=today)
    logbook = db.list_tasks_for_sidebar(inbox, areas, ("smart", SmartList.LOGBOOK.value), today=today)
    assert any(t.template_id == template_id and t.deadline == date(2026, 5, 4) for t in upcoming)
    assert any(t.id == april.id for t in logbook)
