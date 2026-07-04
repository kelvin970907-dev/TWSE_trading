from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from src.tpex_client import (
    TPEXClient,
    format_tpex_query_date,
    parse_daily_quotes_payload,
    should_refresh_recent_empty_daily_payload,
)


SAMPLE_DAILY_QUOTES_PAYLOAD: dict[str, Any] = {
    "date": "20240102",
    "stat": "OK",
    "tables": [
        {
            "title": "上櫃股票行情",
            "date": "113/01/02",
            "fields": [
                "代號",
                "名稱",
                "收盤",
                "漲跌",
                "開盤",
                "最高",
                "最低",
                "均價",
                "成交股數",
                "成交金額(元)",
                "成交筆數",
                "最後買價",
                "最後買量(千股)",
                "最後賣價",
                "最後賣量(千股)",
                "發行股數",
                "次日 參考價",
                "次日 漲停價",
                "次日 跌停價",
            ],
            "data": [
                [
                    "006201",
                    "元大富櫃50",
                    "19.77",
                    "-0.23 ",
                    "20.00",
                    "20.00",
                    "19.77",
                    "19.87",
                    "22,453",
                    "446,146",
                    "37",
                    "19.78",
                    "1",
                    "19.89",
                    "34",
                    "17,446,000",
                    "19.77",
                    "21.74",
                    "17.80",
                ],
                [
                    "8069",
                    "元太",
                    "200.00",
                    "+1.00",
                    "198.00",
                    "201.00",
                    "197.50",
                    "199.50",
                    "2,000",
                    "400,000",
                    "200",
                    "--",
                    "",
                    "--",
                    "",
                    "1,000,000",
                    "--",
                    "--",
                    "--",
                ],
            ],
        }
    ],
}


EMPTY_DAILY_QUOTES_PAYLOAD: dict[str, Any] = {
    "date": "20260702",
    "stat": "ok",
    "tables": [
        {
            "title": "上櫃股票行情",
            "date": "115/07/02",
            "fields": SAMPLE_DAILY_QUOTES_PAYLOAD["tables"][0]["fields"],
            "data": [],
            "listedCompanies": "0",
            "totalCount": 0,
        }
    ],
}


class FakeResponse:
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeSession:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.headers: dict[str, str] = {}
        self.payload = payload
        self.calls = 0

    def get(self, *args: Any, **kwargs: Any) -> FakeResponse:
        self.calls += 1
        assert kwargs["params"]["date"] == "113/01/02"
        return FakeResponse(self.payload)


class FlexibleFakeSession:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.headers: dict[str, str] = {}
        self.payload = payload
        self.calls = 0

    def get(self, *args: Any, **kwargs: Any) -> FakeResponse:
        self.calls += 1
        return FakeResponse(self.payload)


def test_format_tpex_query_date_uses_roc_date() -> None:
    assert format_tpex_query_date("2024-01-02") == "113/01/02"


def test_parse_daily_quotes_payload_normalizes_rows() -> None:
    frame = parse_daily_quotes_payload(SAMPLE_DAILY_QUOTES_PAYLOAD)

    assert list(frame["symbol"]) == ["8069"]
    assert frame.loc[0, "trade_date"] == pd.Timestamp("2024-01-02")
    assert frame.loc[0, "market"] == "TPEX"
    assert frame.loc[0, "name"] == "元太"
    assert frame.loc[0, "open"] == 198.0
    assert frame.loc[0, "high"] == 201.0
    assert frame.loc[0, "low"] == 197.5
    assert frame.loc[0, "close"] == 200.0
    assert frame.loc[0, "volume_shares"] == 2_000
    assert frame.loc[0, "turnover_twd"] == 400_000
    assert frame.loc[0, "trades"] == 200


def test_parse_daily_quotes_payload_can_include_non_stock_instruments() -> None:
    frame = parse_daily_quotes_payload(SAMPLE_DAILY_QUOTES_PAYLOAD, common_stock_only=False)

    assert list(frame["symbol"]) == ["006201", "8069"]


def test_fetch_daily_payload_uses_tpex_cache(tmp_path: Path) -> None:
    session = FakeSession(SAMPLE_DAILY_QUOTES_PAYLOAD)
    client = TPEXClient(session=session, polite_sleep_seconds=0)

    first = client.fetch_daily_payload("2024-01-02", cache_dir=tmp_path)
    second = client.fetch_daily_payload("2024-01-02", cache_dir=tmp_path)

    assert first == SAMPLE_DAILY_QUOTES_PAYLOAD
    assert second == SAMPLE_DAILY_QUOTES_PAYLOAD
    assert session.calls == 1
    assert (tmp_path / "20240102.json").exists()


def test_should_refresh_recent_empty_daily_payload_only_for_recent_empty_dates() -> None:
    assert should_refresh_recent_empty_daily_payload(
        EMPTY_DAILY_QUOTES_PAYLOAD,
        trade_date="2026-07-02",
        recent_days=7,
        today=date(2026, 7, 3),
    )
    assert not should_refresh_recent_empty_daily_payload(
        EMPTY_DAILY_QUOTES_PAYLOAD,
        trade_date="2026-06-01",
        recent_days=7,
        today=date(2026, 7, 3),
    )
    assert not should_refresh_recent_empty_daily_payload(
        SAMPLE_DAILY_QUOTES_PAYLOAD,
        trade_date="2026-07-02",
        recent_days=7,
        today=date(2026, 7, 3),
    )


def test_fetch_daily_payload_refreshes_recent_empty_cache(tmp_path: Path) -> None:
    trade_date = pd.Timestamp.today().normalize()
    cache_path = tmp_path / f"{trade_date:%Y%m%d}.json"
    cache_path.write_text(
        json.dumps(EMPTY_DAILY_QUOTES_PAYLOAD, ensure_ascii=False),
        encoding="utf-8",
    )

    session = FlexibleFakeSession(SAMPLE_DAILY_QUOTES_PAYLOAD)
    client = TPEXClient(session=session, polite_sleep_seconds=0)

    payload = client.fetch_daily_payload(trade_date, cache_dir=tmp_path)

    assert payload == SAMPLE_DAILY_QUOTES_PAYLOAD
    assert session.calls == 1
