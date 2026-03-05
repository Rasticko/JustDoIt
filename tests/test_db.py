from __future__ import annotations

from datetime import date

from justdoit_textual.models import SmartList, TaskStatus


def _titles(db, smart: SmartList, today: date) -> list[str]:
    inbox, areas = db.load_state()
    rows = db.list_tasks_for_sidebar(inbox, areas, ("smart", smart.value), today=today)
    return [task.title for task in rows]


def test_insert_task_defaults_repeating_master_when_rule_present(db) -> None:
    task_id = db.insert_task(
        title="repeat-me",
        notes="",
        tags=[],
        attendees=[],
        deadline=date(2026, 3, 4),
        deadline_time=None,
        recurrence_rule="monthly",
    )
    inbox, _ = db.load_state()
    task = next(t for t in inbox if t.id == task_id)
    assert task.is_repeating_master is True
    assert task.recurrence_rule == "monthly"


def test_insert_task_instance_fields_persist(db) -> None:
    master_id = db.insert_task(
        title="master",
        notes="",
        tags=[],
        attendees=[],
        deadline=date(2026, 3, 4),
        deadline_time=None,
        recurrence_rule="weekly",
    )
    instance_id = db.insert_task(
        title="instance",
        notes="",
        tags=[],
        attendees=[],
        deadline=date(2026, 3, 11),
        deadline_time=None,
        recurrence_rule=None,
        master_task_id=master_id,
        instance_date=date(2026, 3, 11),
        is_repeating_master=False,
    )
    inbox, _ = db.load_state()
    instance = next(t for t in inbox if t.id == instance_id)
    assert instance.master_task_id == master_id
    assert instance.instance_date == date(2026, 3, 11)
    assert instance.is_repeating_master is False


def test_instance_exists_for_master_on_date(db) -> None:
    master_id = db.insert_task(
        title="master",
        notes="",
        tags=[],
        attendees=[],
        deadline=date(2026, 3, 4),
        deadline_time=None,
        recurrence_rule="weekly",
    )
    assert not db.instance_exists_for_master_on_date(master_id, date(2026, 3, 11))
    db.insert_task(
        title="inst",
        notes="",
        tags=[],
        attendees=[],
        deadline=date(2026, 3, 11),
        deadline_time=None,
        recurrence_rule=None,
        master_task_id=master_id,
        instance_date=date(2026, 3, 11),
        is_repeating_master=False,
    )
    assert db.instance_exists_for_master_on_date(master_id, date(2026, 3, 11))


def test_smart_lists_with_recurring_and_normal_tasks(db) -> None:
    today = date(2026, 3, 4)
    db.insert_task(title="inbox_plain", notes="", tags=[], attendees=[], deadline=None, deadline_time=None, recurrence_rule=None)
    db.insert_task(title="inbox_today", notes="", tags=[], attendees=[], deadline=today, deadline_time=None, recurrence_rule=None)
    db.insert_task(
        title="inbox_upcoming",
        notes="",
        tags=[],
        attendees=[],
        deadline=date(2026, 3, 10),
        deadline_time=None,
        recurrence_rule=None,
    )
    db.insert_task(title="inbox_someday", notes="", tags=["someday"], attendees=[], deadline=None, deadline_time=None, recurrence_rule=None)
    master = db.insert_task(
        title="master_upcoming",
        notes="",
        tags=[],
        attendees=[],
        deadline=today,
        deadline_time=None,
        recurrence_rule="monthly",
    )
    db.insert_task(
        title="master_someday",
        notes="",
        tags=["someday"],
        attendees=[],
        deadline=None,
        deadline_time=None,
        recurrence_rule="weekly",
    )
    db.insert_task(
        title="master_upcoming",
        notes="",
        tags=[],
        attendees=[],
        deadline=today,
        deadline_time=None,
        recurrence_rule=None,
        master_task_id=master,
        instance_date=today,
        is_repeating_master=False,
    )

    done = db.insert_task(title="done", notes="", tags=[], attendees=[], deadline=today, deadline_time=None, recurrence_rule=None)
    inbox, areas = db.load_state()
    task = next(t for t in inbox if t.id == done)
    task.status = TaskStatus.COMPLETED
    db.update_task(task)

    trash = db.insert_task(title="trash", notes="", tags=[], attendees=[], deadline=None, deadline_time=None, recurrence_rule=None)
    inbox, _ = db.load_state()
    trash_task = next(t for t in inbox if t.id == trash)
    db.set_task_trashed(trash_task, True)

    assert "inbox_today" in _titles(db, SmartList.TODAY, today)
    assert _titles(db, SmartList.TODAY, today).count("master_upcoming") == 1
    # Legacy repeating masters are hidden from active smart lists; only concrete instances are shown.
    assert "master_upcoming" not in _titles(db, SmartList.UPCOMING, today)
    assert "inbox_upcoming" in _titles(db, SmartList.UPCOMING, today)
    assert "inbox_plain" in _titles(db, SmartList.ANYTIME, today)
    assert "inbox_someday" in _titles(db, SmartList.SOMEDAY, today)
    assert "master_someday" not in _titles(db, SmartList.SOMEDAY, today)
    assert "done" in _titles(db, SmartList.LOGBOOK, today)
    assert "trash" in _titles(db, SmartList.TRASH, today)


def test_upcoming_sorts_by_deadline_for_materialized_instances(db) -> None:
    today = date(2026, 3, 4)
    db.insert_task(
        title="oneoff",
        notes="",
        tags=[],
        attendees=[],
        deadline=date(2026, 3, 10),
        deadline_time=None,
        recurrence_rule=None,
    )
    db.insert_recurrence_template(
        title="weekly_series",
        notes="",
        tags=[],
        attendees=[],
        anchor_date=today,
        deadline_time=None,
        recurrence_rule="weekly",
        area_id=None,
        project_id=None,
    )
    db.insert_recurrence_template(
        title="monthly_31_series",
        notes="",
        tags=[],
        attendees=[],
        anchor_date=date(2026, 1, 31),
        deadline_time=None,
        recurrence_rule="monthly",
        area_id=None,
        project_id=None,
    )
    db.materialize_recurrence_instances(today, horizon_days=80)
    inbox, areas = db.load_state()
    rows = db.list_tasks_for_sidebar(inbox, areas, ("smart", SmartList.UPCOMING.value), today=today)
    deadlines = [task.deadline for task in rows]
    assert deadlines == sorted(deadlines)
    assert rows[0].title == "oneoff"


def test_area_and_project_lists_hide_repeating_masters(db) -> None:
    area_id = db.insert_area("Area")
    project_id = db.insert_project(area_id, "Project")
    db.insert_task(
        title="area_master",
        notes="",
        tags=[],
        attendees=[],
        deadline=date(2026, 3, 4),
        deadline_time=None,
        recurrence_rule="monthly",
        area_id=area_id,
    )
    db.insert_task(
        title="area_normal",
        notes="",
        tags=[],
        attendees=[],
        deadline=None,
        deadline_time=None,
        recurrence_rule=None,
        area_id=area_id,
    )
    db.insert_task(
        title="proj_master",
        notes="",
        tags=[],
        attendees=[],
        deadline=date(2026, 3, 4),
        deadline_time=None,
        recurrence_rule="weekly",
        area_id=area_id,
        project_id=project_id,
    )
    db.insert_task(
        title="proj_normal",
        notes="",
        tags=[],
        attendees=[],
        deadline=None,
        deadline_time=None,
        recurrence_rule=None,
        area_id=area_id,
        project_id=project_id,
    )

    inbox, areas = db.load_state()
    area_rows = db.list_tasks_for_sidebar(inbox, areas, ("area", area_id), today=date(2026, 3, 4))
    project_rows = db.list_tasks_for_sidebar(inbox, areas, ("project", project_id), today=date(2026, 3, 4))
    assert [t.title for t in area_rows] == ["area_normal", "proj_normal"]
    assert [t.title for t in project_rows] == ["proj_normal"]


def test_update_task_persists_location_and_recurrence(db) -> None:
    area_id = db.insert_area("Area")
    project_id = db.insert_project(area_id, "Project")
    task_id = db.insert_task(title="task", notes="", tags=[], attendees=[], deadline=None, deadline_time=None, recurrence_rule=None)
    inbox, _ = db.load_state()
    task = next(t for t in inbox if t.id == task_id)
    task.area_id = area_id
    task.project_id = project_id
    task.recurrence_rule = "weekly"
    task.is_repeating_master = True
    db.update_task(task)

    inbox, areas = db.load_state()
    moved = next(t for a in areas for p in a.projects for t in p.tasks if t.id == task_id)
    assert moved.area_id == area_id
    assert moved.project_id == project_id
    assert moved.recurrence_rule == "weekly"
    assert moved.is_repeating_master is True


def test_swap_task_order(db) -> None:
    first = db.insert_task(title="a", notes="", tags=[], attendees=[], deadline=None, deadline_time=None, recurrence_rule=None)
    second = db.insert_task(title="b", notes="", tags=[], attendees=[], deadline=None, deadline_time=None, recurrence_rule=None)
    db.swap_task_order(first, second)
    inbox, _ = db.load_state()
    ordered = [t.id for t in inbox]
    assert ordered == [second, first]


def test_recurrence_template_materialization_instance_only_views(db) -> None:
    today = date(2026, 3, 4)
    template_id = db.insert_recurrence_template(
        title="series",
        notes="",
        tags=[],
        attendees=[],
        anchor_date=today,
        deadline_time=None,
        recurrence_rule="monthly",
        area_id=None,
        project_id=None,
    )
    assert template_id > 0
    inserted = db.materialize_recurrence_instances(today, horizon_days=70)
    assert inserted == 1
    inbox, areas = db.load_state()
    today_rows = db.list_tasks_for_sidebar(inbox, areas, ("smart", SmartList.TODAY.value), today=today)
    upcoming_rows = db.list_tasks_for_sidebar(inbox, areas, ("smart", SmartList.UPCOMING.value), today=today)
    assert [t.deadline for t in today_rows] == [date(2026, 3, 4)]
    assert upcoming_rows == []
    assert all(t.template_id == template_id for t in today_rows + upcoming_rows)


def test_recurrence_materialization_is_idempotent(db) -> None:
    today = date(2026, 3, 4)
    db.insert_recurrence_template(
        title="series",
        notes="",
        tags=[],
        attendees=[],
        anchor_date=today,
        deadline_time=None,
        recurrence_rule="monthly",
        area_id=None,
        project_id=None,
    )
    first = db.materialize_recurrence_instances(today, horizon_days=70)
    second = db.materialize_recurrence_instances(today, horizon_days=70)
    assert first == 1
    assert second == 0


def test_completed_instance_stays_in_logbook_when_template_disabled(db) -> None:
    today = date(2026, 3, 4)
    template_id = db.insert_recurrence_template(
        title="series",
        notes="",
        tags=[],
        attendees=[],
        anchor_date=today,
        deadline_time=None,
        recurrence_rule="monthly",
        area_id=None,
        project_id=None,
    )
    db.materialize_recurrence_instances(today, horizon_days=70)
    inbox, _ = db.load_state()
    inst = next(t for t in inbox if t.template_id == template_id and t.deadline == today)
    inst.status = TaskStatus.COMPLETED
    db.update_task(inst)
    db.set_recurrence_template_enabled(template_id, False)

    inbox, areas = db.load_state()
    upcoming = db.list_tasks_for_sidebar(inbox, areas, ("smart", SmartList.UPCOMING.value), today=today)
    logbook = db.list_tasks_for_sidebar(inbox, areas, ("smart", SmartList.LOGBOOK.value), today=today)
    assert all(t.template_id != template_id for t in upcoming)
    assert any(t.id == inst.id for t in logbook)
