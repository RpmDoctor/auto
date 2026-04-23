from __future__ import annotations

import math
from decimal import Decimal, ROUND_DOWN
from typing import Any

from binance.client import Client


class ExchangeFilters:
    _cache: dict[str, dict[str, Any]] = {}

    @classmethod
    def get_symbol_info(cls, client: Client, symbol: str) -> dict[str, Any]:
        if symbol not in cls._cache:
            info = client.futures_exchange_info()
            for s in info.get("symbols", []):
                cls._cache[s["symbol"]] = s
        return cls._cache.get(symbol, {})

    @classmethod
    def get_filters(cls, client: Client, symbol: str) -> dict[str, Any]:
        info = cls.get_symbol_info(client, symbol)
        filters = {f["filterType"]: f for f in info.get("filters", [])}
        return filters

    @classmethod
    def round_quantity(cls, client: Client, symbol: str, quantity: float) -> float:
        filters = cls.get_filters(client, symbol)
        lot_size = filters.get("LOT_SIZE")
        if not lot_size:
            return quantity

        step_size = float(lot_size["stepSize"])
        precision = int(round(-math.log10(step_size)))
        
        # Decimal for exact precision
        q_dec = Decimal(str(quantity))
        step_dec = Decimal(str(step_size))
        
        rounded = (q_dec // step_dec) * step_dec
        return float(rounded.quantize(Decimal(str(step_size)), rounding=ROUND_DOWN))

    @classmethod
    def round_price(cls, client: Client, symbol: str, price: float) -> float:
        filters = cls.get_filters(client, symbol)
        price_filter = filters.get("PRICE_FILTER")
        if not price_filter:
            return price

        tick_size = float(price_filter["tickSize"])
        
        p_dec = Decimal(str(price))
        tick_dec = Decimal(str(tick_size))
        
        rounded = (p_dec // tick_dec) * tick_dec
        return float(rounded.quantize(Decimal(str(tick_size)), rounding=ROUND_DOWN))

    @classmethod
    def get_min_notional(cls, client: Client, symbol: str) -> float:
        filters = cls.get_filters(client, symbol)
        min_notional = filters.get("MIN_NOTIONAL")
        if not min_notional:
            # Some symbols might use NOTIONAL instead of MIN_NOTIONAL in newer API versions
            min_notional = filters.get("NOTIONAL")
            
        if min_notional:
            return float(min_notional.get("notional") or min_notional.get("minNotional") or 0)
        return 0.0
