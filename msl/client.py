"""HTTP client for MyStudyLife's backend (be.mystudylife.com).

MyStudyLife's web app is Flutter and has no HTML to scrape, but it talks to
a plain JSON API. This module reproduces the handful of calls the web app
makes, using the same paths and payloads found in the shipped
`main.dart.js`:

    POST /auth/login                 {"email","password","provider":"email"}
                                     -> data.{user,accessToken,refreshToken}
    POST /auth/refresh-token         {"refreshToken"} -> data.accessToken
    GET  /user-calendar/dashboard    today's class list + counts
    GET  /user-calendar/calendar     ?start_date=&end_date=  (ISO dates)
    GET  /user-calendar/activities   ?entity_type=TASK|EXAM|HOLIDAY|EXTRA
                                     &page=&limit=&order=
    GET  /user/schedule-data         (unused; kept for reference)

`provider` is not optional: the API answers
"provider must be one of the following values: google, apple, microsoft,
email, facebook, dev" without it.

Access tokens expire in ~24h and refresh tokens in ~30d, so unlike Skyward
we keep the refresh token in ~/.config/msl/session.json (chmod 600) and log
in fresh only when it stops working. Passwords are never written.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import requests

BASE = "https://be.mystudylife.com"
WEB_ORIGIN = "https://web.mystudylife.com"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
)


class MSLError(Exception):
    """Base class for MyStudyLife failures."""


class LoginError(MSLError):
    """Wrong credentials, unverified email, or an unsupported provider."""


class AuthExpired(MSLError):
    """Access token rejected and the refresh token could not renew it."""


@dataclass
class Credentials:
    email: str
    password: str
    provider: str = "email"


@dataclass
class User:
    id: str = ""
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    provider: str = ""
    is_subscribed: bool = False
    using_password: bool = True

    @property
    def name(self) -> str:
        full = f"{self.first_name} {self.last_name}".strip()
        return full or self.email


@dataclass
class Session:
    user: User
    access_token: str = ""
    refresh_token: str = ""
    saved_at: float = field(default_factory=time.time)

    def to_file(self) -> dict[str, Any]:
        return {
            "user": self.user.__dict__,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "saved_at": self.saved_at,
        }

    @classmethod
    def from_file(cls, data: dict[str, Any]) -> "Session":
        return cls(
            user=User(**{k: v for k, v in (data.get("user") or {}).items()
                         if k in User.__dataclass_fields__}),
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token", ""),
            saved_at=float(data.get("saved_at") or 0),
        )


def config_dir() -> str:
    return os.environ.get("MSL_CONFIG_DIR") or os.path.expanduser("~/.config/msl")


def _path(name: str) -> str:
    return os.path.join(config_dir(), name)


def load_credentials() -> dict[str, str]:
    """Read ~/.config/msl/config (KEY=value, # comments)."""
    path = os.environ.get("MSL_CONFIG") or _path("config")
    out: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip().lower()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


def save_session(session: Session) -> None:
    os.makedirs(config_dir(), exist_ok=True)
    path = _path("session.json")
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(session.to_file(), fh, indent=2)
    os.replace(tmp, path)


def load_session() -> Session | None:
    try:
        with open(_path("session.json"), encoding="utf-8") as fh:
            return Session.from_file(json.load(fh))
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return None


def clear_session() -> None:
    try:
        os.unlink(_path("session.json"))
    except FileNotFoundError:
        pass


class MSLClient:
    def __init__(self, base: str = BASE, timeout: float = 30.0,
                 http: requests.Session | None = None):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.http = http or requests.Session()
        self.http.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": WEB_ORIGIN,
            "Referer": WEB_ORIGIN + "/",
            "User-Agent": USER_AGENT,
        })
        self.session: Session | None = None

    # ── auth ─────────────────────────────────────────────────────────────

    def login(self, creds: Credentials) -> Session:
        r = self.http.post(self.base + "/auth/login", timeout=self.timeout, json={
            "email": creds.email,
            "password": creds.password,
            "provider": creds.provider or "email",   # never send an empty provider
        })
        payload = self._json(r)
        if not payload.get("success"):
            raise LoginError(self._message(payload) or "login failed")
        data = payload.get("data") or {}
        user = User(**{k: v for k, v in (data.get("user") or {}).items()
                       if k in User.__dataclass_fields__})
        self.session = Session(
            user=user,
            access_token=data.get("accessToken", ""),
            refresh_token=data.get("refreshToken", ""),
        )
        return self.session

    def refresh(self, refresh_token: str) -> Session:
        r = self.http.post(self.base + "/auth/refresh-token", timeout=self.timeout,
                           json={"refreshToken": refresh_token})
        payload = self._json(r)
        if not payload.get("success"):
            raise AuthExpired(self._message(payload) or "refresh token rejected")
        data = payload.get("data") or {}
        if self.session is None:
            raise AuthExpired("no session to refresh")
        self.session.access_token = data.get("accessToken", "")
        if data.get("refreshToken"):
            self.session.refresh_token = data["refreshToken"]
        self.session.saved_at = time.time()
        return self.session

    def ensure_session(self, creds: Credentials | None = None) -> Session:
        """Use the cached token if it still works, else refresh, else log in."""
        cached = load_session()
        if cached and cached.access_token and self._token_alive(cached.access_token):
            self.session = cached
            return cached
        if cached and cached.refresh_token:
            try:
                self.session = cached
                self.refresh(cached.refresh_token)
                save_session(self.session)
                return self.session
            except AuthExpired:
                pass
        if creds is None:
            raise LoginError("session expired and no credentials to re-login with")
        self.login(creds)
        save_session(self.session)
        return self.session

    @staticmethod
    def _token_alive(token: str) -> bool:
        """Cheap JWT exp check so we don't make a doomed request."""
        import base64

        try:
            body = token.split(".")[1]
            body += "=" * (-len(body) % 4)
            claims = json.loads(base64.urlsafe_b64decode(body))
            return float(claims.get("exp", 0)) - 60 > time.time()
        except (IndexError, ValueError, TypeError):
            return False

    # ── requests ─────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        tok = self.session.access_token if self.session else ""
        if not tok:
            raise LoginError("not logged in")
        return {"Authorization": "Bearer " + tok}

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        r = self.http.get(self.base + path, params=params, headers=self._headers(),
                          timeout=self.timeout)
        return self._data(r)

    def _data(self, r: requests.Response) -> Any:
        payload = self._json(r)
        message = self._message(payload)
        if r.status_code == 401 or "Token Verification Failed" in message:
            raise AuthExpired(message or "token rejected")
        if not payload.get("success"):
            raise MSLError(self._message(payload) or f"HTTP {r.status_code}")
        return payload.get("data")

    @staticmethod
    def _json(r: requests.Response) -> dict[str, Any]:
        try:
            payload = r.json()
        except ValueError:
            raise MSLError(f"HTTP {r.status_code}: response was not JSON")
        return payload if isinstance(payload, dict) else {"success": True, "data": payload}

    @staticmethod
    def _message(payload: dict[str, Any]) -> str:
        msg = payload.get("message") or ""
        if isinstance(msg, list):
            msg = "; ".join(str(m) for m in msg)
        return str(msg)

    # ── endpoints ────────────────────────────────────────────────────────

    def dashboard(self) -> dict[str, Any]:
        """Today's schedule plus task/exam counts."""
        return self.get("/user-calendar/dashboard") or {}

    def calendar(self, start: date, end: date) -> list[dict[str, Any]]:
        """Every class/event occurrence between two dates, inclusive of `end`.

        MSL treats `end_date` as exclusive and degrades badly when both dates
        are equal (a same-day range returns a single item rather than that
        day's schedule), so we always add a day.
        """
        if end < start:
            start, end = end, start
        return self.get("/user-calendar/calendar", {
            "start_date": start.isoformat(),
            "end_date": (end + timedelta(days=1)).isoformat(),
        }) or []

    def activities(self, entity_type: str = "TASK", page: int = 1, limit: int = 100,
                   order: str = "asc", **extra: Any) -> list[dict[str, Any]]:
        """Tasks, exams, holidays or non-academic events.

        `entity_type` is one of TASK, EXAM, HOLIDAY, EXTRA. The API answers
        "Invalid Entity Tyoe" (sic) for anything else, including a missing
        value, so this is always sent explicitly.
        """
        params = {"entity_type": entity_type.upper(), "page": page, "limit": limit,
                  "order": order, **extra}
        return self.get("/user-calendar/activities", params) or []

    def tasks(self, lookback: int = 90, lookahead: int = 365) -> list[dict[str, Any]]:
        """All tasks in a date window, past and future.

        `/user-calendar/activities?entity_type=TASK` only returns tasks dated
        tomorrow or later — anything due today or earlier is invisible there
        (verified live). The calendar endpoint returns every occurrence in a
        range, so that is the source of truth for a task list.
        """
        today = date.today()
        seen: dict[str, dict[str, Any]] = {}
        for t in self.calendar(today - timedelta(days=lookback),
                               today + timedelta(days=lookahead)):
            if (t.get("entity_type") or "").upper() != "TASK":
                continue
            # the calendar repeats an occurrence per day of its span; one row
            # per task id is what a task list wants
            key = str(t.get("id") or t.get("schedule_id") or "")
            if key and key not in seen:
                seen[key] = t
        return list(seen.values())

    def exams(self, **kw: Any) -> list[dict[str, Any]]:
        return self.activities("EXAM", **kw)

    def profile(self) -> dict[str, Any]:
        return self.get("/user/profile") or {}

    # ── writes ───────────────────────────────────────────────────────────
    #
    # MSL distinguishes two ids on every task:
    #   id           the *occurrence*  -> task-completed / task-incompleted
    #   schedule_id  the *schedule*    -> edit / delete via /user-schedule
    # Sending the wrong one is not caught: completion 500s, and a delete
    # silently does nothing. So callers must pass the pair, not a bare id.

    def create_task(self, title: str, *, due: date | None = None,
                    subject_id: str | None = None, description: str = "",
                    start: datetime | None = None, end: datetime | None = None,
                    external_id: str | None = None,
                    source: str | None = None) -> dict[str, Any]:
        """Create an open task. Returns the raw created schedule record.

        Its `id` is the creation response id, which is NOT the occurrence id
        you get back from `tasks()` — always re-read the task list to act on
        it (see `find_task`).
        """
        when = start or (datetime.combine(due, datetime.min.time()) if due else None)
        stamp = when.strftime("%Y-%m-%dT%H:%M:%S.000") if when else None
        meta: dict[str, Any] = {"title": title.strip(), "type": "revision"}
        if description:
            meta["description"] = description
        # meta_data is free-form and round-trips, so it carries our own tags:
        # `external_id` lets a re-sync update a task instead of duplicating it.
        if external_id:
            meta["external_id"] = external_id
        if source:
            meta["source"] = source
        body: dict[str, Any] = {
            "entity_type": "TASK",
            "type": "ONCE",
            "is_reoccuring": False,
            "meta_data": meta,
            "task_has_time": bool(start),
        }
        if subject_id:
            body["subject_id"] = subject_id
        if stamp:
            # The list endpoint never returns `due_date`; the date lives in
            # `start_time`. Both are sent because the portal validates the
            # pair, but only start_time comes back.
            body["due_date"] = stamp
            body["start_time"] = stamp
        if end:
            body["end_time"] = end.strftime("%Y-%m-%dT%H:%M:%S.000")
        return self._post("/user-schedule", body)

    def set_task_done(self, occurrence_id: str, done: bool = True) -> dict[str, Any]:
        """Mark complete/incomplete. Needs the occurrence `id`."""
        verb = "task-completed" if done else "task-incompleted"
        r = self.http.get(self.base + f"/user-calendar/{verb}/{occurrence_id}",
                          headers=self._headers(), timeout=self.timeout)
        return self._data(r) or {"ok": True}

    def update_task(self, task: dict[str, Any], *, title: str | None = None,
                    due: date | None = None, description: str | None = None,
                    subject_id: str | None = None) -> dict[str, Any]:
        """Change a task's title/date in place.

        PATCH replaces the record, so omitting `start_time` clears the date
        and the task drops out of every list view. The full existing record is
        therefore resubmitted with only the requested fields changed.
        """
        meta = dict(task.get("meta_data") or {})
        if title is not None:
            meta["title"] = title.strip()
        if description is not None:
            meta["description"] = description
        start = task.get("start_time")
        if due is not None:
            start = f"{due.isoformat()}T00:00:00.000"
        body: dict[str, Any] = {
            "entity_type": "TASK",
            "type": task.get("type") or "ONCE",
            "is_reoccuring": bool(task.get("is_reoccuring")),
            "task_has_time": bool(task.get("task_has_time")),
            "meta_data": meta,
            "start_time": start,
            "due_date": start,
        }
        if task.get("end_time"):
            body["end_time"] = task["end_time"]
        subject = subject_id if subject_id is not None else task.get("subject_id")
        if subject:
            body["subject_id"] = subject
        r = self.http.patch(self.base + "/user-schedule/" + str(task.get("schedule_id")),
                            json=body, headers=self._headers(), timeout=self.timeout)
        return self._data(r) or {}

    def move_task(self, task: dict[str, Any], due: date) -> dict[str, Any]:
        """Reschedule a task (what MSL's own app calls a move)."""
        return self.update_task(task, due=due)

    def delete_task(self, schedule_id: str) -> dict[str, Any]:
        """Delete a task. Needs `schedule_id`, not `id`."""
        r = self.http.delete(self.base + f"/user-schedule/{schedule_id}",
                             headers=self._headers(), timeout=self.timeout)
        return self._data(r) or {"ok": True}

    def find_task(self, needle: str) -> list[dict[str, Any]]:
        """Open tasks whose title contains `needle` (case-insensitive)."""
        needle = needle.strip().lower()
        return [t for t in self.tasks()
                if needle in str((t.get("meta_data") or {}).get("title", "")).lower()]

    def find_by_external_id(self, external_id: str) -> dict[str, Any] | None:
        """The task we created for a given external id, if it still exists."""
        for t in self.tasks():
            if (t.get("meta_data") or {}).get("external_id") == external_id:
                return t
        return None

    def subjects(self) -> list[dict[str, Any]]:
        """Distinct subjects seen on the calendar, for --course lookups."""
        from datetime import timedelta

        seen: dict[str, dict[str, Any]] = {}
        today = date.today()
        for item in self.calendar(today - timedelta(days=14), today + timedelta(days=14)):
            subject = item.get("subject") or {}
            if item.get("subject_id") and subject.get("name"):
                seen.setdefault(item["subject_id"], {"id": item["subject_id"],
                                                     "name": subject["name"]})
        return list(seen.values())

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self.http.post(self.base + path, json=body, headers=self._headers(),
                           timeout=self.timeout)
        return self._data(r) or {}


# ── credentials resolution ───────────────────────────────────────────────

def resolve_credentials(email: str | None = None, password: str | None = None,
                        prompt: bool = True) -> Credentials:
    """flag > env > config > prompt (getpass, so no shell history)."""
    cfg = load_credentials()
    mail = (email or os.environ.get("MSL_EMAIL") or cfg.get("email") or "").strip()
    pw = password or os.environ.get("MSL_PASSWORD") or cfg.get("password") or ""
    provider = os.environ.get("MSL_PROVIDER") or cfg.get("provider") or "email"
    if prompt and not mail:
        mail = input("MyStudyLife email: ").strip()
    if prompt and not pw:
        import getpass

        pw = getpass.getpass("MyStudyLife password: ")
    if not mail or not pw:
        raise LoginError(
            "no credentials: pass --email/--password, set MSL_EMAIL/MSL_PASSWORD, "
            f"or write {_path('config')} with email=/password= lines"
        )
    return Credentials(email=mail, password=pw, provider=provider)
