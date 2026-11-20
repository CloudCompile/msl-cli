"""Client tests: login protocol, token cache, refresh, and the API traps."""
import json
import time

import pytest

from fake_msl import ACCESS, EMAIL, PASSWORD, REFRESH, FakeMSL
from msl import client as msl


@pytest.fixture
def fake():
    with FakeMSL() as f:
        yield f


@pytest.fixture(autouse=True)
def config_dir(tmp_path, monkeypatch):
    """Keep every test's session/config inside tmp_path."""
    monkeypatch.setenv("MSL_CONFIG_DIR", str(tmp_path))
    return tmp_path


def test_login_sends_provider_email(fake):
    c = msl.MSLClient(base=fake.base)
    session = c.login(msl.Credentials(EMAIL, PASSWORD))
    assert session.user.email == EMAIL
    assert session.access_token == ACCESS
    assert session.refresh_token == REFRESH
    body = [r for r in fake.requests if r[1].endswith("/auth/login")][0][2]["body"]
    assert body["provider"] == "email", "the API rejects a login without provider"
    assert body["email"] == EMAIL and body["password"] == PASSWORD


def test_empty_provider_defaults_to_email(fake):
    c = msl.MSLClient(base=fake.base)
    c.login(msl.Credentials(EMAIL, PASSWORD, provider=""))
    body = [r for r in fake.requests if r[1].endswith("/auth/login")][0][2]["body"]
    assert body["provider"] == "email"


def test_wrong_password_raises(fake):
    c = msl.MSLClient(base=fake.base)
    with pytest.raises(msl.LoginError) as e:
        c.login(msl.Credentials(EMAIL, "nope"))
    assert "Invalid email or password" in str(e.value)


def test_calendar_end_date_is_inclusive(fake):
    """A same-day range must return that day, not one arbitrary item."""
    c = msl.MSLClient(base=fake.base)
    c.login(msl.Credentials(EMAIL, PASSWORD))
    from datetime import date

    same_day = c.calendar(date.today(), date.today())
    classes = [x for x in same_day if x.get("entity_type") == "CLASS"]
    assert len(classes) == 4, "3 classes + lunch"
    assert any(x.get("entity_type") == "EXTRA" for x in same_day)
    sent = [r for r in fake.requests if "/user-calendar/calendar" in r[1]][-1][2]["query"]
    assert sent["end_date"][0] == (date.today().fromordinal(date.today().toordinal() + 1)).isoformat()


def test_tasks_come_from_calendar_and_are_deduped(fake):
    """The activities endpoint hides past/today tasks; the calendar sees all.

    The fake returns each task twice (as the real API does) to prove `tasks()`
    dedupes by id.
    """
    c = msl.MSLClient(base=fake.base)
    c.login(msl.Credentials(EMAIL, PASSWORD))
    tasks = c.tasks()
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids)), "duplicate occurrences must collapse"
    assert len(tasks) == 4


def test_activities_requires_entity_type(fake):
    c = msl.MSLClient(base=fake.base)
    c.login(msl.Credentials(EMAIL, PASSWORD))
    assert c.exams()[0]["meta_data"]["name"] == "Midterm"
    assert c.activities("HOLIDAY") == []


def test_bad_entity_type_surfaces_server_message(fake):
    c = msl.MSLClient(base=fake.base)
    c.login(msl.Credentials(EMAIL, PASSWORD))
    with pytest.raises(msl.MSLError) as e:
        c.activities("HOMEWORK")
    assert "Invalid Entity Tyoe" in str(e.value)   # sic, from the real API


def test_requests_require_token(fake):
    c = msl.MSLClient(base=fake.base)
    with pytest.raises(msl.LoginError):
        c.dashboard()


def test_expired_token_triggers_refresh(fake):
    c = msl.MSLClient(base=fake.base)
    c.login(msl.Credentials(EMAIL, PASSWORD))
    fake.valid_access.clear()          # server forgets our access token
    c.session.access_token = "stale"
    with pytest.raises(msl.AuthExpired):
        c.dashboard()
    c.refresh(c.session.refresh_token)
    assert fake.refresh_calls == 1
    assert c.session.access_token == "access-token-2"
    assert c.dashboard()["classCount"] == 3


def test_ensure_session_uses_cache_without_password(fake):
    c1 = msl.MSLClient(base=fake.base)
    c1.login(msl.Credentials(EMAIL, PASSWORD))
    msl.save_session(c1.session)

    c2 = msl.MSLClient(base=fake.base)
    session = c2.ensure_session(None)      # no credentials at all
    assert session.access_token in fake.valid_access
    assert not [r for r in fake.requests[1:] if r[1].endswith("/auth/login")]


def test_ensure_session_refreshes_expired_cache(fake, monkeypatch):
    c1 = msl.MSLClient(base=fake.base)
    c1.login(msl.Credentials(EMAIL, PASSWORD))
    msl.save_session(c1.session)
    monkeypatch.setattr(msl.MSLClient, "_token_alive", staticmethod(lambda tok: False))

    c2 = msl.MSLClient(base=fake.base)
    session = c2.ensure_session(None)
    assert fake.refresh_calls == 1
    assert session.user.email == EMAIL


def test_ensure_session_falls_back_to_login(fake, monkeypatch, tmp_path):
    import json as _json

    (_json_path := tmp_path / "session.json").write_text(_json.dumps({
        "user": {"email": EMAIL}, "access_token": "dead", "refresh_token": "dead", "saved_at": 0,
    }))
    monkeypatch.setattr(msl.MSLClient, "_token_alive", staticmethod(lambda tok: False))
    c = msl.MSLClient(base=fake.base)
    session = c.ensure_session(msl.Credentials(EMAIL, PASSWORD))
    assert session.access_token == ACCESS


def test_session_file_is_private(fake, tmp_path):
    c = msl.MSLClient(base=fake.base)
    c.login(msl.Credentials(EMAIL, PASSWORD))
    msl.save_session(c.session)
    path = tmp_path / "session.json"
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600
    text = path.read_text()
    assert "correct horse" not in text and PASSWORD not in text


def test_clear_session(fake):
    c = msl.MSLClient(base=fake.base)
    c.login(msl.Credentials(EMAIL, PASSWORD))
    msl.save_session(c.session)
    msl.clear_session()
    assert msl.load_session() is None


def test_token_alive_reads_exp():
    import base64

    def jwt(exp):
        head = base64.urlsafe_b64encode(b'{"alg":"HS256"}').decode().rstrip("=")
        body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
        return f"{head}.{body}.sig"

    assert msl.MSLClient._token_alive(jwt(time.time() + 3600))
    assert not msl.MSLClient._token_alive(jwt(time.time() - 10))
    assert not msl.MSLClient._token_alive("garbage")


def test_resolve_credentials_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("MSL_CONFIG", str(tmp_path / "config"))
    (tmp_path / "config").write_text("email=file@example.com\npassword=filepw\nprovider=email\n")
    for var in ("MSL_EMAIL", "MSL_PASSWORD", "MSL_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    creds = msl.resolve_credentials(prompt=False)
    assert (creds.email, creds.password) == ("file@example.com", "filepw")

    monkeypatch.setenv("MSL_EMAIL", "env@example.com")
    assert msl.resolve_credentials("flag@example.com", prompt=False).email == "flag@example.com"
    assert msl.resolve_credentials(prompt=False).email == "env@example.com"


def test_resolve_credentials_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("MSL_CONFIG", str(tmp_path / "missing"))
    for var in ("MSL_EMAIL", "MSL_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(msl.LoginError) as e:
        msl.resolve_credentials(prompt=False)
    assert "no credentials" in str(e.value)
