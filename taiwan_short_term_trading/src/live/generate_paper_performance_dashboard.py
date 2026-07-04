"""Generate a paper-trading performance dashboard for closed-limit-up profiles."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config.settings import get_settings
from src.db import get_connection, init_db
from src.live.evaluate_closed_limit_up_signals import EVALUATION_COLUMNS, LEDGER_OUTPUT


PROFILE_OUTPUT = "paper_performance_by_profile.csv"
DATE_OUTPUT = "paper_performance_by_date.csv"
SYMBOL_OUTPUT = "paper_performance_by_symbol.csv"
UNIQUE_OUTPUT = "paper_performance_unique_symbol_date.csv"
DASHBOARD_MD = "paper_performance_dashboard.md"
DEFAULT_INITIAL_CAPITAL_TWD = 1_000_000.0

PROFILE_COLUMNS = [
    "profile_name",
    "total_trades",
    "evaluated_trades",
    "pending_trades",
    "cumulative_net_pnl",
    "avg_net_return",
    "median_net_return",
    "win_rate",
    "profit_factor",
    "best_trade_symbol",
    "best_trade_signal_date",
    "best_trade_net_pnl",
    "worst_trade_symbol",
    "worst_trade_signal_date",
    "worst_trade_net_pnl",
    "best_signal_date",
    "best_signal_date_net_pnl",
    "worst_signal_date",
    "worst_signal_date_net_pnl",
    "final_equity",
    "max_drawdown_twd",
    "max_drawdown_pct",
    "manual_observations_completed",
    "manual_observations_missing",
    "execution_confirmed_trades",
    "execution_unconfirmed_trades",
]

DATE_COLUMNS = [
    "profile_name",
    "signal_date",
    "planned_orders",
    "evaluated_orders",
    "missing_day1_data",
    "invalid_price",
    "net_pnl",
    "avg_net_return",
    "win_rate",
    "manual_fill_completion_rate",
    "cumulative_net_pnl",
    "equity",
]

SYMBOL_COLUMNS = [
    "profile_name",
    "symbol",
    "name",
    "market",
    "sector",
    "industry",
    "evaluated_paper_trades",
    "cumulative_net_pnl",
    "avg_net_return",
    "manual_observation_count",
    "execution_confirmed_count",
]

UNIQUE_COLUMNS = [
    "signal_date",
    "symbol",
    "market",
    "name",
    "profiles_containing_symbol",
    "profile_level_rows",
    "selected_profile",
    "status",
    "planned_buy_notional_twd",
    "net_pnl",
    "net_return",
    "profile_level_net_pnl",
    "dedup_pnl_difference",
]


def generate_paper_performance_dashboard(
    *,
    db_path: Path | str,
    ledger_path: Path | str,
    output_dir: Path | str | None = None,
    initial_capital_twd: float = DEFAULT_INITIAL_CAPITAL_TWD,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, Path]:
    """Build dashboard CSVs and Markdown from the paper ledger and fill log."""

    init_db(db_path)
    report_dir = Path(output_dir) if output_dir is not None else Path(ledger_path).parent
    report_dir.mkdir(parents=True, exist_ok=True)

    ledger = load_ledger(ledger_path)
    observations = load_manual_observations(db_path)
    sector_map = load_sector_map(db_path)
    enriched = enrich_ledger(ledger, observations=observations, sector_map=sector_map)

    by_date = build_by_date(enriched, initial_capital_twd=initial_capital_twd)
    by_profile = build_by_profile(enriched, by_date=by_date, initial_capital_twd=initial_capital_twd)
    by_symbol = build_by_symbol(enriched)
    unique = build_unique_symbol_date(enriched)

    profile_path = report_dir / PROFILE_OUTPUT
    date_path = report_dir / DATE_OUTPUT
    symbol_path = report_dir / SYMBOL_OUTPUT
    unique_path = report_dir / UNIQUE_OUTPUT
    md_path = report_dir / DASHBOARD_MD

    by_profile.to_csv(profile_path, index=False)
    by_date.to_csv(date_path, index=False)
    by_symbol.to_csv(symbol_path, index=False)
    unique.to_csv(unique_path, index=False)
    md_path.write_text(
        build_dashboard_markdown(
            ledger=enriched,
            by_profile=by_profile,
            by_date=by_date,
            by_symbol=by_symbol,
            unique=unique,
            initial_capital_twd=initial_capital_twd,
            output_paths=[profile_path, date_path, symbol_path, unique_path],
        ),
        encoding="utf-8",
    )
    return by_profile, by_date, by_symbol, unique, md_path


def load_ledger(path: Path | str) -> pd.DataFrame:
    """Load the paper ledger, returning an empty frame with expected columns if needed."""

    ledger_path = Path(path)
    if not ledger_path.exists():
        return pd.DataFrame(columns=EVALUATION_COLUMNS)
    try:
        frame = pd.read_csv(ledger_path, dtype={"symbol": str})
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=EVALUATION_COLUMNS)
    for column in EVALUATION_COLUMNS:
        if column not in frame.columns:
            frame[column] = np.nan
    return normalize_ledger(frame[EVALUATION_COLUMNS])


def normalize_ledger(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    if output.empty:
        return output
    output["signal_date"] = pd.to_datetime(output["signal_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    output["day1_trade_date"] = pd.to_datetime(output["day1_trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    output["profile_name"] = text_column(output, "profile_name").replace("", "legacy")
    output["candidate_hash"] = text_column(output, "candidate_hash")
    output["symbol"] = output["symbol"].astype("string").str.strip()
    output["market"] = output["market"].astype("string").str.upper().str.strip()
    output["name"] = text_column(output, "name")
    output["sector"] = text_column(output, "sector")
    output["industry"] = text_column(output, "industry")
    output["status"] = text_column(output, "status")
    numeric_columns = [
        "planned_entry_price",
        "planned_shares",
        "planned_buy_notional_twd",
        "day1_open",
        "day1_high",
        "day1_low",
        "day1_close",
        "gross_return",
        "open_to_high_return",
        "open_to_low_return",
        "open_to_close_return",
        "gross_pnl",
        "net_pnl",
        "net_return",
    ]
    for column in numeric_columns:
        output[column] = pd.to_numeric(output[column], errors="coerce")
    return output


def load_manual_observations(db_path: Path | str) -> pd.DataFrame:
    with get_connection(db_path, read_only=True) as conn:
        frame = conn.execute(
            """
            SELECT
                signal_date,
                COALESCE(profile_name, '') AS profile_name,
                COALESCE(candidate_hash, '') AS candidate_hash,
                symbol,
                market,
                fill_status,
                actual_filled_shares,
                actual_avg_fill_price
            FROM manual_fill_observations
            """
        ).fetch_df()
    if frame.empty:
        return frame
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    frame["profile_name"] = text_column(frame, "profile_name")
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    frame["fill_status"] = text_column(frame, "fill_status").str.lower()
    frame["actual_filled_shares"] = pd.to_numeric(frame["actual_filled_shares"], errors="coerce")
    frame["actual_avg_fill_price"] = pd.to_numeric(frame["actual_avg_fill_price"], errors="coerce")
    frame["execution_confirmed"] = frame["fill_status"].isin(["fully_filled", "partially_filled"]) & (
        frame["actual_filled_shares"].fillna(0) > 0
    )
    return frame


def load_sector_map(db_path: Path | str) -> pd.DataFrame:
    with get_connection(db_path, read_only=True) as conn:
        frame = conn.execute(
            """
            SELECT symbol, market, name AS map_name, sector AS map_sector, industry AS map_industry
            FROM stock_sector_map
            """
        ).fetch_df()
    if frame.empty:
        return frame
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    for column in ["map_name", "map_sector", "map_industry"]:
        frame[column] = text_column(frame, column)
    return frame.drop_duplicates(subset=["symbol", "market"])


def enrich_ledger(
    ledger: pd.DataFrame,
    *,
    observations: pd.DataFrame,
    sector_map: pd.DataFrame,
) -> pd.DataFrame:
    """Join manual-fill and sector metadata onto ledger rows."""

    output = ledger.copy()
    for column in [
        "manual_observation_count",
        "execution_confirmed_count",
        "manual_observation_completed",
        "execution_confirmed",
    ]:
        output[column] = 0
    if output.empty:
        return output

    if not sector_map.empty:
        output = output.merge(sector_map, on=["symbol", "market"], how="left")
        output["name"] = output["name"].mask(output["name"].eq(""), output["map_name"].fillna(""))
        output["sector"] = output["sector"].mask(output["sector"].eq(""), output["map_sector"].fillna(""))
        output["industry"] = output["industry"].mask(output["industry"].eq(""), output["map_industry"].fillna(""))
        output = output.drop(columns=["map_name", "map_sector", "map_industry"])

    if not observations.empty:
        obs_summary = (
            observations.groupby(["profile_name", "signal_date", "symbol", "market"], dropna=False)
            .agg(
                manual_observation_count=("symbol", "size"),
                execution_confirmed_count=("execution_confirmed", "sum"),
            )
            .reset_index()
        )
        output = output.merge(
            obs_summary,
            on=["profile_name", "signal_date", "symbol", "market"],
            how="left",
            suffixes=("", "_obs"),
        )
        output["manual_observation_count"] = output["manual_observation_count_obs"].fillna(0).astype(int)
        output["execution_confirmed_count"] = output["execution_confirmed_count_obs"].fillna(0).astype(int)
        output = output.drop(columns=["manual_observation_count_obs", "execution_confirmed_count_obs"])
    output["manual_observation_completed"] = output["manual_observation_count"].gt(0).astype(int)
    output["execution_confirmed"] = output["execution_confirmed_count"].gt(0).astype(int)
    return output


def build_by_profile(
    ledger: pd.DataFrame,
    *,
    by_date: pd.DataFrame,
    initial_capital_twd: float,
) -> pd.DataFrame:
    if ledger.empty:
        return pd.DataFrame(columns=PROFILE_COLUMNS)

    rows: list[dict[str, Any]] = []
    for profile, group in ledger.groupby("profile_name", dropna=False):
        evaluated = group[group["status"].eq("evaluated")].copy()
        net_pnl = pd.to_numeric(evaluated["net_pnl"], errors="coerce").fillna(0.0)
        net_return = pd.to_numeric(evaluated["net_return"], errors="coerce")
        gains = net_pnl[net_pnl > 0].sum()
        losses = -net_pnl[net_pnl < 0].sum()
        profile_dates = by_date[by_date["profile_name"].eq(profile)].copy()

        best_trade = evaluated.loc[evaluated["net_pnl"].idxmax()] if not evaluated.empty else None
        worst_trade = evaluated.loc[evaluated["net_pnl"].idxmin()] if not evaluated.empty else None
        date_pnl = evaluated.groupby("signal_date", dropna=False)["net_pnl"].sum() if not evaluated.empty else pd.Series(dtype=float)
        best_date = date_pnl.idxmax() if not date_pnl.empty else ""
        worst_date = date_pnl.idxmin() if not date_pnl.empty else ""

        rows.append(
            {
                "profile_name": profile,
                "total_trades": int(len(group)),
                "evaluated_trades": int(len(evaluated)),
                "pending_trades": int(len(group) - len(evaluated)),
                "cumulative_net_pnl": float(net_pnl.sum()),
                "avg_net_return": safe_stat(net_return, "mean"),
                "median_net_return": safe_stat(net_return, "median"),
                "win_rate": float((net_pnl > 0).mean()) if len(net_pnl) else np.nan,
                "profit_factor": profit_factor(gains, losses),
                "best_trade_symbol": best_trade.get("symbol", "") if best_trade is not None else "",
                "best_trade_signal_date": best_trade.get("signal_date", "") if best_trade is not None else "",
                "best_trade_net_pnl": float(best_trade.get("net_pnl", np.nan)) if best_trade is not None else np.nan,
                "worst_trade_symbol": worst_trade.get("symbol", "") if worst_trade is not None else "",
                "worst_trade_signal_date": worst_trade.get("signal_date", "") if worst_trade is not None else "",
                "worst_trade_net_pnl": float(worst_trade.get("net_pnl", np.nan)) if worst_trade is not None else np.nan,
                "best_signal_date": best_date,
                "best_signal_date_net_pnl": float(date_pnl.loc[best_date]) if best_date != "" else np.nan,
                "worst_signal_date": worst_date,
                "worst_signal_date_net_pnl": float(date_pnl.loc[worst_date]) if worst_date != "" else np.nan,
                "final_equity": float(initial_capital_twd + net_pnl.sum()),
                "max_drawdown_twd": float(profile_dates["drawdown_twd"].min()) if "drawdown_twd" in profile_dates else 0.0,
                "max_drawdown_pct": float(profile_dates["drawdown_pct"].min()) if "drawdown_pct" in profile_dates else 0.0,
                "manual_observations_completed": int(group["manual_observation_completed"].sum()),
                "manual_observations_missing": int(len(group) - group["manual_observation_completed"].sum()),
                "execution_confirmed_trades": int(group["execution_confirmed"].sum()),
                "execution_unconfirmed_trades": int(len(group) - group["execution_confirmed"].sum()),
            }
        )
    output = pd.DataFrame(rows, columns=PROFILE_COLUMNS)
    return output.sort_values(["cumulative_net_pnl", "evaluated_trades"], ascending=[False, False]).reset_index(drop=True)


def build_by_date(ledger: pd.DataFrame, *, initial_capital_twd: float) -> pd.DataFrame:
    if ledger.empty:
        return pd.DataFrame(columns=DATE_COLUMNS + ["drawdown_twd", "drawdown_pct"])

    rows: list[dict[str, Any]] = []
    for (profile, signal_date), group in ledger.groupby(["profile_name", "signal_date"], dropna=False):
        evaluated = group[group["status"].eq("evaluated")]
        net_pnl = pd.to_numeric(evaluated["net_pnl"], errors="coerce").fillna(0.0)
        net_return = pd.to_numeric(evaluated["net_return"], errors="coerce")
        manual_completion = group["manual_observation_completed"].sum() / len(group) if len(group) else np.nan
        rows.append(
            {
                "profile_name": profile,
                "signal_date": signal_date,
                "planned_orders": int(len(group)),
                "evaluated_orders": int(len(evaluated)),
                "missing_day1_data": int(group["status"].eq("missing_day1_data").sum()),
                "invalid_price": int(group["status"].eq("invalid_price").sum()),
                "net_pnl": float(net_pnl.sum()),
                "avg_net_return": safe_stat(net_return, "mean"),
                "win_rate": float((net_pnl > 0).mean()) if len(net_pnl) else np.nan,
                "manual_fill_completion_rate": float(manual_completion),
            }
        )
    output = pd.DataFrame(rows)
    if output.empty:
        return pd.DataFrame(columns=DATE_COLUMNS + ["drawdown_twd", "drawdown_pct"])
    output = output.sort_values(["profile_name", "signal_date"]).reset_index(drop=True)
    output["cumulative_net_pnl"] = output.groupby("profile_name")["net_pnl"].cumsum()
    output["equity"] = initial_capital_twd + output["cumulative_net_pnl"]
    output["running_peak_equity"] = output.groupby("profile_name")["equity"].cummax()
    output["running_peak_equity"] = output["running_peak_equity"].clip(lower=initial_capital_twd)
    output["drawdown_twd"] = output["equity"] - output["running_peak_equity"]
    output["drawdown_pct"] = output["drawdown_twd"] / output["running_peak_equity"]
    return output[DATE_COLUMNS + ["drawdown_twd", "drawdown_pct"]]


def build_by_symbol(ledger: pd.DataFrame) -> pd.DataFrame:
    if ledger.empty:
        return pd.DataFrame(columns=SYMBOL_COLUMNS)

    rows: list[dict[str, Any]] = []
    for keys, group in ledger.groupby(["profile_name", "symbol", "market"], dropna=False, sort=True):
        profile, symbol, market = keys
        evaluated = group[group["status"].eq("evaluated")]
        net_pnl = pd.to_numeric(evaluated["net_pnl"], errors="coerce").fillna(0.0)
        net_return = pd.to_numeric(evaluated["net_return"], errors="coerce")
        rows.append(
            {
                "profile_name": profile,
                "symbol": symbol,
                "name": first_text(group["name"]),
                "market": market,
                "sector": first_text(group["sector"]),
                "industry": first_text(group["industry"]),
                "evaluated_paper_trades": int(len(evaluated)),
                "cumulative_net_pnl": float(net_pnl.sum()),
                "avg_net_return": safe_stat(net_return, "mean"),
                "manual_observation_count": int(group["manual_observation_count"].sum()),
                "execution_confirmed_count": int(group["execution_confirmed"].sum()),
            }
        )
    return pd.DataFrame(rows, columns=SYMBOL_COLUMNS).sort_values(
        ["cumulative_net_pnl", "evaluated_paper_trades"],
        ascending=[False, False],
    ).reset_index(drop=True)


def build_unique_symbol_date(ledger: pd.DataFrame) -> pd.DataFrame:
    if ledger.empty:
        return pd.DataFrame(columns=UNIQUE_COLUMNS)

    evaluated = ledger[ledger["status"].eq("evaluated")].copy()
    if evaluated.empty:
        return pd.DataFrame(columns=UNIQUE_COLUMNS)
    evaluated["_notional_sort"] = pd.to_numeric(evaluated["planned_buy_notional_twd"], errors="coerce").fillna(0.0)
    evaluated = evaluated.sort_values(
        ["signal_date", "symbol", "market", "_notional_sort", "profile_name"],
        ascending=[True, True, True, False, True],
    )
    rows: list[dict[str, Any]] = []
    for (signal_date, symbol, market), group in evaluated.groupby(["signal_date", "symbol", "market"], sort=False):
        selected = group.iloc[0]
        profile_level_net_pnl = pd.to_numeric(group["net_pnl"], errors="coerce").fillna(0.0).sum()
        selected_net_pnl = float(selected.get("net_pnl", 0.0))
        profiles = ", ".join(group["profile_name"].dropna().astype(str).drop_duplicates().tolist())
        rows.append(
            {
                "signal_date": signal_date,
                "symbol": symbol,
                "market": market,
                "name": selected.get("name", ""),
                "profiles_containing_symbol": profiles,
                "profile_level_rows": int(len(group)),
                "selected_profile": selected.get("profile_name", ""),
                "status": selected.get("status", ""),
                "planned_buy_notional_twd": float(selected.get("planned_buy_notional_twd", np.nan)),
                "net_pnl": selected_net_pnl,
                "net_return": float(selected.get("net_return", np.nan)),
                "profile_level_net_pnl": float(profile_level_net_pnl),
                "dedup_pnl_difference": selected_net_pnl - float(profile_level_net_pnl),
            }
        )
    return pd.DataFrame(rows, columns=UNIQUE_COLUMNS).sort_values(["signal_date", "symbol", "market"]).reset_index(drop=True)


def build_dashboard_markdown(
    *,
    ledger: pd.DataFrame,
    by_profile: pd.DataFrame,
    by_date: pd.DataFrame,
    by_symbol: pd.DataFrame,
    unique: pd.DataFrame,
    initial_capital_twd: float,
    output_paths: list[Path],
) -> str:
    evaluated = ledger[ledger["status"].eq("evaluated")] if not ledger.empty else ledger
    profile_level_pnl = pd.to_numeric(evaluated.get("net_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()
    unique_pnl = pd.to_numeric(unique.get("net_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()
    leader = by_profile.iloc[0]["profile_name"] if not by_profile.empty and by_profile.iloc[0]["evaluated_trades"] > 0 else "None yet"
    enough_data = "No profile has enough data to promote beyond paper tracking yet."
    if not by_profile.empty and by_profile["evaluated_trades"].max() >= 100:
        enough_data = "At least one profile has 100+ evaluated paper trades, but fill observations still decide live readiness."
    missing_fills = int(by_profile["manual_observations_missing"].sum()) if not by_profile.empty else 0
    fill_block = (
        "Live trading remains blocked by missing fill observations."
        if missing_fills
        else "Manual fill observations are complete for current ledger rows, but fill quality should still be reviewed."
    )
    output_list = "\n".join(f"- `{path}`" for path in output_paths)

    lines = [
        "# Closed-Limit-Up Paper Performance Dashboard",
        "",
        "## Executive Summary",
        "",
        f"Ledger rows: {len(ledger):,}",
        f"Evaluated paper trades: {len(evaluated):,}",
        f"Profile-level cumulative net PnL: {profile_level_pnl:,.2f} TWD",
        f"De-duplicated unique symbol/date net PnL: {unique_pnl:,.2f} TWD",
        f"Initial capital assumption for equity curves: {initial_capital_twd:,.0f} TWD",
        "",
        "## Profile Leaderboard",
        "",
        markdown_table(by_profile),
        "",
        "## Daily Performance",
        "",
        markdown_table(by_date),
        "",
        "## Best/Worst Trades",
        "",
        markdown_table(best_worst_trade_table(ledger)),
        "",
        "## Symbol Performance",
        "",
        markdown_table(by_symbol),
        "",
        "## Unique Symbol/Date View",
        "",
        "This removes duplicate same-date/same-symbol rows across profiles before summing PnL.",
        "",
        markdown_table(unique),
        "",
        "## Manual Fill Status",
        "",
        markdown_table(
            by_profile[
                [
                    "profile_name",
                    "manual_observations_completed",
                    "manual_observations_missing",
                    "execution_confirmed_trades",
                    "execution_unconfirmed_trades",
                ]
            ]
            if not by_profile.empty
            else by_profile
        ),
        "",
        "## Execution Caveat",
        "",
        "The price-edge ledger is theoretical until Day0 close limit-up execution is confirmed with broker/order-book observations.",
        "",
        "## Current Recommendation",
        "",
        f"- Leading paper price-performance profile: `{leader}`.",
        f"- {enough_data}",
        f"- {fill_block}",
        "",
        "## Generated Files",
        "",
        output_list,
        "",
    ]
    return "\n".join(lines)


def best_worst_trade_table(ledger: pd.DataFrame) -> pd.DataFrame:
    if ledger.empty:
        return pd.DataFrame()
    evaluated = ledger[ledger["status"].eq("evaluated")].copy()
    if evaluated.empty:
        return pd.DataFrame()
    best = evaluated.loc[evaluated["net_pnl"].idxmax()]
    worst = evaluated.loc[evaluated["net_pnl"].idxmin()]
    return pd.DataFrame(
        [
            {
                "kind": "best",
                "profile_name": best["profile_name"],
                "signal_date": best["signal_date"],
                "symbol": best["symbol"],
                "name": best["name"],
                "net_pnl": best["net_pnl"],
                "net_return": best["net_return"],
            },
            {
                "kind": "worst",
                "profile_name": worst["profile_name"],
                "signal_date": worst["signal_date"],
                "symbol": worst["symbol"],
                "name": worst["name"],
                "net_pnl": worst["net_pnl"],
                "net_return": worst["net_return"],
            },
        ]
    )


def profit_factor(gains: float, losses: float) -> float:
    if losses == 0 and gains > 0:
        return float("inf")
    if losses == 0:
        return np.nan
    return float(gains / losses)


def safe_stat(series: pd.Series, stat: str) -> float:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return np.nan
    if stat == "mean":
        return float(clean.mean())
    if stat == "median":
        return float(clean.median())
    raise ValueError(f"Unsupported stat: {stat}")


def first_text(series: pd.Series) -> str:
    clean = series.astype("string").fillna("").str.strip()
    clean = clean[clean.ne("")]
    return str(clean.iloc[0]) if not clean.empty else ""


def text_column(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series("", index=frame.index, dtype="string")
    return frame[column].astype("string").fillna("").str.strip()


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
    if math.isinf(number):
        return "inf"
    if abs(number) >= 1000:
        return f"{number:,.2f}"
    return f"{number:.4f}"


def escape_markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate closed-limit-up paper performance dashboard")
    parser.add_argument("--db", type=Path, default=get_settings().db_path)
    parser.add_argument(
        "--ledger",
        type=Path,
        default=get_settings().project_root / "reports" / "live_signals" / LEDGER_OUTPUT,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=get_settings().project_root / "reports" / "live_signals",
    )
    parser.add_argument("--initial-capital-twd", type=float, default=DEFAULT_INITIAL_CAPITAL_TWD)
    args = parser.parse_args()

    by_profile, by_date, by_symbol, unique, md_path = generate_paper_performance_dashboard(
        db_path=args.db,
        ledger_path=args.ledger,
        output_dir=args.output_dir,
        initial_capital_twd=args.initial_capital_twd,
    )
    print(f"Wrote {len(by_profile)} profile rows to {args.output_dir / PROFILE_OUTPUT}")
    print(f"Wrote {len(by_date)} date rows to {args.output_dir / DATE_OUTPUT}")
    print(f"Wrote {len(by_symbol)} symbol rows to {args.output_dir / SYMBOL_OUTPUT}")
    print(f"Wrote {len(unique)} unique symbol/date rows to {args.output_dir / UNIQUE_OUTPUT}")
    print(f"Wrote dashboard to {md_path}")


if __name__ == "__main__":
    main()
