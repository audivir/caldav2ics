# CalDAV2ICS

A script that downloads all calendars from a CalDAV server and caches them locally as static
`.ics` files.

Passwords are never written to disk: the local cache stores only synced calendar data, and
credentials are stripped before saving.

# Prerequisites

- Python 3.10 to 3.14.

# Installation

```bash
pip install caldav2ics
```

# Usage

Create a config `.toml` file with one section per CalDAV server:

```toml
[server_a]
url = "https://caldav.server_a.com/calendar/remote.php/dav/calendars/user/"
user = "user"
save-dir = "/absolute/path/to/calendars/server_a"

[server_b]
...
```

`save-dir` must be an absolute path. Run the sync:

```bash
caldav2ics config.toml
```

This prompts for the password. To provide it non-interactively, set `C2I_PASSWORD` instead:

```bash
C2I_PASSWORD=$(cat .password) caldav2ics config.toml
```

Each server writes a `cache.pkl` (its synced state, used for incremental updates) and one `.ics`
file per calendar into its `save-dir`:

```
calendars/server_a/
    calendar1.ics
    calendar2.ics
    cache.pkl
```

To integrate with Google Calendar, either upload the `.ics` files or host `save-dir` publicly
(add basic auth or similar for access control; for stronger security, use a different method).

# License

This project is licensed under the `MIT` license. See `LICENSE` for details.
