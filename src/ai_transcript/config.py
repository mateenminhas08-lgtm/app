from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir

from ai_transcript.utils import atomic_write_text

APP_NAME = "AI Transcript"
# False avoids a redundant vendor/app pair such as AI Transcript/AI Transcript.
APP_AUTHOR = False
KEYRING_SERVICE = "AI Transcript Desktop"
KEYRING_USERNAME = "gemini-api-key"


@dataclass(slots=True)
class AppSettings:
    transcription_provider: str = "Local Whisper"
    whisper_model: str = "small"
    whisper_device: str = "auto"
    compute_type: str = "auto"
    language: str = "auto"
    audio_device_id: str = "default"
    capture_sample_rate: int = 48_000
    sample_rate: int = 16_000
    chunk_seconds: float = 5.0
    no_audio_warning_seconds: int = 12
    gemini_model: str = "gemini-2.5-flash"
    output_language: str = "Same as transcript"
    minimize_to_tray: bool = True
    confirm_session_delete: bool = True


class SettingsStore:
    """Persists non-secret settings as JSON and secrets in the OS credential store."""

    def __init__(self, data_dir: Path | None = None) -> None:
        configured = os.environ.get("AI_TRANSCRIPT_DATA_DIR", "").strip()
        self.data_dir = data_dir or (
            Path(configured).expanduser()
            if configured
            else Path(user_data_dir(APP_NAME, APP_AUTHOR))
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir = self.data_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.data_dir / "settings.json"
        self.database_path = self.data_dir / "sessions.db"
        self.log_path = self.data_dir / "app.log"
        self._lock = threading.RLock()

    def load(self) -> AppSettings:
        with self._lock:
            if not self.settings_path.exists():
                return AppSettings()
            try:
                raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
                allowed = {item.name for item in fields(AppSettings)}
                values = {key: value for key, value in raw.items() if key in allowed}
                return AppSettings(**values)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                # A bad preferences file must never prevent the app from opening.
                return AppSettings()

    def save(self, settings: AppSettings) -> None:
        with self._lock:
            atomic_write_text(
                self.settings_path,
                json.dumps(asdict(settings), indent=2, ensure_ascii=False),
            )

    def get_gemini_api_key(self) -> str:
        environment_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if environment_key:
            return environment_key
        try:
            import keyring

            return (keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME) or "").strip()
        except Exception:
            return ""

    def set_gemini_api_key(self, api_key: str) -> None:
        """Save the API key in Windows Credential Manager through keyring.

        An environment-provided key remains authoritative and is never overwritten.
        """
        try:
            import keyring

            if api_key.strip():
                keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, api_key.strip())
            else:
                try:
                    keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
                except keyring.errors.PasswordDeleteError:
                    pass
        except Exception as exc:
            raise RuntimeError(
                "The API key could not be stored securely. Set GEMINI_API_KEY in your "
                "environment instead."
            ) from exc

    def diagnostics(self) -> dict[str, Any]:
        settings = self.load()
        return {
            "data_directory": str(self.data_dir),
            "settings_file": str(self.settings_path),
            "database_file": str(self.database_path),
            "transcription_provider": settings.transcription_provider,
            "whisper_model": settings.whisper_model,
            "api_key_configured": bool(self.get_gemini_api_key()),
        }
