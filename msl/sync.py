"""Sync parsed Classroom schedules into MyStudyLife tasks.

Design constraints learned the hard way:

* Re-running a sync must not duplicate tasks. Every created task carries
  `meta_data.external_id = "classroom:<assignment-key>"`, and a re-sync
  updates the existing task instead of adding another.
* Teachers' documents are unreliable (see classroom/schedule.py), so entries
  flagged `needs_review` are never written automatically — they're reported.
* Dry run is the default at the CLI level; this module just reports what it
  would do unless `apply=True`.

The external id is derived from stable classroom facts (course + assignment
title + due date), not from MSL ids, so it survives the task being edited in
the app and re-created by us.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable


def key_for(course: str, title: str, due: date | None) -> str:
    """Stable identity for one assignment across syncs."""
    basis = "|".join([
        _norm(course),
        _norm(title),
        due.isoformat() if due else "",
    ])
    return "classroom:" + hashlib.sha1(basis.encode()).hexdigest()[:16]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


@dataclass
class Candidate:
    """One parsed assignment, ready to become an MSL task."""

    course: str
    title: str
    due: date | None
    details: str = ""
    key: str = ""
    needs_review: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.key:
            self.key = key_for(self.course, self.title, self.due)

    def to_dict(self) -> dict[str, Any]:
        return {"course": self.course, "title": self.title,
                "due": self.due.isoformat() if self.due else "",
                "details": self.details, "key": self.key,
                "needs_review": self.needs_review, "reason": self.reason}


@dataclass
class Plan:
    create: list[Candidate] = field(default_factory=list)
    update: list[tuple[Candidate, dict[str, Any]]] = field(default_factory=list)
    unchanged: list[Candidate] = field(default_factory=list)
    skipped: list[Candidate] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.create or self.update)

    def to_dict(self) -> dict[str, Any]:
        return {
            "create": [c.to_dict() for c in self.create],
            "update": [{"candidate": c.to_dict(),
                        "task": _task_brief(t)} for c, t in self.update],
            "unchanged": [c.to_dict() for c in self.unchanged],
            "skipped": [c.to_dict() for c in self.skipped],
        }


def _task_brief(task: dict[str, Any]) -> dict[str, Any]:
    meta = task.get("meta_data") or {}
    return {"title": meta.get("title", ""), "start_time": task.get("start_time", ""),
            "schedule_id": task.get("schedule_id", "")}


def candidates_from_week(week: Any, course: str) -> list[Candidate]:
    """Build candidates from one classroom.schedule.ParsedWeek.

    `week` is duck-typed (title/assignments) so msl doesn't import classroom.
    """
    out: list[Candidate] = []
    for a in getattr(week, "assignments", []):
        out.append(Candidate(
            course=course,
            title=getattr(a, "title", ""),
            due=getattr(a, "due", None),
            details=getattr(a, "details", ""),
            needs_review=getattr(a, "needs_review", False),
            reason=getattr(a, "reason", ""),
        ))
    return out


def build_plan(cands: Iterable[Candidate], existing: list[dict[str, Any]]) -> Plan:
    """Compare candidates against the tasks already in MSL."""
    by_key: dict[str, dict[str, Any]] = {}
    for task in existing:
        key = (task.get("meta_data") or {}).get("external_id")
        if key:
            by_key[key] = task
    plan = Plan()
    for c in cands:
        if c.needs_review or c.due is None:
            plan.skipped.append(c)
            continue
        task = by_key.get(c.key)
        if task is None:
            plan.create.append(c)
            continue
        current = (task.get("start_time") or "")[:10]
        if current != c.due.isoformat():
            plan.update.append((c, task))
        else:
            plan.unchanged.append(c)
    return plan


def match_subject(course: str, subjects: list[dict[str, Any]]) -> str | None:
    """Find the MSL subject for a Classroom course name.

    The two systems abbreviate differently ("Geometry Honors" vs "Geometry H",
    "Environmental Science - 7" vs "Enviro Sci Ho"), so exact matching silently
    attaches nothing. Prefer an exact normalised match, then a prefix/word
    overlap that is unambiguous.
    """
    if not course or not subjects:
        return None
    want = _norm(course)
    for s in subjects:
        if _norm(s.get("name", "")) == want:
            return s["id"]
    def abbrev_match(a: str, b: str) -> bool:
        """'enviro' ~ 'environmental', 'sci' ~ 'science': shared prefix."""
        if a == b:
            return True
        shorter, longer = sorted((a, b), key=len)
        return len(shorter) >= 3 and longer.startswith(shorter[:max(3, len(shorter))])

    def tokens_agree(x: set[str], y: set[str]) -> int:
        return sum(1 for a in x if any(abbrev_match(a, b) for b in y))

    want_tokens = set(want.split())
    best: tuple[float, str] | None = None
    for s in subjects:
        name = _norm(s.get("name", ""))
        if not name:
            continue
        have = set(name.split())
        if not have:
            continue
        agreed = tokens_agree(want_tokens, have)
        overlap = agreed / max(1, min(len(want_tokens), len(have)))
        # a shared first word ("geometry") plus most of the short name
        first = abbrev_match(want.split()[0], name.split()[0])
        score = overlap + (0.25 if first else 0)
        if score >= 0.75 and (best is None or score > best[0]):
            best = (score, s["id"])
    return best[1] if best else None


def apply_plan(client: Any, plan: Plan, *, source: str = "classroom-cli",
               dry_run: bool = False) -> dict[str, Any]:
    """Create/update tasks through an MSL client.

    `client` needs create_task/update_task/delete_task/subjects, i.e. an
    msl.MSLClient. Returns a summary dict.
    """
    result: dict[str, Any] = {"created": [], "updated": [], "failed": [], "dry_run": dry_run}
    subjects = [] if dry_run else client.subjects()

    for cand in plan.create:
        if dry_run:
            result["created"].append(cand.to_dict())
            continue
        try:
            client.create_task(
                cand.title, due=cand.due,
                subject_id=match_subject(cand.course, subjects),
                description=cand.details,
                external_id=cand.key, source=source)
            result["created"].append(cand.to_dict())
        except Exception as e:                      # noqa: BLE001 - report, keep going
            result["failed"].append({**cand.to_dict(), "error": str(e)})

    for cand, task in plan.update:
        if dry_run:
            result["updated"].append({**cand.to_dict(),
                                      "was": (task.get("start_time") or "")[:10]})
            continue
        try:
            client.update_task(task, due=cand.due, title=cand.title,
                               subject_id=match_subject(cand.course, subjects))
            result["updated"].append({**cand.to_dict(),
                                      "was": (task.get("start_time") or "")[:10]})
        except Exception as e:                      # noqa: BLE001
            result["failed"].append({**cand.to_dict(), "error": str(e)})
    return result
