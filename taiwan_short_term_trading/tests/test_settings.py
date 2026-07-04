from __future__ import annotations

from pathlib import Path

from config import settings as settings_module


def test_taiwan_trading_root_controls_default_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TAIWAN_TRADING_ROOT", str(tmp_path))
    monkeypatch.delenv("TAIWAN_TRADING_DATA_DIR", raising=False)
    monkeypatch.delenv("TAIWAN_TRADING_RAW_DATA_DIR", raising=False)
    monkeypatch.delenv("TAIWAN_TRADING_PROCESSED_DATA_DIR", raising=False)
    monkeypatch.delenv("TAIWAN_TRADING_DB_PATH", raising=False)
    settings_module.get_settings.cache_clear()

    loaded = settings_module.get_settings()

    assert loaded.project_root == tmp_path.resolve()
    assert loaded.data_dir == tmp_path / "data"
    assert loaded.raw_data_dir == tmp_path / "data" / "raw"
    assert loaded.processed_data_dir == tmp_path / "data" / "processed"
    assert loaded.db_path == tmp_path / "data" / "taiwan_trading.duckdb"


def test_specific_path_overrides_win_over_root(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "root"
    custom_db = tmp_path / "custom" / "paper.duckdb"
    monkeypatch.setenv("TAIWAN_TRADING_ROOT", str(root))
    monkeypatch.setenv("TAIWAN_TRADING_DB_PATH", str(custom_db))
    settings_module.get_settings.cache_clear()

    loaded = settings_module.get_settings()

    assert loaded.project_root == root.resolve()
    assert loaded.db_path == custom_db


def teardown_function() -> None:
    settings_module.get_settings.cache_clear()

