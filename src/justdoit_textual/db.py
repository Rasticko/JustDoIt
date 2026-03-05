from __future__ import annotations

import sqlite3
from datetime import date, time, timedelta
from pathlib import Path

from .models import Area, Project, SmartList, Task, TaskStatus


def _data_dir() -> Path:
    return Path.home() / ".local" / "share" / "justdoit-textual"


def db_path() -> Path:
    path = _data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path / "justdoit_textual.db"


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _parse_time(raw: str | None) -> time | None:
    if not raw:
        return None
    for fmt in ("%H:%M", "%H.%M"):
        try:
            return time.fromisoformat(raw.replace(".", ":")) if fmt == "%H.%M" else time.fromisoformat(raw)
        except ValueError:
            pass
    return None


def _tags_from_db(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def _tags_to_db(values: list[str]) -> str:
    return ",".join(v.strip() for v in values if v.strip())


class Database:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    @classmethod
    def open(cls) -> "Database":
        conn = sqlite3.connect(db_path())
        db = cls(conn)
        db._init_schema()
        db._seed_if_empty()
        return db

    def _init_schema(self) -> None:
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS areas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                color TEXT NULL
            );

            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                area_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                progress INTEGER NOT NULL DEFAULT 0,
                order_index INTEGER NOT NULL DEFAULT 0,
                color TEXT NULL,
                FOREIGN KEY (area_id) REFERENCES areas(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER,
                area_id INTEGER,
                template_id INTEGER,
                title TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                attendees TEXT NOT NULL DEFAULT '',
                start_date TEXT,
                deadline TEXT,
                deadline_time TEXT,
                recurrence_rule TEXT,
                is_repeating_master INTEGER NOT NULL DEFAULT 0,
                master_task_id INTEGER,
                instance_date TEXT,
                status TEXT NOT NULL CHECK(status IN ('todo','completed','canceled')),
                trashed INTEGER NOT NULL DEFAULT 0,
                order_index INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY (area_id) REFERENCES areas(id) ON DELETE CASCADE,
                FOREIGN KEY (template_id) REFERENCES recurrence_templates(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS recurrence_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                legacy_task_id INTEGER UNIQUE,
                area_id INTEGER,
                project_id INTEGER,
                title TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                attendees TEXT NOT NULL DEFAULT '',
                anchor_date TEXT NOT NULL,
                deadline_time TEXT,
                recurrence_rule TEXT NOT NULL,
                timezone TEXT NOT NULL DEFAULT 'local',
                enabled INTEGER NOT NULL DEFAULT 1,
                last_generated_at TEXT,
                FOREIGN KEY (area_id) REFERENCES areas(id) ON DELETE CASCADE,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            """
        )

        self._ensure_column("tasks", "attendees", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("tasks", "deadline_time", "TEXT")
        self._ensure_column("tasks", "recurrence_rule", "TEXT")
        self._ensure_column("tasks", "is_repeating_master", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("tasks", "master_task_id", "INTEGER")
        self._ensure_column("tasks", "instance_date", "TEXT")
        self._ensure_column("tasks", "template_id", "INTEGER")
        self._ensure_column("tasks", "order_index", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("projects", "order_index", "INTEGER NOT NULL DEFAULT 0")

        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_template_instance_unique ON tasks(template_id, instance_date) WHERE template_id IS NOT NULL AND instance_date IS NOT NULL"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_recurrence_templates_enabled ON recurrence_templates(enabled, anchor_date)"
        )

        self.conn.execute("UPDATE tasks SET order_index = id WHERE order_index = 0")
        self.conn.execute("UPDATE projects SET order_index = id WHERE order_index = 0")
        self.conn.commit()

    def _ensure_column(self, table: str, name: str, declaration: str) -> None:
        cols = {
            row[1]
            for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if name not in cols:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def _seed_if_empty(self) -> None:
        count = self.conn.execute("SELECT COUNT(*) FROM areas").fetchone()[0]
        if count:
            return

        work = self.insert_area("Work")
        personal = self.insert_area("Personal")
        launch = self.insert_project(work, "Q2 Launch")

        today = date.today()
        self.insert_task(
            title="Capture MVP polish ideas",
            notes="- tighten onboarding\n- improve keybinding hints",
            tags=["work"],
            attendees=[],
            deadline=today,
            deadline_time=None,
            recurrence_rule=None,
            area_id=None,
            project_id=None,
        )
        self.insert_task(
            title="Finalize migration guide",
            notes="Include rollback strategy",
            tags=["docs"],
            attendees=[],
            deadline=today,
            deadline_time=None,
            recurrence_rule=None,
            area_id=work,
            project_id=launch,
        )
        self.insert_task(
            title="Plan summer trip",
            notes="Look at train routes",
            tags=["someday"],
            attendees=[],
            deadline=None,
            deadline_time=None,
            recurrence_rule=None,
            area_id=personal,
            project_id=None,
        )

    def load_state(self) -> tuple[list[Task], list[Area]]:
        areas_rows = self.conn.execute("SELECT id, name, color FROM areas ORDER BY id ASC").fetchall()
        areas = [Area(id=r[0], title=r[1], color=r[2], projects=[], tasks=[]) for r in areas_rows]
        area_idx_by_id = {a.id: idx for idx, a in enumerate(areas)}

        project_rows = self.conn.execute(
            "SELECT id, area_id, name, color, notes, order_index FROM projects ORDER BY order_index ASC, id ASC"
        ).fetchall()
        project_idx_by_id: dict[int, tuple[int, int]] = {}
        for row in project_rows:
            project = Project(
                id=row[0],
                area_id=row[1],
                title=row[2],
                color=row[3],
                notes=row[4],
                order_index=row[5],
                tasks=[],
            )
            if row[1] in area_idx_by_id:
                aidx = area_idx_by_id[row[1]]
                pidx = len(areas[aidx].projects)
                areas[aidx].projects.append(project)
                project_idx_by_id[project.id] = (aidx, pidx)

        task_rows = self.conn.execute(
            """
            SELECT id, order_index, project_id, area_id, template_id, title, notes, tags, attendees, start_date, deadline, deadline_time, recurrence_rule, is_repeating_master, master_task_id, instance_date, status, trashed
            FROM tasks
            ORDER BY order_index ASC, id ASC
            """
        ).fetchall()

        inbox: list[Task] = []
        for row in task_rows:
            status_raw = row[16] or "todo"
            try:
                status = TaskStatus(status_raw)
            except ValueError:
                status = TaskStatus.TODO

            task = Task(
                id=row[0],
                order_index=row[1],
                project_id=row[2],
                area_id=row[3],
                template_id=row[4],
                title=row[5],
                notes_markdown=row[6] or "",
                tags=_tags_from_db(row[7] or ""),
                attendees=_tags_from_db(row[8] or ""),
                start_date=_parse_date(row[9]),
                deadline=_parse_date(row[10]),
                deadline_time=_parse_time(row[11]),
                recurrence_rule=row[12] or None,
                is_repeating_master=bool(row[13]),
                master_task_id=row[14],
                instance_date=_parse_date(row[15]),
                status=status,
                trashed=bool(row[17]),
            )

            if task.project_id is not None and task.project_id in project_idx_by_id:
                aidx, pidx = project_idx_by_id[task.project_id]
                areas[aidx].projects[pidx].tasks.append(task)
            elif task.area_id is not None and task.area_id in area_idx_by_id:
                areas[area_idx_by_id[task.area_id]].tasks.append(task)
            else:
                inbox.append(task)

        return inbox, areas

    def list_tasks_for_sidebar(
        self,
        inbox: list[Task],
        areas: list[Area],
        item: tuple[str, int | str | tuple[int, int]],
        today: date,
    ) -> list[Task]:
        all_tasks = self._flatten_tasks(inbox, areas)
        template_enabled = {
            int(row[0]): bool(row[1])
            for row in self.conn.execute("SELECT id, enabled FROM recurrence_templates").fetchall()
        }
        kind, payload = item
        if kind == "smart":
            smart = SmartList(str(payload))
            def is_someday_state(task: Task) -> bool:
                return (
                    "someday" in [x.lower() for x in task.tags]
                    and task.start_date is None
                    and task.deadline is None
                )

            def matches(task: Task) -> bool:
                someday_state = is_someday_state(task)
                if task.is_repeating_master:
                    return smart is SmartList.TRASH and task.trashed
                if task.template_id is not None and task.status == TaskStatus.TODO:
                    if not template_enabled.get(int(task.template_id), True):
                        return smart in {SmartList.TRASH} and task.trashed

                if smart is SmartList.CALENDAR:
                    return False
                if smart is SmartList.INBOX:
                    return (
                        task in inbox
                        and not task.trashed
                        and task.status == TaskStatus.TODO
                        and task.start_date is None
                        and task.deadline is None
                        and not someday_state
                    )
                if smart is SmartList.TODAY:
                    return (
                        not task.trashed
                        and task.status == TaskStatus.TODO
                        and not someday_state
                        and task.deadline == today
                    )
                if smart is SmartList.UPCOMING:
                    return (
                        not task.trashed
                        and task.status == TaskStatus.TODO
                        and not someday_state
                        and (task.deadline is not None and task.deadline > today)
                    )
                if smart is SmartList.ANYTIME:
                    return (
                        not task.trashed
                        and task.status == TaskStatus.TODO
                        and task.deadline is None
                        and not someday_state
                    )
                if smart is SmartList.SOMEDAY:
                    return (
                        not task.trashed
                        and task.status == TaskStatus.TODO
                        and someday_state
                    )
                if smart is SmartList.LOGBOOK:
                    return (
                        not task.trashed
                        and task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELED}
                    )
                return task.trashed

            tasks = [t for t in all_tasks if matches(t)]
            if smart is SmartList.UPCOMING:
                return sorted(
                    tasks,
                    key=lambda t: (
                        self._next_occurrence_for_master(t, today) or date.max,
                        t.deadline_time or time.min,
                        t.order_index,
                        t.id,
                    ),
                )
            return sorted(tasks, key=lambda t: (t.deadline or date.max, t.deadline_time or time.min, t.order_index, t.id))

        if kind == "area":
            area_id = int(payload)
            area = next((a for a in areas if a.id == area_id), None)
            if not area:
                return []
            tasks = [t for t in area.tasks if not t.trashed and not t.is_repeating_master]
            for p in area.projects:
                tasks.extend([t for t in p.tasks if not t.trashed and not t.is_repeating_master])
            return sorted(tasks, key=lambda t: (t.order_index, t.id))

        if kind == "project":
            project_id = int(payload)
            for area in areas:
                for project in area.projects:
                    if project.id == project_id:
                        return [t for t in project.tasks if not t.trashed and not t.is_repeating_master]
            return []

        return []

    def _flatten_tasks(self, inbox: list[Task], areas: list[Area]) -> list[Task]:
        out = list(inbox)
        for area in areas:
            out.extend(area.tasks)
            for project in area.projects:
                out.extend(project.tasks)
        return out

    def _next_occurrence_for_master(self, task: Task, today: date) -> date | None:
        if not task.is_repeating_master or not task.deadline or not task.recurrence_rule:
            return task.deadline
        return self._next_recurrence_after(task.deadline, today, task.recurrence_rule)

    def _next_recurrence_after(self, anchor: date, today: date, rule: str) -> date | None:
        # Next occurrence strictly after `today` for Upcoming display/sort.
        cursor = today
        if rule == "daily":
            if cursor < anchor:
                return anchor
            return cursor + timedelta(days=1)
        if rule == "weekdays":
            d = (anchor - timedelta(days=1)) if cursor < anchor else cursor
            d = d + timedelta(days=1)
            while d.weekday() >= 5:
                d += timedelta(days=1)
            return d
        if rule == "weekly":
            if cursor < anchor:
                return anchor
            days = (cursor - anchor).days
            weeks = (days // 7) + 1
            return anchor + timedelta(days=weeks * 7)
        if rule == "monthly":
            d = date(cursor.year, cursor.month, 1)
            while True:
                candidate = self._month_day_with_carry(d.year, d.month, anchor.day)
                if candidate > cursor and candidate >= anchor:
                    return candidate
                if d.month == 12:
                    d = date(d.year + 1, 1, 1)
                else:
                    d = date(d.year, d.month + 1, 1)
        if rule == "yearly":
            year = cursor.year
            while True:
                candidate = self._year_day_with_carry(year, anchor.month, anchor.day)
                if candidate > cursor and candidate >= anchor:
                    return candidate
                year += 1
        return None

    def _month_day_with_carry(self, year: int, month: int, day: int) -> date:
        last = self._last_day_of_month(year, month)
        return date(year, month, min(day, last))

    def _year_day_with_carry(self, year: int, month: int, day: int) -> date:
        try:
            return date(year, month, day)
        except ValueError:
            # Feb 29 -> Feb 28 on non-leap years.
            return date(year, month, self._last_day_of_month(year, month))

    def _last_day_of_month(self, year: int, month: int) -> int:
        if month == 12:
            nxt = date(year + 1, 1, 1)
        else:
            nxt = date(year, month + 1, 1)
        return (nxt - timedelta(days=1)).day

    def insert_area(self, name: str) -> int:
        cur = self.conn.execute("INSERT INTO areas(name, color) VALUES (?, NULL)", (name,))
        self.conn.commit()
        return int(cur.lastrowid)

    def rename_area(self, area_id: int, name: str) -> None:
        self.conn.execute("UPDATE areas SET name=? WHERE id=?", (name, area_id))
        self.conn.commit()

    def insert_project(self, area_id: int, name: str) -> int:
        next_order = self.conn.execute(
            "SELECT COALESCE(MAX(order_index),0)+1 FROM projects WHERE area_id=?", (area_id,)
        ).fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO projects(area_id, name, notes, progress, order_index, color) VALUES (?, ?, '', 0, ?, NULL)",
            (area_id, name, next_order),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def rename_project(self, project_id: int, name: str) -> None:
        self.conn.execute("UPDATE projects SET name=? WHERE id=?", (name, project_id))
        self.conn.commit()

    def insert_task(
        self,
        *,
        title: str,
        notes: str,
        tags: list[str],
        attendees: list[str],
        deadline: date | None,
        deadline_time: time | None,
                recurrence_rule: str | None,
                status: TaskStatus | str = TaskStatus.TODO,
                trashed: bool = False,
                template_id: int | None = None,
                area_id: int | None = None,
                project_id: int | None = None,
                master_task_id: int | None = None,
                instance_date: date | None = None,
                is_repeating_master: bool | None = None,
    ) -> int:
        next_order = self.conn.execute(
            "SELECT COALESCE(MAX(order_index),0)+1 FROM tasks WHERE project_id IS ? AND area_id IS ?",
            (project_id, area_id),
        ).fetchone()[0]
        status_value = status.value if isinstance(status, TaskStatus) else str(status)
        cur = self.conn.execute(
            """
            INSERT INTO tasks(project_id, area_id, template_id, title, notes, tags, attendees, start_date, deadline, deadline_time, recurrence_rule, is_repeating_master, master_task_id, instance_date, status, trashed, order_index)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                area_id,
                template_id,
                title,
                notes,
                _tags_to_db(tags),
                _tags_to_db(attendees),
                deadline.isoformat() if deadline else None,
                deadline_time.strftime("%H:%M") if deadline_time else None,
                recurrence_rule,
                1 if is_repeating_master is True or (is_repeating_master is None and recurrence_rule) else 0,
                master_task_id,
                instance_date.isoformat() if instance_date else None,
                status_value,
                1 if trashed else 0,
                next_order,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_task(self, task: Task) -> None:
        self.conn.execute(
            """
            UPDATE tasks
            SET project_id=?, area_id=?, template_id=?, title=?, notes=?, tags=?, attendees=?, start_date=?, deadline=?, deadline_time=?, recurrence_rule=?, is_repeating_master=?, master_task_id=?, instance_date=?, status=?, trashed=?, order_index=?
            WHERE id=?
            """,
            (
                task.project_id,
                task.area_id,
                task.template_id,
                task.title,
                task.notes_markdown,
                _tags_to_db(task.tags),
                _tags_to_db(task.attendees),
                task.start_date.isoformat() if task.start_date else None,
                task.deadline.isoformat() if task.deadline else None,
                task.deadline_time.strftime("%H:%M") if task.deadline_time else None,
                task.recurrence_rule,
                1 if task.recurrence_rule else 0,
                task.master_task_id,
                task.instance_date.isoformat() if task.instance_date else None,
                task.status.value,
                1 if task.trashed else 0,
                task.order_index,
                task.id,
            ),
        )
        self.conn.commit()

    def insert_or_replace_task(self, task: Task) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO tasks(
                id, project_id, area_id, template_id, title, notes, tags, attendees, start_date,
                deadline, deadline_time, recurrence_rule, is_repeating_master,
                master_task_id, instance_date, status, trashed, order_index
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.id,
                task.project_id,
                task.area_id,
                task.template_id,
                task.title,
                task.notes_markdown,
                _tags_to_db(task.tags),
                _tags_to_db(task.attendees),
                task.start_date.isoformat() if task.start_date else None,
                task.deadline.isoformat() if task.deadline else None,
                task.deadline_time.strftime("%H:%M") if task.deadline_time else None,
                task.recurrence_rule,
                1 if task.is_repeating_master else 0,
                task.master_task_id,
                task.instance_date.isoformat() if task.instance_date else None,
                task.status.value,
                1 if task.trashed else 0,
                task.order_index,
            ),
        )
        self.conn.commit()

    def insert_recurrence_template(
        self,
        *,
        title: str,
        notes: str,
        tags: list[str],
        attendees: list[str],
        anchor_date: date,
        deadline_time: time | None,
        recurrence_rule: str,
        area_id: int | None,
        project_id: int | None,
        legacy_task_id: int | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO recurrence_templates(
                legacy_task_id, area_id, project_id, title, notes, tags, attendees,
                anchor_date, deadline_time, recurrence_rule, timezone, enabled, last_generated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'local', 1, NULL)
            """,
            (
                legacy_task_id,
                area_id,
                project_id,
                title,
                notes,
                _tags_to_db(tags),
                _tags_to_db(attendees),
                anchor_date.isoformat(),
                deadline_time.strftime("%H:%M") if deadline_time else None,
                recurrence_rule,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def load_recurrence_templates_state(self) -> dict[int, tuple]:
        rows = self.conn.execute(
            """
            SELECT id, legacy_task_id, area_id, project_id, title, notes, tags, attendees,
                   anchor_date, deadline_time, recurrence_rule, timezone, enabled, last_generated_at
            FROM recurrence_templates
            """
        ).fetchall()
        return {int(row[0]): tuple(row) for row in rows}

    def insert_or_replace_recurrence_template_row(self, row: tuple) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO recurrence_templates(
                id, legacy_task_id, area_id, project_id, title, notes, tags, attendees,
                anchor_date, deadline_time, recurrence_rule, timezone, enabled, last_generated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        self.conn.commit()

    def get_recurrence_template(self, template_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT id, legacy_task_id, area_id, project_id, title, notes, tags, attendees,
                   anchor_date, deadline_time, recurrence_rule, timezone, enabled, last_generated_at
            FROM recurrence_templates
            WHERE id=?
            """,
            (template_id,),
        ).fetchone()

    def update_recurrence_template(
        self,
        template_id: int,
        *,
        title: str,
        notes: str,
        tags: list[str],
        attendees: list[str],
        anchor_date: date,
        deadline_time: time | None,
        recurrence_rule: str,
        area_id: int | None,
        project_id: int | None,
        enabled: bool,
    ) -> None:
        self.conn.execute(
            """
            UPDATE recurrence_templates
            SET area_id=?, project_id=?, title=?, notes=?, tags=?, attendees=?,
                anchor_date=?, deadline_time=?, recurrence_rule=?, enabled=?
            WHERE id=?
            """,
            (
                area_id,
                project_id,
                title,
                notes,
                _tags_to_db(tags),
                _tags_to_db(attendees),
                anchor_date.isoformat(),
                deadline_time.strftime("%H:%M") if deadline_time else None,
                recurrence_rule,
                1 if enabled else 0,
                template_id,
            ),
        )
        self.conn.commit()

    def set_recurrence_template_enabled(self, template_id: int, enabled: bool) -> None:
        self.conn.execute(
            "UPDATE recurrence_templates SET enabled=? WHERE id=?",
            (1 if enabled else 0, template_id),
        )
        self.conn.commit()

    def set_tasks_trashed_for_template(self, template_id: int, trashed: bool) -> None:
        self.conn.execute(
            "UPDATE tasks SET trashed=? WHERE template_id=?",
            (1 if trashed else 0, template_id),
        )
        self.conn.commit()

    def migrate_legacy_repeating_masters(self) -> int:
        rows = self.conn.execute(
            """
            SELECT id, area_id, project_id, title, notes, tags, attendees, deadline, deadline_time, recurrence_rule
            FROM tasks
            WHERE is_repeating_master=1 AND recurrence_rule IS NOT NULL AND deadline IS NOT NULL
            """
        ).fetchall()
        created = 0
        with self.conn:
            for row in rows:
                exists = self.conn.execute(
                    "SELECT id FROM recurrence_templates WHERE legacy_task_id=? LIMIT 1",
                    (row[0],),
                ).fetchone()
                if exists is not None:
                    continue
                self.conn.execute(
                    """
                    INSERT INTO recurrence_templates(
                        legacy_task_id, area_id, project_id, title, notes, tags, attendees,
                        anchor_date, deadline_time, recurrence_rule, timezone, enabled, last_generated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'local', 1, NULL)
                    """,
                    (
                        row[0],
                        row[1],
                        row[2],
                        row[3],
                        row[4] or "",
                        row[5] or "",
                        row[6] or "",
                        row[7],
                        row[8],
                        row[9],
                    ),
                )
                created += 1
        return created

    def materialize_recurrence_instances(self, today: date, *, horizon_days: int = 400) -> int:
        templates = self.conn.execute(
            """
            SELECT id, area_id, project_id, title, notes, tags, attendees, anchor_date, deadline_time, recurrence_rule, enabled
            FROM recurrence_templates
            WHERE enabled=1
            """
        ).fetchall()
        inserted = 0
        end = today + timedelta(days=horizon_days)
        with self.conn:
            for row in templates:
                template_id = int(row[0])
                area_id = row[1]
                project_id = row[2]
                title = row[3]
                notes = row[4] or ""
                tags = _tags_from_db(row[5] or "")
                attendees = _tags_from_db(row[6] or "")
                anchor = _parse_date(row[7])
                deadline_time = _parse_time(row[8])
                rule = row[9] or ""
                if anchor is None:
                    continue
                has_open = self.conn.execute(
                    "SELECT 1 FROM tasks WHERE template_id=? AND status='todo' AND trashed=0 LIMIT 1",
                    (template_id,),
                ).fetchone()
                if has_open is not None:
                    # Keep one active occurrence per template (Things-style behavior).
                    continue
                for due in self._iter_occurrences(anchor, rule, start=today, end=end):
                    exists = self.conn.execute(
                        "SELECT 1 FROM tasks WHERE template_id=? AND instance_date=? LIMIT 1",
                        (template_id, due.isoformat()),
                    ).fetchone()
                    if exists is not None:
                        continue
                    next_order = self.conn.execute(
                        "SELECT COALESCE(MAX(order_index),0)+1 FROM tasks WHERE project_id IS ? AND area_id IS ?",
                        (project_id, area_id),
                    ).fetchone()[0]
                    self.conn.execute(
                        """
                        INSERT INTO tasks(project_id, area_id, template_id, title, notes, tags, attendees, start_date, deadline, deadline_time, recurrence_rule, is_repeating_master, master_task_id, instance_date, status, trashed, order_index)
                        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, 0, NULL, ?, 'todo', 0, ?)
                        """,
                        (
                            project_id,
                            area_id,
                            template_id,
                            title,
                            notes,
                            _tags_to_db([t for t in tags if t.lower() != "someday"]),
                            _tags_to_db(attendees),
                            due.isoformat(),
                            deadline_time.strftime("%H:%M") if deadline_time else None,
                            due.isoformat(),
                            next_order,
                        ),
                    )
                    inserted += 1
                    break
        return inserted

    def _iter_occurrences(self, anchor: date, rule: str, *, start: date, end: date) -> list[date]:
        if end < start:
            return []
        out: list[date] = []
        cursor = anchor
        if rule == "daily":
            if cursor < start:
                delta = (start - cursor).days
                cursor = cursor + timedelta(days=delta)
            while cursor <= end:
                out.append(cursor)
                cursor += timedelta(days=1)
            return out
        if rule == "weekdays":
            if cursor < start:
                cursor = start
            while cursor <= end:
                if cursor.weekday() < 5:
                    out.append(cursor)
                cursor += timedelta(days=1)
            return out
        if rule == "weekly":
            if cursor < start:
                delta = (start - cursor).days
                weeks = delta // 7
                cursor = cursor + timedelta(days=weeks * 7)
                while cursor < start:
                    cursor += timedelta(days=7)
            while cursor <= end:
                out.append(cursor)
                cursor += timedelta(days=7)
            return out
        if rule == "monthly":
            y, m = anchor.year, anchor.month
            while True:
                d = min(anchor.day, self._last_day_of_month(y, m))
                candidate = date(y, m, d)
                if candidate > end:
                    break
                if candidate >= start and candidate >= anchor:
                    out.append(candidate)
                if m == 12:
                    y += 1
                    m = 1
                else:
                    m += 1
            return out
        if rule == "yearly":
            y = anchor.year
            while True:
                d = min(anchor.day, self._last_day_of_month(y, anchor.month))
                candidate = date(y, anchor.month, d)
                if candidate > end:
                    break
                if candidate >= start and candidate >= anchor:
                    out.append(candidate)
                y += 1
            return out
        return out

    def _last_day_of_month(self, year: int, month: int) -> int:
        if month == 12:
            nxt = date(year + 1, 1, 1)
        else:
            nxt = date(year, month + 1, 1)
        return (nxt - timedelta(days=1)).day

    def instance_exists_for_master_on_date(self, master_task_id: int, on_date: date) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM tasks WHERE master_task_id=? AND instance_date=? LIMIT 1",
            (master_task_id, on_date.isoformat()),
        ).fetchone()
        return row is not None

    def toggle_task(self, task: Task) -> None:
        task.status = TaskStatus.COMPLETED if task.status is TaskStatus.TODO else TaskStatus.TODO
        self.update_task(task)

    def set_task_trashed(self, task: Task, trashed: bool) -> None:
        task.trashed = trashed
        self.conn.execute("UPDATE tasks SET trashed=? WHERE id=?", (1 if trashed else 0, task.id))
        self.conn.commit()

    def delete_task_permanently(self, task_id: int) -> None:
        self.conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        self.conn.commit()

    def delete_project_to_trash(self, project_id: int) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE tasks
                SET trashed = CASE WHEN status = 'todo' THEN 1 ELSE 0 END,
                    project_id = NULL
                WHERE project_id = ?
                """,
                (project_id,),
            )
            self.conn.execute("DELETE FROM projects WHERE id=?", (project_id,))

    def delete_area_to_trash(self, area_id: int) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE tasks
                SET trashed = CASE WHEN status = 'todo' THEN 1 ELSE 0 END,
                    project_id = NULL,
                    area_id = NULL
                WHERE area_id = ?
                   OR project_id IN (SELECT id FROM projects WHERE area_id = ?)
                """,
                (area_id, area_id),
            )
            self.conn.execute("DELETE FROM areas WHERE id=?", (area_id,))

    def swap_task_order(self, first_task_id: int, second_task_id: int) -> None:
        first = self.conn.execute("SELECT order_index FROM tasks WHERE id=?", (first_task_id,)).fetchone()
        second = self.conn.execute("SELECT order_index FROM tasks WHERE id=?", (second_task_id,)).fetchone()
        if first is None or second is None:
            return
        with self.conn:
            self.conn.execute("UPDATE tasks SET order_index=? WHERE id=?", (second[0], first_task_id))
            self.conn.execute("UPDATE tasks SET order_index=? WHERE id=?", (first[0], second_task_id))

    def close(self) -> None:
        self.conn.close()
