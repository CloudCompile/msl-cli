# msl

[![tests](https://github.com/CloudCompile/msl-cli/actions/workflows/tests.yml/badge.svg)](https://github.com/CloudCompile/msl-cli/actions/workflows/tests.yml)

MyStudyLife timetable, tasks and exams in your terminal — built the same
way as `skyward`: Python, typer/rich, `--json` everywhere, and a small
config file instead of storing secrets in shell history.

MyStudyLife's web app is Flutter, so there is no HTML to scrape. This talks
to the app's own backend (`be.mystudylife.com`) using the endpoints and
payloads taken from the shipped `main.dart.js`.

```console
$ msl today
Student Name  ·  student@example.com  ·  Wed Nov 18  (today)

   Time                 What                         Where
   8:50 AM – 9:35 AM    Found/Fitness
   9:40 AM – 10:25 AM   WORLD HISTORY
   10:30 AM – 11:15 AM  GEOMETRY H
   11:15 AM – 12:00 PM  Lunch - S1
   12:05 PM – 12:50 PM  ENG 1: FOUND H
   12:55 PM – 1:40 PM   Study Hall - S1
   1:45 PM – 2:40 PM    Enviro Sci HO
   2:45 PM – 3:30 PM    Concert Band
   5:30 PM – 7:00 PM    Tennis  Courts

Tasks due today
no tasks due today
```

## Commands

| command | shows |
| --- | --- |
| `msl today` | today's classes plus tasks due today or overdue |
| `msl week [--next] [--start DATE]` | Mon–Sun: classes, events, tasks, exams per day |
| `msl timetable [--day DATE]` | the recurring timetable for a weekday |
| `msl tasks [--all] [--overdue]` | your task list, soonest first |
| `msl exams` | scheduled exams |
| `msl whoami` | which account the cached session belongs to |
| `msl logout` | forget the cached tokens |

Writing:

| command | does |
| --- | --- |
| `msl add "Read ch 4" --due tomorrow [-c Biology] [-n note] [--at 15:30]` | create a task |
| `msl done "ch 4"` / `msl undone "ch 4"` | mark complete / reopen |
| `msl rm "ch 4" [-y]` | delete (asks unless `-y`) |

`today`, `week` and `timetable` also take `--json`; `add` takes `--json` too.

`--due` is required and accepts `YYYY-MM-DD`, `today`, `tomorrow`, or a
weekday name. When several tasks match a `done`/`rm` fragment, it lists them
and refuses to guess unless you pass `--yes`.

## Login

```
email=you@example.com
password=…
provider=email        # only if you don't sign in with email
```

in `~/.config/msl/config` (installed `chmod 600`), or `--email/-e` and
`--password/-p`, or `MSL_EMAIL` / `MSL_PASSWORD`.

Tokens: access ~24h, refresh ~30d. They're cached in
`~/.config/msl/session.json` (600, written via a 0600 temp file + rename).
Password is only used when the refresh token has also expired. `msl logout`
deletes the cache.

## What the API actually does (gotchas found live)

- `POST /auth/login` **requires** `provider` (`email`, `google`, `apple`,
  `microsoft`, `facebook`, `dev`). Without it: `400 provider must be one of…`
- `GET /user-calendar/activities` **requires** `entity_type`
  (`TASK|EXAM|HOLIDAY|EXTRA`). A missing value gives the typo'd
  `Invalid Entity Tyoe`.
- `GET /user-calendar/calendar` takes `start_date`/`end_date` and `end_date`
  is **exclusive**. Asking for a single day returns exactly one item rather
  than that day's schedule, so the client always adds a day.
- `GET /user-calendar/dashboard` (the "today" payload) has been observed to
  **omit the first period** of the day. `msl today` therefore takes classes
  from the calendar and uses the dashboard only for counts/flags.
- Times come back as naive local strings (`2026-11-18T09:40:00.464`) and
  are rendered as-is.

### Two ids per task (the write-path trap)

Every task has an occurrence `id` and a `schedule_id`, and they are
**different**. Mixing them up fails *quietly*:

| action | endpoint | id needed |
| --- | --- | --- |
| create | `POST /user-schedule` | — |
| complete / reopen | `GET /user-calendar/task-completed/<id>` | occurrence `id` |
| delete | `DELETE /user-schedule/<schedule_id>` | `schedule_id` |

Completion with a `schedule_id` returns **500**; deletion with an `id`
returns **500 and leaves the task in place**. The response to a create also
carries an id that is neither of the two, so `add` re-reads the task list
rather than trusting the create response.

Undated tasks are accepted (201) but **never appear in any list endpoint**,
so `msl add` requires `--due`.

## Tests

```console
$ python3 -m pytest
```

67 tests: parsers over realistic payloads, every CLI command end-to-end
against `tests/fake_msl.py` (login requirement, entity-type check, exclusive
`end_date`, 401 handling), and the write path including the two-id trap:
completing with a `schedule_id` must fail, deleting with an occurrence `id`
must not delete.

## Notes

- Unofficial; no affiliation with MyStudyLife. It makes the same requests
  the web app makes, with your own account.
- MSL's internals are less stable than Skyward's. If something breaks,
  `msl/client.py` holds the endpoints and `msl/parse.py` the field names.

## See also

- [skyward-cli](https://github.com/CloudCompile/skyward-cli) — grades and assignments from the gradebook
- [msl-cli](https://github.com/CloudCompile/msl-cli) — the MyStudyLife planner, including the sync that fills it
- [classroom-cli](https://github.com/CloudCompile/classroom-cli) — Classroom posts, classwork and attachments
- [agenda-cli](https://github.com/CloudCompile/agenda-cli) — all of the above in one view
- [gsuite-cli](https://github.com/CloudCompile/gsuite-cli) — the school Google account's mail, drive, docs and calendar

## License

MIT — see [LICENSE](LICENSE).
