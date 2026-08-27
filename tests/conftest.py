"""Shared pytest fixtures and lightweight CalDAV stand-ins."""

from __future__ import annotations

from typing import TYPE_CHECKING

from caldav.collection import SynchronizableCalendarObjectCollection

if TYPE_CHECKING:
    from collections.abc import Iterable


class FakeDAVClient:
    """Stands in for `caldav.davclient.DAVClient` without any network access."""

    def __init__(self, username: str | None = "user") -> None:
        """Initializes the fake client with `username` and no credentials set."""
        self.username = username
        self.password: str | None = None
        self.auth: object | None = None


class FakeCalDAVCalendar:
    """Stands in for `caldav.collection.Calendar`, backed by fixed event bytes."""

    def __init__(self, name: str, client: FakeDAVClient, event_bytes: Iterable[bytes] = ()) -> None:
        """Initializes the fake calendar `name` with the objects it should yield."""
        self.name = name
        self._client = client
        self._event_bytes = list(event_bytes)

    def objects(self, *, load_objects: bool = True) -> SynchronizableCalendarObjectCollection:
        del load_objects
        objects = [FakeCalDAVObject(data) for data in self._event_bytes]
        return SynchronizableCalendarObjectCollection(
            calendar=FakeCalendarHandle(self._client),
            objects=objects,
            sync_token="fake-token",  # noqa: S106
        )


class FakeCalendarHandle:
    """Stands in for the `.calendar` attribute of a synced collection."""

    def __init__(self, client: FakeDAVClient) -> None:
        """Initializes the handle, exposing `client` as its `.client` attribute."""
        self.client = client


class FakeCalDAVObject:
    """Stands in for a single synced calendar object exposing raw ICS bytes."""

    def __init__(self, data: bytes) -> None:
        """Initializes the fake object with its raw ICS payload."""
        self._data = data


def make_scoc(
    client: FakeDAVClient,
    event_bytes: Iterable[bytes] = (),
    sync_token: str = "fake-token",  # noqa: S107
) -> SynchronizableCalendarObjectCollection:
    """Builds a `SynchronizableCalendarObjectCollection` backed by fixed event bytes."""
    objects = [FakeCalDAVObject(data) for data in event_bytes]
    return SynchronizableCalendarObjectCollection(
        calendar=FakeCalendarHandle(client), objects=objects, sync_token=sync_token
    )


_VTIMEZONE = """\
BEGIN:VTIMEZONE
TZID:Europe/Berlin
BEGIN:STANDARD
DTSTART:19701025T030000
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CET
END:STANDARD
END:VTIMEZONE
"""

EVENT_ICS = f"""\
BEGIN:VCALENDAR
VERSION:2.0
{_VTIMEZONE}\
BEGIN:VEVENT
UID:event-1@example.com
DTSTART:20260101T090000Z
SUMMARY:First event
END:VEVENT
END:VCALENDAR
""".encode()

OTHER_EVENT_ICS = f"""\
BEGIN:VCALENDAR
VERSION:2.0
{_VTIMEZONE}\
BEGIN:VEVENT
UID:event-2@example.com
DTSTART:20260102T090000Z
SUMMARY:Second event
END:VEVENT
END:VCALENDAR
""".encode()
