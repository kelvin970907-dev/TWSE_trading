from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.db import get_connection, init_db, upsert_dataframe
from src.live.evaluate_closed_limit_up_signals import EVALUATION_COLUMNS
from src.live.generate_closed_limit_up_signals import OUTPUT_COLUMNS
from src.live.manual_fill_log import (
    FILL_STATUS_VALUES,
    MANUAL_FILL_COLUMNS,
    export_template,
    generate_manual_fill_action_checklist,
    import_manual_fill_csv,
    summarize_manual_fill_log,
)


def test_template_export_works(tmp_path: Path) -> None:
    output = tmp_path / "manual_fill_observations_template.csv"

    path = export_template(output)
    frame = pd.read_csv(path)

    assert path == output
    assert list(frame.columns) == MANUAL_FILL_COLUMNS
    assert frame.empty


def test_csv_import_validates_required_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    csv_path = tmp_path / "bad.csv"
    init_db(db_path)
    pd.DataFrame([{"symbol": "7001", "market": "TPEX", "fill_status": "fully_filled"}]).to_csv(
        csv_path,
        index=False,
    )

    with pytest.raises(ValueError, match="missing required columns"):
        import_manual_fill_csv(db_path=db_path, csv_path=csv_path)

    invalid = tmp_path / "invalid.csv"
    pd.DataFrame(
        [{"signal_date": "2024-01-10", "symbol": "7001", "market": "TPEX", "fill_status": "filled"}]
    ).to_csv(invalid, index=False)
    with pytest.raises(ValueError, match="Invalid fill_status"):
        import_manual_fill_csv(db_path=db_path, csv_path=invalid)


def test_summarize_works_with_empty_log(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    init_db(db_path)

    summary, csv_path, md_path = summarize_manual_fill_log(db_path=db_path, output_dir=output_dir)

    overall = summary[summary["section"] == "overall"].iloc[0]
    assert overall["observations"] == 0
    assert csv_path.exists()
    assert md_path.exists()
    report = md_path.read_text(encoding="utf-8")
    assert "No manual fill observations logged" in report
    assert "Manual observations: 0" in report
    assert "No completed observations imported yet." in report
    assert "Paper ledger rows available: 0" in report


def test_summarize_works_with_ledger_but_no_observations(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    output_dir.mkdir(parents=True)
    init_db(db_path)
    write_signal_csv(output_dir / "closed_limit_up_signals_2024-01-10.csv")
    pd.DataFrame(
        [
            {
                "profile_name": "",
                "signal_date": "2024-01-10",
                "symbol": "7001",
                "market": "TPEX",
                "status": "evaluated",
                "net_pnl": 1000.0,
            }
        ]
    ).to_csv(output_dir / "closed_limit_up_paper_ledger.csv", index=False)

    summary, _csv_path, md_path = summarize_manual_fill_log(db_path=db_path, output_dir=output_dir)

    overall = summary[summary["section"] == "overall"].iloc[0]
    missing = summary[summary["section"] == "missing_observation"].iloc[0]
    report = md_path.read_text(encoding="utf-8")
    assert overall["observations"] == 0
    assert missing["missing_observation_count"] == 3
    assert "Manual observations: 0" in report
    assert "Paper signal rows available: 3" in report
    assert "Signals missing observations: 3" in report
    assert "Paper ledger rows available: 1" in report
    assert "No completed observations imported yet." in report


def test_summarize_computes_fill_rate_and_missing_signals(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    output_dir.mkdir(parents=True)
    seed_sector_map(db_path)
    csv_path = tmp_path / "manual.csv"
    pd.DataFrame(
        [
            observation_row("7001", "fully_filled", actual_filled_shares=4000, actual_avg_fill_price=50.0),
            observation_row("7002", "not_filled", actual_filled_shares=0, actual_avg_fill_price=None),
        ]
    ).to_csv(csv_path, index=False)
    imported = import_manual_fill_csv(db_path=db_path, csv_path=csv_path)
    write_signal_csv(output_dir / "closed_limit_up_signals_2024-01-10.csv")

    summary, _csv, _md = summarize_manual_fill_log(db_path=db_path, output_dir=output_dir)

    assert imported == 2
    overall = summary[summary["section"] == "overall"].iloc[0]
    assert overall["observations"] == 2
    assert overall["fully_filled"] == 1
    assert overall["not_filled"] == 1
    assert overall["fill_rate"] == pytest.approx(0.5)
    by_sector = summary[(summary["section"] == "by_sector") & (summary["group"] == "Technology/Electronics")]
    assert not by_sector.empty
    missing = summary[summary["section"] == "missing_observation"].iloc[0]
    assert missing["missing_observation_count"] == 1


def test_action_checklist_works_with_empty_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    ledger_path = output_dir / "closed_limit_up_paper_ledger.csv"
    output_dir.mkdir(parents=True)
    init_db(db_path)
    pd.DataFrame(columns=EVALUATION_COLUMNS).to_csv(ledger_path, index=False)

    checklist, csv_path, md_path = generate_manual_fill_action_checklist(
        db_path=db_path,
        ledger_path=ledger_path,
        output_dir=output_dir,
    )

    assert checklist.empty
    assert csv_path.exists()
    assert md_path.exists()
    report = md_path.read_text(encoding="utf-8")
    assert "Total pending profile-specific observations: 0" in report
    assert "Until these observations are completed" in report


def test_action_checklist_groups_duplicate_symbol_profile_rows_and_detects_template(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    ledger_path = output_dir / "closed_limit_up_paper_ledger.csv"
    output_dir.mkdir(parents=True)
    init_db(db_path)
    write_ledger_csv(
        ledger_path,
        [
            ledger_row("profile_a", "2024-01-10", "7001"),
            ledger_row("profile_b", "2024-01-10", "7001"),
        ],
    )
    write_template_csv(output_dir / "manual_fill_observations_2024-01-10_all_profiles_template.csv")
    (output_dir / "manual_fill_observations_2024-01-10_instructions.md").write_text("instructions", encoding="utf-8")

    checklist, _csv_path, _md_path = generate_manual_fill_action_checklist(
        db_path=db_path,
        ledger_path=ledger_path,
        output_dir=output_dir,
    )

    assert len(checklist) == 1
    row = checklist.iloc[0]
    assert row["symbol"] == "7001"
    assert row["profile_specific_rows_needed"] == 2
    assert row["profiles_containing_symbol"] == "profile_a, profile_b"
    assert bool(row["template_exists"])
    assert "manual_fill_observations_2024-01-10_all_profiles_template.csv" in row["template_path"]
    assert "manual_fill_observations_2024-01-10_instructions.md" in row["instruction_file_path"]
    assert row["limit_up_price"] == pytest.approx(50.0)


def test_action_checklist_excludes_already_observed_profile_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    ledger_path = output_dir / "closed_limit_up_paper_ledger.csv"
    output_dir.mkdir(parents=True)
    init_db(db_path)
    write_ledger_csv(
        ledger_path,
        [
            ledger_row("profile_a", "2024-01-10", "7001"),
            ledger_row("profile_b", "2024-01-10", "7001"),
        ],
    )
    pd.DataFrame([observation_row("7001", "fully_filled", actual_filled_shares=4000, actual_avg_fill_price=50.0)]).assign(
        profile_name="profile_a"
    ).to_csv(tmp_path / "observed.csv", index=False)
    import_manual_fill_csv(db_path=db_path, csv_path=tmp_path / "observed.csv")

    checklist, _csv_path, _md_path = generate_manual_fill_action_checklist(
        db_path=db_path,
        ledger_path=ledger_path,
        output_dir=output_dir,
    )

    assert len(checklist) == 1
    row = checklist.iloc[0]
    assert row["profile_specific_rows_needed"] == 1
    assert row["profiles_containing_symbol"] == "profile_b"


def seed_sector_map(db_path: Path) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        upsert_dataframe(
            conn,
            "stock_sector_map",
            pd.DataFrame(
                [
                    {
                        "symbol": "7001",
                        "market": "TPEX",
                        "name": "Stock 7001",
                        "sector": "Technology/Electronics",
                        "industry": "Electronic Components",
                        "source": "unit_test",
                    },
                    {
                        "symbol": "7002",
                        "market": "TPEX",
                        "name": "Stock 7002",
                        "sector": "Technology/Electronics",
                        "industry": "Semiconductor",
                        "source": "unit_test",
                    },
                ]
            ),
            ["symbol", "market"],
        )


def observation_row(
    symbol: str,
    fill_status: str,
    *,
    actual_filled_shares: int,
    actual_avg_fill_price: float | None,
) -> dict[str, object]:
    assert fill_status in FILL_STATUS_VALUES
    return {
        "signal_date": "2024-01-10",
        "symbol": symbol,
        "market": "TPEX",
        "name": f"Stock {symbol}",
        "observed_time": "13:25",
        "broker": "paper",
        "intended_entry_price": 50.0,
        "displayed_best_bid": 50.0,
        "displayed_best_ask": 50.0,
        "displayed_bid_size_shares": 1_000_000,
        "displayed_ask_size_shares": 0,
        "limit_up_price": 50.0,
        "was_limit_up_locked": True,
        "was_order_submitted": True,
        "order_type": "limit",
        "order_quantity_shares": 4000,
        "order_price": 50.0,
        "simulated_queue_position": 100_000,
        "actual_filled_shares": actual_filled_shares,
        "actual_avg_fill_price": actual_avg_fill_price,
        "fill_status": fill_status,
        "reason_not_filled": "" if actual_filled_shares else "queue_not_reached",
        "screenshot_path": "",
        "notes": "unit test",
    }


def write_signal_csv(path: Path) -> None:
    rows = []
    for symbol in ["7001", "7002", "7003"]:
        row = {column: "" for column in OUTPUT_COLUMNS}
        row.update(
            {
                "signal_date": "2024-01-10",
                "symbol": symbol,
                "name": f"Stock {symbol}",
                "market": "TPEX",
                "planned_shares": 4000,
            }
        )
        rows.append(row)
    pd.DataFrame(rows, columns=OUTPUT_COLUMNS).to_csv(path, index=False)


def write_ledger_csv(path: Path, rows: list[dict[str, object]]) -> None:
    normalized = []
    for row in rows:
        full = {column: "" for column in EVALUATION_COLUMNS}
        full.update(row)
        normalized.append(full)
    pd.DataFrame(normalized, columns=EVALUATION_COLUMNS).to_csv(path, index=False)


def ledger_row(profile_name: str, signal_date: str, symbol: str) -> dict[str, object]:
    return {
        "profile_name": profile_name,
        "signal_date": signal_date,
        "symbol": symbol,
        "name": f"Stock {symbol}",
        "market": "TPEX",
        "sector": "Technology/Electronics",
        "industry": "Semiconductor",
        "status": "evaluated",
        "planned_entry_price": 50.0,
        "planned_shares": 4000,
        "planned_buy_notional_twd": 200_000.0,
        "net_pnl": 1000.0,
        "net_return": 0.005,
    }


def write_template_csv(path: Path) -> None:
    row = {column: "" for column in MANUAL_FILL_COLUMNS}
    row.update(
        {
            "signal_date": "2024-01-10",
            "profile_name": "profile_a",
            "symbol": "7001",
            "market": "TPEX",
            "name": "Stock 7001",
            "intended_entry_price": 50.0,
            "limit_up_price": 50.0,
            "order_quantity_shares": 4000,
            "order_price": 50.0,
            "order_type": "limit",
            "fill_status": "not_submitted",
        }
    )
    pd.DataFrame([row], columns=MANUAL_FILL_COLUMNS).to_csv(path, index=False)
