"""SQLite engine, session factory, and first-run initialisation.

The database file lives in the data directory (spec §3.7.9). We enable WAL mode
and ``synchronous=NORMAL`` so events can be flushed promptly on each keying
(§8.3 reliability) without throttling live audio.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from pivot.config import Settings, settings
from pivot.db.migrations import run_migrations
from pivot.db.models import Base


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - thin glue
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


class Database:
    """Owns the engine and session factory for one data directory."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            future=True,
            connect_args={"check_same_thread": False},
        )
        self._sessionmaker = sessionmaker(self.engine, expire_on_commit=False, future=True)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def initialise(self) -> None:
        """First-run init: create schema, run migrations, seed defaults (§2.3)."""
        self.create_all()
        run_migrations(self.engine)
        self._seed_defaults()

    @contextmanager
    def session(self) -> Iterator[Session]:
        sess = self._sessionmaker()
        try:
            yield sess
            sess.commit()
        except Exception:
            sess.rollback()
            raise
        finally:
            sess.close()

    def _seed_defaults(self) -> None:
        """Populate config defaults and the single band_profile row on first run."""
        import json

        from pivot.config import DEFAULT_CONFIG
        from pivot.core.bands import BandProfile
        from pivot.db.models import BandProfileRow, ConfigRow

        with self.session() as sess:
            existing = {row.key for row in sess.query(ConfigRow).all()}
            for key, value in DEFAULT_CONFIG.items():
                if key not in existing:
                    sess.add(ConfigRow(key=key, value=json.dumps(value)))

            if sess.get(BandProfileRow, 1) is None:
                default = BandProfile()
                sess.add(
                    BandProfileRow(
                        id=1,
                        curve_json=json.dumps(default.curve_to_json()),
                        atmospheric_multiplier=default.atmospheric_multiplier,
                        crypto_delay_ms=default.crypto_delay_ms,
                        crypto_enabled=1 if default.crypto_enabled else 0,
                    )
                )


_db: Database | None = None


def init_database(cfg: Settings | None = None) -> Database:
    """Create (or reuse) the process-wide database and initialise it."""
    global _db
    cfg = cfg or settings
    cfg.ensure_dirs()
    _db = Database(cfg.db_path)
    _db.initialise()
    return _db


def get_database() -> Database:
    if _db is None:
        raise RuntimeError("database not initialised; call init_database() first")
    return _db
