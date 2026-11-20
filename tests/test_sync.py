"""Tests for the sync planner and its idempotency guarantees."""
from datetime import date


from msl import sync


class Week:
    def __init__(self, assignments):
        self.assignments = assignments


class A:
    def __init__(self, title, due, needs_review=False, reason="", details=""):
        self.title = title
        self.due = due
        self.needs_review = needs_review
        self.reason = reason
        self.details = details


def test_key_is_stable_and_distinct():
    a = sync.key_for("Geometry Honors", "4.3 SSS & SAS", date(2026, 11, 17))
    b = sync.key_for("geometry honors ", "4.3  sss & sas", date(2026, 11, 17))
    c = sync.key_for("Geometry Honors", "4.3 SSS & SAS", date(2026, 11, 18))
    assert a == b, "whitespace and case must not change identity"
    assert a != c, "a different due date is a different task"
    assert a.startswith("classroom:")


def test_candidates_from_week():
    week = Week([A("4.3 SSS & SAS", date(2026, 11, 17), details="pg.305")])
    cands = sync.candidates_from_week(week, "Geometry Honors")
    assert len(cands) == 1
    assert cands[0].course == "Geometry Honors"
    assert cands[0].details == "pg.305"
    assert cands[0].key.startswith("classroom:")


def test_first_sync_creates_everything():
    cands = sync.candidates_from_week(
        Week([A("A", date(2026, 11, 17)), A("B", date(2026, 11, 18))]), "Geo")
    plan = sync.build_plan(cands, existing=[])
    assert len(plan.create) == 2
    assert not plan.update


def test_second_sync_is_a_no_op():
    cands = sync.candidates_from_week(Week([A("A", date(2026, 11, 17))]), "Geo")
    existing = [{"schedule_id": "s1", "start_time": "2026-11-17T00:00:00.000",
                 "meta_data": {"external_id": cands[0].key, "title": "A"}}]
    plan = sync.build_plan(cands, existing)
    assert not plan.create and not plan.update
    assert len(plan.unchanged) == 1


def test_changed_date_updates_instead_of_duplicating():
    cands = sync.candidates_from_week(Week([A("A", date(2026, 11, 20))]), "Geo")
    existing = [{"schedule_id": "s1", "start_time": "2026-11-17T00:00:00.000",
                 "meta_data": {"external_id": cands[0].key, "title": "A"}}]
    plan = sync.build_plan(cands, existing)
    assert not plan.create, "must not duplicate"
    assert len(plan.update) == 1
    cand, task = plan.update[0]
    assert task["schedule_id"] == "s1"


def test_needs_review_entries_are_never_written():
    cands = sync.candidates_from_week(
        Week([A("Check for Learning", None, needs_review=True, reason="no due date"),
              A("Real", date(2026, 11, 19))]), "Geo")
    plan = sync.build_plan(cands, existing=[])
    assert [c.title for c in plan.create] == ["Real"]
    assert [c.title for c in plan.skipped] == ["Check for Learning"]


def test_undated_entries_are_skipped_even_if_not_flagged():
    cands = [sync.Candidate(course="Geo", title="No date", due=None)]
    plan = sync.build_plan(cands, existing=[])
    assert plan.skipped and not plan.create


def test_unrelated_existing_tasks_are_ignored():
    cands = sync.candidates_from_week(Week([A("A", date(2026, 11, 17))]), "Geo")
    existing = [{"schedule_id": "other", "start_time": "2026-01-01T00:00:00.000",
                 "meta_data": {"external_id": "personally-added", "title": "Mine"}}]
    plan = sync.build_plan(cands, existing)
    assert len(plan.create) == 1


def test_tasks_without_external_id_are_ignored():
    cands = sync.candidates_from_week(Week([A("A", date(2026, 11, 17))]), "Geo")
    existing = [{"schedule_id": "s", "start_time": "2026-11-17T00:00:00.000",
                 "meta_data": {"title": "A"}}]
    plan = sync.build_plan(cands, existing)
    assert len(plan.create) == 1, "a lookalike task the user made is not ours to touch"


# ── applying ─────────────────────────────────────────────────────────────

class FakeClient:
    def __init__(self):
        self.created = []
        self.updated = []

    def subjects(self):
        return [{"id": "subj-geo", "name": "Geometry Honors"}]

    def create_task(self, title, **kw):
        self.created.append((title, kw))
        return {}

    def update_task(self, task, **kw):
        self.updated.append((task["schedule_id"], kw))
        return {}


def test_dry_run_writes_nothing():
    cands = sync.candidates_from_week(Week([A("A", date(2026, 11, 17))]), "Geo")
    plan = sync.build_plan(cands, [])
    client = FakeClient()
    out = sync.apply_plan(client, plan, dry_run=True)
    assert client.created == [] and client.updated == []
    assert len(out["created"]) == 1 and out["dry_run"] is True


def test_apply_creates_with_external_id_and_subject():
    cands = sync.candidates_from_week(Week([A("A", date(2026, 11, 17), details="d")]), "Geometry Honors")
    plan = sync.build_plan(cands, [])
    client = FakeClient()
    out = sync.apply_plan(client, plan)
    assert len(client.created) == 1
    title, kw = client.created[0]
    assert title == "A"
    assert kw["due"] == date(2026, 11, 17)
    assert kw["external_id"] == cands[0].key
    assert kw["subject_id"] == "subj-geo"
    assert kw["description"] == "d"
    assert out["created"]


def test_apply_updates_with_schedule_id():
    cands = sync.candidates_from_week(Week([A("A", date(2026, 11, 20))]), "Geo")
    existing = [{"schedule_id": "sched-9", "start_time": "2026-11-17T00:00:00.000",
                 "meta_data": {"external_id": cands[0].key}}]
    plan = sync.build_plan(cands, existing)
    client = FakeClient()
    sync.apply_plan(client, plan)
    assert client.updated == [("sched-9", {"due": date(2026, 11, 20), "title": "A",
                                           "subject_id": "subj-geo"})]


def test_failures_are_reported_not_raised():
    cands = sync.candidates_from_week(Week([A("A", date(2026, 11, 17)),
                                            A("B", date(2026, 11, 18))]), "Geo")
    plan = sync.build_plan(cands, [])

    class Flaky(FakeClient):
        def create_task(self, title, **kw):
            if title == "A":
                raise RuntimeError("portal said no")
            return super().create_task(title, **kw)

    out = sync.apply_plan(Flaky(), plan)
    assert len(out["failed"]) == 1
    assert out["failed"][0]["title"] == "A"
    assert len(out["created"]) == 1, "one failure must not stop the rest"


# ── subject matching ─────────────────────────────────────────────────────

SUBJECTS = [
    {"id": "s-geo", "name": "Geometry H"},
    {"id": "s-env", "name": "Enviro Sci Ho"},
    {"id": "s-wh", "name": "World History"},
    {"id": "s-lunch", "name": "Lunch S 1"},
]


def test_subject_exact():
    assert sync.match_subject("Geometry H", SUBJECTS) == "s-geo"


def test_subject_abbreviation():
    assert sync.match_subject("Environmental Science - 7", SUBJECTS) == "s-env"
    assert sync.match_subject("Geometry Honors", SUBJECTS) == "s-geo"


def test_subject_unknown_is_none():
    assert sync.match_subject("Astronomy", SUBJECTS) is None
    assert sync.match_subject("", SUBJECTS) is None
    assert sync.match_subject("Geometry", []) is None


def test_subject_does_not_match_on_one_shared_word():
    """'English' alone must not attach to an unrelated class."""
    assert sync.match_subject("English 9H 26-27", SUBJECTS) is None
