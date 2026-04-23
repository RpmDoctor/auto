"""
주문/체결 모듈 (초기 스켈레톤)

여기에는:
- 주문 생성
- 포지션 조회
- 체결/주문 결과 기록
을 구현할 예정입니다.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from binance.client import Client
from binance.exceptions import BinanceAPIException


def set_futures_leverage(client: Client, symbol: str, leverage: int) -> dict:
    """
    심볼별 레버리지 설정 (Futures).
    """
    return client.futures_change_leverage(symbol=symbol, leverage=leverage)


def set_futures_margin_type_isolated(client: Client, symbol: str) -> dict | None:
    """
    선물 마진 타입을 격리(ISOLATED)로 강제합니다.

    - 이미 격리이면 바이낸스에서 에러를 줄 수 있어(-4046 등) 무시하고 넘어갑니다.
    """
    try:
        return client.futures_change_margin_type(symbol=symbol, marginType="ISOLATED")
    except BinanceAPIException as e:
        # Already isolated / No need to change margin type
        if getattr(e, "code", None) in {-4046, -4047}:
            return None
        raise


def place_market_order(
    client: Client,
    symbol: str,
    side: str,
    quantity: float,
) -> dict:
    """
    Futures 시장가 주문 예시.

    side: "BUY" 또는 "SELL"
    """
    return client.futures_create_order(
        symbol=symbol,
        side=side,
        type="MARKET",
        quantity=quantity,
    )


def append_trade_log(
    path: str | Path,
    *,
    symbol: str,
    side: str,
    quantity: float,
    price: str | float | None,
    order_id: str | int | None,
    status: str | None,
    metadata: str | None = None,
) -> None:
    """
    logs/trades.csv 에 한 줄 추가합니다.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "price": "" if price is None else price,
        "order_id": "" if order_id is None else order_id,
        "status": "" if status is None else status,
        "metadata": "" if metadata is None else metadata,
    }

    # 파일이 없으면 헤더부터 생성
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["timestamp", "symbol", "side", "quantity", "price", "order_id", "status", "metadata"],
        )
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def log_completed_trade(
    path: str | Path,
    *,
    symbol: str,
    side: str,
    quantity: float,
    entry_price: float,
    exit_price: float,
    entry_time: str,
    exit_time: str,
    pnl: float,
    roi_pct: float,
    fee: float = 0.0,
    exit_reason: str | None = None,
) -> None:
    """
    진입부터 청산까지의 한 사이클을 logs/trade_history.csv 에 기록합니다.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    row = {
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "pnl": round(pnl, 4),
        "roi_pct": round(roi_pct, 2),
        "fee": round(fee, 4),
        "exit_reason": exit_reason or "",
    }

    fieldnames = [
        "symbol", "side", "quantity", "entry_price", "exit_price", 
        "entry_time", "exit_time", "pnl", "roi_pct", "fee", "exit_reason"
    ]

    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
