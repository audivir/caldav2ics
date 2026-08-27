"""Command-line interface for caldav2ics."""

from __future__ import annotations

import getpass
import logging
import os
import pickle
import string
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

import doctyper
import msgspec
import requests.auth
from caldav.collection import Calendar as CalDAVCalendar
from caldav.collection import SynchronizableCalendarObjectCollection as SyncCollection
from caldav.davclient import DAVClient
from icalendar import Calendar, Event
from typing_extensions import Self

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable

    from _typeshed import StrPath


logger = logging.getLogger(__name__)


# must be functional to use hyphen as key.
Config = TypedDict("Config", {"url": str, "user": str, "save-dir": str})


class CalendarCache(dict[str, SyncCollection]):
    """Manages a local cache of synced calendar objects."""

    def __init__(self, cals: Iterable[CalDAVCalendar]) -> None:
        """Initializes the cache by fetching objects for each calendar in `cals`."""
        for cal in cals:
            start = time.perf_counter()
            elem = cal.objects(load_objects=True)
            # objects() only returns a coroutine for async clients; ours is synchronous.
            assert isinstance(elem, SyncCollection)  # noqa: S101  # pragma: no branch
            remove_passwd(elem)
            name = cal.name
            assert name is not None  # noqa: S101  # pragma: no branch
            self[name] = elem
            logger.info(
                "Loaded %s objects from %s in %ss",
                len(self[name]),
                name,
                round(time.perf_counter() - start, 2),
            )

    @classmethod
    def _load(cls, cache: Any) -> Self:
        """Validates and wraps a decoded pickle payload as a `CalendarCache`."""
        if not isinstance(cache, dict) or not all(
            isinstance(key, str) and isinstance(value, SyncCollection)
            for key, value in cache.items()
        ):
            raise ValueError("Invalid cache")

        obj = cls.__new__(cls)
        for key, value in cache.items():
            obj[key] = value
        return obj

    @classmethod
    def load(cls, path: StrPath) -> Self:
        """Loads cache from `path`."""
        cache = pickle.loads(Path(path).read_bytes())
        return cls._load(cache)

    def save(self, path: StrPath) -> None:
        """Saves cache to `path`."""
        parent = dict(self.items())
        Path(path).write_bytes(pickle.dumps(parent))

    def sync(self, passwd: str) -> None:
        """Syncs the cache with the server."""
        n_updated = 0
        start = time.monotonic()
        for obj in self.values():
            with temp_passwd(obj, passwd):
                updated = obj.sync()
            # sync() only returns a coroutine for async clients; ours is synchronous.
            assert isinstance(updated, tuple)  # noqa: S101  # pragma: no branch
            obj.objects = list(obj.objects)
            n_updated += len(updated[0])
        logger.info("Updated %s objects in %ss", n_updated, round(time.monotonic() - start, 2))


def get_passwd() -> str:
    """Gets the password from the `C2I_PASSWORD` environment variable or prompts for it."""
    passwd = os.getenv("C2I_PASSWORD")
    if not passwd:
        passwd = getpass.getpass("Password: ")
        if not passwd:
            raise SystemExit("Password not provided")
    return passwd


def remove_passwd(elem: SyncCollection) -> None:
    """Removes password from `elem`."""
    client: DAVClient = elem.calendar.client
    client.auth = None
    client.password = None


@contextmanager
def temp_passwd(elem: SyncCollection, passwd: str) -> Generator[None]:
    """Temporarily sets password to `passwd`."""
    client: DAVClient = elem.calendar.client
    if client.username is None:
        raise ValueError("No username set")
    try:
        client.password = passwd
        client.auth = requests.auth.HTTPBasicAuth(client.username, client.password)
        yield
    finally:
        remove_passwd(elem)


def isalnum(c: str) -> bool:
    """Checks whether `c` is an alphanumeric character."""
    return c in set(string.ascii_letters + string.digits)


def get_options(name: str, owner: str = "Owner") -> tuple[str, dict[str, str]]:
    """Builds a filesystem-safe file stem and ICS metadata options for a calendar.

    Args:
        name: Calendar name used for `X-WR-CALNAME`/`X-WR-CALDESC` and the file stem.
        owner: Organization name embedded in the ICS `PRODID`.

    Returns:
        Tuple of the sanitized alphanumeric file stem and the ICS property mapping.
    """
    alnum = "".join([c for c in name if isalnum(c)])
    return alnum, {
        "version": "2.0",
        "prodid": f"-//{owner}//{alnum}//EN",
        "calscale": "GREGORIAN",
        "method": "PUBLISH",
        "refresh-interval;value=duration": "PT15M",
        "x-published-ttl": "PT15M",
        "x-wr-calname": name,
        "x-wr-caldesc": name,
    }


def write_calendar(name: str, cal: SyncCollection, owner: str, save_dir: StrPath) -> None:
    """Writes calendar `name` as an ICS file under `save_dir`, merging its timezones and events."""
    calendar = Calendar()
    timezones_cache = []

    alnum, options = get_options(name, owner)
    for key, value in options.items():
        calendar.add(key, value)

    events = []
    for obj in cal:
        cal_ = Calendar.from_ical(obj._data)  # noqa: SLF001
        for timezone in cal_.walk("VTIMEZONE"):
            if timezone["tzid"] not in timezones_cache:
                timezones_cache.append(timezone["tzid"])
                calendar.add_component(timezone)
        for event in cal_.walk("VEVENT"):
            event_copy = Event(event)
            events.append(event_copy)

    for event in events:
        # event_copy.add('categories', category) # noqa: ERA001
        calendar.add_component(event)

    cal_path = Path(save_dir) / (alnum + ".ics")
    cal_path.write_bytes(calendar.to_ical())


def create_cache(url: str, user: str, passwd: str) -> CalendarCache:
    """Creates a cache by fetching every calendar for `user` from the CalDAV server at `url`."""
    client = DAVClient(url=url, username=user, password=passwd)
    principal = client.principal()  # type: ignore[no-untyped-call]
    calendars = principal.calendars()
    return CalendarCache(calendars)


def update_cache(config: Config, passwd: str) -> None:
    """Updates the cache for `config`, creating it first if missing, then rewrites ICS files."""
    url, user, save_dir = config["url"], config["user"], config["save-dir"]
    save_dir_path = Path(save_dir)
    cache_path = save_dir_path / "cache.pkl"
    logger.info("Cache path: %s", cache_path)

    if not cache_path.exists():
        logger.info("Creating cache...")
        cache = create_cache(url, user, passwd)

    else:
        logger.info("Loading cache...")
        cache = CalendarCache.load(cache_path)
        cache.sync(passwd)

    cache.save(cache_path)

    start = time.monotonic()
    for name, cal in cache.items():
        write_calendar(name, cal, user, save_dir_path)
    logger.info("Wrote calendars in %s seconds", round(time.monotonic() - start, 2))


def sync(config_file: Path) -> None:
    """Syncs calendars from the CalDAV server(s) described in `config_file` to local ICS files.

    Args:
        config_file: Path to the TOML config file.
    """
    logging.basicConfig(level=logging.INFO)

    try:
        content = msgspec.toml.decode(config_file.read_bytes())
        configs = msgspec.convert(content, dict[str, Config])
    except (FileNotFoundError, msgspec.ValidationError) as e:
        raise SystemExit(f"Invalid config file: {e}") from e

    for value in configs.values():
        save_dir_path = Path(value["save-dir"])
        if not save_dir_path.is_absolute():
            raise SystemExit("Save directory must be an absolute path")
        if save_dir_path.is_file():
            raise SystemExit("Save directory must be a directory or non existent")
        save_dir_path.mkdir(parents=True, exist_ok=True)

    passwd = get_passwd()

    for server, conf in configs.items():
        start = time.monotonic()
        update_cache(conf, passwd)
        logger.info("Finished %s in %s seconds", server, round(time.monotonic() - start, 2))


def main() -> None:
    """Runs the `caldav2ics` console script."""
    app = doctyper.DocTyper()
    app.command("sync")(sync)
    app()


if __name__ == "__main__":
    main()
