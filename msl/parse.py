"""Parsers for MyStudyLife's JSON.

Two payload shapes matter:

* `calendar` occurrences — what the timetable renders. `entity_type` is
  CLASS, EXTRA, TASK, EXAM or HOLIDAY. Classes carry `meta_data.title` and a
  `subject`, plus the recurring rule in `userSchedule` (days, rrule).
* `activities` — the task/exam list from the Tasks screen. Tasks carry
  `meta_data.title`, `due_date`, `completed`, `userSubTasks`; exams carry
  `meta_data.name`/`title` and a `start_time`.

Everything here is pure functions over parsed JSON, so the tests can use
hand-written dicts with the same field names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# MSL stores naive local times ("2026-11-18T09:40:00.464"), and the web app
# renders them as-is, so we do the same rather than fight over timezones.
DT_FORMATS = ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")


def parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    cleaned = value.strip().replace("Z", "")
    for fmt in DT_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def _meta(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("meta_data")
    return meta if isinstance(meta, dict) else {}


def _title(item: dict[str, Any]) -> str:
    """Classes use `title`, exams sometimes only `name`, extras use `name`."""
    meta = _meta(item)
    for key in ("title", "name", "module"):
        if meta.get(key):
            return str(meta[key]).strip()
    subject = item.get("subject")
    if isinstance(subject, dict) and subject.get("name"):
        return str(subject["name"]).strip()
    return ""


# ── timetable occurrences ────────────────────────────────────────────────

@dataclass
class Occurrence:
    id: str
    kind: str                       # CLASS / EXTRA / TASK / EXAM / HOLIDAY
    title: str
    start: datetime | None = None
    end: datetime | None = None
    subject: str = ""
    room: str = ""
    teacher: str = ""
    details: str = ""
    completed: bool = False

    @property
    def date(self) -> date | None:
        return self.start.date() if self.start else None

    @property
    def time_label(self) -> str:
        if not self.start:
            return ""
        if not self.end:
            return self.start.strftime("%-I:%M %p")
        return f"{self.start.strftime('%-I:%M %p')} – {self.end.strftime('%-I:%M %p')}"

    @property
    def duration_minutes(self) -> int:
        if not (self.start and self.end):
            return 0
        return int((self.end - self.start).total_seconds() // 60)


def parse_occurrence(item: dict[str, Any]) -> Occurrence:
    meta = _meta(item)
    subject = item.get("subject") if isinstance(item.get("subject"), dict) else {}
    kind = str(item.get("entity_type") or "").upper()
    title = _title(item) or kind.title()
    # extras keep a free-form details blob ("At the rac 530 to 7")
    details = str(meta.get("details") or meta.get("description") or "").strip()
    return Occurrence(
        id=str(item.get("id") or ""),
        kind=kind,
        title=title,
        start=parse_dt(item.get("start_time")),
        end=parse_dt(item.get("end_time")),
        subject=str(subject.get("name") or meta.get("module") or ""),
        room=str(meta.get("room") or ""),
        teacher=str(meta.get("teacher") or ""),
        details=details,
        completed=bool(item.get("completed")),
    )


def parse_calendar(items: list[dict[str, Any]]) -> list[Occurrence]:
    out = [parse_occurrence(i) for i in items if isinstance(i, dict)]
    out = [o for o in out if o.start is not None]
    out.sort(key=lambda o: (o.start, o.title))
    return out


def classes(occurrences: list[Occurrence]) -> list[Occurrence]:
    return [o for o in occurrences if o.kind == "CLASS"]


def on_day(occurrences: list[Occurrence], day: date) -> list[Occurrence]:
    return [o for o in occurrences if o.date == day]


# ── tasks & exams ────────────────────────────────────────────────────────

@dataclass
class Task:
    id: str                    # occurrence id: use for complete/uncomplete
    title: str
    schedule_id: str = ""      # schedule id: use for edit/delete
    due: datetime | None = None
    subject: str = ""
    completed: bool = False
    details: str = ""
    subtasks: list[str] = field(default_factory=list)
    kind: str = "TASK"
    external_id: str = ""
    source: str = ""

    @property
    def due_label(self) -> str:
        if not self.due:
            return "no date"
        return self.due.strftime("%a %b %-d")

    def _due_date(self) -> date | None:
        """Tasks from the API carry a datetime, but tolerate a bare date."""
        if self.due is None:
            return None
        return self.due.date() if isinstance(self.due, datetime) else self.due

    @property
    def overdue(self) -> bool:
        d = self._due_date()
        if self.completed or d is None:
            return False
        return d < date.today()

    @property
    def due_today(self) -> bool:
        return self._due_date() == date.today()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schedule_id": self.schedule_id,
            "title": self.title,
            "due": self.due.isoformat() if self.due else "",
            "subject": self.subject,
            "completed": self.completed,
            "details": self.details,
            "subtasks": self.subtasks,
            "kind": self.kind,
            "external_id": self.external_id,
            "source": self.source,
        }


def _subtask_titles(item: dict[str, Any]) -> list[str]:
    out = []
    for sub in item.get("userSubTasks") or []:
        if not isinstance(sub, dict):
            continue
        title = _meta(sub).get("title") or sub.get("title") or ""
        if title:
            out.append(str(title))
    return out


def parse_task(item: dict[str, Any]) -> Task:
    meta = _meta(item)
    subject = item.get("subject") if isinstance(item.get("subject"), dict) else {}
    due = parse_dt(item.get("due_date")) or parse_dt(item.get("start_time"))
    return Task(
        id=str(item.get("id") or ""),
        title=_title(item) or "(untitled)",
        schedule_id=str(item.get("schedule_id") or ""),
        due=due,
        subject=str(subject.get("name") or meta.get("module") or ""),
        completed=bool(item.get("completed")),
        details=str(meta.get("description") or meta.get("details") or "").strip(),
        subtasks=_subtask_titles(item),
        kind=str(item.get("entity_type") or "TASK").upper(),
        external_id=str(meta.get("external_id") or ""),
        source=str(meta.get("source") or ""),
    )


def parse_tasks(items: list[dict[str, Any]]) -> list[Task]:
    out = [parse_task(i) for i in items if isinstance(i, dict)]
    out.sort(key=lambda t: (t.due is None, t.due or datetime.max, t.title.lower()))
    return out


def open_tasks(tasks: list[Task]) -> list[Task]:
    return [t for t in tasks if not t.completed]


def group_by_day(tasks: list[Task]) -> dict[date, list[Task]]:
    groups: dict[date, list[Task]] = {}
    for t in tasks:
        if t.due:
            groups.setdefault(t.due.date(), []).append(t)
    return dict(sorted(groups.items()))


# ── dashboard ────────────────────────────────────────────────────────────

@dataclass
class DaySummary:
    counts: dict[str, int] = field(default_factory=dict)
    is_holiday: bool = False
    classes: list[Occurrence] = field(default_factory=list)


def parse_dashboard(data: dict[str, Any]) -> DaySummary:
    summary = DaySummary(
        counts={
            "classes": int(data.get("classCount") or 0),
            "exams": int(data.get("examCount") or 0),
            "tasks": int(data.get("taskCount") or 0),
            "events": int(data.get("icalCount") or 0),
        },
        is_holiday=bool(data.get("isHoliday")),
    )
    summary.classes = parse_calendar(data.get("schedules") or [])
    summary.classes.sort(key=lambda o: o.start or datetime.max)
    return summary


def weekday_timetable(occurrences: list[Occurrence], day: date) -> list[Occurrence]:
    """The recurring pattern for a weekday, lifted from one week's calendar.

    MSL expands recurrences per-day, so the Monday of any week shows the
    same Monday classes. This collapses a week into "what a Tuesday looks
    like", which is what a `timetable` command wants.
    """
    return sorted(on_day(occurrences, day), key=lambda o: o.start or datetime.max)


def now_next(occurrences: list[Occurrence], now: datetime | None = None) -> tuple[Occurrence | None, Occurrence | None]:
    """(current class, next class) for today, using naive local times."""
    now = now or datetime.now()
    today = [o for o in on_day(occurrences, now.date()) if o.start and o.end]
    current = next((o for o in today if o.start <= now <= o.end), None)
    upcoming = [o for o in today if o.start and o.start > now]
    return current, (upcoming[0] if upcoming else None)
