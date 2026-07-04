"""Import institutional investor flow data from normalized CSV files."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from config.settings import get_settings
from src.db import get_connection, init_db, upsert_dataframe


INSTITUTIONAL_FLOW_COLUMNS = [
    "symbol",
    "trade_date",
    "market",
    "foreign_net_buy_twd",
    "investment_trust_net_buy_twd",
    "dealer_net_buy_twd",
    "total_institutional_net_buy_twd",
    "source",
]

REQUIRED_COLUMNS = [
    "symbol",
    "trade_date",
    "market",
    "foreign_net_buy_twd",
    "investment_trust_net_buy_twd",
    "dealer_net_buy_twd",
]


class InstitutionalFlowImportError(ValueError):
    """Raised when institutional-flow CSV input is invalid."""


def import_institutional_flows_csv(
    *,
    db_path: Path | str,
    csv_path: Path | str,
    source: str | None = None,
) -> int:
    frame = read_institutional_flows_csv(Path(csv_path), source=source)
    init_db(db_path)
    with get_connection(db_path) as conn:
        return upsert_dataframe(
            conn,
            "institutional_flows",
            frame[INSTITUTIONAL_FLOW_COLUMNS],
            ["symbol", "trade_date"],
        )


def read_institutional_flows_csv(path: Path, *, source: str | None = None) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Institutional flow CSV not found: {path}")
    frame = pd.read_csv(path)
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    missing = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing:
        raise InstitutionalFlowImportError(f"CSV is missing required column(s): {missing}")

    output = frame.copy()
    output["symbol"] = output["symbol"].astype("string").str.strip()
    output["market"] = output["market"].astype("string").str.upper().str.strip()
    output["trade_date"] = pd.to_datetime(output["trade_date"], errors="coerce").dt.date
    if output["trade_date"].isna().any():
        raise InstitutionalFlowImportError("trade_date contains unparsable value(s)")
    if (~output["market"].isin(["TWSE", "TPEX"])).any():
        bad = output.loc[~output["market"].isin(["TWSE", "TPEX"]), "market"].head(5).tolist()
        raise InstitutionalFlowImportError(f"market must be TWSE or TPEX; examples: {bad}")

    for column in [
        "foreign_net_buy_twd",
        "investment_trust_net_buy_twd",
        "dealer_net_buy_twd",
        "total_institutional_net_buy_twd",
    ]:
        if column not in output.columns:
            output[column] = pd.NA
        output[column] = parse_numeric(output[column], column=column, allow_missing=column == "total_institutional_net_buy_twd")

    missing_total = output["total_institutional_net_buy_twd"].isna()
    output.loc[missing_total, "total_institutional_net_buy_twd"] = output.loc[
        missing_total,
        ["foreign_net_buy_twd", "investment_trust_net_buy_twd", "dealer_net_buy_twd"],
    ].sum(axis=1)
    output["source"] = source or (
        output["source"].astype("string").str.strip() if "source" in output.columns else f"csv:{path.name}"
    )
    validate_no_duplicate_keys(output)
    return output[INSTITUTIONAL_FLOW_COLUMNS]


def parse_numeric(values: pd.Series, *, column: str, allow_missing: bool = False) -> pd.Series:
    cleaned = values.astype("string").str.strip().str.replace(",", "", regex=False)
    cleaned = cleaned.replace({"": pd.NA, "--": pd.NA, "nan": pd.NA})
    numeric = pd.to_numeric(cleaned, errors="coerce")
    if not allow_missing and numeric.isna().any():
        examples = values[numeric.isna()].head(5).astype(str).tolist()
        raise InstitutionalFlowImportError(f"{column} contains missing or non-numeric value(s): {examples}")
    return numeric.astype("float64")


def validate_no_duplicate_keys(frame: pd.DataFrame) -> None:
    duplicate_mask = frame.duplicated(subset=["symbol", "trade_date"], keep=False)
    if duplicate_mask.any():
        examples = frame.loc[duplicate_mask, ["symbol", "trade_date"]].head(5).to_dict("records")
        raise InstitutionalFlowImportError(f"CSV contains duplicate symbol/trade_date rows; examples: {examples}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import normalized institutional flow CSV data")
    parser.add_argument("--db", type=Path, default=get_settings().db_path, help="DuckDB path")
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--source")
    args = parser.parse_args()

    inserted = import_institutional_flows_csv(db_path=args.db, csv_path=args.csv, source=args.source)
    print(f"Upserted {inserted} institutional flow row(s)")


if __name__ == "__main__":
    main()
