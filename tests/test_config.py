from pathlib import Path

from mediafetch.config import Config


def test_config_from_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("PUBLIC_ACCESS", "true")
    monkeypatch.setenv("ALLOWED_USER_IDS", "123, 456")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    config = Config.from_env()
    assert config.bot_token == "test-token"
    assert config.public_access is True
    assert config.allowed_user_ids == frozenset({123, 456})
    assert config.max_concurrent_downloads == 2
    assert config.database_path == tmp_path / "state.db"
