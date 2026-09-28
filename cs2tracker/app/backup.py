"""Daily online backups of the SQLite database.

Runs inside the service (same user, so file ownership never changes) with its
own connection, using SQLite's backup API: safe while the app keeps writing.
Each copy is integrity-checked before it counts as a backup.
"""

from __future__ import annotations

import calendar
import contextlib
import logging
import os
import re
import sqlite3
import time
from pathlib import Path

log = logging.getLogger(__name__)

PREFIX = "cs2tracker-"
SUFFIX = ".db"
DAY = 86400
# Only files this module wrote count: an operator's "cs2tracker-before-v2.db" in the
# same folder must neither look like the newest backup nor get pruned.
NAME_RE = re.compile(r"^cs2tracker-(\d{8}-\d{6})\.db$")
FUTURE_SLACK = 300


class BackupError(Exception):
    pass


def taken_at(path: Path) -> float | None:
    """UTC time encoded in a backup's name, or None for any other file."""
    m = NAME_RE.match(path.name)
    if not m:
        return None
    try:
        return float(calendar.timegm(time.strptime(m.group(1), "%Y%m%d-%H%M%S")))
    except ValueError:
        return None


def backups(directory: Path, now: float | None = None) -> list[Path]:
    """Backups written by this module, oldest first by the time in their name.

    Files dated in the future (written while the clock was ahead) are left out:
    they would otherwise count as "fresh" and stop backups until that date.
    """
    if not directory.is_dir():
        return []
    now = time.time() if now is None else now
    found = [(ts, p) for p in directory.iterdir()
             if p.is_file() and (ts := taken_at(p)) is not None and ts <= now + FUTURE_SLACK]
    return [p for _, p in sorted(found)]


def latest_age(directory: Path, now: float | None = None) -> float | None:
    """Seconds since the newest backup was taken, or None if there is none."""
    now = time.time() if now is None else now
    found = backups(directory, now)
    return None if not found else now - taken_at(found[-1])


def backup_database(db_path: Path, directory: Path, keep: int = 14, now: float | None = None) -> Path:
    """Writes a checked copy of the database and prunes all but the newest `keep`."""
    now = time.time() if now is None else now
    name = PREFIX + time.strftime("%Y%m%d-%H%M%S", time.gmtime(now)) + SUFFIX
    final = directory / name
    partial = directory / (name + ".partial")
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            target = sqlite3.connect(partial)
            try:
                source.backup(target)
                status = target.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                target.close()
        finally:
            source.close()
        if status != "ok":
            raise BackupError(f"backup failed its integrity check: {status}")
        os.chmod(partial, 0o600)
        os.replace(partial, final)  # atomic: a half-written file never looks like a backup
    except (sqlite3.Error, OSError) as e:
        raise BackupError(f"backup of {db_path} failed: {e}") from e
    finally:
        with contextlib.suppress(OSError):  # must not hide the error that got us here
            partial.unlink(missing_ok=True)

    for old in backups(directory, now)[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)
    log.info("Database backed up to %s", final)
    return final
