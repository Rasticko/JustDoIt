from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Protocol

from .db import Database
from .models import Task


class Command(Protocol):
    description: str

    def execute(self) -> None: ...

    def rollback(self) -> None: ...


@dataclass
class TaskDeltaCommand:
    db: Database
    description: str
    before: dict[int, Task | None]
    after: dict[int, Task | None]
    before_templates: dict[int, tuple | None]
    after_templates: dict[int, tuple | None]

    @classmethod
    def from_states(
        cls,
        db: Database,
        description: str,
        before_state: dict[int, Task],
        after_state: dict[int, Task],
        before_templates: dict[int, tuple] | None = None,
        after_templates: dict[int, tuple] | None = None,
    ) -> "TaskDeltaCommand":
        ids = set(before_state) | set(after_state)
        before = {task_id: copy.deepcopy(before_state.get(task_id)) for task_id in ids}
        after = {task_id: copy.deepcopy(after_state.get(task_id)) for task_id in ids}
        bt = before_templates or {}
        at = after_templates or {}
        tids = set(bt) | set(at)
        before_t = {tid: copy.deepcopy(bt.get(tid)) for tid in tids}
        after_t = {tid: copy.deepcopy(at.get(tid)) for tid in tids}
        return cls(
            db=db,
            description=description,
            before=before,
            after=after,
            before_templates=before_t,
            after_templates=after_t,
        )

    def has_changes(self) -> bool:
        for task_id in set(self.before) | set(self.after):
            if self.before.get(task_id) != self.after.get(task_id):
                return True
        for template_id in set(self.before_templates) | set(self.after_templates):
            if self.before_templates.get(template_id) != self.after_templates.get(template_id):
                return True
        return False

    def _apply(self, state: dict[int, Task | None], template_state: dict[int, tuple | None]) -> None:
        with self.db.conn:
            for template_id, row in template_state.items():
                if row is None:
                    self.db.conn.execute("DELETE FROM recurrence_templates WHERE id=?", (template_id,))
                else:
                    self.db.insert_or_replace_recurrence_template_row(row)
            for task_id, task in state.items():
                if task is None:
                    self.db.conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
                else:
                    self.db.insert_or_replace_task(task)

    def execute(self) -> None:
        self._apply(self.after, self.after_templates)

    def rollback(self) -> None:
        self._apply(self.before, self.before_templates)


class UndoManager:
    def __init__(self) -> None:
        self._history: list[Command] = []
        self._cursor = 0

    def can_undo(self) -> bool:
        return self._cursor > 0

    def can_redo(self) -> bool:
        return self._cursor < len(self._history)

    def record(self, command: Command) -> None:
        if self._cursor < len(self._history):
            self._history = self._history[: self._cursor]
        self._history.append(command)
        self._cursor += 1

    def undo(self) -> Command | None:
        if not self.can_undo():
            return None
        cmd = self._history[self._cursor - 1]
        cmd.rollback()
        self._cursor -= 1
        return cmd

    def redo(self) -> Command | None:
        if not self.can_redo():
            return None
        cmd = self._history[self._cursor]
        cmd.execute()
        self._cursor += 1
        return cmd
