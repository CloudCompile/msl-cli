"""Sample MyStudyLife payloads with the field names the live API uses.

Times are naive local strings ("2026-11-18T09:40:00.464"), matching what
MSL returns. The calendar below is generated relative to "today" so tests
never depend on the current date.
"""

from __future__ import annotations

from datetime import date, timedelta

USER = {
    "id": "user-1",
    "email": "student@example.com",
    "first_name": "Test",
    "last_name": "Student",
    "login_flow": True,
    "email_verified": True,
    "provider": "email",
    "is_subscribed": False,
    "using_password": True,
    "role": "USER",
}

SUBJECTS = {"math": "Mathematics", "bio": "Biology", "eng": "English"}

# (minutes from midnight start, minutes end, title, subject, room)
PERIODS = [
    (8 * 60 + 50, 9 * 60 + 35, "Algebra I", "math", "101"),
    (9 * 60 + 40, 10 * 60 + 25, "Biology", "bio", "Lab 2"),
    (11 * 60 + 15, 12 * 60 + 0, "Lunch", None, "Cafeteria"),
    (12 * 60 + 5, 12 * 60 + 50, "English 10", "eng", "303"),
]


def _stamp(day: date, minutes: int) -> str:
    return f"{day.isoformat()}T{minutes // 60:02d}:{minutes % 60:02d}:00.000"


def occurrence(day: date, minutes: int, end_minutes: int, title: str, subject: str | None,
               room: str, kind: str = "CLASS", idx: int = 0) -> dict:
    meta = {"title": title}
    if subject:
        meta["module"] = SUBJECTS[subject]
    if room:
        meta["room"] = room
    return {
        "id": f"{kind.lower()}-{day.isoformat()}-{idx}",
        "schedule_id": f"sched-{kind.lower()}-{idx}",
        "start_time": _stamp(day, minutes),
        "end_time": _stamp(day, end_minutes),
        "user_id": "user-1",
        "subject_id": f"subj-{subject}" if subject else None,
        "academic": subject is not None,
        "entity_type": kind,
        "meta_data": meta,
        "completed": False,
        "subject": {"name": SUBJECTS[subject]} if subject else None,
        "userSubTasks": [],
        "userSchedule": {"days": ["MON", "TUE", "WED", "THU", "FRI"], "type": "WEEKLY"},
    }


def day_occurrences(day: date) -> list[dict]:
    out = []
    for i, (start, end, title, subject, room) in enumerate(PERIODS):
        out.append(occurrence(day, start, end, title, subject, room, idx=i))
    # one recurring non-academic activity, 5:30pm
    out.append({
        "id": f"extra-{day.isoformat()}",
        "start_time": _stamp(day, 17 * 60 + 30),
        "end_time": _stamp(day, 19 * 60),
        "entity_type": "EXTRA",
        "meta_data": {"name": "Tennis", "details": "At the rec center", "eventType": "nonAcademic"},
        "subject": None,
        "userSubTasks": [],
    })
    return out


def calendar_between(start: str, end: str) -> list[dict]:
    """end is exclusive, as the real API behaves."""
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    out = []
    day = first
    while day < last:
        if day.weekday() < 5:          # weekdays only
            out.extend(day_occurrences(day))
        day += timedelta(days=1)
    if len(out) <= 1 and first == last - timedelta(days=1):
        pass
    return out


DASHBOARD = {
    # note: the real dashboard omits the first period; tests use the
    # calendar for class lists, and this only supplies counts.
    "schedules": day_occurrences(date.today())[1:],
    "classCount": 3,
    "examCount": 1,
    "taskCount": 2,
    "icalCount": 0,
    "isHoliday": False,
    "ical": [],
}

TODAY = date.today()


def _task(idx: int, title: str, due: date, completed: bool = False, subject: str | None = "math",
          subtasks: list[str] | None = None) -> dict:
    return {
        "id": f"task-{idx}",
        "schedule_id": f"sched-{idx}",
        "due_date": f"{due.isoformat()}T00:00:00.000",
        "start_time": f"{due.isoformat()}T00:00:00.000",
        "end_time": None,
        "completed": completed,
        "entity_type": "TASK",
        "subject_id": f"subj-{subject}" if subject else None,
        "meta_data": {"title": title, "type": "revision",
                      **({"description": "worth 20%"} if idx == 2 else {})},
        "subject": {"name": SUBJECTS[subject]} if subject else None,
        "userSubTasks": [{"meta_data": {"title": t}} for t in (subtasks or [])],
    }


def create_task(body: dict) -> dict:
    """Mirror the real creation response: the returned id is a *schedule* id.

    The occurrence id (what `/user-calendar/activities` lists and what
    completion needs) is generated separately, which is exactly the mismatch
    that made a naive client 500 on completion.
    """
    n = len(body) + abs(hash((body.get("meta_data") or {}).get("title", ""))) % 1000
    due = body.get("due_date") or ""
    return {
        # deliberately different: occurrence id vs schedule id
        "id": f"occurrence-{n}",
        "schedule_id": f"schedule-{n}",
        "due_date": due,
        "start_time": due or None,
        "end_time": body.get("end_time"),
        "completed": False,
        "entity_type": "TASK",
        "subject_id": body.get("subject_id"),
        "subject": ({"name": SUBJECTS["bio"]} if body.get("subject_id") else None),
        "meta_data": body.get("meta_data") or {},
        "userSubTasks": [],
    }


def _exam(idx: int, title: str, start: str, subject: str) -> dict:
    return {
        "id": f"exam-{idx}",
        "schedule_id": f"exam-sched-{idx}",
        "due_date": start,
        "start_time": start,
        "end_time": None,
        "completed": False,
        "entity_type": "EXAM",
        "subject_id": "subj-1",
        "meta_data": {"name": title, "title": title},
        "subject": {"name": SUBJECTS[subject]},
        "userSubTasks": [],
    }


ACTIVITIES = {
    "TASK": [
        _task(1, "Worksheet 3.2", TODAY - timedelta(days=2)),              # overdue
        _task(2, "Lab report", TODAY, subtasks=["Methods", "Results"]),    # due today
        _task(3, "Essay draft", TODAY + timedelta(days=3), subject="eng"),
        _task(4, "Old quiz corrections", TODAY - timedelta(days=9), completed=True),
    ],
    "EXAM": [
        _exam(1, "Midterm", f"{(TODAY + timedelta(days=5)).isoformat()}T09:00:00.000", "bio"),
    ],
    "HOLIDAY": [],
    "EXTRA": [],
}
