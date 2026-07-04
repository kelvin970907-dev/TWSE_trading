"""Import margin, short-balance, and day-trading data from CSV files."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from config.settings import get_settings
from src.db import get_connection, init_db, upsert_dataframe


MARGIN_SHORT_COLUMNS = [
    "symbol",
    "trade_date",
    "market",
    "margin_buy_balance",
    "margin_sell_balance",
    "short_sale_balance",
    "day_trade_volume",
    "source",
]

REQUIRED_COLUMNS = [
    "symbol",
    "trade_date",
    "market",
    "margin_buy_balance",
    "short_sale_balance",
]


class MarginShortImportError(ValueError):
    """Raised when margin/short CSV input is invalid."""


def import_margin_short_csv(
    *,
    db_path: Path | str,
    csv_path: Path | str,
    source: str | None = None,
) -> int:
    frame = read_margin_short_csv(Path(csv_path), source=source)
    init_db(db_path)
    with get_connection(db_path) as conn:
        return upsert_dataframe(conn, "margin_short", frame[MARGIN_SHORT_COLUMNS], ["symbol", "trade_date"])


def read_margin_short_csv(path: Path, *, source: str | None = None) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Margin/short CSV not found: {path}")
    frame = pd.read_csv(path)
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    missing = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing:
        raise MarginShortImportError(f"CSV is missing required column(s): {missing}")

    output = frame.copy()
    output["symbol"] = output["symbol"].astype("string").str.strip()
    output["market"] = output["market"].astype("string").str.upper().str.strip()
    output["trade_date"] = pd.to_datetime(output["trade_date"], errors="coerce").dt.date
    if output["trade_date"].isna().any():
        raise MarginShortImportError("trade_date contains unparsable value(s)")
    if (~output["market"].isin(["TWSE", "TPEX"])).any():
        bad = output.loc[~output["market"].isin(["TWSE", "TPEX"]), "market"].head(5).tolist()
        raise MarginShortImportError(f"market must be TWSE or TPEX; examples: {bad}")

    for column in ["margin_buy_balance", "margin_sell_balance", "short_sale_balance", "day_trade_volume"]:
        if column not in output.columns:
            output[column] = 0 if column in {"margin_sell_balance", "day_trade_volume"} else pd.NA
        output[column] = parse_non_negative_int(output[column], column=column)

    output["source"] = source or (
        output["source"].astype("string").str.strip() if "source" in output.columns else f"csv:{path.name}"
    )
    validate_no_duplicate_keys(output)
    return output[MARGIN_SHORT_COLUMNS]


def parse_non_negative_int(values: pd.Series, *, column: str) -> pd.Series:
    cleaned = values.astype("string").str.strip().str.replace(",", "", regex=False)
    cleaned = cleaned.replace({"": pd.NA, "--": pd.NA, "nan": pd.NA})
    numeric = pd.to_numeric(cleaned, errors="coerce")
    if numeric.isna().any():
        examples = values[numeric.isna()].head(5).astype(str).tolist()
        raise MarginShortImportError(f"{column} contains missing or non-numeric value(s): {examples}")
    if (numeric < 0).any():
        examples = values[numeric < 0].head(5).astype(str).tolist()
        raise MarginShortImportError(f"{column} cannot be negative; examples: {examples}")
    return numeric.round().astype("Int64")


def validate_no_duplicate_keys(frame: pd.DataFrame) -> None:
    duplicate_mask = frame.duplicated(subset=["symbol", "trade_date"], keep=False)
    if duplicate_mask.any():
        examples = frame.loc[duplicate_mask, ["symbol", "trade_date"]].head(5).to_dict("records")
        raise MarginShortImportError(f"CSV contains duplicate symbol/trade_date rows; examples: {examples}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import normalized margin/short CSV data")
    parser.add_argument("--db", type=Path, default=get_settings().db_path, help="DuckDB path")
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--source")
    args = parser.parse_args()

    inserted = import_margin_short_csv(db_path=args.db, csv_path=args.csv, source=args.source)
    print(f"Upserted {inserted} margin/short row(s)")


if __name__ == "__main__":
    main()
