"""Data layer: SQLAlchemy models, engine/session management, migrations.

All data is stored in a single SQLite database (``pivot.db``) that lives in the
data directory, **outside** the swappable application folder so it survives any
update or rollback (spec §3.7.9, §5).
"""

from pivot.db.database import Database, get_database, init_database
from pivot.db.models import (
    Base,
    BandProfileRow,
    ConfigRow,
    EventRow,
    InstructorRadioRow,
    RadioStateRow,
    SessionRow,
    TraineeRow,
    TranscriptionStatus,
)

__all__ = [
    "Base",
    "Database",
    "get_database",
    "init_database",
    "ConfigRow",
    "BandProfileRow",
    "InstructorRadioRow",
    "RadioStateRow",
    "SessionRow",
    "EventRow",
    "TraineeRow",
    "TranscriptionStatus",
]
