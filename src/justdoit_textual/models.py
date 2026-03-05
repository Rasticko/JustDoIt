from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from enum import Enum


class TaskStatus(str, Enum):
    TODO = "todo"
    COMPLETED = "completed"
    CANCELED = "canceled"


class SmartList(str, Enum):
    CALENDAR = "Calendar"
    INBOX = "Inbox"
    TODAY = "Today"
    UPCOMING = "Upcoming"
    ANYTIME = "Anytime"
    SOMEDAY = "Someday"
    LOGBOOK = "Logbook"
    TRASH = "Trash"


@dataclass
class Task:
    id: int
    order_index: int
    title: str
    notes_markdown: str
    tags: list[str] = field(default_factory=list)
    attendees: list[str] = field(default_factory=list)
    start_date: date | None = None
    deadline: date | None = None
    deadline_time: time | None = None
    recurrence_rule: str | None = None
    is_repeating_master: bool = False
    master_task_id: int | None = None
    instance_date: date | None = None
    status: TaskStatus = TaskStatus.TODO
    trashed: bool = False
    area_id: int | None = None
    project_id: int | None = None
    template_id: int | None = None


@dataclass
class Project:
    id: int
    area_id: int
    order_index: int
    title: str
    color: str | None = None
    notes: str = ""
    tasks: list[Task] = field(default_factory=list)


@dataclass
class Area:
    id: int
    title: str
    color: str | None = None
    projects: list[Project] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
