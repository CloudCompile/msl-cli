"""CLI tests: every command against the fake MyStudyLife backend."""
import json
from datetime import date, timedelta

import pytest
from typer.testing import CliRunner

from fake_msl import EMAIL, PASSWORD, FakeMSL
from msl.cli import app

runner = CliRunner()
TODAY = date.today().isoformat()


@pytest.fixture
def fake():
    with FakeMSL() as f:
        yield f


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MSL_CONFIG_DIR", str(tmp_path))
    return tmp_path


def run(fake, *args):
    return runner.invoke(app, list(args) + ["--base", fake.base, "-e", EMAIL, "-p", PASSWORD])


def test_whoami(fake):
    r = run(fake, "whoami")
    assert r.exit_code == 0, r.output
    assert "Test Student" in r.output and EMAIL in r.output
    assert "session.json" in r.output
    assert "classes" in r.output


def test_whoami_caches_and_reuses_session(fake):
    assert run(fake, "whoami").exit_code == 0
    logins = lambda: [x for x in fake.requests if x[1].endswith("/auth/login")]  # noqa: E731
    assert len(logins()) == 1
    r = runner.invoke(app, ["whoami", "--base", fake.base])   # no creds this time
    assert r.exit_code == 0, r.output
    assert len(logins()) == 1, "second run reuses the cached session"


def test_login_failure_exits_2(fake):
    r = runner.invoke(app, ["today", "--base", fake.base, "-e", EMAIL, "-p", "wrong"])
    assert r.exit_code == 2
    assert "login failed" in r.output


def test_today(fake):
    r = run(fake, "today")
    assert r.exit_code == 0, r.output
    assert "today" in r.output
    assert "Algebra I" in r.output and "Tennis" in r.output
    assert "8:50 AM – 9:35 AM" in r.output
    assert "Lab report" in r.output, "tasks due today are listed"
    assert "Worksheet 3.2" in r.output, "overdue tasks show up too"


def test_today_json(fake):
    r = run(fake, "today", "--json")
    data = json.loads(r.stdout)
    assert data["user"]["email"] == EMAIL
    assert any(c["title"] == "Algebra I" for c in data["classes"])
    assert "Worksheet 3.2" in [t["title"] for t in data["tasks"]]


def test_week(fake):
    r = run(fake, "week")
    assert r.exit_code == 0, r.output
    for day in ("Mon", "Tue", "Wed", "Thu", "Fri"):
        assert day in r.output
    assert "Tasks this week" in r.output or "Midterm" in r.output


def test_week_start_and_next(fake):
    monday = date.today() - timedelta(days=date.today().weekday())
    r = run(fake, "week", "--json")
    data = json.loads(r.stdout)
    assert data["start"] == monday.isoformat()

    later = run(fake, "week", "--next", "--json")
    assert json.loads(later.stdout)["start"] == (monday + timedelta(days=7)).isoformat()


def test_week_bad_date(fake):
    r = run(fake, "week", "--start", "soon")
    assert r.exit_code == 1
    assert "YYYY-MM-DD" in r.output


def test_timetable(fake):
    r = run(fake, "timetable")
    assert r.exit_code == 0, r.output
    assert "timetable" in r.output
    assert "Biology" in r.output


def test_timetable_day(fake):
    # The fixture schedule only has weekday classes, so pick the next weekday
    # rather than "tomorrow" (which is Saturday when today is Friday).
    day = date.today() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    r = run(fake, "timetable", "--day", day.isoformat())
    assert r.exit_code == 0, r.output
    assert day.strftime("%A") in r.output
    assert "English 10" in r.output


def test_tasks(fake):
    r = run(fake, "tasks")
    assert r.exit_code == 0, r.output
    assert "Worksheet 3.2" in r.output
    assert "Essay draft" in r.output
    assert "Old quiz corrections" not in r.output, "completed tasks are hidden by default"
    assert "2 steps" in r.output


def test_tasks_all(fake):
    r = run(fake, "tasks", "--all")
    assert "Old quiz corrections" in r.output


def test_tasks_overdue(fake):
    r = run(fake, "tasks", "--overdue", "--json")
    data = json.loads(r.stdout)
    assert [t["title"] for t in data["tasks"]] == ["Worksheet 3.2"]


def test_exams(fake):
    r = run(fake, "exams")
    assert r.exit_code == 0, r.output
    assert "Midterm" in r.output and "Biology" in r.output


def test_logout(fake, isolated):
    assert run(fake, "whoami").exit_code == 0
    assert (isolated / "session.json").exists()
    r = runner.invoke(app, ["logout"])
    assert r.exit_code == 0
    assert not (isolated / "session.json").exists()


def test_help():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    for cmd in ("today", "week", "timetable", "tasks", "exams", "whoami", "logout"):
        assert cmd in r.output
