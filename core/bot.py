from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import threading

from binance.client import Client

from config.settings import Settings
from core.adaptive import get_symbol_performance_score, maybe_auto_tune_params
from core.data import fetch_futures_klines
from core.execution import get_mark_price, place_market_order_and_wait
from core.params import StrategyParams, load_params
from core.strategy import breakout_volume_direction_signal, calculate_atr
from core.trade import append_trade_log, log_completed_trade, set_futures_leverage, set_futures_margin_type_isolated
from core.watchlist import build_auto_watchlist
from core.optimizer import StrategyOptimizer


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BotState:
    last_trade_ts: float = 0.0
    trades_done: int = 0
    in_position_symbol: str | None = None
    entry_price: float | None = None
    entry_time: float | None = None
    entry_side: str | None = None  # "LONG" or "SHORT"
    high_water_mark: float | None = None  # 트레일링 스탑용 최고/최저가
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    
    # 일일 손실 제한용
    daily_start_balance: float | None = None
    daily_reset_ts: float = 0.0
    
    # 연속 손실 제한용
    consecutive_losses: int = 0


def _pick_symbol(client: Client, settings: Settings) -> str:
    if settings.watchlist_mode == "auto":
        wl = build_auto_watchlist(client, size=settings.watchlist_size)
        if wl:
            scored_wl = sorted(wl, key=lambda x: get_symbol_performance_score(x.symbol), reverse=True)
            return scored_wl[0].symbol
    return settings.trading_symbol


def _get_open_position_amt(client: Client, symbol: str) -> float:
    positions = client.futures_position_information()
    pos = next((p for p in positions if p.get("symbol") == symbol), None)
    if not pos: return 0.0
    try: return float(pos.get("positionAmt", 0) or 0)
    except: return 0.0


def _get_any_open_position(client: Client) -> tuple[str | None, float]:
    positions = client.futures_position_information()
    for p in positions:
        amt = float(p.get("positionAmt", 0) or 0)
        if amt != 0.0: return p.get("symbol"), amt
    return None, 0.0


def _get_all_open_positions(client: Client) -> list[tuple[str, float]]:
    positions = client.futures_position_information()
    res = []
    for p in positions:
        amt = float(p.get("positionAmt", 0) or 0)
        if amt != 0.0: res.append((p.get("symbol"), amt))
    return res


def _should_exit(state: BotState, mark: float, params: StrategyParams) -> tuple[bool, str]:
    entry_side = state.entry_side
    entry_price = state.entry_price
    if not entry_side or not entry_price: return False, ""

    if state.stop_loss_price is not None:
        if entry_side == "LONG" and mark <= state.stop_loss_price: return True, "ATR손절"
        if entry_side == "SHORT" and mark >= state.stop_loss_price: return True, "ATR손절"
            
    if state.take_profit_price is not None:
        if entry_side == "LONG" and mark >= state.take_profit_price: return True, "ATR익절"
        if entry_side == "SHORT" and mark <= state.take_profit_price: return True, "ATR익절"

    ts = params.trailing_stop_pct / 100.0
    if entry_side == "LONG":
        if state.high_water_mark is None or mark > state.high_water_mark: state.high_water_mark = mark
        if ts > 0 and mark <= state.high_water_mark * (1 - ts) and mark > entry_price:
            return True, f"트레일링스탑({params.trailing_stop_pct}%)"
    else:
        if state.high_water_mark is None or mark < state.high_water_mark: state.high_water_mark = mark
        if ts > 0 and mark >= state.high_water_mark * (1 + ts) and mark < entry_price:
            return True, f"트레일링스탑({params.trailing_stop_pct}%)"

    return False, ""


def _run_optimizer_loop(client: Client, settings: Settings):
    optimizer = StrategyOptimizer(client, settings)
    while True:
        try:
            wl = build_auto_watchlist(client, size=15)
            symbols = [item.symbol for item in wl]
            if symbols: optimizer.run_autonomous_optimization(symbols)
        except Exception as e: print(f"[Optimizer] Error: {e}")
        time.sleep(86400)


def run_bot(client: Client, settings: Settings) -> None:
    params = load_params(settings)
    state = BotState()

    threading.Thread(target=_run_optimizer_loop, args=(client, settings), daemon=True).start()

    print("=== BOT START (23:44 RECOVERY) ===")
    while True:
        open_positions = _get_all_open_positions(client)
        pos_symbol, pos_amt = open_positions[0] if open_positions else (None, 0.0)
        symbol = pos_symbol if pos_symbol else _pick_symbol(client, settings)
        
        params = load_params(settings)
        open_amt = pos_amt if pos_symbol == symbol else _get_open_position_amt(client, symbol)
        now = time.time()

        if open_amt != 0.0:
            if state.in_position_symbol != symbol:
                state.in_position_symbol = symbol
                state.entry_time = now
                state.entry_price = get_mark_price(client, symbol)
                state.entry_side = "LONG" if open_amt > 0 else "SHORT"
                state.high_water_mark = state.entry_price

            mark = get_mark_price(client, symbol)
            if now - state.entry_time >= params.max_hold_seconds:
                do_exit, exit_reason = True, "시간청산"
            else:
                do_exit, exit_reason = _should_exit(state, mark, params)

            if do_exit:
                close_side = "SELL" if open_amt > 0 else "BUY"
                qty = abs(open_amt)
                print(f"[{_utc_now()}] EXIT {symbol} reason={exit_reason} mark={mark}")

                if settings.trading_dry_run:
                    log_completed_trade(
                        Path("logs") / "trade_history.csv",
                        symbol=symbol, side=state.entry_side, quantity=qty,
                        entry_price=state.entry_price, exit_price=mark,
                        entry_time=datetime.fromtimestamp(state.entry_time, tz=timezone.utc).isoformat(),
                        exit_time=_utc_now(),
                        pnl=(mark - state.entry_price) * qty if state.entry_side == "LONG" else (state.entry_price - mark) * qty,
                        roi_pct=((mark - state.entry_price) / state.entry_price * 100 * params.leverage) if state.entry_side == "LONG" else ((state.entry_price - mark) / state.entry_price * 100 * params.leverage),
                        exit_reason=exit_reason,
                        leverage=params.leverage
                    )
                else:
                    filled = place_market_order_and_wait(client, symbol, close_side, qty, reduce_only=True)
                    exit_price = float(filled.get("avgPrice") or mark)
                    log_completed_trade(
                        Path("logs") / "trade_history.csv",
                        symbol=symbol, side=state.entry_side, quantity=qty,
                        entry_price=state.entry_price, exit_price=exit_price,
                        entry_time=datetime.fromtimestamp(state.entry_time, tz=timezone.utc).isoformat(),
                        exit_time=_utc_now(),
                        pnl=(exit_price - state.entry_price) * qty * params.leverage if state.entry_side == "LONG" else (state.entry_price - exit_price) * qty * params.leverage,
                        roi_pct=((exit_price - state.entry_price) / state.entry_price * 100 * params.leverage) if state.entry_side == "LONG" else ((state.entry_price - exit_price) / state.entry_price * 100 * params.leverage),
                        exit_reason=exit_reason,
                        leverage=params.leverage
                    )
                
                state.in_position_symbol = None
                state.trades_done += 1

        else:
            if not settings.trading_dry_run:
                try:
                    set_futures_margin_type_isolated(client, symbol)
                    set_futures_leverage(client, symbol, int(params.leverage))
                except: pass

            df = fetch_futures_klines(client, symbol, params.trading_interval, limit=100)
            signal, reason = breakout_volume_direction_signal(df, params)

            if signal in {"LONG", "SHORT"}:
                mark = float(df["close"].iloc[-1])
                print(f"[{_utc_now()}] ENTER {symbol} {signal} reason={reason}")

                state.in_position_symbol = symbol
                state.entry_price = mark
                state.entry_time = now
                state.entry_side = signal
                state.high_water_mark = mark
                
                atr_val = calculate_atr(df).iloc[-1]
                if signal == "LONG":
                    state.stop_loss_price = mark - (atr_val * params.atr_multiplier_sl)
                    state.take_profit_price = mark + (atr_val * params.atr_multiplier_tp)
                else:
                    state.stop_loss_price = mark + (atr_val * params.atr_multiplier_sl)
                    state.take_profit_price = mark - (atr_val * params.atr_multiplier_tp)

        time.sleep(settings.bot_loop_seconds)
