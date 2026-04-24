"""
데이터 수집 모듈 (초기 스켈레톤)

여기에는:
- 캔들(klines) 수집
- 데이터프레임 변환
- 파일 저장/로드
같은 기능을 모아둘 예정입니다.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from binance.client import Client


DATA_DIR = Path("data/historical")


def fetch_futures_klines(
    client: Client,
    symbol: str,
    interval: str = "1m",
    limit: int = 500,
) -> pd.DataFrame:
    """
    Binance Futures 캔들 데이터를 가져와 pandas DataFrame으로 반환합니다.
    """
    raw: list[list[Any]] = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
    return _raw_to_df(raw)


def fetch_historical_klines_paginated(
    client: Client,
    symbol: str,
    interval: str = "1m",
    days: int = 30,
) -> pd.DataFrame:
    """
    장기 데이터를 가져오고 로컬에 캐싱합니다. 부족한 부분만 거래소에서 보충합니다.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    file_path = DATA_DIR / f"{symbol}_{interval}.csv"

    columns = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
    ]

    existing_df = pd.DataFrame()
    start_ts = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)

    if file_path.exists():
        try:
            existing_df = pd.read_csv(file_path)
            if not existing_df.empty:
                # 마지막 시간 이후부터 가져오기
                last_ts = int(existing_df["open_time"].iloc[-1])
                start_ts = max(start_ts, last_ts + 1)
        except Exception:
            existing_df = pd.DataFrame()

    # 현재 시간보다 start_ts가 과거인 경우에만 추가 수집
    now_ts = int(datetime.now().timestamp() * 1000)
    if start_ts < now_ts - 60000: # 최소 1분 차이는 나야 함
        start_str = str(start_ts)
        raw = client.futures_historical_klines(
            symbol=symbol,
            interval=interval,
            start_str=start_str
        )
        if raw:
            new_df = _raw_to_df(raw)
            if not existing_df.empty:
                # 중복 제거 및 병합
                df = pd.concat([existing_df, new_df]).drop_duplicates(subset=["open_time"]).sort_values("open_time")
            else:
                df = new_df
            
            # 너무 오래된 데이터는 삭제 (days 기준)
            cutoff_ts = int((datetime.now() - timedelta(days=days + 1)).timestamp() * 1000)
            df = df[df["open_time"] >= cutoff_ts]
            
            df.to_csv(file_path, index=False)
            return df

    return existing_df


def _raw_to_df(raw: list[list[Any]]) -> pd.DataFrame:
    columns = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
    ]
    df = pd.DataFrame(raw, columns=columns)
    # 숫자 컬럼을 float로 변환
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = df[col].astype(float)
    return df

