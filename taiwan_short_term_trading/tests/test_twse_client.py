from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import requests

from src.twse_client import TWSEClient, normalize_number, parse_mi_index_payload, parse_twse_date


SAMPLE_MI_INDEX_PAYLOAD: dict[str, Any] = {
    "stat": "OK",
    "date": "20240102",
    "tables": [
        {
            "title": "113年01月02日 價格指數(臺灣證券交易所)",
            "fields": ["指數", "收盤指數"],
            "data": [["發行量加權股價指數", "17,853.76"]],
        },
        {
            "title": "113年01月02日 每日收盤行情(全部(不含權證、牛熊證、可展延牛熊證))",
            "fields": [
                "證券代號",
                "證券名稱",
                "成交股數",
                "成交筆數",
                "成交金額",
                "開盤價",
                "最高價",
                "最低價",
                "收盤價",
                "漲跌(+/-)",
                "漲跌價差",
                "最後揭示買價",
                "最後揭示買量",
                "最後揭示賣價",
                "最後揭示賣量",
                "本益比",
            ],
            "data": [
                [
                    "2330",
                    "台積電",
                    "20,000,000",
                    "45,678",
                    "12,000,000,000",
                    "590.00",
                    "600.00",
                    "588.00",
                    "599.00",
                    "<p style= color:red>+</p>",
                    "6.00",
                    "598.00",
                    "12",
                    "599.00",
                    "8",
                    "18.50",
                ],
                [
                    "1101",
                    "台泥",
                    "--",
                    "",
                    "--",
                    "--",
                    "--",
                    "--",
                    "--",
                    "",
                    "--",
                    "--",
                    "",
                    "--",
                    "",
                    "0.00",
                ],
            ],
        },
    ],
}


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} test error")
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
        return FakeResponse(self.payload)


class FakeSequenceSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.headers: dict[str, str] = {}
        self.responses = responses
        self.calls = 0
        self.urls: list[str] = []

    def get(self, url: str, *args: Any, **kwargs: Any) -> FakeResponse:
        self.calls += 1
        self.urls.append(url)
        return self.responses.pop(0)


def test_parse_twse_date_handles_roc_and_gregorian_dates() -> None:
    assert parse_twse_date("113/01/02") == pd.Timestamp("2024-01-02")
    assert parse_twse_date("113年01月02日") == pd.Timestamp("2024-01-02")
    assert parse_twse_date("20240102") == pd.Timestamp("2024-01-02")
    assert parse_twse_date("2024-01-02") == pd.Timestamp("2024-01-02")


def test_normalize_number_handles_commas_blanks_and_html() -> None:
    assert normalize_number("12,345.67") == 12345.67
    assert normalize_number("--") is None
    assert normalize_number("") is None
    assert normalize_number("<p style= color:red>+</p>1.5") == 1.5


def test_parse_mi_index_payload_normalizes_daily_prices() -> None:
    frame = parse_mi_index_payload(SAMPLE_MI_INDEX_PAYLOAD)

    assert list(frame["symbol"]) == ["2330", "1101"]
    assert frame.loc[0, "trade_date"] == pd.Timestamp("2024-01-02")
    assert frame.loc[0, "market"] == "TWSE"
    assert frame.loc[0, "name"] == "台積電"
    assert frame.loc[0, "volume_shares"] == 20_000_000
    assert frame.loc[0, "turnover_twd"] == 12_000_000_000
    assert frame.loc[0, "trades"] == 45_678
    assert pd.isna(frame.loc[1, "open"])


def test_fetch_daily_payload_uses_cache(tmp_path: Path) -> None:
    session = FakeSession(SAMPLE_MI_INDEX_PAYLOAD)
    client = TWSEClient(session=session, polite_sleep_seconds=0)

    first = client.fetch_daily_payload("2024-01-02", cache_dir=tmp_path)
    second = client.fetch_daily_payload("2024-01-02", cache_dir=tmp_path)

    assert first == SAMPLE_MI_INDEX_PAYLOAD
    assert second == SAMPLE_MI_INDEX_PAYLOAD
    assert session.calls == 1
    assert (tmp_path / "20240102.json").exists()


def test_fetch_daily_payload_falls_back_to_rwd_endpoint(tmp_path: Path) -> None:
    session = FakeSequenceSession(
        [
            FakeResponse({}, status_code=404),
            FakeResponse(SAMPLE_MI_INDEX_PAYLOAD),
        ]
    )
    client = TWSEClient(session=session, polite_sleep_seconds=0, max_retries=1)

    payload = client.fetch_daily_payload("2023-01-12", cache_dir=tmp_path)

    assert payload == SAMPLE_MI_INDEX_PAYLOAD
    assert session.calls == 2
    assert session.urls[0].endswith("/exchangeReport/MI_INDEX")
    assert session.urls[1].endswith("/rwd/zh/afterTrading/MI_INDEX")
    assert (tmp_path / "20230112.json").exists()
