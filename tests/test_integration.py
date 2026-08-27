"""Integration tests running `caldav2ics` against a real local Radicale server."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from caldav.davclient import DAVClient
from caldav.testing import RadicaleServer
from conftest import EVENT_ICS, OTHER_EVENT_ICS

from caldav2ics import cli

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def radicale_server() -> Iterator[RadicaleServer]:
    server = RadicaleServer()
    server.start()
    try:
        yield server
    finally:
        # RadicaleServer.stop() closes shutdown_socket but forgets shutdown_socket_out,
        # leaking it until GC; grab the reference before stop() clears it and close it.
        leaked_socket = server.shutdown_socket_out
        server.stop()
        if leaked_socket is not None:  # pragma: no branch
            leaked_socket.close()


def write_config(path: Path, server: RadicaleServer, save_dir: Path) -> None:
    path.write_text(
        f'[radicale]\nurl = "{server.url}"\nuser = "{server.username}"\nsave-dir = "{save_dir}"\n'
    )


def test_sync_writes_ics_from_a_real_calendar_and_resyncs_incrementally(
    radicale_server: RadicaleServer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = DAVClient(
        url=radicale_server.url,
        username=radicale_server.username,
        password=radicale_server.password or "unused",
    )
    principal = client.principal()  # type: ignore[no-untyped-call]
    calendar = principal.make_calendar(name="Integration Test")
    calendar.add_event(ical=EVENT_ICS.decode())

    monkeypatch.setattr(cli, "get_passwd", lambda: "unused")
    config_path = tmp_path / "config.toml"
    save_dir = tmp_path / "out"
    write_config(config_path, radicale_server, save_dir)

    cli.sync(config_path)

    ics_files = list(save_dir.glob("*.ics"))
    assert len(ics_files) == 1
    first_content = ics_files[0].read_text()
    assert "event-1@example.com" in first_content
    assert (save_dir / "cache.pkl").exists()

    calendar.add_event(ical=OTHER_EVENT_ICS.decode())
    cli.sync(config_path)

    second_content = ics_files[0].read_text()
    assert "event-1@example.com" in second_content
    assert "event-2@example.com" in second_content
