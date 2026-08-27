"""Tests for `caldav2ics.cli`."""

from __future__ import annotations

import getpass
import sys
from pathlib import Path  # noqa: TC003 (doctyper evaluates this annotation at runtime)
from unittest.mock import MagicMock

import pytest
import requests.auth
from caldav.collection import SynchronizableCalendarObjectCollection
from conftest import (
    EVENT_ICS,
    OTHER_EVENT_ICS,
    FakeCalDAVCalendar,
    FakeCalDAVObject,
    FakeDAVClient,
    make_scoc,
)
from icalendar import Calendar

from caldav2ics import cli


def test_get_options_sanitizes_name_but_keeps_full_name_in_display_fields() -> None:
    alnum, options = cli.get_options("My Calendar #1!", owner="Acme")

    assert alnum == "MyCalendar1"
    assert options["prodid"] == "-//Acme//MyCalendar1//EN"
    assert options["x-wr-calname"] == "My Calendar #1!"
    assert options["x-wr-caldesc"] == "My Calendar #1!"


def test_write_calendar_merges_events_and_dedupes_shared_timezone(tmp_path: Path) -> None:
    objects = [FakeCalDAVObject(EVENT_ICS), FakeCalDAVObject(OTHER_EVENT_ICS)]
    scoc = make_scoc(FakeDAVClient(), event_bytes=[])
    scoc.objects = objects

    cli.write_calendar("Team Calendar", scoc, "Acme", tmp_path)

    written = tmp_path / "TeamCalendar.ics"
    assert written.exists()
    parsed = Calendar.from_ical(written.read_bytes())
    uids = {str(event["UID"]) for event in parsed.walk("VEVENT")}
    assert uids == {"event-1@example.com", "event-2@example.com"}
    assert len(parsed.walk("VTIMEZONE")) == 1


def test_calendar_cache_save_and_load_round_trip(tmp_path: Path) -> None:
    client = FakeDAVClient(username="alice")
    cache = cli.CalendarCache.__new__(cli.CalendarCache)
    cache["Personal"] = make_scoc(client, event_bytes=[EVENT_ICS], sync_token="abc")  # noqa: S106

    cache_path = tmp_path / "cache.pkl"
    cache.save(cache_path)
    loaded = cli.CalendarCache.load(cache_path)

    assert set(loaded) == {"Personal"}
    assert loaded["Personal"].sync_token == "abc"  # noqa: S105
    assert len(loaded["Personal"].objects) == 1
    assert loaded["Personal"].calendar.client.username == "alice"


def test_calendar_cache_load_rejects_non_dict_payload(tmp_path: Path) -> None:
    import pickle

    path = tmp_path / "cache.pkl"
    path.write_bytes(pickle.dumps(["not", "a", "dict"]))

    with pytest.raises(ValueError, match="Invalid cache"):
        cli.CalendarCache.load(path)


def test_calendar_cache_load_rejects_wrong_value_types(tmp_path: Path) -> None:
    import pickle

    path = tmp_path / "cache.pkl"
    path.write_bytes(pickle.dumps({"Personal": "not-a-collection"}))

    with pytest.raises(ValueError, match="Invalid cache"):
        cli.CalendarCache.load(path)


def test_calendar_cache_init_fetches_objects_and_strips_credentials() -> None:
    client = FakeDAVClient(username="bob")
    client.password = "leftover-secret"  # noqa: S105
    client.auth = "leftover-auth"
    fake_cal = FakeCalDAVCalendar("Work", client, event_bytes=[EVENT_ICS, OTHER_EVENT_ICS])

    cache = cli.CalendarCache([fake_cal])  # type: ignore[list-item]

    assert set(cache) == {"Work"}
    assert len(cache["Work"].objects) == 2
    assert getattr(client, "password") is None  # noqa: B009
    assert getattr(client, "auth") is None  # noqa: B009


def test_calendar_cache_sync_aggregates_update_counts_across_calendars(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fake_sync(self: SynchronizableCalendarObjectCollection) -> tuple[list[str], list[str]]:
        del self
        return (["a", "b"], [])

    monkeypatch.setattr(SynchronizableCalendarObjectCollection, "sync", fake_sync)

    client = FakeDAVClient(username="carol")
    cache = cli.CalendarCache.__new__(cli.CalendarCache)
    cache["A"] = make_scoc(client, event_bytes=[EVENT_ICS])
    cache["B"] = make_scoc(client, event_bytes=[])

    with caplog.at_level("INFO"):
        cache.sync("pw")

    assert "Updated 4 objects" in caplog.text
    assert client.password is None
    assert client.auth is None


def test_get_passwd_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("C2I_PASSWORD", "from-env")

    assert cli.get_passwd() == "from-env"


def test_get_passwd_prompts_when_env_var_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("C2I_PASSWORD", raising=False)
    monkeypatch.setattr(getpass, "getpass", lambda prompt: "typed-password")  # noqa: ARG005

    assert cli.get_passwd() == "typed-password"


def test_get_passwd_raises_when_nothing_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("C2I_PASSWORD", raising=False)
    monkeypatch.setattr(getpass, "getpass", lambda prompt: "")  # noqa: ARG005

    with pytest.raises(SystemExit, match="Password not provided"):
        cli.get_passwd()


def test_remove_passwd_clears_client_credentials() -> None:
    client = FakeDAVClient(username="dave")
    client.password = "secret"  # noqa: S105
    client.auth = "some-auth"
    scoc = make_scoc(client)

    cli.remove_passwd(scoc)

    assert getattr(client, "password") is None  # noqa: B009
    assert getattr(client, "auth") is None  # noqa: B009


def test_temp_passwd_sets_basic_auth_then_restores_none() -> None:
    client = FakeDAVClient(username="erin")
    scoc = make_scoc(client)

    with cli.temp_passwd(scoc, "hunter2"):
        assert client.password == "hunter2"  # noqa: S105
        assert isinstance(client.auth, requests.auth.HTTPBasicAuth)
        assert client.auth.username == "erin"
        assert client.auth.password == "hunter2"  # noqa: S105

    assert getattr(client, "password") is None  # noqa: B009
    assert getattr(client, "auth") is None  # noqa: B009


def test_temp_passwd_raises_without_username() -> None:
    client = FakeDAVClient(username=None)
    scoc = make_scoc(client)

    with pytest.raises(ValueError, match="No username set"), cli.temp_passwd(scoc, "pw"):
        pass  # pragma: no cover (unreachable: temp_passwd raises before yielding)


def test_create_cache_builds_collection_from_principal_calendars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    class FakePrincipal:
        def calendars(self) -> list[FakeCalDAVCalendar]:
            return [FakeCalDAVCalendar("Shared", FakeDAVClient(), event_bytes=[EVENT_ICS])]

    class FakeDAVClientClass:
        def __init__(self, url: str, username: str, password: str) -> None:
            captured["url"] = url
            captured["username"] = username
            captured["password"] = password

        def principal(self) -> FakePrincipal:
            return FakePrincipal()

    monkeypatch.setattr(cli, "DAVClient", FakeDAVClientClass)

    cache = cli.create_cache("https://caldav.example.com", "frank", "pw")

    assert captured == {"url": "https://caldav.example.com", "username": "frank", "password": "pw"}
    assert set(cache) == {"Shared"}


def test_update_cache_creates_cache_and_writes_ics_when_none_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calendar_client = FakeDAVClient(username="grace")
    fresh_cache = cli.CalendarCache.__new__(cli.CalendarCache)
    fresh_cache["Personal"] = make_scoc(calendar_client, event_bytes=[EVENT_ICS])
    monkeypatch.setattr(
        cli,
        "create_cache",
        lambda url, user, passwd: fresh_cache,  # noqa: ARG005
    )

    config: cli.Config = {"url": "https://x", "user": "grace", "save-dir": str(tmp_path)}
    cli.update_cache(config, "pw")

    assert (tmp_path / "cache.pkl").exists()
    assert (tmp_path / "Personal.ics").exists()


def test_update_cache_syncs_existing_cache_instead_of_recreating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_sync(self: SynchronizableCalendarObjectCollection) -> tuple[list[str], list[str]]:
        del self
        return ([], [])

    monkeypatch.setattr(SynchronizableCalendarObjectCollection, "sync", fake_sync)

    client = FakeDAVClient(username="heidi")
    existing = cli.CalendarCache.__new__(cli.CalendarCache)
    existing["Personal"] = make_scoc(client, event_bytes=[EVENT_ICS])
    existing.save(tmp_path / "cache.pkl")

    create_cache = MagicMock()
    monkeypatch.setattr(cli, "create_cache", create_cache)

    config: cli.Config = {"url": "https://x", "user": "heidi", "save-dir": str(tmp_path)}
    cli.update_cache(config, "pw")

    create_cache.assert_not_called()
    assert (tmp_path / "Personal.ics").exists()


def test_sync_exits_when_config_file_is_missing(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="Invalid config file"):
        cli.sync(tmp_path / "missing.toml")


def test_sync_exits_on_schema_mismatch(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[server_a]\nurl = "https://x"\nsave-dir = "/tmp/x"\n')

    with pytest.raises(SystemExit, match="Invalid config file"):
        cli.sync(config_path)


def test_sync_exits_when_save_dir_is_relative(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[server_a]\nurl = "https://x"\nuser = "u"\nsave-dir = "relative"\n')

    with pytest.raises(SystemExit, match="Save directory must be an absolute path"):
        cli.sync(config_path)


def test_sync_exits_when_save_dir_is_a_file(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("occupied")
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[server_a]\nurl = "https://x"\nuser = "u"\nsave-dir = "{blocked}"\n')

    with pytest.raises(SystemExit, match="Save directory must be a directory or non existent"):
        cli.sync(config_path)


def test_sync_updates_every_configured_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    save_dir_a = tmp_path / "a"
    save_dir_b = tmp_path / "b"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[server_a]\nurl = "https://a"\nuser = "u"\nsave-dir = "{save_dir_a}"\n\n'
        f'[server_b]\nurl = "https://b"\nuser = "u"\nsave-dir = "{save_dir_b}"\n'
    )
    monkeypatch.setattr(cli, "get_passwd", lambda: "pw")
    updated: list[tuple[str, str]] = []
    monkeypatch.setattr(
        cli,
        "update_cache",
        lambda config, passwd: updated.append((config["url"], passwd)),
    )

    cli.sync(config_path)

    assert sorted(updated) == [("https://a", "pw"), ("https://b", "pw")]
    assert save_dir_a.is_dir()
    assert save_dir_b.is_dir()


def test_main_registers_sync_as_the_default_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    received: dict[str, Path] = {}

    def fake_sync(config_file: Path) -> None:
        received["config_file"] = config_file

    monkeypatch.setattr(cli, "sync", fake_sync)
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(sys, "argv", ["caldav2ics", str(config_path)])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 0
    assert received["config_file"] == config_path
