"""Import market regime and sector context data."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from io import StringIO
from typing import Any

import numpy as np
import pandas as pd
import requests

from config.settings import get_settings
from src.db import get_connection, init_db, upsert_dataframe


TWSE_TAIEX_URL = "https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST"
TWSE_COMPANY_INFO_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TWSE_COMPANY_INFO_URL_CANDIDATES = [
    TWSE_COMPANY_INFO_URL,
    "https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv",
]
TPEX_COMPANY_INFO_URL_CANDIDATES = [
    "https://mopsfin.twse.com.tw/opendata/t187ap03_O.csv",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
    "https://www.tpex.org.tw/openapi/v1/t187ap03_O",
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_O",
]

INDEX_COLUMNS = [
    "index_symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover_twd",
    "daily_return",
    "ma5",
    "ma20",
    "ma60",
    "close_above_ma20",
    "close_above_ma60",
    "drawdown_from_60d_high",
    "source",
]

SECTOR_MAP_COLUMNS = ["symbol", "market", "name", "sector", "industry", "source"]

TAIWAN_INDUSTRY_CODE_NAMES = {
    "01": "Cement",
    "02": "Food",
    "03": "Plastics",
    "04": "Textiles",
    "05": "Electric Machinery",
    "06": "Electrical Cable",
    "07": "Chemicals/Biotech/Medical",
    "08": "Glass/Ceramics",
    "09": "Paper",
    "10": "Steel",
    "11": "Rubber",
    "12": "Automobile",
    "14": "Building Materials/Construction",
    "15": "Shipping/Transportation",
    "16": "Tourism/Hospitality",
    "17": "Financial/Insurance",
    "18": "Trading/Department Stores",
    "20": "Other",
    "21": "Chemical",
    "22": "Biotechnology/Medical",
    "23": "Oil/Gas/Electricity",
    "24": "Semiconductor",
    "25": "Computer/Peripheral",
    "26": "Optoelectronics",
    "27": "Communications/Internet",
    "28": "Electronic Components",
    "29": "Electronic Distribution",
    "30": "Information Services",
    "31": "Other Electronics",
    "32": "Cultural/Creative",
    "33": "Agricultural Technology",
    "34": "E-commerce",
    "35": "Green Energy/Environmental",
    "36": "Digital/Cloud",
    "37": "Sports/Leisure",
    "38": "Home Living",
}

SECTOR_FEATURE_COLUMNS = [
    "sector",
    "trade_date",
    "equal_weight_return",
    "value_weight_return",
    "num_advancers",
    "num_decliners",
    "num_limit_up",
    "sector_momentum_5d",
    "sector_momentum_20d",
]


class MarketContextError(ValueError):
    """Raised when market-context input cannot be normalized."""


def import_index_daily_csv(
    *,
    db_path: Path | str,
    csv_path: Path | str,
    default_index_symbol: str = "TAIEX",
    source: str = "csv",
) -> int:
    """Import index daily prices and compute returns/moving averages."""

    frame = read_csv_lower(Path(csv_path))
    normalized = normalize_index_daily_frame(frame, default_index_symbol=default_index_symbol, source=source)
    init_db(db_path)
    with get_connection(db_path) as conn:
        return upsert_dataframe(conn, "index_daily_prices", normalized[INDEX_COLUMNS], ["index_symbol", "trade_date"])


def collect_taiex_public(
    *,
    db_path: Path | str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    cache_dir: Path | str | None = None,
    pause_seconds: float = 0.4,
    timeout_seconds: float = 20.0,
    force_refresh: bool = False,
) -> int:
    """Collect TAIEX daily OHLC from TWSE's public monthly index endpoint.

    TWSE's `MI_5MINS_HIST` endpoint is a public monthly source for TAIEX
    open/high/low/close history. It generally does not provide turnover. Raw
    JSON responses are cached because public endpoints can be slow or rate
    limited.
    """

    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    if end_ts < start_ts:
        raise MarketContextError("end must be on or after start")
    cache_path = Path(cache_dir) if cache_dir is not None else get_settings().project_root / "data" / "raw" / "twse_taiex_index"
    cache_path.mkdir(parents=True, exist_ok=True)

    monthly_frames: list[pd.DataFrame] = []
    for month_start in pd.date_range(start_ts.replace(day=1), end_ts, freq="MS"):
        payload = fetch_twse_taiex_month(
            month_start,
            cache_dir=cache_path,
            timeout_seconds=timeout_seconds,
            force_refresh=force_refresh,
        )
        parsed = parse_twse_taiex_payload(payload)
        if not parsed.empty:
            monthly_frames.append(parsed)
        time.sleep(max(0.0, pause_seconds))

    if not monthly_frames:
        raise MarketContextError("TWSE TAIEX public endpoint returned no parseable rows")
    raw = pd.concat(monthly_frames, ignore_index=True)
    raw = raw[(pd.to_datetime(raw["trade_date"]) >= start_ts) & (pd.to_datetime(raw["trade_date"]) <= end_ts)]
    normalized = normalize_index_daily_frame(raw, default_index_symbol="TAIEX", source="twse_public_mi_5mins_hist")
    init_db(db_path)
    with get_connection(db_path) as conn:
        return upsert_dataframe(conn, "index_daily_prices", normalized[INDEX_COLUMNS], ["index_symbol", "trade_date"])


def fetch_twse_taiex_month(
    month_start: pd.Timestamp,
    *,
    cache_dir: Path,
    timeout_seconds: float,
    max_retries: int = 3,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Fetch or load one TWSE TAIEX monthly JSON payload."""

    month_key = pd.Timestamp(month_start).strftime("%Y%m")
    cache_file = cache_dir / f"MI_5MINS_HIST_{month_key}.json"
    if cache_file.exists() and cache_file.stat().st_size > 0 and not force_refresh:
        return json.loads(cache_file.read_text(encoding="utf-8"))

    params = {"date": f"{month_key}01", "response": "json"}
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = requests.get(TWSE_TAIEX_URL, params=params, timeout=timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return payload
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            time.sleep(1.0 + attempt)
    raise MarketContextError(f"Failed to fetch TWSE TAIEX data for {month_key}: {last_error}") from last_error


def parse_twse_taiex_payload(payload: dict[str, Any]) -> pd.DataFrame:
    """Parse TWSE `MI_5MINS_HIST` payload into normalized raw columns."""

    rows = payload.get("data") or payload.get("aaData") or []
    fields = payload.get("fields") or []
    if not rows:
        return pd.DataFrame(columns=["trade_date", "open", "high", "low", "close"])

    normalized_fields = [str(field).strip() for field in fields]
    field_map = {field: i for i, field in enumerate(normalized_fields)}
    aliases = {
        "trade_date": ["日期", "Date"],
        "open": ["開盤指數", "Open"],
        "high": ["最高指數", "High"],
        "low": ["最低指數", "Low"],
        "close": ["收盤指數", "Close"],
    }

    def value_from(row: list[Any], key: str) -> Any:
        for alias in aliases[key]:
            if alias in field_map and field_map[alias] < len(row):
                return row[field_map[alias]]
        # Known TWSE fallback order: 日期, 開盤, 最高, 最低, 收盤.
        fallback_index = {"trade_date": 0, "open": 1, "high": 2, "low": 3, "close": 4}[key]
        return row[fallback_index] if fallback_index < len(row) else None

    output_rows = []
    for raw_row in rows:
        row = list(raw_row)
        output_rows.append(
            {
                "trade_date": parse_twse_index_date(value_from(row, "trade_date")),
                "open": value_from(row, "open"),
                "high": value_from(row, "high"),
                "low": value_from(row, "low"),
                "close": value_from(row, "close"),
                "volume": np.nan,
                "turnover_twd": np.nan,
            }
        )
    output = pd.DataFrame(output_rows)
    output = output.dropna(subset=["trade_date"])
    return output


def import_sector_map_csv(*, db_path: Path | str, csv_path: Path | str, source: str | None = None) -> int:
    """Import stock-to-sector mapping."""

    frame = read_csv_lower(Path(csv_path))
    normalized = normalize_sector_map_frame(frame, source=source)
    init_db(db_path)
    with get_connection(db_path) as conn:
        return upsert_dataframe(conn, "stock_sector_map", normalized[SECTOR_MAP_COLUMNS], ["symbol", "market"])


def collect_sector_map_public(
    *,
    db_path: Path | str,
    market: str = "BOTH",
    cache_dir: Path | str | None = None,
    timeout_seconds: float = 20.0,
) -> int:
    """Collect stock sector metadata from official public company-info endpoints.

    TWSE listed-company metadata is available from TWSE OpenAPI. TPEx company
    metadata endpoints have moved over time, so this collector tries a small
    list of official candidate endpoints and raises a clear error if none
    returns parseable JSON.
    """

    requested = normalize_market_request(market)
    cache_path = Path(cache_dir) if cache_dir is not None else get_settings().project_root / "data" / "raw" / "sector_map"
    cache_path.mkdir(parents=True, exist_ok=True)

    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    if "TWSE" in requested:
        try:
            frames.append(
                fetch_and_parse_company_info(
                    market="TWSE",
                    urls=TWSE_COMPANY_INFO_URL_CANDIDATES,
                    cache_dir=cache_path,
                    timeout_seconds=timeout_seconds,
                )
            )
        except MarketContextError as exc:
            errors.append(str(exc))
    if "TPEX" in requested:
        try:
            frames.append(
                fetch_and_parse_company_info(
                    market="TPEX",
                    urls=TPEX_COMPANY_INFO_URL_CANDIDATES,
                    cache_dir=cache_path,
                    timeout_seconds=timeout_seconds,
                )
            )
        except MarketContextError as exc:
            errors.append(str(exc))

    if errors:
        raise MarketContextError("; ".join(errors))
    if not frames:
        raise MarketContextError(f"No sector-map markets requested from {market!r}")
    normalized = pd.concat(frames, ignore_index=True)
    init_db(db_path)
    with get_connection(db_path) as conn:
        return upsert_dataframe(conn, "stock_sector_map", normalized[SECTOR_MAP_COLUMNS], ["symbol", "market"])


def normalize_market_request(market: str) -> list[str]:
    value = str(market).upper().strip()
    if value == "BOTH":
        return ["TWSE", "TPEX"]
    if value in {"TWSE", "TPEX"}:
        return [value]
    raise MarketContextError("market must be TWSE, TPEX, or BOTH")


def fetch_and_parse_company_info(
    *,
    market: str,
    urls: list[str],
    cache_dir: Path,
    timeout_seconds: float,
) -> pd.DataFrame:
    """Fetch company-info JSON from the first working official endpoint."""

    errors: list[str] = []
    for index, url in enumerate(urls, start=1):
        cache_json = cache_dir / f"{market.lower()}_company_info_{index}.json"
        cache_csv = cache_dir / f"{market.lower()}_company_info_{index}.csv"
        try:
            payload: list[dict[str, Any]] | None = None
            if cache_json.exists() and cache_json.stat().st_size > 0:
                payload = json.loads(cache_json.read_text(encoding="utf-8"))
            elif cache_csv.exists() and cache_csv.stat().st_size > 0:
                payload = parse_company_info_response(cache_csv.read_bytes())
                cached_normalized = normalize_public_company_info(payload, market=market, source=url)
                if not cached_normalized.empty:
                    return cached_normalized
                errors.append(f"{market} cached endpoint {url} returned no company rows; refetching")
                payload = None
            if payload is None:
                response = requests.get(url, timeout=timeout_seconds)
                response.raise_for_status()
                payload = parse_company_info_response(response.content)
                if isinstance(payload, list):
                    if url.lower().endswith(".csv"):
                        cache_csv.write_bytes(response.content)
                    else:
                        cache_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            normalized = normalize_public_company_info(payload, market=market, source=url)
            if not normalized.empty:
                return normalized
            errors.append(f"{market} endpoint {url} returned no company rows")
        except (requests.RequestException, ValueError, MarketContextError) as exc:
            errors.append(f"{market} endpoint {url} failed: {exc}")
    raise MarketContextError(f"Could not collect {market} sector map from public endpoints. " + " | ".join(errors))


def parse_company_info_response(content: bytes | str) -> list[dict[str, Any]]:
    """Parse official company-info response text as JSON first, then CSV."""

    if isinstance(content, bytes):
        json_text = content.decode("utf-8-sig", errors="replace")
    else:
        json_text = content
    try:
        payload = json.loads(json_text)
        if isinstance(payload, list):
            return payload
    except ValueError:
        pass
    texts = [json_text]
    if isinstance(content, bytes):
        for encoding in ["utf-8-sig", "utf-8", "cp950", "big5"]:
            try:
                decoded = content.decode(encoding)
            except UnicodeDecodeError:
                continue
            if decoded not in texts:
                texts.append(decoded)
    for text in texts:
        try:
            frame = pd.read_csv(StringIO(text))
        except Exception:
            continue
        if not frame.empty:
            return frame.to_dict("records")
    return []


def normalize_public_company_info(payload: Any, *, market: str, source: str) -> pd.DataFrame:
    """Normalize official company metadata into stock_sector_map rows."""

    if not isinstance(payload, list):
        raise MarketContextError(f"{market} company-info payload is not a JSON list")
    rows: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        symbol = first_nonblank(
            item,
            ["公司代號", "有價證券代號", "股票代號", "SecuritiesCompanyCode", "code", "symbol"],
        )
        if not symbol:
            continue
        name = first_nonblank(
            item,
            ["公司簡稱", "公司名稱", "有價證券名稱", "CompanyAbbreviation", "CompanyName", "name", "short_name"],
        )
        industry_value = first_nonblank(
            item,
            ["產業別", "產業類別", "SecuritiesIndustryCode", "industry", "industry_code"],
        )
        industry_code = normalize_industry_code(industry_value)
        industry = industry_name_from_code(industry_code) if industry_code else clean_text(industry_value)
        rows.append(
            {
                "symbol": clean_text(symbol),
                "market": market,
                "name": clean_text(name),
                "sector": sector_from_industry_code(industry_code),
                "industry": industry,
                "source": source,
            }
        )
    if not rows:
        return pd.DataFrame(columns=SECTOR_MAP_COLUMNS)
    normalized = normalize_sector_map_frame(pd.DataFrame(rows), source=source)
    return normalized[SECTOR_MAP_COLUMNS]


def build_and_store_sector_daily_features(
    *,
    db_path: Path | str,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
) -> int:
    """Build sector daily features from daily prices plus stock sector map."""

    init_db(db_path)
    with get_connection(db_path) as conn:
        daily = load_daily_prices_with_sector(conn, start=start, end=end)
        features = build_sector_daily_features(daily)
        if features.empty:
            return 0
        return upsert_dataframe(
            conn,
            "sector_daily_features",
            features[SECTOR_FEATURE_COLUMNS],
            ["sector", "trade_date"],
        )


def normalize_index_daily_frame(
    frame: pd.DataFrame,
    *,
    default_index_symbol: str = "TAIEX",
    source: str = "csv",
) -> pd.DataFrame:
    required = {"trade_date", "open", "high", "low", "close"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise MarketContextError(f"index CSV is missing required column(s): {missing}")

    output = frame.copy()
    if "index_symbol" not in output.columns:
        output["index_symbol"] = default_index_symbol
    output["index_symbol"] = output["index_symbol"].astype("string").str.strip().str.upper()
    output["trade_date"] = pd.to_datetime(output["trade_date"], errors="coerce").dt.date
    if output["trade_date"].isna().any():
        raise MarketContextError("index CSV contains unparsable trade_date value(s)")

    for column in ["open", "high", "low", "close"]:
        if column in output.columns:
            output[column] = parse_numeric(output[column], column=column)
        else:
            output[column] = np.nan
    if "turnover_twd" in output.columns:
        output["turnover_twd"] = parse_optional_numeric(output["turnover_twd"])
    else:
        output["turnover_twd"] = np.nan
    if "volume" in output.columns:
        output["volume"] = parse_optional_numeric(output["volume"])
    else:
        output["volume"] = np.nan
    if "source" not in output.columns:
        output["source"] = source
    output["source"] = output["source"].fillna(source).astype("string").str.strip()

    output = output.sort_values(["index_symbol", "trade_date"]).reset_index(drop=True)
    output["daily_return"] = output.groupby("index_symbol")["close"].pct_change()
    for window in [5, 20, 60]:
        output[f"ma{window}"] = output.groupby("index_symbol")["close"].transform(
            lambda values: values.rolling(window=window, min_periods=1).mean()
        )
    output["close_above_ma20"] = output["close"] > output["ma20"]
    output["close_above_ma60"] = output["close"] > output["ma60"]
    rolling_60d_high = output.groupby("index_symbol")["close"].transform(
        lambda values: values.rolling(window=60, min_periods=1).max()
    )
    output["drawdown_from_60d_high"] = np.where(rolling_60d_high > 0, output["close"] / rolling_60d_high - 1.0, np.nan)
    validate_no_duplicates(output, ["index_symbol", "trade_date"], label="index_daily_prices")
    return output[INDEX_COLUMNS]


def normalize_sector_map_frame(frame: pd.DataFrame, *, source: str | None = None) -> pd.DataFrame:
    required = {"symbol", "market"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise MarketContextError(f"sector map CSV is missing required column(s): {missing}")

    output = frame.copy()
    output["symbol"] = output["symbol"].astype("string").str.strip()
    output["market"] = output["market"].astype("string").str.upper().str.strip()
    output["name"] = output["name"].astype("string").str.strip() if "name" in output else ""
    if "sector" not in output.columns:
        output["sector"] = ""
    output["sector"] = output["sector"].astype("string").str.strip()
    if "industry" not in output.columns and "industry_code" in output.columns:
        output["industry"] = output["industry_code"].map(
            lambda value: industry_name_from_code(normalize_industry_code(value))
        )
    output["industry"] = output["industry"].astype("string").str.strip() if "industry" in output else ""
    if (output["sector"].str.len().fillna(0) == 0).any():
        industry_codes = output["industry_code"] if "industry_code" in output.columns else output["industry"]
        output["sector"] = [
            sector if str(sector).strip() else sector_from_industry_code(normalize_industry_code(code))
            for sector, code in zip(output["sector"], industry_codes, strict=False)
        ]
    output["source"] = source or (
        output["source"].astype("string").str.strip() if "source" in output else "csv"
    )
    invalid_market = ~output["market"].isin(["TWSE", "TPEX"])
    if invalid_market.any():
        bad = output.loc[invalid_market, "market"].head(5).tolist()
        raise MarketContextError(f"sector map market must be TWSE or TPEX; examples: {bad}")
    if (output["symbol"].str.len().fillna(0) == 0).any() or (output["sector"].astype("string").str.len().fillna(0) == 0).any():
        raise MarketContextError("sector map contains blank symbol or sector")
    output["sector"] = output["sector"].astype("string").str.strip()
    output["industry"] = output["industry"].astype("string").str.strip()
    validate_no_duplicates(output, ["symbol", "market"], label="stock_sector_map")
    return output[SECTOR_MAP_COLUMNS]


def first_nonblank(row: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in row and str(row[key]).strip():
            return row[key]
    return ""


def clean_text(value: Any) -> str:
    text = str(value).strip()
    return "" if text in {"", "nan", "None", "－", "-"} else text


def normalize_industry_code(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    digits = "".join(character for character in text if character.isdigit())
    if not digits:
        return ""
    return digits.zfill(2)[-2:]


def industry_name_from_code(industry_code: str) -> str:
    code = normalize_industry_code(industry_code)
    if not code:
        return ""
    return TAIWAN_INDUSTRY_CODE_NAMES.get(code, f"Industry code {code}")


def sector_from_industry_code(industry_code: str) -> str:
    code = normalize_industry_code(industry_code)
    if code in {"24", "25", "26", "27", "28", "29", "30", "31", "34", "36"}:
        return "Technology/Electronics"
    if code == "17":
        return "Financials"
    if code in {"02", "04", "12", "16", "18", "32", "37", "38"}:
        return "Consumer/Services"
    if code in {"01", "03", "08", "09", "10", "11", "21"}:
        return "Materials"
    if code in {"05", "06", "14", "15", "20", "33", "35"}:
        return "Industrials/Other"
    if code in {"07", "22"}:
        return "Healthcare"
    if code == "23":
        return "Energy/Utilities"
    return "Unknown"


def build_sector_daily_features(daily_with_sector: pd.DataFrame) -> pd.DataFrame:
    """Compute daily sector breadth and momentum features."""

    if daily_with_sector.empty:
        return pd.DataFrame(columns=SECTOR_FEATURE_COLUMNS)

    frame = daily_with_sector.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
    frame["daily_return"] = pd.to_numeric(frame["daily_return"], errors="coerce")
    frame["turnover_twd"] = pd.to_numeric(frame.get("turnover_twd"), errors="coerce")
    frame["closed_limit_up"] = frame.get("closed_limit_up", False)
    frame["closed_limit_up"] = frame["closed_limit_up"].fillna(False).astype(bool)
    frame = frame.dropna(subset=["sector", "trade_date", "daily_return"])
    if frame.empty:
        return pd.DataFrame(columns=SECTOR_FEATURE_COLUMNS)

    rows = pd.DataFrame(
        [
            {"sector": sector, "trade_date": trade_date, **_sector_daily_metrics(group)}
            for (sector, trade_date), group in frame.groupby(["sector", "trade_date"], dropna=False)
        ]
    )
    rows = rows.sort_values(["sector", "trade_date"]).reset_index(drop=True)
    for window in [5, 20]:
        rows[f"sector_momentum_{window}d"] = rows.groupby("sector")["equal_weight_return"].transform(
            lambda values: (1.0 + values).rolling(window=window, min_periods=1).apply(np.prod, raw=True) - 1.0
        )
    return rows[SECTOR_FEATURE_COLUMNS]


def _sector_daily_metrics(group: pd.DataFrame) -> dict[str, float | int]:
    returns = pd.to_numeric(group["daily_return"], errors="coerce")
    value_weight_return = np.nan
    if "market_cap_twd" in group.columns:
        market_cap = pd.to_numeric(group["market_cap_twd"], errors="coerce")
        if market_cap.notna().any() and market_cap.fillna(0.0).sum() > 0:
            value_weight_return = float(np.average(returns.fillna(0.0), weights=market_cap.fillna(0.0)))
    return {
        "equal_weight_return": float(returns.mean()),
        "value_weight_return": value_weight_return,
        "num_advancers": int((returns > 0).sum()),
        "num_decliners": int((returns < 0).sum()),
        "num_limit_up": int(group["closed_limit_up"].sum()),
    }


def load_daily_prices_with_sector(conn, *, start: str | pd.Timestamp | None, end: str | pd.Timestamp | None) -> pd.DataFrame:
    filters = ["m.sector IS NOT NULL"]
    params: list[Any] = []
    if start is not None:
        filters.append("d.trade_date >= ?")
        params.append(pd.Timestamp(start).date())
    if end is not None:
        filters.append("d.trade_date <= ?")
        params.append(pd.Timestamp(end).date())
    return conn.execute(
        f"""
        SELECT
            d.symbol,
            d.market,
            d.trade_date,
            d.daily_return,
            d.turnover_twd,
            d.closed_limit_up,
            m.sector
        FROM daily_prices AS d
        JOIN stock_sector_map AS m
          ON d.symbol = m.symbol
         AND d.market = m.market
        WHERE {" AND ".join(filters)}
        ORDER BY m.sector, d.trade_date, d.symbol
        """,
        params,
    ).fetch_df()


def read_csv_lower(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    frame = pd.read_csv(path)
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    return frame


def parse_numeric(values: pd.Series, *, column: str) -> pd.Series:
    cleaned = values.astype("string").str.strip().str.replace(",", "", regex=False)
    cleaned = cleaned.replace({"": pd.NA, "--": pd.NA, "nan": pd.NA})
    numeric = pd.to_numeric(cleaned, errors="coerce")
    if numeric.isna().any():
        examples = values[numeric.isna()].head(5).astype(str).tolist()
        raise MarketContextError(f"{column} contains missing or non-numeric value(s), examples: {examples}")
    return numeric.astype("float64")


def parse_optional_numeric(values: pd.Series) -> pd.Series:
    cleaned = values.astype("string").str.strip().str.replace(",", "", regex=False)
    cleaned = cleaned.replace({"": pd.NA, "--": pd.NA, "nan": pd.NA, "None": pd.NA})
    return pd.to_numeric(cleaned, errors="coerce").astype("float64")


def parse_twse_index_date(value: Any) -> pd.Timestamp | pd.NaT:
    text = str(value).strip()
    if not text:
        return pd.NaT
    parts = text.replace("-", "/").split("/")
    if len(parts) == 3 and parts[0].isdigit() and len(parts[0]) <= 3:
        year = int(parts[0]) + 1911
        return pd.Timestamp(year=year, month=int(parts[1]), day=int(parts[2]))
    return pd.to_datetime(text, errors="coerce")


def validate_no_duplicates(frame: pd.DataFrame, key_columns: list[str], *, label: str) -> None:
    duplicate_mask = frame.duplicated(subset=key_columns, keep=False)
    if duplicate_mask.any():
        examples = frame.loc[duplicate_mask, key_columns].head(5).to_dict("records")
        raise MarketContextError(f"{label} contains duplicate key rows; examples: {examples}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import market regime and sector context")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("import-index-csv")
    index_parser.add_argument("--db", type=Path, default=get_settings().db_path)
    index_parser.add_argument("--csv", type=Path, required=True)
    index_parser.add_argument("--index-symbol", default="TAIEX")
    index_parser.add_argument("--source", default="csv")

    taiex_parser = subparsers.add_parser("collect-taiex-public")
    taiex_parser.add_argument("--db", type=Path, default=get_settings().db_path)
    taiex_parser.add_argument("--start", required=True)
    taiex_parser.add_argument("--end", required=True)
    taiex_parser.add_argument("--cache-dir", type=Path)
    taiex_parser.add_argument("--pause-seconds", type=float, default=0.4)
    taiex_parser.add_argument("--force-refresh", action="store_true")

    sector_public_parser = subparsers.add_parser("collect-sector-map-public")
    sector_public_parser.add_argument("--db", type=Path, default=get_settings().db_path)
    sector_public_parser.add_argument("--market", choices=["TWSE", "TPEX", "BOTH"], default="BOTH")
    sector_public_parser.add_argument("--cache-dir", type=Path)

    sector_parser = subparsers.add_parser("import-sector-map-csv")
    sector_parser.add_argument("--db", type=Path, default=get_settings().db_path)
    sector_parser.add_argument("--csv", type=Path, required=True)
    sector_parser.add_argument("--source")

    sector_alias_parser = subparsers.add_parser("import-sector-map")
    sector_alias_parser.add_argument("--db", type=Path, default=get_settings().db_path)
    sector_alias_parser.add_argument("--csv", type=Path, required=True)
    sector_alias_parser.add_argument("--source")

    features_parser = subparsers.add_parser("build-sector-features")
    features_parser.add_argument("--db", type=Path, default=get_settings().db_path)
    features_parser.add_argument("--start")
    features_parser.add_argument("--end")

    args = parser.parse_args()
    if args.command == "import-index-csv":
        rows = import_index_daily_csv(
            db_path=args.db,
            csv_path=args.csv,
            default_index_symbol=args.index_symbol,
            source=args.source,
        )
        print(f"Imported {rows} index daily row(s) from {args.csv}")
    elif args.command == "collect-taiex-public":
        rows = collect_taiex_public(
            db_path=args.db,
            start=args.start,
            end=args.end,
            cache_dir=args.cache_dir,
            pause_seconds=args.pause_seconds,
            force_refresh=args.force_refresh,
        )
        print(f"Collected {rows} TAIEX index daily row(s) from TWSE public endpoint")
    elif args.command == "collect-sector-map-public":
        rows = collect_sector_map_public(
            db_path=args.db,
            market=args.market,
            cache_dir=args.cache_dir,
        )
        print(f"Collected {rows} stock sector map row(s) from public company-info endpoint(s)")
    elif args.command in {"import-sector-map-csv", "import-sector-map"}:
        rows = import_sector_map_csv(db_path=args.db, csv_path=args.csv, source=args.source)
        print(f"Imported {rows} stock sector map row(s) from {args.csv}")
    elif args.command == "build-sector-features":
        rows = build_and_store_sector_daily_features(db_path=args.db, start=args.start, end=args.end)
        print(f"Built {rows} sector daily feature row(s)")


if __name__ == "__main__":
    main()
