"""
프로젝트 설정 로더

- 보안상 API 키/시크릿은 .env로 관리합니다.
- python-dotenv로 환경변수를 로드합니다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


# .env 파일을 현재 작업 디렉터리 기준으로 로드
load_dotenv()


@dataclass(frozen=True)
class Settings:
    binance_api_key: str
    binance_api_secret: str
    use_testnet: bool
    app_mode: str
    trading_enable: bool
    trading_dry_run: bool
    trading_symbol: str
    trading_interval: str
    trading_limit: int
    trading_quantity: float
    trading_leverage: int
    test_force_action: str
    watchlist_symbols: tuple[str, ...]
    usdt_krw_rate: float | None
    watchlist_mode: str
    watchlist_size: int
    watchlist_min_quote_usdt: float
    watchlist_max_vol_pct_24h: float
    watchlist_exclude_symbols: tuple[str, ...]
    watchlist_exclude_keywords: tuple[str, ...]
    bot_max_trades: int
    bot_loop_seconds: int
    bot_cooldown_seconds: int
    bot_max_hold_seconds: int
    strat_lookback: int
    strat_volume_mult: float
    strat_min_body_pct: float
    risk_stop_loss_pct: float
    risk_take_profit_pct: float
    leverage_mode: str
    leverage_max: int
    leverage_min: int
    dashboard_trades_limit: int
    dashboard_close_rows: int
    strat_rsi_period: int
    strat_rsi_low: float
    strat_rsi_high: float
    strat_ema_fast: int
    strat_ema_slow: int
    strat_macd_fast: int
    strat_macd_slow: int
    strat_macd_signal: int
    risk_trailing_stop_pct: float
    risk_daily_loss_limit_pct: float
    risk_max_consecutive_losses: int
    risk_max_open_positions: int
    trading_use_exchange_filters: bool
    risk_atr_multiplier_sl: float
    risk_atr_multiplier_tp: float


def _parse_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_int(value: str | None, default: int) -> int:
    if value is None or not value.strip():
        return default
    return int(value.strip())


def _parse_float(value: str | None, default: float) -> float:
    if value is None or not value.strip():
        return default
    return float(value.strip())


def get_settings() -> Settings:
    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_API_SECRET", "").strip()
    use_testnet = _parse_bool(os.getenv("BINANCE_USE_TESTNET"), default=True)
    app_mode = (os.getenv("APP_MODE", "run") or "run").strip().lower()
    trading_enable = _parse_bool(os.getenv("TRADING_ENABLE"), default=False)
    trading_dry_run = _parse_bool(os.getenv("TRADING_DRY_RUN"), default=True)
    trading_symbol = os.getenv("TRADING_SYMBOL", "BTCUSDT").strip() or "BTCUSDT"
    trading_interval = os.getenv("TRADING_INTERVAL", "1m").strip() or "1m"
    trading_limit = _parse_int(os.getenv("TRADING_LIMIT"), default=200)
    trading_quantity = _parse_float(os.getenv("TRADING_QUANTITY"), default=0.001)
    trading_leverage = _parse_int(os.getenv("TRADING_LEVERAGE"), default=1)
    test_force_action = (os.getenv("TEST_FORCE_ACTION", "none") or "none").strip().lower()
    watchlist_raw = os.getenv("WATCHLIST_SYMBOLS", "BTCUSDT,ETHUSDT").strip()
    watchlist_symbols = tuple(
        s.strip().upper()
        for s in watchlist_raw.split(",")
        if s.strip()
    )
    usdt_krw_rate_raw = os.getenv("USDT_KRW_RATE", "").strip()
    usdt_krw_rate = None if not usdt_krw_rate_raw else float(usdt_krw_rate_raw)

    watchlist_mode = (os.getenv("WATCHLIST_MODE", "auto") or "auto").strip().lower()
    watchlist_size = _parse_int(os.getenv("WATCHLIST_SIZE"), default=10)
    watchlist_min_quote_usdt = _parse_float(os.getenv("WATCHLIST_MIN_QUOTE_USDT"), default=50_000_000.0)
    watchlist_max_vol_pct_24h = _parse_float(os.getenv("WATCHLIST_MAX_VOL_PCT_24H"), default=12.0)
    watchlist_exclude_symbols = tuple(
        s.strip().upper()
        for s in (os.getenv("WATCHLIST_EXCLUDE_SYMBOLS", "") or "").split(",")
        if s.strip()
    )
    watchlist_exclude_keywords = tuple(
        s.strip().upper()
        for s in (os.getenv("WATCHLIST_EXCLUDE_KEYWORDS", "") or "").split(",")
        if s.strip()
    )

    bot_max_trades = _parse_int(os.getenv("BOT_MAX_TRADES"), default=0)
    bot_loop_seconds = _parse_int(os.getenv("BOT_LOOP_SECONDS"), default=10)
    bot_cooldown_seconds = _parse_int(os.getenv("BOT_COOLDOWN_SECONDS"), default=30)
    bot_max_hold_seconds = _parse_int(os.getenv("BOT_MAX_HOLD_SECONDS"), default=300)

    strat_lookback = _parse_int(os.getenv("STRAT_LOOKBACK"), default=20)
    strat_volume_mult = _parse_float(os.getenv("STRAT_VOLUME_MULT"), default=1.5)
    strat_min_body_pct = _parse_float(os.getenv("STRAT_MIN_BODY_PCT"), default=0.05)

    risk_stop_loss_pct = _parse_float(os.getenv("RISK_STOP_LOSS_PCT"), default=0.5)
    risk_take_profit_pct = _parse_float(os.getenv("RISK_TAKE_PROFIT_PCT"), default=0.9)

    leverage_mode = (os.getenv("LEVERAGE_MODE", "auto") or "auto").strip().lower()
    leverage_max = _parse_int(os.getenv("LEVERAGE_MAX"), default=5)
    leverage_min = _parse_int(os.getenv("LEVERAGE_MIN"), default=1)

    dashboard_trades_limit = _parse_int(os.getenv("DASHBOARD_TRADES_LIMIT"), default=1000)
    dashboard_close_rows = _parse_int(os.getenv("DASHBOARD_CLOSE_ROWS"), default=300)

    strat_rsi_period = _parse_int(os.getenv("STRAT_RSI_PERIOD"), default=14)
    strat_rsi_low = _parse_float(os.getenv("STRAT_RSI_LOW"), default=30.0)
    strat_rsi_high = _parse_float(os.getenv("STRAT_RSI_HIGH"), default=70.0)
    strat_ema_fast = _parse_int(os.getenv("STRAT_EMA_FAST"), default=20)
    strat_ema_slow = _parse_int(os.getenv("STRAT_EMA_SLOW"), default=50)
    strat_macd_fast = _parse_int(os.getenv("STRAT_MACD_FAST"), default=12)
    strat_macd_slow = _parse_int(os.getenv("STRAT_MACD_SLOW"), default=26)
    strat_macd_signal = _parse_int(os.getenv("STRAT_MACD_SIGNAL"), default=9)

    risk_trailing_stop_pct = _parse_float(os.getenv("RISK_TRAILING_STOP_PCT"), default=0.3)
    risk_daily_loss_limit_pct = _parse_float(os.getenv("RISK_DAILY_LOSS_LIMIT_PCT"), default=3.0)
    risk_max_consecutive_losses = _parse_int(os.getenv("RISK_MAX_CONSECUTIVE_LOSSES"), default=5)
    risk_max_open_positions = _parse_int(os.getenv("RISK_MAX_OPEN_POSITIONS"), default=1)
    trading_use_exchange_filters = _parse_bool(os.getenv("TRADING_USE_EXCHANGE_FILTERS"), default=True)
    risk_atr_multiplier_sl = _parse_float(os.getenv("RISK_ATR_MULTIPLIER_SL"), default=1.5)
    risk_atr_multiplier_tp = _parse_float(os.getenv("RISK_ATR_MULTIPLIER_TP"), default=3.0)

    placeholder_values = {"YOUR_API_KEY_HERE", "YOUR_API_SECRET_HERE"}
    if (
        not api_key
        or not api_secret
        or api_key in placeholder_values
        or api_secret in placeholder_values
    ):
        raise ValueError(
            "BINANCE_API_KEY / BINANCE_API_SECRET가 비어 있습니다.\n"
            "`.env` 파일에 키를 넣고 다시 실행하세요."
        )

    if trading_limit <= 0:
        raise ValueError("TRADING_LIMIT은 1 이상이어야 합니다.")
    if trading_quantity <= 0:
        raise ValueError("TRADING_QUANTITY는 0보다 커야 합니다.")
    if trading_leverage <= 0:
        raise ValueError("TRADING_LEVERAGE는 1 이상이어야 합니다.")
    if app_mode not in {"run", "check", "bot"}:
        raise ValueError("APP_MODE는 run/check/bot 여야 합니다.")
    if test_force_action not in {"none", "open_long", "open_short", "close"}:
        raise ValueError("TEST_FORCE_ACTION은 none/open_long/open_short/close 중 하나여야 합니다.")
    if watchlist_mode not in {"auto", "manual"}:
        raise ValueError("WATCHLIST_MODE는 auto 또는 manual 여야 합니다.")
    if watchlist_size <= 0:
        raise ValueError("WATCHLIST_SIZE는 1 이상이어야 합니다.")
    if watchlist_min_quote_usdt < 0:
        raise ValueError("WATCHLIST_MIN_QUOTE_USDT는 0 이상이어야 합니다.")
    if watchlist_max_vol_pct_24h < 0:
        raise ValueError("WATCHLIST_MAX_VOL_PCT_24H는 0 이상이어야 합니다.")
    if bot_max_trades < 0:
        raise ValueError("BOT_MAX_TRADES는 0 이상이어야 합니다.")
    if bot_loop_seconds <= 0:
        raise ValueError("BOT_LOOP_SECONDS는 1 이상이어야 합니다.")
    if bot_cooldown_seconds < 0:
        raise ValueError("BOT_COOLDOWN_SECONDS는 0 이상이어야 합니다.")
    if bot_max_hold_seconds <= 0:
        raise ValueError("BOT_MAX_HOLD_SECONDS는 1 이상이어야 합니다.")
    if strat_lookback <= 1:
        raise ValueError("STRAT_LOOKBACK는 2 이상이어야 합니다.")
    if strat_volume_mult < 0:
        raise ValueError("STRAT_VOLUME_MULT는 0 이상이어야 합니다.")
    if strat_min_body_pct < 0:
        raise ValueError("STRAT_MIN_BODY_PCT는 0 이상이어야 합니다.")
    if risk_stop_loss_pct <= 0:
        raise ValueError("RISK_STOP_LOSS_PCT는 0보다 커야 합니다.")
    if risk_take_profit_pct <= 0:
        raise ValueError("RISK_TAKE_PROFIT_PCT는 0보다 커야 합니다.")
    if leverage_mode not in {"auto", "manual"}:
        raise ValueError("LEVERAGE_MODE는 auto 또는 manual 여야 합니다.")
    if leverage_max <= 0:
        raise ValueError("LEVERAGE_MAX는 1 이상이어야 합니다.")
    if leverage_min <= 0:
        raise ValueError("LEVERAGE_MIN는 1 이상이어야 합니다.")
    if leverage_min > leverage_max:
        raise ValueError("LEVERAGE_MIN는 LEVERAGE_MAX보다 클 수 없습니다.")
    if dashboard_trades_limit <= 0:
        raise ValueError("DASHBOARD_TRADES_LIMIT는 1 이상이어야 합니다.")
    if dashboard_close_rows <= 0:
        raise ValueError("DASHBOARD_CLOSE_ROWS는 1 이상이어야 합니다.")
    if strat_rsi_period <= 0:
        raise ValueError("STRAT_RSI_PERIOD는 1 이상이어야 합니다.")
    if risk_trailing_stop_pct < 0:
        raise ValueError("RISK_TRAILING_STOP_PCT는 0 이상이어야 합니다.")
    if risk_daily_loss_limit_pct <= 0:
        raise ValueError("RISK_DAILY_LOSS_LIMIT_PCT는 0보다 커야 합니다.")
    if risk_max_consecutive_losses < 0:
        raise ValueError("RISK_MAX_CONSECUTIVE_LOSSES는 0 이상이어야 합니다.")
    if risk_max_open_positions <= 0:
        raise ValueError("RISK_MAX_OPEN_POSITIONS는 1 이상이어야 합니다.")

    return Settings(
        binance_api_key=api_key,
        binance_api_secret=api_secret,
        use_testnet=use_testnet,
        app_mode=app_mode,
        trading_enable=trading_enable,
        trading_dry_run=trading_dry_run,
        trading_symbol=trading_symbol,
        trading_interval=trading_interval,
        trading_limit=trading_limit,
        trading_quantity=trading_quantity,
        trading_leverage=trading_leverage,
        test_force_action=test_force_action,
        watchlist_symbols=watchlist_symbols,
        usdt_krw_rate=usdt_krw_rate,
        watchlist_mode=watchlist_mode,
        watchlist_size=watchlist_size,
        watchlist_min_quote_usdt=watchlist_min_quote_usdt,
        watchlist_max_vol_pct_24h=watchlist_max_vol_pct_24h,
        watchlist_exclude_symbols=watchlist_exclude_symbols,
        watchlist_exclude_keywords=watchlist_exclude_keywords,
        bot_max_trades=bot_max_trades,
        bot_loop_seconds=bot_loop_seconds,
        bot_cooldown_seconds=bot_cooldown_seconds,
        bot_max_hold_seconds=bot_max_hold_seconds,
        strat_lookback=strat_lookback,
        strat_volume_mult=strat_volume_mult,
        strat_min_body_pct=strat_min_body_pct,
        risk_stop_loss_pct=risk_stop_loss_pct,
        risk_take_profit_pct=risk_take_profit_pct,
        leverage_mode=leverage_mode,
        leverage_max=leverage_max,
        leverage_min=leverage_min,
        dashboard_trades_limit=dashboard_trades_limit,
        dashboard_close_rows=dashboard_close_rows,
        strat_rsi_period=strat_rsi_period,
        strat_rsi_low=strat_rsi_low,
        strat_rsi_high=strat_rsi_high,
        strat_ema_fast=strat_ema_fast,
        strat_ema_slow=strat_ema_slow,
        strat_macd_fast=strat_macd_fast,
        strat_macd_slow=strat_macd_slow,
        strat_macd_signal=strat_macd_signal,
        risk_trailing_stop_pct=risk_trailing_stop_pct,
        risk_daily_loss_limit_pct=risk_daily_loss_limit_pct,
        risk_max_consecutive_losses=risk_max_consecutive_losses,
        risk_max_open_positions=risk_max_open_positions,
        trading_use_exchange_filters=trading_use_exchange_filters,
        risk_atr_multiplier_sl=risk_atr_multiplier_sl,
        risk_atr_multiplier_tp=risk_atr_multiplier_tp,
    )
