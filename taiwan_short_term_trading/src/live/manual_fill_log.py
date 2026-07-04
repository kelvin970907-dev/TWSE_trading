"""Manual broker/order-book fill observation log for paper trading."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config.settings import get_settings
from src.backtests.costs import calculate_trade_costs
from src.db import get_connection, init_db, upsert_dataframe
from src.live.evaluate_closed_limit_up_signals import EVALUATION_COLUMNS
from src.live.generate_closed_limit_up_signals import OUTPUT_COLUMNS


FILL_STATUS_VALUES = {"not_submitted", "fully_filled", "partially_filled", "not_filled", "unknown"}
MANUAL_FILL_SUMMARY_CSV = "manual_fill_summary.csv"
MANUAL_FILL_SUMMARY_MD = "manual_fill_summary.md"
ACTION_CHECKLIST_CSV = "manual_fill_action_checklist.csv"
ACTION_CHECKLIST_MD = "manual_fill_action_checklist.md"

MANUAL_FILL_COLUMNS = [
    "observation_id",
    "signal_date",
    "profile_name",
    "candidate_hash",
    "symbol",
    "market",
    "name",
    "observed_time",
    "broker",
    "intended_entry_price",
    "displayed_best_bid",
    "displayed_best_ask",
    "displayed_bid_size_shares",
    "displayed_ask_size_shares",
    "limit_up_price",
    "was_limit_up_locked",
    "was_order_submitted",
    "order_type",
    "order_quantity_shares",
    "order_price",
    "simulated_queue_position",
    "actual_filled_shares",
    "actual_avg_fill_price",
    "fill_status",
    "reason_not_filled",
    "screenshot_path",
    "notes",
    "created_at",
]

REQUIRED_IMPORT_COLUMNS = ["signal_date", "symbol", "market", "fill_status"]

SUMMARY_COLUMNS = [
    "section",
    "group",
    "signal_date",
    "symbol",
    "market",
    "observations",
    "fully_filled",
    "partially_filled",
    "not_filled",
    "not_submitted",
    "unknown",
    "fill_rate",
    "avg_displayed_bid_size_shares",
    "avg_displayed_ask_size_shares",
    "avg_fill_vs_intended_bps",
    "paper_net_pnl",
    "actual_fill_adjusted_net_pnl",
    "missing_observation_count",
    "notes",
]

ACTION_CHECKLIST_COLUMNS = [
    "signal_date",
    "symbol",
    "name",
    "market",
    "sector",
    "industry",
    "profiles_containing_symbol",
    "intended_entry_price",
    "limit_up_price",
    "max_planned_shares_across_profiles",
    "profile_specific_rows_needed",
    "template_exists",
    "template_path",
    "instruction_file_path",
    "observe_best_bid_near_close",
    "observe_best_ask_near_close",
    "observe_visible_bid_size",
    "observe_visible_ask_size",
    "observe_whether_limit_up_locked",
    "observe_whether_hypothetical_limit_order_would_fill",
    "record_screenshot_path",
    "record_notes",
]


def init_manual_fill_log(db_path: Path | str) -> Path:
    """Create the manual fill observation table if needed."""

    return init_db(db_path)


def export_template(output: Path | str) -> Path:
    """Export a blank manual fill observation CSV template."""

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(columns=MANUAL_FILL_COLUMNS).to_csv(output_path, index=False)
    return output_path


def import_manual_fill_csv(*, db_path: Path | str, csv_path: Path | str) -> int:
    """Validate and import manual fill observations into DuckDB."""

    init_db(db_path)
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Manual fill CSV does not exist: {path}")
    frame = pd.read_csv(path)
    normalized = normalize_manual_fill_frame(frame)
    with get_connection(db_path) as conn:
        return upsert_dataframe(
            conn,
            "manual_fill_observations",
            normalized[MANUAL_FILL_COLUMNS],
            ["observation_id"],
        )


def normalize_manual_fill_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize manual-fill rows and generate missing IDs/timestamps."""

    missing_columns = sorted(set(REQUIRED_IMPORT_COLUMNS) - set(frame.columns))
    if missing_columns:
        raise ValueError(f"Manual fill CSV is missing required columns: {missing_columns}")
    output = frame.copy()
    for column in MANUAL_FILL_COLUMNS:
        if column not in output.columns:
            output[column] = np.nan

    output["signal_date"] = pd.to_datetime(output["signal_date"], errors="coerce").dt.normalize()
    if output["signal_date"].isna().any():
        raise ValueError("Manual fill CSV contains invalid or blank signal_date values")
    output["symbol"] = output["symbol"].astype("string").str.strip()
    output["market"] = output["market"].astype("string").str.upper().str.strip()
    output["profile_name"] = output["profile_name"].astype("string").fillna("").str.strip()
    output["candidate_hash"] = output["candidate_hash"].astype("string").fillna("").str.strip()
    output["fill_status"] = output["fill_status"].astype("string").str.strip().str.lower().fillna("unknown")
    blank_required = output[["symbol", "market", "fill_status"]].isna() | output[
        ["symbol", "market", "fill_status"]
    ].eq("")
    if blank_required.any().any():
        raise ValueError("Manual fill CSV contains blank symbol, market, or fill_status values")
    invalid_statuses = sorted(set(output["fill_status"]) - FILL_STATUS_VALUES)
    if invalid_statuses:
        raise ValueError(f"Invalid fill_status values: {invalid_statuses}. Allowed: {sorted(FILL_STATUS_VALUES)}")

    text_columns = [
        "name",
        "observed_time",
        "broker",
        "order_type",
        "reason_not_filled",
        "screenshot_path",
        "notes",
    ]
    for column in text_columns:
        output[column] = output[column].astype("string").fillna("").str.strip()

    numeric_columns = [
        "intended_entry_price",
        "displayed_best_bid",
        "displayed_best_ask",
        "displayed_bid_size_shares",
        "displayed_ask_size_shares",
        "limit_up_price",
        "order_quantity_shares",
        "order_price",
        "simulated_queue_position",
        "actual_filled_shares",
        "actual_avg_fill_price",
    ]
    for column in numeric_columns:
        output[column] = pd.to_numeric(output[column], errors="coerce")
    for column in [
        "displayed_bid_size_shares",
        "displayed_ask_size_shares",
        "order_quantity_shares",
        "simulated_queue_position",
        "actual_filled_shares",
    ]:
        output[column] = output[column].astype("Int64")

    for column in ["was_limit_up_locked", "was_order_submitted"]:
        output[column] = output[column].map(parse_optional_bool).astype("boolean")

    output["created_at"] = pd.to_datetime(output["created_at"], errors="coerce")
    output["created_at"] = output["created_at"].fillna(pd.Timestamp.now().floor("s"))
    output["observation_id"] = output["observation_id"].astype("string").fillna("").str.strip()
    blank_ids = output["observation_id"].eq("")
    output.loc[blank_ids, "observation_id"] = output.loc[blank_ids].apply(make_observation_id, axis=1)
    return output[MANUAL_FILL_COLUMNS].reset_index(drop=True)


def summarize_manual_fill_log(
    *,
    db_path: Path | str,
    output_dir: Path | str | None = None,
) -> tuple[pd.DataFrame, Path, Path]:
    """Generate CSV and Markdown summaries for manual fill observations."""

    init_db(db_path)
    report_dir = Path(output_dir) if output_dir is not None else get_settings().project_root / "reports" / "live_signals"
    report_dir.mkdir(parents=True, exist_ok=True)
    observations = load_manual_observations(db_path)
    signal_orders = load_signal_orders(report_dir)
    missing = find_missing_manual_observations(observations, signal_orders)
    ledger = load_paper_ledger(report_dir)
    summary = build_manual_fill_summary(observations, missing=missing, ledger=ledger)
    csv_path = report_dir / MANUAL_FILL_SUMMARY_CSV
    md_path = report_dir / MANUAL_FILL_SUMMARY_MD
    summary.to_csv(csv_path, index=False)
    md_path.write_text(
        build_manual_fill_markdown(
            observations=observations,
            summary=summary,
            missing=missing,
            ledger=ledger,
            signal_orders=signal_orders,
        ),
        encoding="utf-8",
    )
    return summary, csv_path, md_path


def load_manual_observations(db_path: Path | str) -> pd.DataFrame:
    """Load observations joined to sector metadata if available."""

    with get_connection(db_path, read_only=True) as conn:
        return conn.execute(
            """
            SELECT
                o.*,
                COALESCE(NULLIF(o.name, ''), m.name) AS resolved_name,
                m.sector,
                m.industry
            FROM manual_fill_observations o
            LEFT JOIN stock_sector_map m
              ON m.symbol = o.symbol
             AND m.market = o.market
            ORDER BY o.signal_date, o.market, o.symbol, o.observed_time
            """
        ).fetch_df()


def build_manual_fill_summary(
    observations: pd.DataFrame,
    *,
    missing: pd.DataFrame,
    ledger: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if observations.empty:
        rows.append(empty_summary_row(section="overall", group="all", notes="No manual fill observations logged."))
    else:
        rows.append({**fill_metrics(observations), "section": "overall", "group": "all", "notes": ""})
        for status, group in observations.groupby("fill_status", dropna=False, observed=False):
            rows.append({**fill_metrics(group), "section": "by_fill_status", "group": str(status), "notes": ""})
        for column, section in [
            ("symbol", "by_symbol"),
            ("sector", "by_sector"),
            ("industry", "by_industry"),
            ("was_limit_up_locked", "by_limit_up_locked"),
        ]:
            if column in observations.columns:
                for value, group in observations.groupby(column, dropna=False, observed=False):
                    rows.append({**fill_metrics(group), "section": section, "group": str(value), "notes": ""})
        pnl_row = linked_pnl_comparison(observations, ledger)
        rows.append(pnl_row)

    if missing.empty:
        rows.append(empty_summary_row(section="missing_observation", group="none", notes="No missing signal observations."))
    else:
        rows.append(
            {
                **empty_summary_row(section="missing_observation", group="all"),
                "missing_observation_count": int(len(missing)),
                "notes": "Signals exist without a matching manual fill observation.",
            }
        )
        for row in missing.to_dict("records"):
            rows.append(
                {
                    **empty_summary_row(section="missing_observation_detail", group=str(row.get("symbol", ""))),
                    "signal_date": date_string(row.get("signal_date")),
                    "symbol": row.get("symbol"),
                    "market": row.get("market"),
                    "missing_observation_count": 1,
                    "notes": row.get("name", ""),
                }
            )

    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def fill_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    statuses = frame["fill_status"].astype("string").fillna("unknown")
    observations = int(len(frame))
    fully = int(statuses.eq("fully_filled").sum())
    partial = int(statuses.eq("partially_filled").sum())
    not_filled = int(statuses.eq("not_filled").sum())
    not_submitted = int(statuses.eq("not_submitted").sum())
    unknown = int(statuses.eq("unknown").sum())
    fill_rate = (fully + partial) / observations if observations else np.nan
    intended = pd.to_numeric(frame["intended_entry_price"], errors="coerce")
    fill_price = pd.to_numeric(frame["actual_avg_fill_price"], errors="coerce")
    fill_vs_intended = (fill_price / intended - 1.0) * 10_000
    return {
        "signal_date": "",
        "symbol": "",
        "market": "",
        "observations": observations,
        "fully_filled": fully,
        "partially_filled": partial,
        "not_filled": not_filled,
        "not_submitted": not_submitted,
        "unknown": unknown,
        "fill_rate": fill_rate,
        "avg_displayed_bid_size_shares": pd.to_numeric(
            frame["displayed_bid_size_shares"], errors="coerce"
        ).mean(),
        "avg_displayed_ask_size_shares": pd.to_numeric(
            frame["displayed_ask_size_shares"], errors="coerce"
        ).mean(),
        "avg_fill_vs_intended_bps": fill_vs_intended.mean(),
        "paper_net_pnl": np.nan,
        "actual_fill_adjusted_net_pnl": np.nan,
        "missing_observation_count": 0,
    }


def linked_pnl_comparison(observations: pd.DataFrame, ledger: pd.DataFrame) -> dict[str, Any]:
    row = empty_summary_row(section="paper_vs_actual_fill_pnl", group="linked")
    if observations.empty or ledger.empty:
        row["notes"] = "No linked paper-ledger rows available."
        return row
    obs = observations.copy()
    obs["signal_date"] = pd.to_datetime(obs["signal_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    obs["profile_name"] = text_column(obs, "profile_name")
    obs["symbol"] = obs["symbol"].astype("string").str.strip()
    obs["market"] = obs["market"].astype("string").str.upper().str.strip()
    led = ledger.copy()
    led["signal_date"] = pd.to_datetime(led["signal_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    led["profile_name"] = text_column(led, "profile_name")
    led["symbol"] = led["symbol"].astype("string").str.strip()
    led["market"] = led["market"].astype("string").str.upper().str.strip()
    joined = obs.merge(
        led,
        on=["profile_name", "signal_date", "symbol", "market"],
        how="inner",
        suffixes=("", "_ledger"),
    )
    if joined.empty:
        row["notes"] = "No manual observations match paper-ledger rows."
        return row

    actual_rows = joined[
        (pd.to_numeric(joined["actual_filled_shares"], errors="coerce") > 0)
        & (pd.to_numeric(joined["actual_avg_fill_price"], errors="coerce") > 0)
        & (pd.to_numeric(joined["theoretical_exit_price"], errors="coerce") > 0)
    ].copy()
    if actual_rows.empty:
        row["observations"] = int(len(joined))
        row["paper_net_pnl"] = pd.to_numeric(joined.get("net_pnl", np.nan), errors="coerce").sum()
        row["notes"] = "Linked rows exist, but no actual filled shares/prices were logged."
        return row

    actual_pnl = 0.0
    for item in actual_rows.to_dict("records"):
        costs = calculate_trade_costs(
            side="long",
            entry_price=float(item["actual_avg_fill_price"]),
            exit_price=float(item["theoretical_exit_price"]),
            shares=int(item["actual_filled_shares"]),
            sell_tax_rate=0.003,
            slippage_bps_per_side=5.0,
            minimum_commission_twd=20.0,
            is_day_trade=False,
            normal_sell_tax_rate=0.003,
        )
        actual_pnl += costs["net_pnl"]
    row["observations"] = int(len(actual_rows))
    row["paper_net_pnl"] = pd.to_numeric(actual_rows.get("net_pnl", np.nan), errors="coerce").sum()
    row["actual_fill_adjusted_net_pnl"] = actual_pnl
    row["notes"] = "Compared paper theoretical PnL with actual logged fill prices/sizes."
    return row


def missing_manual_observation_status(*, db_path: Path | str, output_dir: Path | str) -> dict[str, Any]:
    """Return selected signal count and missing manual-observation count for pipeline reports."""

    init_db(db_path)
    report_dir = Path(output_dir)
    observations = load_manual_observations(db_path)
    signals = load_signal_orders(report_dir)
    missing = find_missing_manual_observations(observations, signals)
    return {
        "total_signal_orders": int(len(signals)),
        "missing_manual_observations": int(len(missing)),
        "missing_symbols": missing["symbol"].astype(str).tolist() if not missing.empty else [],
    }


def load_signal_orders(output_dir: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(output_dir.glob("closed_limit_up_signals_*.csv")):
        if path.stem.startswith("closed_limit_up_signals_all_profiles_"):
            continue
        try:
            frame = pd.read_csv(path, dtype={"symbol": str})
        except pd.errors.EmptyDataError:
            continue
        if frame.empty:
            continue
        for column in OUTPUT_COLUMNS:
            if column not in frame.columns:
                frame[column] = np.nan
        frames.append(frame[OUTPUT_COLUMNS])
    if not frames:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    output = pd.concat(frames, ignore_index=True)
    output["signal_date"] = pd.to_datetime(output["signal_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    output["profile_name"] = text_column(output, "profile_name")
    output["symbol"] = output["symbol"].astype("string").str.strip()
    output["market"] = output["market"].astype("string").str.upper().str.strip()
    return output.dropna(subset=["signal_date", "symbol", "market"]).reset_index(drop=True)


def find_missing_manual_observations(observations: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
    if signals.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    if observations.empty:
        return signals.copy()
    obs_keys = observations[["signal_date", "profile_name", "symbol", "market"]].copy()
    obs_keys["signal_date"] = pd.to_datetime(obs_keys["signal_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    obs_keys["profile_name"] = text_column(obs_keys, "profile_name")
    obs_keys["symbol"] = obs_keys["symbol"].astype("string").str.strip()
    obs_keys["market"] = obs_keys["market"].astype("string").str.upper().str.strip()
    obs_keys = obs_keys.drop_duplicates()
    signals = signals.copy()
    signals["profile_name"] = text_column(signals, "profile_name")
    merged = signals.merge(
        obs_keys.assign(_has_observation=True),
        on=["profile_name", "signal_date", "symbol", "market"],
        how="left",
    )
    missing_mask = ~merged["_has_observation"].fillna(False).astype(bool)
    return merged[missing_mask].drop(columns=["_has_observation"]).reset_index(drop=True)


def load_paper_ledger(output_dir: Path) -> pd.DataFrame:
    path = output_dir / "closed_limit_up_paper_ledger.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype={"symbol": str})
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def text_column(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series("", index=frame.index, dtype="string")
    return frame[column].astype("string").fillna("").str.strip()


def build_manual_fill_markdown(
    *,
    observations: pd.DataFrame,
    summary: pd.DataFrame,
    missing: pd.DataFrame,
    ledger: pd.DataFrame,
    signal_orders: pd.DataFrame,
) -> str:
    if observations.empty:
        observation_note = "No completed observations imported yet."
    else:
        observation_note = "Completed manual observations have been imported."
    if missing.empty:
        pending_note = "No pending manual observations."
    else:
        pending_note = f"{len(missing):,} paper signal(s) still require manual fill observations."
    lines = [
        "# Manual Fill Observation Summary",
        "",
        "## Executive Snapshot",
        "",
        f"Manual observations: {len(observations):,}",
        observation_note,
        f"Paper signal rows available: {len(signal_orders):,}",
        f"Signals missing observations: {len(missing):,}",
        f"Paper ledger rows available: {len(ledger):,}",
        pending_note,
        "",
        "## Summary",
        "",
        markdown_table(summary),
        "",
        "## Missing Manual Observations",
        "",
        markdown_table(missing[["signal_date", "symbol", "market", "name", "planned_shares"]] if not missing.empty else missing),
        "",
        "## Reminder",
        "",
        "Daily OHLCV fill proxies are not enough. Treat the strategy as paper-only until manual broker/order-book observations show reliable Day0 close fillability.",
        "",
    ]
    return "\n".join(lines)


def generate_manual_fill_action_checklist(
    *,
    db_path: Path | str,
    ledger_path: Path | str,
    output_dir: Path | str | None = None,
) -> tuple[pd.DataFrame, Path, Path]:
    """Create a consolidated checklist for missing manual fill observations."""

    init_db(db_path)
    report_dir = Path(output_dir) if output_dir is not None else Path(ledger_path).parent
    report_dir.mkdir(parents=True, exist_ok=True)
    ledger = load_paper_ledger_file(ledger_path)
    observations = load_manual_observations(db_path)
    pending = find_missing_ledger_observations(ledger, observations)
    helpers = load_unique_symbol_helpers(report_dir)
    templates = load_manual_fill_templates(report_dir)
    checklist = build_action_checklist(
        pending=pending,
        helpers=helpers,
        templates=templates,
        output_dir=report_dir,
    )

    csv_path = report_dir / ACTION_CHECKLIST_CSV
    md_path = report_dir / ACTION_CHECKLIST_MD
    checklist.to_csv(csv_path, index=False)
    md_path.write_text(
        build_action_checklist_markdown(
            checklist=checklist,
            pending=pending,
            observations=observations,
            csv_path=csv_path,
        ),
        encoding="utf-8",
    )
    return checklist, csv_path, md_path


def load_paper_ledger_file(path: Path | str) -> pd.DataFrame:
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
    if frame.empty:
        return frame[EVALUATION_COLUMNS]
    output = frame[EVALUATION_COLUMNS].copy()
    output["signal_date"] = pd.to_datetime(output["signal_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    output["profile_name"] = text_column(output, "profile_name").replace("", "legacy")
    output["symbol"] = output["symbol"].astype("string").str.strip()
    output["market"] = output["market"].astype("string").str.upper().str.strip()
    output["name"] = text_column(output, "name")
    output["sector"] = text_column(output, "sector")
    output["industry"] = text_column(output, "industry")
    for column in ["planned_entry_price", "planned_shares", "planned_buy_notional_twd"]:
        output[column] = pd.to_numeric(output[column], errors="coerce")
    return output.dropna(subset=["signal_date", "symbol", "market"]).reset_index(drop=True)


def find_missing_ledger_observations(ledger: pd.DataFrame, observations: pd.DataFrame) -> pd.DataFrame:
    if ledger.empty:
        return pd.DataFrame(columns=EVALUATION_COLUMNS)
    if observations.empty:
        return ledger.copy()
    obs_keys = observations[["signal_date", "profile_name", "symbol", "market"]].copy()
    obs_keys["signal_date"] = pd.to_datetime(obs_keys["signal_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    obs_keys["profile_name"] = text_column(obs_keys, "profile_name").replace("", "legacy")
    obs_keys["symbol"] = obs_keys["symbol"].astype("string").str.strip()
    obs_keys["market"] = obs_keys["market"].astype("string").str.upper().str.strip()
    obs_keys = obs_keys.drop_duplicates()
    pending = ledger.merge(
        obs_keys.assign(_has_observation=True),
        on=["signal_date", "profile_name", "symbol", "market"],
        how="left",
    )
    return pending[~pending["_has_observation"].fillna(False).astype(bool)].drop(columns=["_has_observation"])


def load_unique_symbol_helpers(output_dir: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(output_dir.glob("manual_fill_observations_*_unique_symbols_helper.csv")):
        try:
            frame = pd.read_csv(path, dtype={"symbol": str})
        except pd.errors.EmptyDataError:
            continue
        if frame.empty:
            continue
        frame["_helper_path"] = str(path)
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    output = pd.concat(frames, ignore_index=True)
    output["signal_date"] = pd.to_datetime(output["signal_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    output["symbol"] = output["symbol"].astype("string").str.strip()
    output["market"] = output["market"].astype("string").str.upper().str.strip()
    for column in ["name", "sector", "industry"]:
        if column not in output.columns:
            output[column] = ""
        output[column] = text_column(output, column)
    for column in ["intended_entry_price", "limit_up_price", "max_order_quantity_shares"]:
        if column not in output.columns:
            output[column] = np.nan
        output[column] = pd.to_numeric(output[column], errors="coerce")
    return output.dropna(subset=["signal_date", "symbol", "market"]).reset_index(drop=True)


def load_manual_fill_templates(output_dir: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(output_dir.glob("manual_fill_observations_*_template.csv")):
        try:
            frame = pd.read_csv(path, dtype={"symbol": str})
        except pd.errors.EmptyDataError:
            continue
        if frame.empty:
            continue
        frame["_template_path"] = str(path)
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    output = pd.concat(frames, ignore_index=True)
    output["signal_date"] = pd.to_datetime(output["signal_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    output["profile_name"] = text_column(output, "profile_name").replace("", "legacy")
    output["symbol"] = output["symbol"].astype("string").str.strip()
    output["market"] = output["market"].astype("string").str.upper().str.strip()
    output["name"] = text_column(output, "name")
    for column in ["intended_entry_price", "limit_up_price", "order_quantity_shares"]:
        output[column] = pd.to_numeric(output[column], errors="coerce")
    return output.dropna(subset=["signal_date", "symbol", "market"]).reset_index(drop=True)


def build_action_checklist(
    *,
    pending: pd.DataFrame,
    helpers: pd.DataFrame,
    templates: pd.DataFrame,
    output_dir: Path,
) -> pd.DataFrame:
    if pending.empty:
        return pd.DataFrame(columns=ACTION_CHECKLIST_COLUMNS)

    helper_lookup = helper_lookup_by_symbol_date(helpers)
    template_lookup = template_lookup_by_symbol_date(templates)
    rows: list[dict[str, Any]] = []
    for (signal_date, symbol, market), group in pending.groupby(["signal_date", "symbol", "market"], sort=True):
        first = group.iloc[0]
        helper = helper_lookup.get((signal_date, symbol, market), {})
        template = template_lookup.get((signal_date, symbol, market), {})
        profiles = ", ".join(group["profile_name"].astype(str).drop_duplicates().tolist())
        template_paths = str(template.get("template_path", ""))
        instruction_paths = matching_instruction_paths(output_dir, str(signal_date))
        rows.append(
            {
                "signal_date": signal_date,
                "symbol": symbol,
                "name": first_nonblank(first.get("name"), helper.get("name"), template.get("name")),
                "market": market,
                "sector": first_nonblank(first.get("sector"), helper.get("sector")),
                "industry": first_nonblank(first.get("industry"), helper.get("industry")),
                "profiles_containing_symbol": profiles,
                "intended_entry_price": first_numeric(
                    first.get("planned_entry_price"),
                    helper.get("intended_entry_price"),
                    template.get("intended_entry_price"),
                ),
                "limit_up_price": first_numeric(helper.get("limit_up_price"), template.get("limit_up_price")),
                "max_planned_shares_across_profiles": int(
                    pd.to_numeric(group["planned_shares"], errors="coerce").fillna(0).max()
                ),
                "profile_specific_rows_needed": int(len(group)),
                "template_exists": bool(template_paths),
                "template_path": template_paths,
                "instruction_file_path": "; ".join(instruction_paths),
                "observe_best_bid_near_close": "required",
                "observe_best_ask_near_close": "required",
                "observe_visible_bid_size": "required",
                "observe_visible_ask_size": "required",
                "observe_whether_limit_up_locked": "required",
                "observe_whether_hypothetical_limit_order_would_fill": "required",
                "record_screenshot_path": "recommended",
                "record_notes": "recommended",
            }
        )
    return pd.DataFrame(rows, columns=ACTION_CHECKLIST_COLUMNS).sort_values(
        ["signal_date", "market", "symbol"]
    ).reset_index(drop=True)


def helper_lookup_by_symbol_date(helpers: pd.DataFrame) -> dict[tuple[str, str, str], dict[str, Any]]:
    lookup: dict[tuple[str, str, str], dict[str, Any]] = {}
    if helpers.empty:
        return lookup
    for row in helpers.to_dict("records"):
        key = (str(row.get("signal_date")), str(row.get("symbol")), str(row.get("market")))
        lookup[key] = row
    return lookup


def template_lookup_by_symbol_date(templates: pd.DataFrame) -> dict[tuple[str, str, str], dict[str, Any]]:
    lookup: dict[tuple[str, str, str], dict[str, Any]] = {}
    if templates.empty:
        return lookup
    for key, group in templates.groupby(["signal_date", "symbol", "market"], sort=False):
        first = group.iloc[0]
        paths = sorted(set(group["_template_path"].astype(str)))
        lookup[(str(key[0]), str(key[1]), str(key[2]))] = {
            "name": first.get("name", ""),
            "intended_entry_price": first_numeric(*group["intended_entry_price"].tolist()),
            "limit_up_price": first_numeric(*group["limit_up_price"].tolist()),
            "order_quantity_shares": pd.to_numeric(group["order_quantity_shares"], errors="coerce").max(),
            "template_path": "; ".join(paths),
        }
    return lookup


def matching_instruction_paths(output_dir: Path, signal_date: str) -> list[str]:
    return [str(path) for path in sorted(output_dir.glob(f"manual_fill_observations_{signal_date}*instructions.md"))]


def build_action_checklist_markdown(
    *,
    checklist: pd.DataFrame,
    pending: pd.DataFrame,
    observations: pd.DataFrame,
    csv_path: Path,
) -> str:
    confirmed_count = execution_confirmed_observation_count(observations)
    not_filled_count = fill_status_count(observations, "not_filled")
    unknown_count = fill_status_count(observations, "unknown")
    pending_by_date = count_table(pending, ["signal_date"], "pending_profile_observations")
    pending_by_profile = count_table(pending, ["profile_name"], "pending_profile_observations")
    pending_by_market = count_table(pending, ["market"], "pending_profile_observations")
    pending_by_sector = count_table(pending, ["sector"], "pending_profile_observations")
    lines = [
        "# Manual Fill Action Checklist",
        "",
        "## Summary",
        "",
        f"Checklist CSV: `{csv_path}`",
        f"Total pending profile-specific observations: {len(pending):,}",
        f"Total pending unique symbol/date observations: {len(checklist):,}",
        f"Execution-confirmed observations count: {confirmed_count:,}",
        f"Not-filled observations count: {not_filled_count:,}",
        f"Unknown observations count: {unknown_count:,}",
        "",
        "## Pending By Date",
        "",
        markdown_table(pending_by_date),
        "",
        "## Pending By Profile",
        "",
        markdown_table(pending_by_profile),
        "",
        "## Pending By Market",
        "",
        markdown_table(pending_by_market),
        "",
        "## Pending By Sector",
        "",
        markdown_table(pending_by_sector),
        "",
        "## Action Checklist",
        "",
        markdown_table(checklist),
        "",
        "## Observation Fields Needed",
        "",
        "- Best bid near close",
        "- Best ask near close",
        "- Visible bid size",
        "- Visible ask size",
        "- Whether limit-up was locked",
        "- Whether a hypothetical limit order would have filled",
        "- Screenshot path",
        "- Notes",
        "",
        "Until these observations are completed, paper PnL validates only the price edge, not live executability.",
        "",
    ]
    return "\n".join(lines)


def execution_confirmed_observation_count(observations: pd.DataFrame) -> int:
    if observations.empty:
        return 0
    fill_status = observations["fill_status"].astype("string").str.lower()
    filled_shares = pd.to_numeric(observations.get("actual_filled_shares", 0), errors="coerce").fillna(0)
    return int((fill_status.isin(["fully_filled", "partially_filled"]) & filled_shares.gt(0)).sum())


def fill_status_count(observations: pd.DataFrame, status: str) -> int:
    if observations.empty:
        return 0
    return int(observations["fill_status"].astype("string").str.lower().eq(status).sum())


def count_table(frame: pd.DataFrame, columns: list[str], count_name: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=columns + [count_name])
    return frame.groupby(columns, dropna=False).size().reset_index(name=count_name)


def first_nonblank(*values: Any) -> str:
    for value in values:
        if value is None or pd.isna(value):
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def first_numeric(*values: Any) -> float:
    for value in values:
        number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        if not pd.isna(number):
            return float(number)
    return np.nan


def empty_summary_row(*, section: str, group: str, notes: str = "") -> dict[str, Any]:
    return {
        "section": section,
        "group": group,
        "signal_date": "",
        "symbol": "",
        "market": "",
        "observations": 0,
        "fully_filled": 0,
        "partially_filled": 0,
        "not_filled": 0,
        "not_submitted": 0,
        "unknown": 0,
        "fill_rate": np.nan,
        "avg_displayed_bid_size_shares": np.nan,
        "avg_displayed_ask_size_shares": np.nan,
        "avg_fill_vs_intended_bps": np.nan,
        "paper_net_pnl": np.nan,
        "actual_fill_adjusted_net_pnl": np.nan,
        "missing_observation_count": 0,
        "notes": notes,
    }


def parse_optional_bool(value: Any) -> bool | None:
    if pd.isna(value) or value == "":
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    return None


def make_observation_id(row: pd.Series) -> str:
    signal_date = date_string(row.get("signal_date"))
    payload = "|".join(
        [
            signal_date,
            str(row.get("profile_name", "")),
            str(row.get("symbol", "")),
            str(row.get("market", "")),
            str(row.get("observed_time", "")),
            str(row.get("broker", "")),
            str(row.get("order_quantity_shares", "")),
            str(row.get("fill_status", "")),
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


def date_string(value: Any) -> str:
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return ""
    return ts.date().isoformat()


def markdown_table(frame: pd.DataFrame, *, max_rows: int = 60) -> str:
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
    parser = argparse.ArgumentParser(description="Manual fill observation log")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--db", type=Path, default=get_settings().db_path)

    import_parser = subparsers.add_parser("import-csv")
    import_parser.add_argument("--db", type=Path, default=get_settings().db_path)
    import_parser.add_argument("--csv", type=Path, required=True)

    template_parser = subparsers.add_parser("export-template")
    template_parser.add_argument("--output", type=Path, required=True)

    summarize_parser = subparsers.add_parser("summarize")
    summarize_parser.add_argument("--db", type=Path, default=get_settings().db_path)
    summarize_parser.add_argument("--output-dir", type=Path, default=get_settings().project_root / "reports" / "live_signals")

    checklist_parser = subparsers.add_parser("action-checklist")
    checklist_parser.add_argument("--db", type=Path, default=get_settings().db_path)
    checklist_parser.add_argument(
        "--ledger",
        type=Path,
        default=get_settings().project_root / "reports" / "live_signals" / "closed_limit_up_paper_ledger.csv",
    )
    checklist_parser.add_argument(
        "--output-dir",
        type=Path,
        default=get_settings().project_root / "reports" / "live_signals",
    )

    args = parser.parse_args()
    if args.command == "init":
        path = init_manual_fill_log(args.db)
        print(f"Initialized manual_fill_observations in {path}")
    elif args.command == "import-csv":
        rows = import_manual_fill_csv(db_path=args.db, csv_path=args.csv)
        print(f"Imported {rows} manual fill observation row(s)")
    elif args.command == "export-template":
        path = export_template(args.output)
        print(f"Wrote manual fill observation template to {path}")
    elif args.command == "summarize":
        summary, csv_path, md_path = summarize_manual_fill_log(db_path=args.db, output_dir=args.output_dir)
        print(f"Wrote {len(summary)} summary rows to {csv_path}")
        print(f"Wrote Markdown summary to {md_path}")
    elif args.command == "action-checklist":
        checklist, csv_path, md_path = generate_manual_fill_action_checklist(
            db_path=args.db,
            ledger_path=args.ledger,
            output_dir=args.output_dir,
        )
        print(f"Wrote {len(checklist)} pending unique symbol/date row(s) to {csv_path}")
        print(f"Wrote Markdown action checklist to {md_path}")


if __name__ == "__main__":
    main()
