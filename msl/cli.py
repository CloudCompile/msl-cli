"""msl — MyStudyLife timetable, tasks and exams in your terminal."""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from typing import Optional

import typer

from . import client as msl_client
from . import parse
from . import render

app = typer.Typer(
    help="MyStudyLife timetable, tasks and exams in your terminal.",
    no_args_is_help=True,
    add_completion=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)

BASE_HELP = "API base URL (default: https://be.mystudylife.com)"
CRED = dict(
    email=typer.Option(None, "--email", "-e", help="Account email (or MSL_EMAIL)"),
    password=typer.Option(None, "--password", "-p", help="Password (or MSL_PASSWORD)"),
)


def _connect(base: str, email: Optional[str], password: Optional[str]) -> tuple[msl_client.MSLClient, msl_client.User]:
    """Reuse the cached token, refresh it, or log in — then confirm identity."""
    try:
        c = msl_client.MSLClient(base=base)
        creds = None
        cached = msl_client.load_session()
        if not (cached and (cached.access_token or cached.refresh_token)):
            creds = msl_client.resolve_credentials(email, password)
        session = c.ensure_session(creds)
    except msl_client.LoginError as e:
        render.err_console.print(f"[red]login failed:[/] {e}")
        raise typer.Exit(2)
    except msl_client.MSLError as e:
        render.err_console.print(f"[red]mystudylife error:[/] {e}")
        raise typer.Exit(3)
    return c, session.user


def _parse_day(value: str) -> date:
    """Accept YYYY-MM-DD, today, tomorrow, or a weekday name."""
    value = value.strip().lower()
    today = date.today()
    if value in ("today", "now"):
        return today
    if value == "tomorrow":
        return today + timedelta(days=1)
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, name in enumerate(names):
        if value.startswith(name[:3]) or value == name:
            delta = (i - today.weekday()) % 7 or 7
            return today + timedelta(days=delta)
    raise typer.BadParameter(f"{value!r} is not a date; use YYYY-MM-DD, today, or tomorrow")


def _week_start(day: date, monday: bool = True) -> date:
    return day - timedelta(days=day.weekday() if monday else (day.weekday() + 1) % 7)


def fetch_day(client: msl_client.MSLClient, day: date) -> tuple[parse.DaySummary, list[parse.Occurrence]]:
    """A day's events from the calendar endpoint, plus dashboard counts.

    The dashboard's `/user-calendar/dashboard` list is unreliable (it has
    been observed to omit the first period of the day), so the calendar is
    the source of truth and the dashboard only supplies counts/flags.
    """
    summary = parse.parse_dashboard(client.dashboard())
    items = parse.on_day(parse.parse_calendar(client.calendar(day, day)), day)
    return summary, items


@app.command()
def today(
    base: str = typer.Option(msl_client.BASE, "--base", envvar="MSL_BASE", help=BASE_HELP),
    email: Optional[str] = CRED["email"],
    password: Optional[str] = CRED["password"],
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Today's classes plus anything due today."""
    c, user = _connect(base, email, password)
    summary, today_items = fetch_day(c, date.today())
    tasks = [t for t in parse.open_tasks(parse.parse_tasks(c.tasks()))
             if t.due and t.due.date() <= date.today()]
    if json_out:
        typer.echo(render.to_json({
            "user": user, "date": date.today(), "is_holiday": summary.is_holiday,
            "classes": today_items, "tasks": tasks, "counts": summary.counts,
        }))
        return
    render.render_today(user, today_items, tasks, holiday=summary.is_holiday)


@app.command()
def week(
    base: str = typer.Option(msl_client.BASE, "--base", envvar="MSL_BASE", help=BASE_HELP),
    email: Optional[str] = CRED["email"],
    password: Optional[str] = CRED["password"],
    start: Optional[str] = typer.Option(None, "--start", help="Any date in the week (YYYY-MM-DD)"),
    next_week: bool = typer.Option(False, "--next", help="Show next week"),
    json_out: bool = typer.Option(False, "--json"),
):
    """The week ahead: classes, events, tasks and exams by day."""
    day = date.today()
    if start:
        try:
            day = date.fromisoformat(start)
        except ValueError:
            render.err_console.print(f"[red]bad --start {start!r}; use YYYY-MM-DD[/]")
            raise typer.Exit(1)
    day += timedelta(days=7 if next_week else 0)
    first = _week_start(day)
    last = first + timedelta(days=6)
    c, user = _connect(base, email, password)
    items = parse.parse_calendar(c.calendar(first, last))
    tasks = [t for t in parse.open_tasks(parse.parse_tasks(c.tasks()))
             if t.due and first <= t.due.date() <= last]
    exams = [e for e in parse.parse_tasks(c.exams())
             if e.due and first <= e.due.date() <= last]
    if json_out:
        typer.echo(render.to_json({
            "user": user, "start": first, "end": last,
            "events": items, "tasks": tasks, "exams": exams,
        }))
        return
    render.render_week(user, items, first)
    if tasks:
        console_tasks(tasks)
    if exams:
        console_exams(exams)


def console_tasks(tasks: list[parse.Task]) -> None:
    render.console.print()
    render._render_task_block(tasks, title="Tasks this week", empty="none")


def console_exams(exams: list[parse.Task]) -> None:
    render.console.print()
    render.render_exams(None, exams)


@app.command()
def timetable(
    base: str = typer.Option(msl_client.BASE, "--base", envvar="MSL_BASE", help=BASE_HELP),
    email: Optional[str] = CRED["email"],
    password: Optional[str] = CRED["password"],
    day: Optional[str] = typer.Option(None, "--day", help="Weekday to show (default: today)"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Your recurring class timetable for a weekday."""
    when = date.today()
    if day:
        try:
            when = date.fromisoformat(day)
        except ValueError:
            render.err_console.print(f"[red]bad --day {day!r}; use YYYY-MM-DD[/]")
            raise typer.Exit(1)
    c, user = _connect(base, email, password)
    # a full week so the requested weekday's pattern is present
    first = _week_start(when)
    items = parse.parse_calendar(c.calendar(first, first + timedelta(days=6)))
    day_items = parse.on_day(items, first + timedelta(days=when.weekday()))
    if json_out:
        typer.echo(render.to_json({"user": user, "date": when, "classes": day_items}))
        return
    render.render_timetable(user, day_items, when)


@app.command()
def tasks(
    base: str = typer.Option(msl_client.BASE, "--base", envvar="MSL_BASE", help=BASE_HELP),
    email: Optional[str] = CRED["email"],
    password: Optional[str] = CRED["password"],
    show_completed: bool = typer.Option(False, "--all", help="Include completed tasks"),
    overdue: bool = typer.Option(False, "--overdue", help="Only overdue tasks"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Every task on your list, soonest first."""
    c, user = _connect(base, email, password)
    items = parse.parse_tasks(c.tasks())
    if overdue:
        items = [t for t in items if t.overdue]
    if json_out:
        typer.echo(render.to_json({
            "user": user,
            "tasks": items if show_completed else parse.open_tasks(items),
        }))
        return
    render.render_tasks(user, items, title="tasks", show_completed=show_completed)


@app.command()
def exams(
    base: str = typer.Option(msl_client.BASE, "--base", envvar="MSL_BASE", help=BASE_HELP),
    email: Optional[str] = CRED["email"],
    password: Optional[str] = CRED["password"],
    json_out: bool = typer.Option(False, "--json"),
):
    """Upcoming exams."""
    c, user = _connect(base, email, password)
    items = parse.parse_tasks(c.exams())
    if json_out:
        typer.echo(render.to_json({"user": user, "exams": items}))
        return
    render.render_exams(user, items)


@app.command()
def whoami(
    base: str = typer.Option(msl_client.BASE, "--base", envvar="MSL_BASE", help=BASE_HELP),
    email: Optional[str] = CRED["email"],
    password: Optional[str] = CRED["password"],
):
    """Show which account the session belongs to (and where it is cached)."""
    c, user = _connect(base, email, password)
    render.console.print(render.header(user, "signed in"))
    render.console.print(f"[dim]session: {msl_client._path('session.json')}[/]")
    # classCount is unreliable (observed 6, 7 and 8 for the same day), so show
    # the real count from the calendar and only report task/exam counts.
    _, items = fetch_day(c, date.today())
    dash = parse.parse_dashboard(c.dashboard())
    render.console.print(
        f"[dim]today: {len(items)} classes · {dash.counts['tasks']} tasks · "
        f"{dash.counts['exams']} exams[/]")


@app.command()
def add(
    title: str = typer.Argument(..., help="What the task is"),
    base: str = typer.Option(msl_client.BASE, "--base", envvar="MSL_BASE", help=BASE_HELP),
    email: Optional[str] = CRED["email"],
    password: Optional[str] = CRED["password"],
    due: str = typer.Option(..., "--due", "-d",
                            help="Due date: YYYY-MM-DD, today, tomorrow, or a weekday"),
    at: Optional[str] = typer.Option(None, "--at", help="Due time, e.g. 15:30 (makes it a timed task)"),
    course: Optional[str] = typer.Option(None, "--course", "-c", help="Subject name, e.g. 'Biology'"),
    note: str = typer.Option("", "--note", "-n", help="Description/notes"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Add a task to your list.

    A due date is required: MSL accepts undated tasks but never lists them
    (the activities endpoint only returns dated ones), so they would be
    invisible the moment `add` exits.
    """
    c, user = _connect(base, email, password)
    when = _parse_day(due)
    if at and not when:
        render.err_console.print("[red]--at needs --due[/] [dim](a time alone has no date)[/]")
        raise typer.Exit(1)
    start = None
    if when and at:
        try:
            hour, minute = (int(x) for x in at.strip().split(":", 1))
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise ValueError
            start = datetime.combine(when, datetime.min.time()).replace(hour=hour, minute=minute)
        except ValueError:
            render.err_console.print(f"[red]bad --at {at!r}; use 24h HH:MM, e.g. 15:30[/]")
            raise typer.Exit(1)
    subject_id = None
    if course:
        matches = [s for s in c.subjects() if course.lower() in s["name"].lower()]
        if not matches:
            names = ", ".join(s["name"] for s in c.subjects()) or "none found"
            render.err_console.print(f"[red]no subject matching {course!r}[/] [dim]({names})[/]")
            raise typer.Exit(1)
        subject_id = matches[0]["id"]
    created = c.create_task(title, due=when, subject_id=subject_id,
                            description=note, start=start)
    # the create response id is not the id the task list returns, so look it up
    found = [t for t in c.find_task(title) if not (t.get("completed") or False)]
    listed = found[-1] if found else None
    if json_out:
        typer.echo(render.to_json({"user": user, "created": created,
                                   "task": parse.parse_task(listed) if listed else None}))
        return
    render.console.print(f"[green]added[/] {title.strip()}")
    if listed:
        task = parse.parse_task(listed)
        render.console.print(f"[dim]  {task.due_label}  ·  {task.subject or 'no course'}[/]")


@app.command()
def done(
    match: str = typer.Argument(..., help="Part of the task title"),
    base: str = typer.Option(msl_client.BASE, "--base", envvar="MSL_BASE", help=BASE_HELP),
    email: Optional[str] = CRED["email"],
    password: Optional[str] = CRED["password"],
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask if several tasks match"),
):
    """Mark a task complete."""
    _set_done(match, True, base, email, password, yes)


@app.command()
def undone(
    match: str = typer.Argument(..., help="Part of the task title"),
    base: str = typer.Option(msl_client.BASE, "--base", envvar="MSL_BASE", help=BASE_HELP),
    email: Optional[str] = CRED["email"],
    password: Optional[str] = CRED["password"],
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """Reopen a completed task."""
    _set_done(match, False, base, email, password, yes)


def _set_done(match: str, value: bool, base: str, email, password, yes: bool) -> None:
    c, user = _connect(base, email, password)
    task = _one_task(c, match, yes, want_completed=not value)
    c.set_task_done(task.id, value)
    verb = "done" if value else "reopened"
    render.console.print(f"[green]{verb}[/] {task.title}")


@app.command()
def move(
    match: str = typer.Argument(..., help="Part of the task title"),
    due: str = typer.Argument(..., help="New due date (YYYY-MM-DD, today, tomorrow, weekday)"),
    base: str = typer.Option(msl_client.BASE, "--base", envvar="MSL_BASE", help=BASE_HELP),
    email: Optional[str] = CRED["email"],
    password: Optional[str] = CRED["password"],
):
    """Reschedule a task."""
    c, user = _connect(base, email, password)
    when = _parse_day(due)
    task = _one_task(c, match, True, any_state=True)
    raw = [t for t in c.tasks() if t.get("id") == task.id][0]
    c.move_task(raw, when)
    render.console.print(f"[green]moved[/] {task.title} [dim]→ {when.strftime('%a %b %-d')}[/]")


@app.command()
def edit(
    match: str = typer.Argument(..., help="Part of the task title"),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="New title"),
    due: Optional[str] = typer.Option(None, "--due", "-d", help="New due date"),
    note: Optional[str] = typer.Option(None, "--note", "-n", help="New description"),
    base: str = typer.Option(msl_client.BASE, "--base", envvar="MSL_BASE", help=BASE_HELP),
    email: Optional[str] = CRED["email"],
    password: Optional[str] = CRED["password"],
):
    """Change a task's title, date or notes."""
    if not any((title, due, note)):
        render.err_console.print("[red]nothing to change[/] [dim](use --title/--due/--note)[/]")
        raise typer.Exit(1)
    c, user = _connect(base, email, password)
    task = _one_task(c, match, True, any_state=True)
    raw = [t for t in c.tasks() if t.get("id") == task.id][0]
    c.update_task(raw, title=title, due=_parse_day(due) if due else None, description=note)
    render.console.print(f"[green]updated[/] {title or task.title}")


@app.command()
def rm(
    match: str = typer.Argument(..., help="Part of the task title"),
    base: str = typer.Option(msl_client.BASE, "--base", envvar="MSL_BASE", help=BASE_HELP),
    email: Optional[str] = CRED["email"],
    password: Optional[str] = CRED["password"],
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation"),
):
    """Delete a task."""
    c, user = _connect(base, email, password)
    task = _one_task(c, match, yes, any_state=True)
    if not yes:
        confirm = typer.confirm(f"Delete “{task.title}”?", default=False)
        if not confirm:
            render.console.print("[dim]cancelled[/]")
            raise typer.Exit(1)
    if not task.schedule_id:
        render.err_console.print("[red]this task has no schedule id; refusing to guess[/]")
        raise typer.Exit(3)
    c.delete_task(task.schedule_id)
    render.console.print(f"[green]deleted[/] {task.title}")


def _one_task(c: msl_client.MSLClient, match: str, yes: bool, *,
              want_completed: bool | None = False, any_state: bool = False) -> parse.Task:
    """Resolve a title fragment to exactly one task, or explain why not."""
    raw = c.tasks()
    needle = match.strip().lower()
    hits = [t for t in raw
            if needle in str((t.get("meta_data") or {}).get("title", "")).lower()]
    if not any_state:
        hits = [t for t in hits if bool(t.get("completed")) == bool(want_completed)]
    if not hits:
        render.err_console.print(f"[red]no task matching {match!r}[/]")
        raise typer.Exit(1)
    if len(hits) > 1 and not yes:
        render.err_console.print(f"[red]{len(hits)} tasks match {match!r}:[/]")
        for h in hits:
            render.err_console.print(f"  [dim]·[/] {(h.get('meta_data') or {}).get('title')}")
        render.err_console.print("[dim]be more specific, or pass --yes to take the newest[/]")
        raise typer.Exit(1)
    return parse.parse_task(hits[-1])


@app.command()
def logout():
    """Forget the cached session tokens."""
    msl_client.clear_session()
    render.console.print("[dim]session cleared[/]")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)
