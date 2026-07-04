"""TPEx public daily-price client and parsers."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from config.settings import get_settings
from src.twse_client import DAILY_PRICE_COLUMNS, normalize_int, normalize_number, parse_twse_date


TPEX_DAILY_SOURCE = "TPEX_DAILY_QUOTES"
TPEX_DAILY_ENDPOINT = "/www/zh-tw/afterTrading/dailyQuotes"
COMMON_STOCK_SYMBOL_RE = re.compile(r"^\d{4}$")


class TPEXClientError(RuntimeError):
    """Raised when TPEx data cannot be fetched or parsed."""


def format_tpex_query_date(value: date | str | pd.Timestamp) -> str:
    """Format a Gregorian date as the ROC date string required by TPEx."""

    ts = parse_twse_date(value)
    roc_year = ts.year - 1911
    return f"{roc_year:03d}/{ts.month:02d}/{ts.day:02d}"


def format_tpex_cache_date(value: date | str | pd.Timestamp) -> str:
    """Format a date for TPEx cache filenames."""

    return parse_twse_date(value).strftime("%Y%m%d")


def empty_daily_price_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=DAILY_PRICE_COLUMNS)


def parse_daily_quotes_payload(
    payload: dict[str, Any],
    trade_date: date | str | pd.Timestamp | None = None,
    market: str = "TPEX",
    common_stock_only: bool = True,
) -> pd.DataFrame:
    """Parse a TPEx dailyQuotes payload into `daily_prices` rows."""

    stat = str(payload.get("stat", "")).strip()
    if stat and stat.upper() != "OK":
        return empty_daily_price_frame()

    parsed_trade_date = _payload_trade_date(payload, trade_date)
    table = _find_daily_quote_table(payload)
    if table is None:
        return empty_daily_price_frame()

    fields = table.get("fields") or []
    rows = table.get("data") or []
    field_index = {str(field).strip(): index for index, field in enumerate(fields)}

    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        if not row:
            continue
        symbol = str(_cell(row, field_index, "代號") or "").strip()
        if common_stock_only and not is_common_stock_symbol(symbol):
            continue
        normalized_rows.append(
            {
                "symbol": symbol,
                "trade_date": parsed_trade_date,
                "market": market,
                "name": _cell(row, field_index, "名稱"),
                "open": normalize_number(_cell(row, field_index, "開盤")),
                "high": normalize_number(_cell(row, field_index, "最高")),
                "low": normalize_number(_cell(row, field_index, "最低")),
                "close": normalize_number(_cell(row, field_index, "收盤")),
                "volume_shares": normalize_int(_cell(row, field_index, "成交股數")),
                "turnover_twd": normalize_number(_cell(row, field_index, "成交金額(元)")),
                "trades": normalize_int(_cell(row, field_index, "成交筆數")),
                "source": TPEX_DAILY_SOURCE,
            }
        )

    if not normalized_rows:
        return empty_daily_price_frame()

    frame = pd.DataFrame(normalized_rows, columns=DAILY_PRICE_COLUMNS)
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["name"] = frame["name"].astype("string").str.strip()
    frame = frame[frame["symbol"].notna() & (frame["symbol"] != "")]
    return frame.reset_index(drop=True)


def is_common_stock_symbol(symbol: Any) -> bool:
    """Return True for standard four-digit Taiwan common stock symbols."""

    return bool(COMMON_STOCK_SYMBOL_RE.fullmatch(str(symbol).strip()))


@dataclass
class TPEXClient:
    """Client for Taipei Exchange public daily quote endpoint."""

    base_url: str = "https://www.tpex.org.tw"
    session: requests.Session | None = None
    max_retries: int = 3
    backoff_seconds: float = 1.5
    polite_sleep_seconds: float = 0.7
    timeout_seconds: float | None = None
    recent_empty_cache_refresh_days: int = 7
    _last_request_time: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        settings = get_settings()
        self.timeout_seconds = self.timeout_seconds or settings.request_timeout_seconds
        self.session = self.session or requests.Session()
        self.session.headers.update({"User-Agent": settings.user_agent})

    def fetch_daily_prices(
        self,
        trade_date: date | str | pd.Timestamp,
        *,
        cache_dir: Path | str | None = None,
        refresh_cache: bool = False,
    ) -> pd.DataFrame:
        """Fetch and parse all TPEx daily prices for one trade date."""

        payload = self.fetch_daily_payload(
            trade_date,
            cache_dir=cache_dir,
            refresh_cache=refresh_cache,
        )
        return parse_daily_quotes_payload(payload, trade_date=trade_date, market="TPEX")

    def fetch_daily_payload(
        self,
        trade_date: date | str | pd.Timestamp,
        *,
        cache_dir: Path | str | None = None,
        refresh_cache: bool = False,
    ) -> dict[str, Any]:
        """Fetch one raw TPEx dailyQuotes payload, using cache when possible."""

        query_date = format_tpex_query_date(trade_date)
        cache_date = format_tpex_cache_date(trade_date)
        cache_path = _cache_path(cache_dir, cache_date) if cache_dir is not None else None

        if cache_path is not None and cache_path.exists() and not refresh_cache:
            cached_payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if not should_refresh_recent_empty_daily_payload(
                cached_payload,
                trade_date=trade_date,
                recent_days=self.recent_empty_cache_refresh_days,
            ):
                return cached_payload

        payload = self.get_json(
            TPEX_DAILY_ENDPOINT,
            params={
                "date": query_date,
                "id": "",
                "response": "json",
            },
        )

        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
                encoding="utf-8",
            )

        return payload

    def get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET a JSON endpoint with retry, exponential backoff, and polite pacing."""

        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            self._sleep_if_needed()
            try:
                response = self.session.get(url, params=params, timeout=self.timeout_seconds)
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    response.raise_for_status()
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
                time.sleep(self.backoff_seconds * (2 ** (attempt - 1)))

        raise TPEXClientError(
            f"Failed to fetch TPEx JSON from {url} with params {params} "
            f"after {self.max_retries} attempt(s): {last_error}"
        )

    def _sleep_if_needed(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if self._last_request_time and elapsed < self.polite_sleep_seconds:
            time.sleep(self.polite_sleep_seconds - elapsed)
        self._last_request_time = time.monotonic()


def _payload_trade_date(
    payload: dict[str, Any],
    trade_date: date | str | pd.Timestamp | None,
) -> pd.Timestamp:
    if trade_date is not None:
        return parse_twse_date(trade_date)
    if payload.get("date"):
        return parse_twse_date(payload["date"])
    for table in payload.get("tables") or []:
        table_date = table.get("date")
        if table_date:
            return parse_twse_date(table_date)
    raise TPEXClientError("Cannot determine trade_date from TPEx payload")


def _find_daily_quote_table(payload: dict[str, Any]) -> dict[str, Any] | None:
    required_fields = {"代號", "名稱", "收盤", "開盤", "最高", "最低", "成交股數", "成交金額(元)", "成交筆數"}
    for table in payload.get("tables") or []:
        fields = {str(field).strip() for field in table.get("fields") or []}
        if required_fields.issubset(fields):
            return table
    return None


def _cell(row: list[Any], field_index: dict[str, int], field_name: str) -> Any:
    index = field_index.get(field_name)
    if index is None or index >= len(row):
        return None
    return row[index]


def should_refresh_recent_empty_daily_payload(
    payload: dict[str, Any],
    *,
    trade_date: date | str | pd.Timestamp,
    recent_days: int,
    today: date | pd.Timestamp | None = None,
) -> bool:
    """Return True for recent TPEx daily caches likely captured before publication."""

    if recent_days < 0 or not _is_empty_daily_payload(payload):
        return False

    trade_ts = parse_twse_date(trade_date).normalize()
    today_ts = pd.Timestamp(today if today is not None else date.today()).normalize()
    age_days = (today_ts - trade_ts).days
    return 0 <= age_days <= recent_days


def _is_empty_daily_payload(payload: dict[str, Any]) -> bool:
    stat = str(payload.get("stat", "")).strip()
    if stat and stat.upper() != "OK":
        return False

    table = _find_daily_quote_table(payload)
    if table is None:
        return False

    rows = table.get("data") or []
    total_count = normalize_int(table.get("totalCount"))
    listed_companies = normalize_int(table.get("listedCompanies"))
    return len(rows) == 0 and (total_count in (None, 0)) and (listed_companies in (None, 0))


def _cache_path(cache_dir: Path | str | None, cache_date: str) -> Path:
    if cache_dir is None:
        raise ValueError("cache_dir is required")
    return Path(cache_dir) / f"{cache_date}.json"
