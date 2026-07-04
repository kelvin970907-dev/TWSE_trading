from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.data_collectors.collect_intraday_prices import (
    IntradayImportError,
    PublicIntradayUnavailableError,
    import_intraday_csv,
    import_intraday_folder,
    normalize_intraday_frame,
    public_download_intraday,
)
from src.db import get_connection, init_db


def sample_intraday_rows() -> list[dict[str, object]]:
    return [
        {
            "symbol": "2330",
            "market": "TWSE",
            "bar_time": "2024-01-02 09:00:00",
            "open": "590",
            "high": "592",
            "low": "589",
            "close": "591",
            "volume_shares": "1,000",
            "turnover_twd": "591000",
        },
        {
            "symbol": "2330",
            "market": "TWSE",
            "bar_time": "2024-01-02 09:01:00",
            "open": "591",
            "high": "593",
            "low": "590",
            "close": "592",
            "volume_shares": "2000",
            "turnover_twd": "1184000",
        },
    ]


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


def test_import_intraday_csv_derives_trade_date_and_stores_rows(tmp_path: Path) -> None:
    csv_path = tmp_path / "intraday.csv"
    db_path = tmp_path / "taiwan_trading.duckdb"
    write_csv(csv_path, sample_intraday_rows())

    rows, coverage = import_intraday_csv(db_path=db_path, csv_path=csv_path)

    assert rows == 2
    assert coverage.loc[0, "symbol"] == "2330"
    assert coverage.loc[0, "rows"] == 2
    assert coverage.loc[0, "missing_days"] == 0

    with get_connection(db_path, read_only=True) as conn:
        stored = conn.execute(
            """
            SELECT symbol, market, trade_date, bar_time, open, volume_shares, source
            FROM intraday_bars
            ORDER BY bar_time
            """
        ).fetch_df()

    assert len(stored) == 2
    assert pd.Timestamp(stored["trade_date"].iloc[0]).date() == pd.Timestamp("2024-01-02").date()
    assert stored["open"].iloc[0] == 590.0
    assert stored["volume_shares"].iloc[0] == 1000
    assert stored["source"].iloc[0] == "csv:intraday.csv"


def test_import_intraday_folder_validates_combined_duplicate_keys(tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    write_csv(folder / "a.csv", sample_intraday_rows()[:1])
    write_csv(folder / "b.csv", sample_intraday_rows()[:1])

    with pytest.raises(IntradayImportError, match="duplicate symbol/bar_time"):
        import_intraday_folder(db_path=tmp_path / "taiwan_trading.duckdb", folder=folder)


def test_import_intraday_folder_imports_multiple_files(tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    write_csv(folder / "a.csv", sample_intraday_rows()[:1])
    write_csv(
        folder / "b.csv",
        [
            {
                **sample_intraday_rows()[1],
                "bar_time": "2024-01-03 09:00:00",
            }
        ],
    )

    rows, coverage = import_intraday_folder(db_path=tmp_path / "taiwan_trading.duckdb", folder=folder)

    assert rows == 2
    assert coverage.loc[0, "observed_days"] == 2
    assert coverage.loc[0, "expected_days"] == 2
    assert coverage.loc[0, "missing_days"] == 0


def test_normalize_intraday_frame_rejects_negative_prices_and_volume() -> None:
    bad_price = pd.DataFrame([{**sample_intraday_rows()[0], "open": -1}])
    with pytest.raises(IntradayImportError, match="open must be positive"):
        normalize_intraday_frame(bad_price, source="test")

    bad_volume = pd.DataFrame([{**sample_intraday_rows()[0], "volume_shares": -100}])
    with pytest.raises(IntradayImportError, match="volume_shares cannot be negative"):
        normalize_intraday_frame(bad_volume, source="test")


def test_normalize_intraday_frame_rejects_outside_taiwan_session() -> None:
    frame = pd.DataFrame([{**sample_intraday_rows()[0], "bar_time": "2024-01-02 08:59:00"}])

    with pytest.raises(IntradayImportError, match="Taiwan regular session"):
        normalize_intraday_frame(frame, source="test")


def test_coverage_report_uses_trading_calendar_when_available(tmp_path: Path) -> None:
    csv_path = tmp_path / "intraday.csv"
    db_path = tmp_path / "taiwan_trading.duckdb"
    write_csv(
        csv_path,
        [
            sample_intraday_rows()[0],
            {**sample_intraday_rows()[1], "bar_time": "2024-01-04 09:00:00"},
        ],
    )
    init_db(db_path)
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO trading_calendar (trade_date, is_open, market, notes)
            VALUES
                ('2024-01-02', TRUE, 'TWSE', NULL),
                ('2024-01-03', TRUE, 'TWSE', NULL),
                ('2024-01-04', TRUE, 'TWSE', NULL)
            """
        )

    _, coverage = import_intraday_csv(db_path=db_path, csv_path=csv_path)

    assert coverage.loc[0, "expected_days"] == 3
    assert coverage.loc[0, "missing_days"] == 1
    assert coverage.loc[0, "missing_day_list"] == "2024-01-03"


def test_public_download_mode_has_clear_error(tmp_path: Path) -> None:
    with pytest.raises(PublicIntradayUnavailableError, match="No public historical 1-minute intraday adapter"):
        public_download_intraday(
            db_path=tmp_path / "taiwan_trading.duckdb",
            market="TWSE",
            symbols=["2330"],
            start="2024-01-01",
            end="2024-01-31",
        )
