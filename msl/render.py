"""Rich renderers for msl output."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .client import User
from .parse import Occurrence, Task

console = Console()
err_console = Console(stderr=True)

KIND_STYLE = {
    "CLASS": "cyan",
    "EXTRA": "magenta",
    "TASK": "yellow",
    "EXAM": "bold red",
    "HOLIDAY": "green",
}


def header(user: User | None, subtitle: str = "") -> Text:
    t = Text()
    if user and user.name:
        t.append(user.name, style="bold")
    if user and user.email and user.name != user.email:
        t.append(f"  ·  {user.email}", style="dim")
    if user and user.is_subscribed:
        t.append("  ·  MSL+", style="dim yellow")
    if subtitle:
        t.append(f"  ·  {subtitle}", style="dim")
    return t


def day_heading(day: date) -> str:
    today = date.today()
    delta = (day - today).days
    label = day.strftime("%a %b %-d")
    if delta == 0:
        return f"{label}  (today)"
    if delta == 1:
        return f"{label}  (tomorrow)"
    if delta == -1:
        return f"{label}  (yesterday)"
    return label


def events_table(items: list[Occurrence], *, show_date: bool = False,
                 now_marker: bool = False) -> Table:
    t = Table(box=None, pad_edge=False, header_style="dim")
    if show_date:
        t.add_column("", style="dim", width=1)
        t.add_column("When", no_wrap=True)
    else:
        t.add_column("", style="dim", width=1)
        t.add_column("Time", no_wrap=True)
    t.add_column("What")
    t.add_column("Where", style="dim")
    marked = False
    for o in items:
        is_now = bool(now_marker and o.start and o.end
                      and o.start <= datetime.now() <= o.end and not marked)
        if is_now:
            marked = True
        marker = "▸" if is_now else ""
        when = (o.time_label if not show_date
                else o.start.strftime("%a %-m/%-d %-I:%M %p") if o.start else "—")
        what = Text()
        # only label things that aren't ordinary classes or plain events
        if o.kind in ("TASK", "EXAM", "HOLIDAY"):
            what.append(f"{o.kind.title()} ", style=KIND_STYLE.get(o.kind, ""))
        what.append(o.title, style="bold" if is_now else "")
        if o.details:
            what.append(f"  {o.details}", style="dim")
        where_bits = [b for b in (o.room, o.teacher) if b]
        t.add_row(marker, when, what, " · ".join(where_bits), style="bold" if is_now else "")
    return t


def render_today(user: User | None, items: list[Occurrence],
                 tasks: list[Task], holiday: bool = False) -> None:
    today = date.today()
    console.print(header(user, day_heading(today)))
    console.print()
    if holiday:
        console.print("[green]Holiday — no classes scheduled[/]")
        console.print()
    elif not items:
        console.print("[dim]nothing scheduled today[/]")
        console.print()
    else:
        console.print(events_table(items, now_marker=True))
        console.print()
    _render_task_block(tasks, title="Tasks due today", empty="no tasks due today")


def _render_task_block(tasks: list[Task], *, title: str, empty: str) -> None:
    console.print(Text(title, style="dim"))
    if not tasks:
        console.print(f"[dim]{empty}[/]")
        return
    t = Table(box=None, pad_edge=False, header_style="dim")
    t.add_column("", width=1)
    t.add_column("Due", no_wrap=True)
    t.add_column("Task")
    t.add_column("Course", style="dim")
    for task in tasks:
        mark = "✗" if task.overdue else ("✓" if task.completed else "")
        style = "red" if task.overdue else ("dim" if task.completed else "")
        label = task.due_label if task.due else "—"
        t.add_row(Text(mark, style=style), Text(label, style=style),
                  Text(task.title, style=("strike dim" if task.completed else style)),
                  task.subject)
    console.print(t)


def render_week(user: User | None, items: list[Occurrence], start: date) -> None:
    console.print(header(user, f"week of {start.strftime('%b %-d')}"))
    for i in range(7):
        day = start.fromordinal(start.toordinal() + i)
        day_items = [o for o in items if o.date == day]
        console.print()
        console.print(Text(day_heading(day), style="bold"))
        if not day_items:
            console.print("[dim]  nothing[/]")
            continue
        console.print(events_table(day_items, now_marker=(day == date.today())))


def render_timetable(user: User | None, items: list[Occurrence], day: date) -> None:
    console.print(header(user, f"timetable · {day.strftime('%A')}"))
    console.print()
    if not items:
        console.print("[dim]no classes on this weekday[/]")
        return
    console.print(events_table(items))


def render_tasks(user: User | None, tasks: list[Task], title: str = "tasks",
                 show_completed: bool = False) -> None:
    console.print(header(user, title))
    console.print()
    shown = tasks if show_completed else [t for t in tasks if not t.completed]
    if not shown:
        console.print("[dim]nothing here[/]")
        return
    t = Table(box=None, pad_edge=False, header_style="dim")
    t.add_column("", width=1)
    t.add_column("Due", no_wrap=True)
    t.add_column("Task")
    t.add_column("Course", style="dim")
    t.add_column("Notes", style="dim")
    for task in shown:
        mark = "✗" if task.overdue else ("✓" if task.completed else "")
        style = "red" if task.overdue else ("dim" if task.completed else "")
        notes = []
        if task.subtasks:
            notes.append(f"{len(task.subtasks)} steps")
        if task.details:
            notes.append(task.details)
        t.add_row(Text(mark, style=style),
                  Text(task.due_label, style=style),
                  Text(task.title, style=("strike dim" if task.completed else style)),
                  task.subject,
                  " · ".join(notes))
    console.print(t)


def render_exams(user: User | None, exams: list[Task]) -> None:
    console.print(header(user, "exams"))
    console.print()
    if not exams:
        console.print("[dim]no exams scheduled[/]")
        return
    t = Table(box=None, pad_edge=False, header_style="dim")
    t.add_column("When", no_wrap=True)
    t.add_column("Exam")
    t.add_column("Course", style="dim")
    for e in exams:
        when = e.due.strftime("%a %b %-d %-I:%M %p") if e.due else "—"
        t.add_row(Text(when, style="bold red"), Text(e.title, style="bold"), e.subject)
    console.print(t)


def to_json(obj: Any) -> str:
    def default(o: Any) -> Any:
        if is_dataclass(o) and not isinstance(o, type):
            data = asdict(o)
            return data
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        raise TypeError(f"{type(o).__name__} is not JSON serialisable")
    return json.dumps(obj, indent=2, default=default, ensure_ascii=False)
