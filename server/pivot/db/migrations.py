"""Forward-compatible schema migrations (spec §9.3, §3.7.9).

The current schema version is stored in the ``config`` table under
``schema_version``. On startup of a (possibly newer) build, any migrations with a
target version greater than the stored version are applied in order. Downgrades
are supported by keeping data outside the swap folder; the update manager warns
and offers a backup before crossing a migration boundary (§3.7.9) — that policy
check lives in :mod:`pivot.updates`, this module only moves the schema forward.

For v1.0 the baseline schema (version 1) is created directly from the ORM
metadata, so there are no incremental steps yet. New migrations are appended to
:data:`MIGRATIONS` as ``(target_version, callable)`` pairs.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.engine import Engine

CURRENT_SCHEMA_VERSION = 2


def _migrate_v2_net_scenarios(conn) -> None:
    """v2: per-net instructor scenario overrides on the band profile (§3.1.5)."""
    cols = {row[1] for row in conn.execute(text("PRAGMA table_info(band_profile)"))}
    if "net_scenarios_json" not in cols:
        conn.execute(
            text(
                "ALTER TABLE band_profile "
                "ADD COLUMN net_scenarios_json TEXT NOT NULL DEFAULT '[]'"
            )
        )


# (target_version, migrate_fn). migrate_fn receives a live connection and should
# perform idempotent DDL/DML to move the schema from target_version-1 to
# target_version. Append new steps here; never edit released ones.
MIGRATIONS: list[tuple[int, Callable[[object], None]]] = [
    (2, _migrate_v2_net_scenarios),
]


def _read_schema_version(conn) -> int:
    row = conn.execute(
        text("SELECT value FROM config WHERE key = 'schema_version'")
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(json.loads(row[0]))
    except (ValueError, TypeError):
        return 0


def _write_schema_version(conn, version: int) -> None:
    conn.execute(
        text(
            "INSERT INTO config (key, value) VALUES ('schema_version', :v) "
            "ON CONFLICT(key) DO UPDATE SET value = :v"
        ),
        {"v": json.dumps(version)},
    )


def run_migrations(engine: Engine) -> int:
    """Apply outstanding forward migrations. Returns the resulting version."""
    with engine.begin() as conn:
        current = _read_schema_version(conn)
        # Baseline: tables already exist (create_all). Record the baseline
        # version if this is a brand-new database.
        if current == 0:
            current = CURRENT_SCHEMA_VERSION
            _write_schema_version(conn, current)
            return current

        for target, migrate in sorted(MIGRATIONS, key=lambda m: m[0]):
            if target > current:
                migrate(conn)
                _write_schema_version(conn, target)
                current = target
        return current


def crosses_migration_boundary(from_version: int, to_version: int) -> bool:
    """True if downgrading ``from_version`` → ``to_version`` crosses a migration.

    Used by the update manager to decide whether to warn and offer a backup
    before a downgrade (§3.7.9).
    """
    return to_version < from_version
