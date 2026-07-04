from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.reports.diagnose_tech_industry_concentration import (
    CONCENTRATION_OUTPUT,
    REPORT_OUTPUT,
    STRESS_OUTPUT,
    SUMMARY_OUTPUT,
    generate_tech_industry_concentration_report,
)


def test_tech_industry_concentration_outputs_files_and_metrics(tmp_path: Path) -> None:
    csv_path = tmp_path / "sector_diagnostic.csv"
    output_dir = tmp_path / "reports"
    pd.DataFrame(
        [
            row("A1", "Alpha", "Semiconductor", 0.02, 1_000.0, "2024-01-03"),
            row("A2", "Beta", "Semiconductor", 0.01, 500.0, "2024-01-04"),
            row("B1", "Gamma", "Electronic Components", -0.005, -200.0, "2024-01-05"),
            row("C1", "Delta", "Computer/Peripheral", 0.03, 1_200.0, "2024-01-06"),
            row("N1", "Other", "Electric Machinery", 0.10, 5_000.0, "2024-01-07", sector="Industrials/Other"),
        ]
    ).to_csv(csv_path, index=False)

    summary, concentration, stress, report_path = generate_tech_industry_concentration_report(
        sector_diagnostic_path=csv_path,
        output_dir=output_dir,
    )

    assert set(summary["industry"]) == {"Semiconductor", "Electronic Components", "Computer/Peripheral"}
    semiconductor = summary[summary["industry"] == "Semiconductor"].iloc[0]
    assert semiconductor["trades"] == 2
    assert semiconductor["net_pnl"] == pytest.approx(1_500.0)
    assert concentration["top_1_industry_trade_share"].iloc[0] == pytest.approx(0.5)
    assert concentration["top_1_industry_pnl_share"].iloc[0] == pytest.approx(1_500.0 / 2_500.0)
    all_row = stress[stress["scenario"] == "trade_all_tech"].iloc[0]
    removed = stress[stress["scenario"] == "remove_top_1_industry_by_pnl"].iloc[0]
    assert all_row["trades"] == 4
    assert removed["trades"] == 2
    assert removed["net_pnl"] == pytest.approx(1_000.0)
    assert (output_dir / SUMMARY_OUTPUT).exists()
    assert (output_dir / CONCENTRATION_OUTPUT).exists()
    assert (output_dir / STRESS_OUTPUT).exists()
    assert report_path == output_dir / REPORT_OUTPUT
    assert "Technology/Electronics Industry Drill-Down" in report_path.read_text(encoding="utf-8")


def row(
    symbol: str,
    name: str,
    industry: str,
    net_return: float,
    net_pnl: float,
    exit_date: str,
    *,
    sector: str = "Technology/Electronics",
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "name": name,
        "sector": sector,
        "industry": industry,
        "signal_date": "2024-01-02",
        "entry_date": "2024-01-02",
        "exit_date": exit_date,
        "net_return": net_return,
        "net_pnl": net_pnl,
        "buy_notional": 100_000.0,
    }
