from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _ids(value: str) -> frozenset[int]:
    try:
        result = frozenset(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise RuntimeError("ALLOWED_USER_IDS must contain comma-separated Telegram user IDs") from exc
    return result


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    bot_token: str
    public_access: bool
    allowed_user_ids: frozenset[int]
    data_dir: Path
    bot_api_base: str
    max_file_bytes: int
    max_duration_seconds: int
    max_concurrent_downloads: int
    poll_timeout: int
    port: int

    @property
    def database_path(self) -> Path:
        return self.data_dir / "state.db"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @classmethod
    def from_env(cls) -> "Config":
        data_dir = Path(os.getenv("DATA_DIR", "/data")).resolve()
        return cls(
            bot_token=_required("TELEGRAM_BOT_TOKEN"),
            public_access=_bool(os.getenv("PUBLIC_ACCESS", "false")),
            allowed_user_ids=_ids(os.getenv("ALLOWED_USER_IDS", "")),
            data_dir=data_dir,
            bot_api_base=os.getenv("BOT_API_BASE", "https://api.telegram.org").rstrip("/"),
            max_file_bytes=int(os.getenv("MAX_FILE_BYTES", "49000000")),
            max_duration_seconds=int(os.getenv("MAX_DURATION_SECONDS", "2700")),
            max_concurrent_downloads=max(1, int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2"))),
            poll_timeout=int(os.getenv("POLL_TIMEOUT", "45")),
            port=int(os.getenv("PORT", "8080")),
        )
