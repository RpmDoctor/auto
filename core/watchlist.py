from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import re
import pandas as pd
from binance.client import Client


@dataclass(frozen=True)
class WatchlistItem:
    symbol: str
    quote_volume_usdt: float
    volatility_pct_24h: float
    last_price: float | None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _tradable_usdt_perp_symbols(exchange_info: dict[str, Any]) -> set[str]:
    symbols: set[str] = set()
    for s in exchange_info.get("symbols", []):
        if s.get("status") != "TRADING":
            continue
        if s.get("quoteAsset") != "USDT":
            continue
        ct = s.get("contractType")
        if ct and ct != "PERPETUAL":
            continue
        sym = str(s.get("symbol") or "").upper()
        if sym:
            symbols.add(sym)
    return symbols


def fetch_futures_24h_tickers(client: Client) -> pd.DataFrame:
    """
    Futures 24h ticker 통계(대부분 심볼 전체)를 DataFrame으로 반환합니다.

    python-binance의 Client.futures_ticker()는 24h ticker 리스트를 반환합니다.
    """
    raw = client.futures_ticker()
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return pd.DataFrame()

    df = pd.DataFrame(raw)
    if df.empty:
        return df

    # 주요 숫자 컬럼 변환
    for col in ["quoteVolume", "priceChangePercent", "lastPrice"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "symbol" in df.columns:
        df["symbol"] = df["symbol"].astype(str).str.upper()

    return df


def build_auto_watchlist(
    client: Client,
    *,
    size: int = 10,
    min_quote_volume_usdt: float = 50_000_000,
    max_volatility_pct_24h: float = 12.0,
    exclude_symbols: tuple[str, ...] = (),
    exclude_keywords: tuple[str, ...] = (),
    always_include: tuple[str, ...] = ("BTCUSDT", "ETHUSDT"),
    prefer_bluechips: bool = True,
) -> list[WatchlistItem]:
    """
    자동 관심코인 선정(실전용 최소 기준).

    기준(단순/안전):
    - USDT 무기한(Perpetual) + TRADING
    - 24h quoteVolume(USDT) 상위
    - min_quote_volume_usdt 이상
    - 24h 변동률(|priceChangePercent|)이 max_volatility_pct_24h 이하
    - 블랙리스트(exclude_symbols / exclude_keywords) 제외
    - 우량주(Blue-chips) 우선 고려
    """
    # 우량 알트코인 리스트 (시가총액 상위 및 안정성 검증된 종목 위주)
    BLUECHIPS = {
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", 
        "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "MATICUSDT",
        "LTCUSDT", "BCHUSDT", "ATOMUSDT", "ETCUSDT", "NEARUSDT",
        "ALGOUSDT", "XLMUSDT", "TRXUSDT"
    }
    try:
        exchange_info = client.futures_exchange_info()
    except Exception:
        exchange_info = {}

    tradable = _tradable_usdt_perp_symbols(exchange_info) if exchange_info else set()

    try:
        tickers = fetch_futures_24h_tickers(client)
    except Exception:
        tickers = pd.DataFrame()

    items: list[WatchlistItem] = []
    if not tickers.empty and "symbol" in tickers.columns:
        df = tickers.copy()
        if tradable:
            df = df[df["symbol"].isin(tradable)]

        # 밈/잡코인 제외(심볼/키워드)
        if exclude_symbols:
            ex = {s.upper() for s in exclude_symbols}
            df = df[~df["symbol"].isin(ex)]
        if exclude_keywords:
            keys = [k.upper() for k in exclude_keywords if k.strip()]
            if keys:
                pat = "|".join([re.escape(k) for k in keys])
                df = df[~df["symbol"].str.contains(pat, case=False, regex=True, na=False)]

        # 과변동 제외 (24h %)
        if "priceChangePercent" in df.columns and max_volatility_pct_24h is not None:
            df = df[df["priceChangePercent"].abs().fillna(0) <= float(max_volatility_pct_24h)]

        if "quoteVolume" in df.columns:
            df = df[df["quoteVolume"].fillna(0) >= float(min_quote_volume_usdt)]
            df = df.sort_values("quoteVolume", ascending=False)

        # 상위 후보 생성
        for _, row in df.head(max(size * 5, 50)).iterrows():
            symbol = str(row.get("symbol", "")).upper()
            if not symbol:
                continue
            qv = float(row.get("quoteVolume") or 0.0)
            vol = abs(float(row.get("priceChangePercent") or 0.0))
            last = _safe_float(row.get("lastPrice"))
            items.append(
                WatchlistItem(
                    symbol=symbol,
                    quote_volume_usdt=qv,
                    volatility_pct_24h=vol,
                    last_price=last,
                )
            )

    # 정렬 및 선정
    # 1. 블루칩 우선 + 거래대금 순
    # 2. 기타 코인 거래대금 순
    
    bluechip_items = [i for i in items if i.symbol in BLUECHIPS]
    other_items = [i for i in items if i.symbol not in BLUECHIPS]
    
    bluechip_items.sort(key=lambda x: x.quote_volume_usdt, reverse=True)
    other_items.sort(key=lambda x: x.quote_volume_usdt, reverse=True)
    
    if prefer_bluechips:
        merged = bluechip_items + other_items
    else:
        merged = sorted(items, key=lambda x: x.quote_volume_usdt, reverse=True)

    return merged[: max(1, int(size))]
