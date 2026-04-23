from __future__ import annotations

import time
from typing import Any

from binance.client import Client
from binance.exceptions import BinanceAPIException


def get_mark_price(client: Client, symbol: str) -> float:
    data = client.futures_mark_price(symbol=symbol)
    if isinstance(data, list):
        data = next((x for x in data if x.get("symbol") == symbol), {})  # type: ignore[assignment]
    price = data.get("markPrice") or data.get("price")
    return float(price)


def wait_futures_order_filled(
    client: Client,
    *,
    symbol: str,
    order_id: int,
    timeout_s: int = 15,
    poll_s: float = 0.5,
) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        try:
            last = client.futures_get_order(symbol=symbol, orderId=order_id)
        except BinanceAPIException as e:
            # 직후 조회 시 간헐적으로 -2013(Order does not exist)가 발생할 수 있어 재시도합니다.
            try:
                if getattr(e, "code", None) == -2013:
                    time.sleep(poll_s)
                    continue
            except Exception:
                pass
            raise
        status = str(last.get("status", "")).upper()
        if status in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
            return last
        time.sleep(poll_s)
    return last


def place_market_order_and_wait(
    client: Client,
    *,
    symbol: str,
    side: str,
    quantity: float,
    reduce_only: bool = False,
) -> dict[str, Any]:
    order = client.futures_create_order(
        symbol=symbol,
        side=side,
        type="MARKET",
        quantity=quantity,
        reduceOnly=reduce_only,
    )
    oid = int(order.get("orderId"))
    return wait_futures_order_filled(client, symbol=symbol, order_id=oid)
