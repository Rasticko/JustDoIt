from __future__ import annotations

from datetime import date

from justdoit_textual.models import Task, TaskStatus
from justdoit_textual.undo import TaskDeltaCommand, UndoManager


def _task(task_id: int, title: str) -> Task:
    return Task(
        id=task_id,
        order_index=task_id,
        title=title,
        notes_markdown="",
        deadline=date(2026, 3, 4),
        status=TaskStatus.TODO,
    )


def test_task_delta_command_execute_and_rollback(db) -> None:
    before = {}
    after = {1: _task(1, "a")}
    cmd = TaskDeltaCommand.from_states(db, "add", before, after)
    assert cmd.has_changes()
    cmd.execute()
    inbox, _ = db.load_state()
    assert [t.title for t in inbox] == ["a"]
    cmd.rollback()
    inbox, _ = db.load_state()
    assert inbox == []


def test_undo_manager_record_undo_redo(db) -> None:
    manager = UndoManager()
    cmd = TaskDeltaCommand.from_states(db, "add", {}, {1: _task(1, "a")})
    cmd.execute()
    manager.record(cmd)
    assert manager.can_undo()
    undone = manager.undo()
    assert undone is not None
    inbox, _ = db.load_state()
    assert inbox == []
    redone = manager.redo()
    assert redone is not None
    inbox, _ = db.load_state()
    assert [t.title for t in inbox] == ["a"]


def test_task_delta_command_rolls_back_template_rows(db) -> None:
    before_tasks = {}
    before_templates = db.load_recurrence_templates_state()
    template_id = db.insert_recurrence_template(
        title="series",
        notes="",
        tags=[],
        attendees=[],
        anchor_date=date(2026, 3, 4),
        deadline_time=None,
        recurrence_rule="monthly",
        area_id=None,
        project_id=None,
    )
    db.materialize_recurrence_instances(date(2026, 3, 4), horizon_days=40)
    inbox, areas = db.load_state()
    after_tasks = {t.id: t for t in db._flatten_tasks(inbox, areas)}
    after_templates = db.load_recurrence_templates_state()
    assert template_id in after_templates
    cmd = TaskDeltaCommand.from_states(
        db,
        "template create",
        before_tasks,
        after_tasks,
        before_templates=before_templates,
        after_templates=after_templates,
    )
    cmd.rollback()
    assert db.load_recurrence_templates_state() == {}
    inbox, areas = db.load_state()
    assert db._flatten_tasks(inbox, areas) == []
