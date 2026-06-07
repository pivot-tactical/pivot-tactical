"""Configuration & settings (spec §3.1.6, §3.7.8, §3.8, §5.1 ``config`` table).

Two layers:

* :class:`Settings` — *bootstrap* configuration (data directory, bind host/port,
  sample rate). Read from the environment with the ``PIVOT_`` prefix so the
  packaged exe and dev runs behave the same. The data directory deliberately
  lives **outside** the swappable application folder so updates/rollbacks never
  touch training data (spec §3.7.9).

* :data:`DEFAULT_CONFIG` — *runtime, instructor-tunable* settings persisted in
  the SQLite ``config`` table and edited through the instructor Settings page in
  the browser (no config files to hand-edit, per the design principles §1.1). The
  DB-backed accessor is
  :class:`pivot.db.config_store.ConfigStore`.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Canonical repository used as the default update source (spec §3.7.2).
DEFAULT_GITHUB_REPO = "pivot-tactical/pivot-tactical"

# Recordings are 16-bit mono WAV at 16 kHz, captured pre-DSP (spec §3.5.1).
RECORDING_SAMPLE_RATE = 16_000


class Settings(BaseSettings):
    """Bootstrap settings from environment / first-run wizard."""

    model_config = SettingsConfigDict(env_prefix="PIVOT_", extra="ignore")

    # Data directory — DB, recordings, logs, user settings. OUTSIDE the app
    # folder so it survives any update or rollback (§3.7.9, §5).
    data_dir: Path = Field(default=Path("data"))

    # FastAPI bind. LAN-only by default; instructor controls are gated to the
    # local machine in the API layer (§8.4).
    host: str = "0.0.0.0"
    port: int = 8080

    sample_rate: int = RECORDING_SAMPLE_RATE

    # Continuous ambient band noise ("hash") on tuned channels (§3.2.2). On by
    # default for realism; set PIVOT_AMBIENT_NOISE=0 for a silent-when-idle net.
    ambient_noise: bool = True

    # Where retained versions for rollback are stored — alongside the install,
    # NOT inside the data dir (§3.7.7). Resolved relative to the app folder.
    versions_dir: Path = Field(default=Path("versions"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "pivot.db"

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.recordings_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)

    def session_recordings_dir(self, session_id: str) -> Path:
        """``/recordings/{session_id}/`` per §3.5.1."""
        d = self.recordings_dir / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d


# Runtime config defaults persisted to the ``config`` table on first run.
# Values are stored JSON-encoded (spec §5.1). Grouped by spec section.
DEFAULT_CONFIG: dict[str, object] = {
    "schema_version": 1,
    # --- transcription (§3.1.6) ---
    "whisper_model": "small",            # tiny/base/small/medium/large-v3
    "whisper_compute_type": "auto",      # auto / int8 / int8_float16 / float16
    "whisper_language": "en",
    "transcription_confidence_threshold": 0.80,
    "transcription_skip_under_seconds": 0.5,
    # initial_prompt biases decoding toward callsigns/prowords (§10 mitigation)
    "whisper_initial_prompt": "",
    "whisper_custom_vocabulary": [],
    # --- time (§3.8) ---
    "display_timezone": "UTC",
    # --- crypto / band (§3.4.3, §4.3) ---
    "crypto_enabled": True,
    "crypto_delay_ms": 1500,
    "crypto_tone_preset": "ky57",
    "tuning_step_hz": 100.0,
    # --- updates (§3.7.8) ---
    "github_repo": DEFAULT_GITHUB_REPO,
    "github_token": "",
    "update_check_on_startup": False,
    "update_channel": "stable",          # stable / include_prereleases
    "auto_update": False,                # apply updates automatically on check
    "retained_versions": 3,
    "verify_checksums": True,
    # --- audio / logging (§7.1, §8.6) ---
    "audio_input_device": "",
    "log_level": "INFO",
}


# Process-wide settings instance (env-driven). Tests can construct their own.
settings = Settings()
