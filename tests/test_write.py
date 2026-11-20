"""Write-path tests: create, complete, reopen, delete.

Pins down the two-id rule found live: completion needs the occurrence `id`,
deletion needs `schedule_id`, and mixing them up fails *silently* on the
real API — completion 500s and delete leaves the task in place.
"""
import re
from datetime import date, timedelta

import pytest
from typer.testing import CliRunner

from fake_msl import EMAIL, PASSWORD, FakeMSL
from msl.cli import app
from msl import client as msl

runner = CliRunner()


@pytest.fixture
def fake():
    with FakeMSL() as f:
        yield f


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MSL_CONFIG_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def c(fake):
    client = msl.MSLClient(base=fake.base)
    client.login(msl.Credentials(EMAIL, PASSWORD))
    return client


def plain(text: str) -> str:
    """CLI output without ANSI colour or rich's line wrapping.

    Typer's pretty errors are a boxed panel whose width follows the terminal,
    so the option name can end up split across lines. Whether the message
    reads properly is not what these tests are about: they check that the
    command refuses to run at all.
    """
    return re.sub(r"\s+", " ", re.sub(r"\x1b\[[0-9;]*m", "", text or ""))


def cli(fake, *args):
    return runner.invoke(app, list(args) + ["--base", fake.base, "-e", EMAIL, "-p", PASSWORD])


def titles(fake):
    return [t["meta_data"]["title"] for t in fake.tasks]


def test_create_task_body(c, fake):
    due = date.today() + timedelta(days=2)
    c.create_task("Read chapter 4", due=due, description="pages 40-60")
    body = [r for r in fake.requests if r[1].endswith("/user-schedule")
            and r[0] == "POST"][0][2]["body"]
    assert body["entity_type"] == "TASK"
    assert body["type"] == "ONCE"
    assert body["is_reoccuring"] is False
    assert body["meta_data"] == {"title": "Read chapter 4", "type": "revision",
                                 "description": "pages 40-60"}
    assert body["due_date"] == f"{due.isoformat()}T00:00:00.000"
    assert body["task_has_time"] is False
    assert "Read chapter 4" in titles(fake)


def test_create_timed_task(c, fake):
    from datetime import datetime
    start = datetime.combine(date.today(), datetime.min.time()).replace(hour=15, minute=30)
    c.create_task("Lab", start=start)
    body = [r for r in fake.requests if r[1].endswith("/user-schedule")
            and r[0] == "POST"][0][2]["body"]
    assert body["task_has_time"] is True
    assert body["start_time"].endswith("15:30:00.000")


def test_complete_uses_occurrence_id(c, fake):
    task = c.find_task("Worksheet 3.2")[0]
    assert task["id"] != task["schedule_id"]
    c.set_task_done(task["id"], True)
    path = [r[1] for r in fake.requests if "task-completed" in r[1]][0]
    assert path.endswith(task["id"])
    done = [t for t in fake.tasks if t["id"] == task["id"]][0]
    assert done["completed"] is True


def test_complete_with_schedule_id_fails(c, fake):
    """The real API 500s here; the client must surface it, not swallow it."""
    task = c.find_task("Worksheet 3.2")[0]
    with pytest.raises(msl.MSLError):
        c.set_task_done(task["schedule_id"], True)
    still = [t for t in fake.tasks if t["id"] == task["id"]][0]
    assert still["completed"] is False


def test_delete_requires_schedule_id(c, fake):
    task = c.find_task("Worksheet 3.2")[0]
    with pytest.raises(msl.MSLError):
        c.delete_task(task["id"])          # wrong id: nothing deleted
    assert "Worksheet 3.2" in titles(fake)
    c.delete_task(task["schedule_id"])
    assert "Worksheet 3.2" not in titles(fake)


def test_reopen(c, fake):
    task = c.find_task("Old quiz corrections")[0]
    catalog = [t for t in fake.tasks if t["id"] == task["id"]][0]
    catalog["completed"] = True
    c.set_task_done(task["id"], False)
    assert [t for t in fake.tasks if t["id"] == task["id"]][0]["completed"] is False


# ── CLI ─────────────────────────────────────────────────────────────────

def test_cli_add_then_lists(c, fake):
    r = cli(fake, "add", "Vocab quiz", "--due", "tomorrow", "--note", "unit 5")
    assert r.exit_code == 0, r.output
    assert "added" in r.output
    assert "Vocab quiz" in titles(fake)
    shown = cli(fake, "tasks")
    assert "Vocab quiz" in shown.output
    assert "unit 5" in shown.output


def test_cli_add_due_words(c, fake):
    for word, expected in (("today", date.today()),
                           ("tomorrow", date.today() + timedelta(days=1))):
        r = cli(fake, "add", f"task {word}", "--due", word, "--json")
        assert r.exit_code == 0, r.output
        assert r.stdout.strip().startswith("{")
    assert len(fake.tasks) == 6


def test_cli_add_unknown_course_lists_choices(fake):
    r = cli(fake, "add", "Thing", "--due", "today", "--course", "Astronomy")
    assert r.exit_code == 1
    assert "no subject matching" in r.output
    assert "Biology" in r.output, "shows the real subjects as choices"


def test_cli_add_with_course(fake):
    r = cli(fake, "add", "Cell diagram", "--due", "today", "--course", "bio", "--json")
    assert r.exit_code == 0, r.output
    created = [t for t in fake.tasks if t["meta_data"]["title"] == "Cell diagram"][0]
    assert created["subject_id"] == "subj-bio", "resolved --course to the matching subject id"


def test_cli_add_bad_time(fake):
    r = cli(fake, "add", "x", "--due", "today", "--at", "quarter to five")
    assert r.exit_code == 1
    assert "HH:MM" in r.output
    r = cli(fake, "add", "x", "--due", "today", "--at", "25:00")
    assert r.exit_code == 1


def test_cli_add_with_valid_time(fake):
    """A real --at must work end to end (only invalid times were tested before)."""
    r = cli(fake, "add", "Timed thing", "--due", "today", "--at", "16:45", "--json")
    assert r.exit_code == 0, r.output
    body = [x for x in fake.requests if x[0] == "POST" and x[1].endswith("/user-schedule")][-1][2]["body"]
    assert body["task_has_time"] is True
    assert body["start_time"].endswith("16:45:00.000")


def test_cli_add_time_without_date(fake):
    r = cli(fake, "add", "x", "--at", "16:00")
    assert r.exit_code == 2, plain(r.output)
    assert "--due" in plain(r.output), plain(r.output)
    assert not [q for q in fake.requests if q[0] == "POST"], "nothing may reach the API"


def test_cli_add_requires_due_date(fake):
    """Undated tasks are invisible in MSL, so --due is mandatory."""
    r = cli(fake, "add", "no date here")
    assert r.exit_code == 2, plain(r.output)
    assert "--due" in plain(r.output), plain(r.output)
    assert not [t for t in fake.tasks if t["meta_data"]["title"] == "no date here"]


def test_cli_done_and_undone(fake):
    r = cli(fake, "done", "Essay draft")
    assert r.exit_code == 0, r.output
    assert [t for t in fake.tasks if "Essay draft" in t["meta_data"]["title"]][0]["completed"]
    r = cli(fake, "undone", "Essay draft")
    assert r.exit_code == 0, r.output
    assert not [t for t in fake.tasks if "Essay draft" in t["meta_data"]["title"]][0]["completed"]


def test_cli_done_ambiguous_needs_yes(fake):
    r = cli(fake, "add", "Worksheet 3.2 extra", "--due", "today")
    assert r.exit_code == 0, r.output
    r = cli(fake, "done", "Worksheet 3.2")
    assert r.exit_code == 1
    assert "2 tasks match" in r.output
    r = cli(fake, "done", "Worksheet 3.2", "--yes")
    assert r.exit_code == 0, r.output


def test_cli_done_unknown(fake):
    r = cli(fake, "done", "nonexistent homework")
    assert r.exit_code == 1
    assert "no task matching" in r.output


def test_cli_rm_needs_confirmation(fake):
    r = runner.invoke(app, ["rm", "Lab report", "--base", fake.base, "-e", EMAIL, "-p", PASSWORD],
                      input="n\n")
    assert r.exit_code == 1
    assert "Lab report" in titles(fake)

    r = runner.invoke(app, ["rm", "Lab report", "--base", fake.base, "-e", EMAIL, "-p", PASSWORD],
                      input="y\n")
    assert r.exit_code == 0, r.output
    assert "Lab report" not in titles(fake)


def test_cli_rm_completed_task(fake):
    """`rm` should find completed tasks too, not just open ones."""
    for t in fake.tasks:
        if "Old quiz" in t["meta_data"]["title"]:
            t["completed"] = True
    r = cli(fake, "rm", "Old quiz", "--yes")
    assert r.exit_code == 0, r.output
    assert "Old quiz corrections" not in titles(fake)


def test_write_commands_in_help():
    r = runner.invoke(app, ["--help"])
    for cmd in ("add", "done", "undone", "rm"):
        assert cmd in r.output
