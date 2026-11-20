"""Parser tests: real payload shapes, no network."""
from datetime import date, datetime, timedelta

from fixtures import data
from msl import parse

TODAY = date.today()
NEXT_MONDAY = TODAY + timedelta(days=(7 - TODAY.weekday()) % 7 or 7)


def test_parse_dt_handles_msl_formats():
    assert parse.parse_dt("2026-11-18T09:40:00.464") == datetime(2026, 11, 18, 9, 40, 0, 464000)
    assert parse.parse_dt("2026-11-18T09:40:00") == datetime(2026, 11, 18, 9, 40)
    assert parse.parse_dt("2026-11-18 09:40:00") == datetime(2026, 11, 18, 9, 40)
    assert parse.parse_dt("") is None
    assert parse.parse_dt(None) is None
    assert parse.parse_dt("not a date") is None


def test_class_occurrence_fields():
    occ = parse.parse_occurrence(data.day_occurrences(TODAY)[0])
    assert occ.kind == "CLASS"
    assert occ.title == "Algebra I"
    assert occ.subject == "Mathematics"
    assert occ.room == "101"
    assert occ.time_label == "8:50 AM – 9:35 AM"
    assert occ.duration_minutes == 45
    assert occ.date == TODAY


def test_calendar_sorting_and_day_filter():
    items = parse.parse_calendar(data.calendar_between(TODAY.isoformat(),
                                                       (TODAY + timedelta(days=7)).isoformat()))
    starts = [o.start for o in items]
    assert starts == sorted(starts)
    todays = parse.on_day(items, TODAY)
    assert [o.title for o in todays][:2] == ["Algebra I", "Biology"]
    assert all(o.date == TODAY for o in todays)


def test_extra_event_keeps_details():
    extra = [o for o in parse.parse_calendar(data.day_occurrences(TODAY)) if o.kind == "EXTRA"][0]
    assert extra.title == "Tennis"
    assert extra.details == "At the rec center"
    assert extra.time_label == "5:30 PM – 7:00 PM"


def test_classes_filter():
    occ = parse.parse_calendar(data.day_occurrences(TODAY))
    assert [o.title for o in parse.classes(occ)] == ["Algebra I", "Biology", "Lunch", "English 10"]


def test_task_flags():
    tasks = parse.parse_tasks(data.ACTIVITIES["TASK"])
    by_name = {t.title: t for t in tasks}
    assert by_name["Worksheet 3.2"].overdue
    assert by_name["Lab report"].due_today
    assert not by_name["Essay draft"].overdue
    assert by_name["Old quiz corrections"].completed
    assert not by_name["Old quiz corrections"].overdue, "completed work is never overdue"
    assert by_name["Lab report"].subtasks == ["Methods", "Results"]
    assert by_name["Lab report"].subject == "Mathematics"


def test_task_sorting_and_open_filter():
    tasks = parse.parse_tasks(data.ACTIVITIES["TASK"])
    dues = [t.due for t in tasks if t.due]
    assert dues == sorted(dues)
    assert {t.title for t in parse.open_tasks(tasks)} == {
        "Worksheet 3.2", "Lab report", "Essay draft"}


def test_group_by_day():
    groups = parse.group_by_day(parse.parse_tasks(data.ACTIVITIES["TASK"]))
    assert list(groups) == sorted(groups)
    assert [t.title for t in groups[TODAY]] == ["Lab report"]


def test_undated_task_sorts_last_and_has_no_label():
    raw = [{"id": "x", "meta_data": {"title": "No deadline"}, "entity_type": "TASK"}]
    task = parse.parse_tasks(raw)[0]
    assert task.due is None
    assert task.due_label == "no date"
    assert not task.overdue and not task.due_today


def test_exam_parsing():
    exam = parse.parse_tasks(data.ACTIVITIES["EXAM"])[0]
    assert exam.kind == "EXAM"
    assert exam.title == "Midterm"
    assert exam.subject == "Biology"
    assert exam.due.hour == 9


def test_dashboard_counts():
    summary = parse.parse_dashboard(data.DASHBOARD)
    assert summary.counts == {"classes": 3, "exams": 1, "tasks": 2, "events": 0}
    assert summary.is_holiday is False
    assert len(summary.classes) == 4


def test_now_next():
    now = datetime.combine(TODAY, datetime.min.time()).replace(hour=8, minute=55)
    current, nxt = parse.now_next(parse.parse_calendar(data.day_occurrences(TODAY)), now)
    assert current and current.title == "Algebra I"
    assert nxt and nxt.title == "Biology"

    gaptime = now.replace(hour=10, minute=30)
    current, nxt = parse.now_next(parse.parse_calendar(data.day_occurrences(TODAY)), gaptime)
    assert current is None
    assert nxt and nxt.title == "Lunch"


def test_weekday_timetable():
    day = NEXT_MONDAY
    items = parse.parse_calendar(data.day_occurrences(day))
    assert [o.title for o in parse.weekday_timetable(items, day)][0] == "Algebra I"


def test_title_fallbacks():
    classy = {"entity_type": "CLASS", "meta_data": {}, "subject": {"name": "Chemistry"}}
    named = {"entity_type": "EXAM", "meta_data": {"name": "Final"}}
    blank = {"entity_type": "TASK", "meta_data": {}}
    assert parse._title(classy) == "Chemistry"
    assert parse._title(named) == "Final"
    assert parse._title(blank) == ""


def test_garbage_rows_are_skipped():
    items = [None, "nope", {"entity_type": "CLASS"}]
    assert parse.parse_calendar(items) == []
    assert parse.parse_tasks([{"id": "t"}])[0].title == "(untitled)"
