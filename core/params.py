from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from config.settings import Settings


PARAMS_PATH = Path("config") / "auto_params.json"


@dataclass
class StrategyParams:
    lookback: int
    volume_mult: float
    min_body_pct: float
    stop_loss_pct: float
    take_profit_pct: float
    max_hold_seconds: int
    cooldown_seconds: int
    leverage: int
    rsi_period: int
    rsi_low: float
    rsi_high: float
    ema_fast: int
    ema_slow: int
    macd_fast: int
    macd_slow: int
    macd_signal: int
    trailing_stop_pct: float
    daily_loss_limit_pct: float
    atr_multiplier_sl: float
    atr_multiplier_tp: float
    trading_interval: str # 자율 선택된 타임프레임


def _coerce_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except Exception:
        return fallback


def _coerce_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except Exception:
        return fallback


def default_params_from_settings(settings: Settings) -> StrategyParams:
    base_leverage = max(settings.leverage_min, min(settings.trading_leverage, settings.leverage_max))
    return StrategyParams(
        lookback=settings.strat_lookback,
        volume_mult=settings.strat_volume_mult,
        min_body_pct=settings.strat_min_body_pct,
        stop_loss_pct=settings.risk_stop_loss_pct,
        take_profit_pct=settings.risk_take_profit_pct,
        max_hold_seconds=settings.bot_max_hold_seconds,
        cooldown_seconds=settings.bot_cooldown_seconds,
        leverage=base_leverage,
        rsi_period=settings.strat_rsi_period,
        rsi_low=settings.strat_rsi_low,
        rsi_high=settings.strat_rsi_high,
        ema_fast=settings.strat_ema_fast,
        ema_slow=settings.strat_ema_slow,
        macd_fast=settings.strat_macd_fast,
        macd_slow=settings.strat_macd_slow,
        macd_signal=settings.strat_macd_signal,
        trailing_stop_pct=settings.risk_trailing_stop_pct,
        daily_loss_limit_pct=settings.risk_daily_loss_limit_pct,
        atr_multiplier_sl=settings.risk_atr_multiplier_sl,
        atr_multiplier_tp=settings.risk_atr_multiplier_tp,
        trading_interval=settings.trading_interval,
    )


def load_params(settings: Settings) -> StrategyParams:
    """
    .env(Settings) 기본값 + config/auto_params.json(있으면)로 오버라이드.
    """
    params = default_params_from_settings(settings)
    if not PARAMS_PATH.exists():
        return params

    try:
        data = json.loads(PARAMS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return params

    return StrategyParams(
        lookback=_coerce_int(data.get("lookback"), params.lookback),
        volume_mult=_coerce_float(data.get("volume_mult"), params.volume_mult),
        min_body_pct=_coerce_float(data.get("min_body_pct"), params.min_body_pct),
        stop_loss_pct=_coerce_float(data.get("stop_loss_pct"), params.stop_loss_pct),
        take_profit_pct=_coerce_float(data.get("take_profit_pct"), params.take_profit_pct),
        max_hold_seconds=_coerce_int(data.get("max_hold_seconds"), params.max_hold_seconds),
        cooldown_seconds=_coerce_int(data.get("cooldown_seconds"), params.cooldown_seconds),
        leverage=_coerce_int(data.get("leverage"), params.leverage),
        rsi_period=_coerce_int(data.get("rsi_period"), params.rsi_period),
        rsi_low=_coerce_float(data.get("rsi_low"), params.rsi_low),
        rsi_high=_coerce_float(data.get("rsi_high"), params.rsi_high),
        ema_fast=_coerce_int(data.get("ema_fast"), params.ema_fast),
        ema_slow=_coerce_int(data.get("ema_slow"), params.ema_slow),
        macd_fast=_coerce_int(data.get("macd_fast"), params.macd_fast),
        macd_slow=_coerce_int(data.get("macd_slow"), params.macd_slow),
        macd_signal=_coerce_int(data.get("macd_signal"), params.macd_signal),
        trailing_stop_pct=_coerce_float(data.get("trailing_stop_pct"), params.trailing_stop_pct),
        daily_loss_limit_pct=_coerce_float(data.get("daily_loss_limit_pct"), params.daily_loss_limit_pct),
        atr_multiplier_sl=_coerce_float(data.get("atr_multiplier_sl"), params.atr_multiplier_sl),
        atr_multiplier_tp=_coerce_float(data.get("atr_multiplier_tp"), params.atr_multiplier_tp),
        trading_interval=str(data.get("trading_interval", params.trading_interval)),
    )


def save_params(params: StrategyParams) -> None:
    PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PARAMS_PATH.write_text(json.dumps(asdict(params), ensure_ascii=False, indent=2), encoding="utf-8")
