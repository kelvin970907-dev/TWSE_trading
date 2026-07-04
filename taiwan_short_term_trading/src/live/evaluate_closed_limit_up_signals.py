"""Next-day evaluator for closed-limit-up paper-trading signal sheets."""

from __future__ import annotations

import argparse
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config.settings import get_settings
from src.backtests.costs import calculate_trade_costs
from src.db import get_connection, init_db
from src.live.generate_closed_limit_up_signals import EXECUTION_WARNING, OUTPUT_COLUMNS


THEORETICAL_REMINDER = "This remains theoretical unless actual broker fill data confirms Day0 close execution."
LEDGER_OUTPUT = "closed_limit_up_paper_ledger.csv"

EVALUATION_COLUMNS = [
    "profile_name",
    "candidate_hash",
    "signal_date",
    "evaluation_date",
    "symbol",
    "name",
    "market",
    "sector",
    "industry",
    "status",
    "planned_entry_price",
    "planned_shares",
    "planned_buy_notional_twd",
    "day1_trade_date",
    "day1_open",
    "day1_high",
    "day1_low",
    "day1_close",
    "theoretical_exit_price",
    "gross_return",
    "open_to_high_return",
    "open_to_low_return",
    "open_to_close_return",
    "gross_pnl",
    "net_pnl",
    "net_return",
    "buy_commission",
    "sell_commission",
    "sell_tax",
    "slippage_cost",
    "total_cost",
    "paper_fill_assumed",
    "actual_broker_fill_known",
    "actual_broker_filled",
    "notes",
    "execution_warning",
]

SUMMARY_COLUMNS = [
    "metric",
    "value",
]


@dataclass(frozen=True)
class EvaluationCostConfig:
    """Cost assumptions for overnight paper-trade evaluation."""

    commission_rate: float = 0.001425
    commission_discount: float = 0.28
    sell_tax_rate: float = 0.003
    slippage_bps_per_side: float = 5.0
    minimum_commission_twd: float = 20.0


def evaluate_closed_limit_up_signals(
    *,
    db_path: Path | str,
    signals_csv: Path | str,
    output_dir: Path | str | None = None,
    cost_config: EvaluationCostConfig | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, Path, Path, Path]:
    """Evaluate a generated signal CSV after Day1 data is available."""

    config = cost_config or EvaluationCostConfig()
    init_db(db_path)
    signals_path = Path(signals_csv)
    signals = read_signal_csv(signals_path)
    signal_date = infer_signal_date(signals, signals_path)
    report_dir = Path(output_dir) if output_dir is not None else signals_path.parent
    report_dir.mkdir(parents=True, exist_ok=True)

    if signals.empty:
        evaluations = empty_evaluations()
    else:
        normalized = normalize_signal_rows(signals)
        day1 = load_day1_daily_prices(db_path=db_path, signals=normalized)
        evaluations = evaluate_signal_rows(normalized, day1=day1, cost_config=config)

    summary = summarize_evaluations(evaluations, planned_orders=len(signals))
    signal_label = signal_label_from_path(signals_path, signal_date)
    eval_csv_path = report_dir / f"closed_limit_up_eval_{signal_label}.csv"
    eval_md_path = report_dir / f"closed_limit_up_eval_{signal_label}.md"
    ledger_path = report_dir / LEDGER_OUTPUT

    evaluations.to_csv(eval_csv_path, index=False)
    append_to_ledger(evaluations, ledger_path=ledger_path)
    eval_md_path.write_text(
        build_markdown_report(
            signal_date=signal_date,
            signals_path=signals_path,
            evaluations=evaluations,
            summary=summary,
            cost_config=config,
        ),
        encoding="utf-8",
    )
    return evaluations, summary, eval_csv_path, eval_md_path, ledger_path


def read_signal_csv(path: Path) -> pd.DataFrame:
    """Read signal CSV and tolerate header-only files."""

    if not path.exists():
        raise FileNotFoundError(f"Signal CSV does not exist: {path}")
    try:
        frame = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        frame = pd.DataFrame(columns=OUTPUT_COLUMNS)
    for column in OUTPUT_COLUMNS:
        if column not in frame.columns:
            frame[column] = np.nan
    if "profile_name" not in frame.columns:
        frame["profile_name"] = infer_profile_name(path)
    if "candidate_hash" not in frame.columns:
        frame["candidate_hash"] = ""
    inferred_profile = infer_profile_name(path)
    if inferred_profile:
        profile_values = frame["profile_name"].astype("string").fillna("").str.strip()
        frame["profile_name"] = profile_values.mask(profile_values.eq(""), inferred_profile)
    return frame


def infer_signal_date(signals: pd.DataFrame, path: Path) -> pd.Timestamp:
    if "signal_date" in signals.columns and signals["signal_date"].notna().any():
        return pd.to_datetime(signals["signal_date"].dropna().iloc[0], errors="coerce").normalize()
    match = re.search(r"closed_limit_up_signals_(?:.+_)?(\d{4}-\d{2}-\d{2})\.csv$", path.name)
    if match:
        return pd.Timestamp(match.group(1)).normalize()
    return pd.NaT


def normalize_signal_rows(signals: pd.DataFrame) -> pd.DataFrame:
    """Normalize signal CSV columns before DB join and evaluation."""

    frame = signals.copy().reset_index(drop=True)
    frame["_signal_row_id"] = np.arange(len(frame))
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="coerce").dt.normalize()
    frame["profile_name"] = frame.get("profile_name", "").astype("string").fillna("").str.strip()
    blank_profile = frame["profile_name"].eq("")
    if blank_profile.any():
        frame.loc[blank_profile, "profile_name"] = infer_profile_name_from_frame(frame)
    frame["candidate_hash"] = frame.get("candidate_hash", "").astype("string").fillna("").str.strip()
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    for column in [
        "planned_entry_price",
        "planned_shares",
        "planned_buy_notional_twd",
        "close_price",
    ]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["planned_entry_price"] = frame["planned_entry_price"].fillna(frame["close_price"])
    frame["planned_shares"] = frame["planned_shares"].fillna(0).astype("int64")
    return frame


def load_day1_daily_prices(*, db_path: Path | str, signals: pd.DataFrame) -> pd.DataFrame:
    """Find the next available trading date for each symbol/market after Day0."""

    if signals.empty:
        return empty_day1_frame()
    keys = signals[["_signal_row_id", "symbol", "market", "signal_date"]].copy()
    keys["signal_date"] = pd.to_datetime(keys["signal_date"], errors="coerce").dt.date
    with get_connection(db_path, read_only=True) as conn:
        conn.register("_paper_signal_keys", keys)
        return conn.execute(
            """
            SELECT
                _signal_row_id,
                day1_trade_date,
                day1_open,
                day1_high,
                day1_low,
                day1_close
            FROM (
                SELECT
                    k._signal_row_id,
                    dp.trade_date AS day1_trade_date,
                    dp.open AS day1_open,
                    dp.high AS day1_high,
                    dp.low AS day1_low,
                    dp.close AS day1_close,
                    ROW_NUMBER() OVER (
                        PARTITION BY k._signal_row_id
                        ORDER BY dp.trade_date
                    ) AS rn
                FROM _paper_signal_keys k
                LEFT JOIN daily_prices dp
                  ON dp.symbol = k.symbol
                 AND dp.market = k.market
                 AND dp.trade_date > k.signal_date
            )
            WHERE rn = 1
            ORDER BY _signal_row_id
            """
        ).fetch_df()


def evaluate_signal_rows(
    signals: pd.DataFrame,
    *,
    day1: pd.DataFrame,
    cost_config: EvaluationCostConfig,
) -> pd.DataFrame:
    """Compute order-level next-day paper-trade results."""

    if signals.empty:
        return empty_evaluations()
    frame = signals.merge(day1, on="_signal_row_id", how="left")
    rows: list[dict[str, Any]] = []
    evaluation_date = pd.Timestamp.today().normalize().date().isoformat()

    for row in frame.to_dict("records"):
        entry_price = safe_float(row.get("planned_entry_price"))
        shares = int(safe_float(row.get("planned_shares"), 0))
        day1_open = safe_float(row.get("day1_open"))
        day1_high = safe_float(row.get("day1_high"))
        day1_low = safe_float(row.get("day1_low"))
        day1_close = safe_float(row.get("day1_close"))
        day1_trade_date = row.get("day1_trade_date")

        if pd.isna(day1_trade_date):
            status = "missing_day1_data"
        elif entry_price <= 0 or day1_open <= 0 or shares <= 0:
            status = "invalid_price"
        else:
            status = "evaluated"

        metrics = empty_metric_values()
        if status == "evaluated":
            costs = calculate_trade_costs(
                side="long",
                entry_price=entry_price,
                exit_price=day1_open,
                shares=shares,
                commission_rate=cost_config.commission_rate,
                commission_discount=cost_config.commission_discount,
                sell_tax_rate=cost_config.sell_tax_rate,
                slippage_bps_per_side=cost_config.slippage_bps_per_side,
                minimum_commission_twd=cost_config.minimum_commission_twd,
                is_day_trade=False,
                normal_sell_tax_rate=cost_config.sell_tax_rate,
            )
            metrics.update(
                {
                    "theoretical_exit_price": day1_open,
                    "gross_return": day1_open / entry_price - 1.0,
                    "open_to_high_return": day1_high / day1_open - 1.0 if day1_high > 0 else np.nan,
                    "open_to_low_return": day1_low / day1_open - 1.0 if day1_low > 0 else np.nan,
                    "open_to_close_return": day1_close / day1_open - 1.0 if day1_close > 0 else np.nan,
                    "gross_pnl": costs["gross_pnl"],
                    "net_pnl": costs["net_pnl"],
                    "net_return": costs["net_return"],
                    "buy_commission": costs["buy_commission"],
                    "sell_commission": costs["sell_commission"],
                    "sell_tax": costs["sell_tax"],
                    "slippage_cost": costs["slippage_cost"],
                    "total_cost": costs["total_cost"],
                }
            )

        rows.append(
            {
                "signal_date": date_string(row.get("signal_date")),
                "profile_name": str(row.get("profile_name", "")),
                "candidate_hash": str(row.get("candidate_hash", "")),
                "evaluation_date": evaluation_date,
                "symbol": str(row.get("symbol", "")),
                "name": str(row.get("name", "")),
                "market": str(row.get("market", "")),
                "sector": str(row.get("sector", "")),
                "industry": str(row.get("industry", "")),
                "status": status,
                "planned_entry_price": entry_price,
                "planned_shares": shares,
                "planned_buy_notional_twd": safe_float(row.get("planned_buy_notional_twd")),
                "day1_trade_date": date_string(day1_trade_date),
                "day1_open": day1_open,
                "day1_high": day1_high,
                "day1_low": day1_low,
                "day1_close": day1_close,
                **metrics,
                "paper_fill_assumed": True,
                "actual_broker_fill_known": False,
                "actual_broker_filled": np.nan,
                "notes": status_note(status),
                "execution_warning": THEORETICAL_REMINDER,
            }
        )
    return pd.DataFrame(rows, columns=EVALUATION_COLUMNS)


def summarize_evaluations(evaluations: pd.DataFrame, *, planned_orders: int) -> pd.DataFrame:
    """Aggregate paper evaluation metrics."""

    evaluated = evaluations[evaluations["status"].eq("evaluated")] if not evaluations.empty else evaluations
    net_pnl = pd.to_numeric(evaluated.get("net_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    gross_pnl = pd.to_numeric(evaluated.get("gross_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    net_return = pd.to_numeric(evaluated.get("net_return", pd.Series(dtype=float)), errors="coerce")
    gains = net_pnl[net_pnl > 0].sum()
    losses = abs(net_pnl[net_pnl < 0].sum())
    rows = [
        ("planned_orders", planned_orders),
        ("evaluated_orders", int(len(evaluated))),
        ("missing_day1_data", int((evaluations.get("status", pd.Series(dtype=str)) == "missing_day1_data").sum())),
        ("invalid_price", int((evaluations.get("status", pd.Series(dtype=str)) == "invalid_price").sum())),
        ("gross_pnl", float(gross_pnl.sum())),
        ("net_pnl", float(net_pnl.sum())),
        ("avg_net_return", float(net_return.mean()) if not net_return.empty else np.nan),
        ("median_net_return", float(net_return.median()) if not net_return.empty else np.nan),
        ("win_rate", float((net_pnl > 0).mean()) if len(net_pnl) else np.nan),
        ("profit_factor", profit_factor(gains, losses)),
    ]
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def append_to_ledger(evaluations: pd.DataFrame, *, ledger_path: Path) -> None:
    """Append evaluation rows and de-duplicate by profile, signal date, symbol, and market."""

    if ledger_path.exists():
        try:
            ledger = pd.read_csv(ledger_path)
        except pd.errors.EmptyDataError:
            ledger = pd.DataFrame(columns=EVALUATION_COLUMNS)
    else:
        ledger = pd.DataFrame(columns=EVALUATION_COLUMNS)

    combined = pd.concat([ledger, evaluations], ignore_index=True)
    for column in EVALUATION_COLUMNS:
        if column not in combined.columns:
            combined[column] = np.nan
    if not combined.empty:
        combined["signal_date"] = pd.to_datetime(combined["signal_date"], errors="coerce").dt.strftime("%Y-%m-%d")
        combined["profile_name"] = combined.get("profile_name", "").astype("string").fillna("").str.strip()
        combined["profile_name"] = combined["profile_name"].mask(combined["profile_name"].eq(""), "legacy")
        combined["candidate_hash"] = combined.get("candidate_hash", "").astype("string").fillna("").str.strip()
        combined["symbol"] = combined["symbol"].astype("string").str.strip()
        combined["market"] = combined["market"].astype("string").str.upper().str.strip()
        combined = combined.drop_duplicates(
            subset=["profile_name", "signal_date", "symbol", "market"],
            keep="last",
        )
        combined = combined.sort_values(["profile_name", "signal_date", "market", "symbol"]).reset_index(drop=True)
    combined[EVALUATION_COLUMNS].to_csv(ledger_path, index=False)


def build_markdown_report(
    *,
    signal_date: pd.Timestamp,
    signals_path: Path,
    evaluations: pd.DataFrame,
    summary: pd.DataFrame,
    cost_config: EvaluationCostConfig,
) -> str:
    """Build Markdown evaluation report."""

    missing = evaluations[evaluations["status"].eq("missing_day1_data")] if not evaluations.empty else evaluations
    date_label = signal_date.date().isoformat() if not pd.isna(signal_date) else "unknown"
    if evaluations.empty:
        order_text = "No planned paper orders were present in the signal CSV, so there is nothing to evaluate."
    else:
        order_text = markdown_table(evaluations)

    lines = [
        f"# Closed-Limit-Up Paper Evaluation - {date_label}",
        "",
        f"Signal CSV: `{signals_path}`",
        f"Signal date: `{date_label}`",
        f"Evaluation date: `{pd.Timestamp.today().normalize().date().isoformat()}`",
        "",
        "## Reminder",
        "",
        THEORETICAL_REMINDER,
        "",
        "## Cost Assumptions",
        "",
        markdown_table(pd.DataFrame([asdict(cost_config)])),
        "",
        "## Summary Metrics",
        "",
        markdown_table(summary),
        "",
        "## Order-Level Results",
        "",
        order_text,
        "",
        "## Missing Data Warnings",
        "",
        markdown_table(missing[["signal_date", "symbol", "market", "status", "notes"]] if not missing.empty else missing),
        "",
        "## Fill Verification Checklist",
        "",
        "- Confirm whether the Day0 close order was actually accepted and filled by the broker.",
        "- Record auction/order-book evidence when available.",
        "- Compare actual broker fill price with planned Day0 close.",
        "- Compare actual exit with theoretical Day1 open.",
        "",
        EXECUTION_WARNING,
        "",
    ]
    return "\n".join(lines)


def empty_evaluations() -> pd.DataFrame:
    return pd.DataFrame(columns=EVALUATION_COLUMNS)


def signal_label_from_path(path: Path, signal_date: pd.Timestamp) -> str:
    stem = path.stem.removeprefix("closed_limit_up_signals_")
    if stem:
        return stem
    return signal_date.strftime("%Y-%m-%d") if not pd.isna(signal_date) else "unknown"


def infer_profile_name(path: Path) -> str:
    stem = path.stem.removeprefix("closed_limit_up_signals_")
    match = re.match(r"(.+)_(\d{4}-\d{2}-\d{2})$", stem)
    if match:
        value = match.group(1)
        return "" if value == "all_profiles" else value
    return ""


def infer_profile_name_from_frame(frame: pd.DataFrame) -> str:
    if "profile_name" in frame.columns and frame["profile_name"].astype("string").str.len().gt(0).any():
        return str(frame["profile_name"].dropna().iloc[0])
    return "legacy"


def empty_day1_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["_signal_row_id", "day1_trade_date", "day1_open", "day1_high", "day1_low", "day1_close"]
    )


def empty_metric_values() -> dict[str, float]:
    return {
        "theoretical_exit_price": np.nan,
        "gross_return": np.nan,
        "open_to_high_return": np.nan,
        "open_to_low_return": np.nan,
        "open_to_close_return": np.nan,
        "gross_pnl": np.nan,
        "net_pnl": np.nan,
        "net_return": np.nan,
        "buy_commission": np.nan,
        "sell_commission": np.nan,
        "sell_tax": np.nan,
        "slippage_cost": np.nan,
        "total_cost": np.nan,
    }


def status_note(status: str) -> str:
    if status == "evaluated":
        return "Evaluated using next available daily_prices row for the same symbol and market."
    if status == "missing_day1_data":
        return "No later daily_prices row found for this symbol and market."
    return "Cannot evaluate because entry, exit, or share count is invalid."


def date_string(value: Any) -> str:
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return ""
    return ts.date().isoformat()


def safe_float(value: Any, default: float = np.nan) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def profit_factor(gains: float, losses: float) -> float:
    if losses == 0 and gains > 0:
        return float("inf")
    if losses == 0:
        return np.nan
    return float(gains / losses)


def markdown_table(frame: pd.DataFrame, *, max_rows: int = 40) -> str:
    if frame.empty:
        return "_No rows._"
    view = frame.head(max_rows).copy().fillna("")
    for column in view.columns:
        if pd.api.types.is_float_dtype(view[column]):
            view[column] = view[column].map(format_float)
    headers = [str(column) for column in view.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(escape_markdown_cell(row[column]) for column in view.columns) + " |")
    return "\n".join(lines)


def format_float(value: Any) -> str:
    if pd.isna(value):
        return ""
    number = float(value)
    if abs(number) >= 1000:
        return f"{number:,.2f}"
    return f"{number:.4f}"


def escape_markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate closed-limit-up paper signals after Day1 data arrives")
    parser.add_argument("--db", type=Path, default=get_settings().db_path)
    parser.add_argument("--signals-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--commission-rate", type=float, default=0.001425)
    parser.add_argument("--commission-discount", type=float, default=0.28)
    parser.add_argument("--sell-tax-rate", type=float, default=0.003)
    parser.add_argument("--slippage-bps-per-side", type=float, default=5.0)
    parser.add_argument("--minimum-commission-twd", type=float, default=20.0)
    args = parser.parse_args()

    evaluations, summary, eval_csv, eval_md, ledger = evaluate_closed_limit_up_signals(
        db_path=args.db,
        signals_csv=args.signals_csv,
        output_dir=args.output_dir,
        cost_config=EvaluationCostConfig(
            commission_rate=args.commission_rate,
            commission_discount=args.commission_discount,
            sell_tax_rate=args.sell_tax_rate,
            slippage_bps_per_side=args.slippage_bps_per_side,
            minimum_commission_twd=args.minimum_commission_twd,
        ),
    )
    print(f"Wrote {len(evaluations)} evaluation rows to {eval_csv}")
    print(f"Wrote Markdown report to {eval_md}")
    print(f"Updated ledger at {ledger}")
    print("\nSummary")
    print(summary.to_string(index=False))
    print(f"\n{THEORETICAL_REMINDER}")


if __name__ == "__main__":
    main()
