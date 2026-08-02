"""DB-backed runtime config accessor (spec §5.1 ``config`` table).

Instructor-tunable settings (transcription, timezone, crypto, update channel,
etc.) are persisted JSON-encoded under string keys. The instructor Settings page
and the admin API read/write through this store so there are no config files to edit
(design principle §1.1).
"""


import copy
import functools
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pivot.config import DEFAULT_CONFIG
from pivot.db.models import ConfigRow


@functools.lru_cache(maxsize=128)
def _parse_config_value(val: str) -> Any:
    return json.loads(val)


class ConfigStore:
    """Thin typed wrapper over the ``config`` key/value table."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, key: str, default: Any = None) -> Any:
        row = self.session.get(ConfigRow, key)
        if row is None:
            return DEFAULT_CONFIG.get(key, default)
        try:
            val = _parse_config_value(row.value)
            if isinstance(val, (list, dict)):
                return copy.deepcopy(val)
            return val
        except (ValueError, TypeError):
            return DEFAULT_CONFIG.get(key, default)

    def set(self, key: str, value: Any) -> None:
        row = self.session.get(ConfigRow, key)
        encoded = json.dumps(value)
        if row is None:
            self.session.add(ConfigRow(key=key, value=encoded))
        else:
            row.value = encoded

    def all(self) -> dict[str, Any]:
        """Full effective config (defaults overlaid with stored values)."""
        result = dict(DEFAULT_CONFIG)
        for key, value in self.session.execute(select(ConfigRow.key, ConfigRow.value)):
            try:
                val = _parse_config_value(value)
                if isinstance(val, (list, dict)):
                    val = copy.deepcopy(val)
                result[key] = val
            except (ValueError, TypeError):
                continue
        return result

    # Typed convenience accessors used across the app.
    def display_timezone(self) -> str:
        return str(self.get("display_timezone", "UTC"))

    def crypto_enabled(self) -> bool:
        return bool(self.get("crypto_enabled", True))

    def crypto_delay_ms(self) -> int:
        return int(self.get("crypto_delay_ms", 1500))

    def tuning_step_hz(self) -> float:
        return float(self.get("tuning_step_hz", 100.0))

    def default_frequency_hz(self) -> float:
        return float(self.get("default_frequency_hz", 7_000_000.0))
