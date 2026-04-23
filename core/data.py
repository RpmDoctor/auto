"""
데이터 수집 모듈 (초기 스켈레톤)

여기에는:
- 캔들(klines) 수집
- 데이터프레임 변환
- 파일 저장/로드
같은 기능을 모아둘 예정입니다.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from binance.client import Client


def fetch_futures_klines(
    client: Client,
    symbol: str,
    interval: str = "1m",
    limit: int = 500,
) -> pd.DataFrame:
    """
    Binance Futures 캔들 데이터를 가져와 pandas DataFrame으로 반환합니다.

    참고:
    - interval 예: "1m", "5m", "15m", "1h", "4h", "1d"
    """
    raw: list[list[Any]] = client.futures_klines(symbol=symbol, interval=interval, limit=limit)

    columns = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_asset_volume",
        "number_of_trades",
        "taker_buy_base_asset_volume",
        "taker_buy_quote_asset_volume",
        "ignore",
    ]
    df = pd.DataFrame(raw, columns=columns)

    # 숫자 컬럼을 float로 변환 (전략에서 계산하기 쉬움)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    return df

