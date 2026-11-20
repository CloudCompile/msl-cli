"""A fake MyStudyLife backend reproducing the real API's quirks.

Confirmed behaviours worth pinning down:

* `/auth/login` rejects a missing `provider` with
  "provider must be one of the following values: ...".
* `/user-calendar/activities` rejects a missing/unknown `entity_type`
  with "Invalid Entity Tyoe" (sic).
* `/user-calendar/calendar` treats `end_date` as **exclusive** and returns
  a single item when start == end.
* Everything except auth answers 401 `Token Verification Failed` without
  a bearer token.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from fixtures import data

EMAIL = "student@example.com"
PASSWORD = "correct horse"
ACCESS = "access-token-1"
REFRESH = "refresh-token-1"
PROVIDERS = {"google", "apple", "microsoft", "email", "facebook", "dev"}


class FakeMSL:
    def __init__(self):
        self.requests: list[tuple[str, str, dict]] = []
        # writable copy, so tests can create/complete/delete without touching
        # the module-level fixture data
        self.tasks: list[dict] = [dict(t) for t in data.ACTIVITIES["TASK"]]
        self.refresh_calls = 0
        self.valid_access = {ACCESS}

    # ── responses ───────────────────────────────────────────────────────

    @staticmethod
    def _ok(data, status=200):
        return status, {"success": True, "statusCode": status, "data": data}

    @staticmethod
    def _err(message, status=400):
        return status, {"success": False, "statusCode": status, "message": message}

    def handle(self, method: str, raw_path: str, query: dict[str, list[str]],
               body: dict, headers: dict[str, str]):
        self.requests.append((method, raw_path, {"query": query, "body": body}))
        path = urlsplit(raw_path).path
        if path == "/auth/login" and method == "POST":
            provider = body.get("provider")
            if provider not in PROVIDERS:
                return self._err(
                    " provider must be one of the following values: " + ", ".join(sorted(PROVIDERS)))
            if body.get("email") != EMAIL or body.get("password") != PASSWORD:
                return self._err("Invalid email or password", 401)
            return self._ok({"user": data.USER, "accessToken": ACCESS,
                             "refreshToken": REFRESH}, 201)
        if path == "/auth/refresh-token" and method == "POST":
            self.refresh_calls += 1
            if body.get("refreshToken") != REFRESH:
                return self._err("Invalid refresh token", 401)
            new = "access-token-2"
            self.valid_access.add(new)
            return self._ok({"accessToken": new}, 201)

        auth = headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] not in self.valid_access:
            return self._err("Token Verification Failed", 401)

        if path == "/user-calendar/dashboard":
            return self._ok(data.DASHBOARD)
        if path == "/user-calendar/calendar":
            start = (query.get("start_date") or [""])[0]
            end = (query.get("end_date") or [""])[0]
            if not start or not end:
                return self._err(" start_date should not be empty,  end_date should not be empty")
            # Tasks live here too. The real API repeats an occurrence per day
            # of its span, which is what `tasks()` must dedupe.
            tasks = []
            for t in self.tasks:
                row = dict(t)
                row["entity_type"] = "TASK"
                row.setdefault("start_time", row.get("due_date") or None)
                tasks.append(row)
                tasks.append(dict(row))       # second occurrence, same id
            return self._ok(data.calendar_between(start, end) + tasks)
        if path == "/user-calendar/activities":
            entity = (query.get("entity_type") or [""])[0].upper()
            if entity not in ("TASK", "EXAM", "HOLIDAY", "EXTRA"):
                return self._err("Invalid Entity Tyoe")
            if entity == "TASK":
                return self._ok([dict(t) for t in self.tasks])
            return self._ok(data.ACTIVITIES.get(entity, []))
        if path == "/user/profile":
            return self._ok({"user": data.USER})

        # ── writes ──────────────────────────────────────────────────────
        if path == "/user-schedule" and method == "POST":
            title = (body.get("meta_data") or {}).get("title")
            if not title:
                return self._err("meta_data.title should not be empty")
            created = data.create_task(body)
            self.tasks.append(created)
            return self._ok(created, 201)
        for prefix in ("/user-calendar/task-completed/", "/user-calendar/task-incompleted/"):
            if path.startswith(prefix) and method == "GET":
                occ_id = path[len(prefix):]
                # the real API 500s when handed a schedule id instead of an
                # occurrence id, and silently keeps the task uncompleted
                if not any(t["id"] == occ_id for t in self.tasks):
                    return self._err("Internal server error", 500)
                done = prefix.endswith("task-completed/")
                for t in self.tasks:
                    if t["id"] == occ_id:
                        t["completed"] = done
                return self._ok({"ok": True})
        if path.startswith("/user-schedule/") and method == "DELETE":
            target = path[len("/user-schedule/"):]
            if not any(t["schedule_id"] == target for t in self.tasks):
                return self._err("Internal server error", 500)
            self.tasks = [t for t in self.tasks if t["schedule_id"] != target]
            return self._ok({"id": target})
        return 404, {"message": f"Cannot GET {path}", "statusCode": 404}

    # ── server plumbing ─────────────────────────────────────────────────

    def __enter__(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status: int, payload: dict):
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                parts = urlsplit(self.path)
                query = parse_qs(parts.query, keep_blank_values=True)
                status, payload = fake.handle("GET", self.path, query, {}, dict(self.headers))
                self._send(status, payload)

            def do_DELETE(self):
                parts = urlsplit(self.path)
                query = parse_qs(parts.query, keep_blank_values=True)
                status, payload = fake.handle("DELETE", self.path, query, {}, dict(self.headers))
                self._send(status, payload)

            def do_POST(self):
                parts = urlsplit(self.path)
                query = parse_qs(parts.query, keep_blank_values=True)
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    body = {}
                status, payload = fake.handle("POST", self.path, query, body, dict(self.headers))
                self._send(status, payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()

    @property
    def base(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"
