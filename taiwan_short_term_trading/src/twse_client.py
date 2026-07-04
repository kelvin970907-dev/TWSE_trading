"""TWSE public daily-price client and parsers."""

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
from bs4 import BeautifulSoup

from config.settings import get_settings


TWSE_DAILY_SOURCE = "TWSE_MI_INDEX"
TWSE_DAILY_ENDPOINT = "/rwd/zh/afterTrading/MI_INDEX"
TWSE_LEGACY_DAILY_ENDPOINT = "/exchangeReport/MI_INDEX"
TWSE_DAILY_TYPE = "ALLBUT0999"

DAILY_PRICE_COLUMNS = [
    "symbol",
    "trade_date",
    "market",
    "name",
    "open",
    "high",
    "low",
    "close",
    "volume_shares",
    "turnover_twd",
    "trades",
    "source",
]

NUMERIC_NULL_VALUES = {"", "--", "---", "X", "x", "N/A", "NA", "null", "None"}
TAG_RE = re.compile(r"<[^>]*>")
GREGORIAN_DATE_RE = re.compile(r"^(?P<year>\d{4})\D+(?P<month>\d{1,2})\D+(?P<day>\d{1,2})")
ROC_DATE_RE = re.compile(r"^(?P<year>\d{2,3})\D+(?P<month>\d{1,2})\D+(?P<day>\d{1,2})")


class TWSEClientError(RuntimeError):
    """Raised when TWSE data cannot be fetched or parsed."""


def normalize_number(value: Any) -> float | None:
    """Normalize TWSE numeric text into a Python float or null."""

    if value is None or pd.isna(value):
        return None

    text = TAG_RE.sub("", str(value)).strip()
    text = text.replace(",", "").replace("\u3000", "").strip()
    if text in NUMERIC_NULL_VALUES:
        return None

    try:
        return float(text)
    except ValueError:
        return None


def normalize_int(value: Any) -> int | None:
    """Normalize TWSE integer text into a Python int or null."""

    number = normalize_number(value)
    if number is None:
        return None
    return int(number)


def parse_twse_date(value: Any) -> pd.Timestamp:
    """Parse TWSE Gregorian or ROC/Taiwan date strings into a normalized date."""

    if value is None or pd.isna(value):
        raise ValueError("TWSE date value cannot be null")

    if isinstance(value, pd.Timestamp):
        return value.normalize()
    if isinstance(value, date):
        return pd.Timestamp(value).normalize()

    text = str(value).strip()
    if not text:
        raise ValueError("TWSE date value cannot be blank")

    if re.fullmatch(r"\d{8}", text):
        return pd.to_datetime(text, format="%Y%m%d").normalize()

    gregorian_match = GREGORIAN_DATE_RE.search(text)
    if gregorian_match:
        return pd.Timestamp(
            year=int(gregorian_match.group("year")),
            month=int(gregorian_match.group("month")),
            day=int(gregorian_match.group("day")),
        ).normalize()

    match = ROC_DATE_RE.search(text)
    if match:
        year = int(match.group("year"))
        if year < 1911:
            year += 1911
        return pd.Timestamp(
            year=year,
            month=int(match.group("month")),
            day=int(match.group("day")),
        ).normalize()

    return pd.Timestamp(text).normalize()


def format_twse_query_date(value: date | str | pd.Timestamp) -> str:
    """Format a date for TWSE query parameters."""

    return parse_twse_date(value).strftime("%Y%m%d")


def empty_daily_price_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=DAILY_PRICE_COLUMNS)


def parse_mi_index_payload(
    payload: dict[str, Any],
    trade_date: date | str | pd.Timestamp | None = None,
    market: str = "TWSE",
) -> pd.DataFrame:
    """Parse a TWSE MI_INDEX daily market payload into `daily_prices` rows."""

    stat = str(payload.get("stat", "")).strip()
    if stat and stat.upper() != "OK":
        return empty_daily_price_frame()

    parsed_trade_date = _payload_trade_date(payload, trade_date)
    table = _find_daily_stock_table(payload)
    if table is None:
        return empty_daily_price_frame()

    fields = table.get("fields") or []
    rows = table.get("data") or []
    field_index = {str(field).strip(): index for index, field in enumerate(fields)}

    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        if not row:
            continue
        normalized_rows.append(
            {
                "symbol": _cell(row, field_index, "證券代號"),
                "trade_date": parsed_trade_date,
                "market": market,
                "name": _cell(row, field_index, "證券名稱"),
                "open": normalize_number(_cell(row, field_index, "開盤價")),
                "high": normalize_number(_cell(row, field_index, "最高價")),
                "low": normalize_number(_cell(row, field_index, "最低價")),
                "close": normalize_number(_cell(row, field_index, "收盤價")),
                "volume_shares": normalize_int(_cell(row, field_index, "成交股數")),
                "turnover_twd": normalize_number(_cell(row, field_index, "成交金額")),
                "trades": normalize_int(_cell(row, field_index, "成交筆數")),
                "source": TWSE_DAILY_SOURCE,
            }
        )

    if not normalized_rows:
        return empty_daily_price_frame()

    frame = pd.DataFrame(normalized_rows, columns=DAILY_PRICE_COLUMNS)
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["name"] = frame["name"].astype("string").str.strip()
    frame = frame[frame["symbol"].notna() & (frame["symbol"] != "")]
    return frame.reset_index(drop=True)


@dataclass
class TWSEClient:
    """Client for selected Taiwan Stock Exchange public endpoints."""

    base_url: str = "https://www.twse.com.tw"
    session: requests.Session | None = None
    max_retries: int = 3
    backoff_seconds: float = 1.5
    polite_sleep_seconds: float = 0.7
    timeout_seconds: float | None = None
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
        """Fetch and parse all TWSE daily prices for one trade date."""

        payload = self.fetch_daily_payload(
            trade_date,
            cache_dir=cache_dir,
            refresh_cache=refresh_cache,
        )
        return parse_mi_index_payload(payload, trade_date=trade_date, market="TWSE")

    def fetch_daily_payload(
        self,
        trade_date: date | str | pd.Timestamp,
        *,
        cache_dir: Path | str | None = None,
        refresh_cache: bool = False,
    ) -> dict[str, Any]:
        """Fetch one raw TWSE MI_INDEX payload, using the raw JSON cache when possible."""

        query_date = format_twse_query_date(trade_date)
        cache_path = _cache_path(cache_dir, query_date) if cache_dir is not None else None

        if cache_path is not None and cache_path.exists() and not refresh_cache:
            return json.loads(cache_path.read_text(encoding="utf-8"))

        params = {
            "date": query_date,
            "type": TWSE_DAILY_TYPE,
            "response": "json",
        }
        errors: list[str] = []
        payload: dict[str, Any] | None = None
        # The rwd path intermittently returns CDN 404s for historical dates that
        # the official legacy path still serves, so prefer legacy for backfills.
        for endpoint in (TWSE_LEGACY_DAILY_ENDPOINT, TWSE_DAILY_ENDPOINT):
            try:
                payload = self.get_json(endpoint, params=params)
                break
            except TWSEClientError as exc:
                errors.append(f"{endpoint}: {exc}")

        if payload is None:
            raise TWSEClientError(
                "Failed to fetch TWSE MI_INDEX payload from both current and legacy "
                f"endpoints for {query_date}. Details: {' | '.join(errors)}"
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

        raise TWSEClientError(
            f"Failed to fetch TWSE JSON from {url} with params {params} "
            f"after {self.max_retries} attempt(s): {last_error}"
        )

    def _sleep_if_needed(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if self._last_request_time and elapsed < self.polite_sleep_seconds:
            time.sleep(self.polite_sleep_seconds - elapsed)
        self._last_request_time = time.monotonic()


def html_tables(html: str) -> list[pd.DataFrame]:
    """Parse HTML tables with BeautifulSoup/lxml-backed pandas readers."""

    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        return []
    return pd.read_html(str(soup))


def _cache_path(cache_dir: Path | str | None, query_date: str) -> Path:
    if cache_dir is None:
        raise ValueError("cache_dir is required")
    return Path(cache_dir) / f"{query_date}.json"


def _payload_trade_date(
    payload: dict[str, Any],
    trade_date: date | str | pd.Timestamp | None,
) -> pd.Timestamp:
    if trade_date is not None:
        return parse_twse_date(trade_date)
    if payload.get("date"):
        return parse_twse_date(payload["date"])
    for table in payload.get("tables") or []:
        title = table.get("title")
        if title:
            try:
                return parse_twse_date(title)
            except (TypeError, ValueError):
                continue
    raise TWSEClientError("Cannot determine trade_date from TWSE payload")


def _find_daily_stock_table(payload: dict[str, Any]) -> dict[str, Any] | None:
    required_fields = {"證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"}
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
