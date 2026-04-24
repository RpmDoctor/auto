from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
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
    # 관심코인: auto면 자동 watchlist 최상단, manual이면 trading_symbol 우선
    if settings.watchlist_mode == "auto":
        wl = build_auto_watchlist(
            client,
            size=settings.watchlist_size,
            min_quote_volume_usdt=settings.watchlist_min_quote_usdt,
            max_volatility_pct_24h=settings.watchlist_max_vol_pct_24h,
            exclude_symbols=settings.watchlist_exclude_symbols,
            exclude_keywords=settings.watchlist_exclude_keywords,
        )
        if wl:
            # 최근 성과(수익률/승률)가 가장 좋은 종목 우선 선정
            scored_wl = sorted(wl, key=lambda x: get_symbol_performance_score(x.symbol), reverse=True)
            return scored_wl[0].symbol
    return settings.trading_symbol


def _get_open_position_amt(client: Client, symbol: str) -> float:
    positions = client.futures_position_information()
    pos = next((p for p in positions if p.get("symbol") == symbol), None)
    if not pos:
        return 0.0
    try:
        return float(pos.get("positionAmt", 0) or 0)
    except Exception:
        return 0.0


def _get_any_open_position(client: Client) -> tuple[str | None, float]:
    try:
        positions = client.futures_position_information()
    except Exception:
        return None, 0.0
    for p in positions:
        try:
            amt = float(p.get("positionAmt", 0) or 0)
        except Exception:
            continue
        if amt != 0.0:
            return str(p.get("symbol") or "").upper() or None, amt
    return None, 0.0


def _get_all_open_positions(client: Client) -> list[tuple[str, float]]:
    try:
        positions = client.futures_position_information()
    except Exception:
        return []
    
    res = []
    for p in positions:
        try:
            amt = float(p.get("positionAmt", 0) or 0)
        except Exception:
            continue
        if amt != 0.0:
            res.append((str(p.get("symbol") or "").upper(), amt))
    return res


def _check_daily_loss(client: Client, state: BotState, params: StrategyParams) -> bool:
    """
    오늘 손실이 한도를 넘었는지 체크.
    """
    now = time.time()
    # 24시간마다 리셋 (간단 구현)
    if now - state.daily_reset_ts > 86400:
        try:
            acc = client.futures_account()
            state.daily_start_balance = float(acc.get("totalMarginBalance", 0))
            state.daily_reset_ts = now
            print(f"[{_utc_now()}] 일일 잔고 리셋: {state.daily_start_balance:.2f} USDT")
        except Exception:
            return False

    if state.daily_start_balance is None or state.daily_start_balance <= 0:
        return False

    try:
        acc = client.futures_account()
        cur_balance = float(acc.get("totalMarginBalance", 0))
    except Exception:
        return False

    drawdown_pct = (state.daily_start_balance - cur_balance) / state.daily_start_balance * 100.0
    if drawdown_pct >= params.daily_loss_limit_pct:
        print(f"[{_utc_now()}] 일일 손실 한도 도달 ({drawdown_pct:.2f}% >= {params.daily_loss_limit_pct}%). 매매 중단.")
        return True
    return False


def _should_exit(state: BotState, mark: float, params: StrategyParams) -> tuple[bool, str]:
    entry_side = state.entry_side
    entry_price = state.entry_price
    if not entry_side or not entry_price:
        return False, ""

    # ATR 기반 가격 우선 적용
    if state.stop_loss_price is not None:
        if entry_side == "LONG" and mark <= state.stop_loss_price:
            return True, "ATR손절"
        if entry_side == "SHORT" and mark >= state.stop_loss_price:
            return True, "ATR손절"
            
    if state.take_profit_price is not None:
        if entry_side == "LONG" and mark >= state.take_profit_price:
            return True, "ATR익절"
        if entry_side == "SHORT" and mark <= state.take_profit_price:
            return True, "ATR익절"

    # 기존 퍼센트 기반 (ATR 가격 없을 때만)
    sl = params.stop_loss_pct / 100.0
    tp = params.take_profit_pct / 100.0
    ts = params.trailing_stop_pct / 100.0

    if entry_side == "LONG":
        # 트레일링 스탑 업데이트
        if state.high_water_mark is None or mark > state.high_water_mark:
            state.high_water_mark = mark
        
        # 고정 손절 (ATR 없을 때)
        if state.stop_loss_price is None and mark <= entry_price * (1 - sl):
            return True, "고정손절"
        # 고정 익절 (ATR 없을 때)
        if state.take_profit_price is None and mark >= entry_price * (1 + tp):
            return True, "고정익절"
        # 트레일링 스탑
        if ts > 0 and mark <= state.high_water_mark * (1 - ts) and mark > entry_price:
            return True, f"트레일링스탑(고점대비 {params.trailing_stop_pct}%)"
            
    else:  # SHORT
        if state.high_water_mark is None or mark < state.high_water_mark:
            state.high_water_mark = mark
            
        if state.stop_loss_price is None and mark >= entry_price * (1 + sl):
            return True, "고정손절"
        if state.take_profit_price is None and mark <= entry_price * (1 - tp):
            return True, "고정익절"
        if ts > 0 and mark >= state.high_water_mark * (1 + ts) and mark < entry_price:
            return True, f"트레일링스탑(저점대비 {params.trailing_stop_pct}%)"

    return False, ""


def _run_optimizer_loop(client: Client, settings: Settings):
    """
    24시간마다 백그라운드에서 최적의 파라미터와 타임프레임을 찾아 자동 갱신합니다.
    """
    optimizer = StrategyOptimizer(client, settings)
    while True:
        try:
            # 현재 관심 종목 리스트 가져오기
            wl = build_auto_watchlist(client, size=20)
            symbols = [item.symbol for item in wl]
            if symbols:
                optimizer.run_autonomous_optimization(symbols)
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] [Optimizer] 자율 최적화 오류: {e}")
        
        # 24시간 대기 (테스트를 위해 짧게 조정 가능)
        time.sleep(86400)


def run_bot(client: Client, settings: Settings) -> None:
    """
    자동 진입/청산 루프.
    """
    params = load_params(settings)
    state = BotState()

    # 자율 최적화 스레드 시작
    threading.Thread(target=_run_optimizer_loop, args=(client, settings), daemon=True).start()

    print("=== BOT START (Autonomous Mode) ===")
    print(f"mode=testnet={settings.use_testnet}, dry_run={settings.trading_dry_run}, max_trades={settings.bot_max_trades}")

    while True:
        # 연속 손실 제한 체크
        if settings.risk_max_consecutive_losses > 0 and state.consecutive_losses >= settings.risk_max_consecutive_losses:
            print(f"[{_utc_now()}] 연속 손실 제한 도달 ({state.consecutive_losses}회). 매매 중단.")
            time.sleep(300)
            continue

        # 현재 열려있는 모든 포지션 확인
        open_positions = _get_all_open_positions(client)
        
        pos_symbol, pos_amt = None, 0.0
        if open_positions:
            pos_symbol, pos_amt = open_positions[0]
            symbol = pos_symbol
        else:
            symbol = _pick_symbol(client, settings)

        params = load_params(settings)

        # 데이터가 쌓이면 자동 조정(아주 보수적)
        params = maybe_auto_tune_params(client, settings, params)

        open_amt = pos_amt if pos_symbol == symbol else _get_open_position_amt(client, symbol)
        now = time.time()

        # 포지션이 있으면 청산 조건 체크
        if open_amt != 0.0:
            if state.in_position_symbol != symbol:
                state.in_position_symbol = symbol
                state.entry_time = state.entry_time or now
                state.entry_price = state.entry_price or get_mark_price(client, symbol)
                state.entry_side = "LONG" if open_amt > 0 else "SHORT"
                state.high_water_mark = state.entry_price

            mark = get_mark_price(client, symbol)
            assert state.entry_price is not None and state.entry_time is not None and state.entry_side is not None

            # 시간 제한
            if now - state.entry_time >= params.max_hold_seconds:
                do_exit = True
                exit_reason = "시간청산"
            else:
                do_exit, exit_reason = _should_exit(state, mark, params)

            if do_exit:
                close_side = "SELL" if open_amt > 0 else "BUY"
                qty = abs(open_amt)
                print(f"[{_utc_now()}] EXIT {symbol} {state.entry_side} qty={qty} reason={exit_reason} mark={mark}")

                if settings.trading_dry_run:
                    append_trade_log(
                        Path("logs") / "trades.csv",
                        symbol=symbol,
                        side=f"DRY_CLOSE_{close_side}",
                        quantity=qty,
                        price=mark,
                        order_id=None,
                        status=exit_reason,
                    )
                    
                    # DRY RUN 용 히스토리 기록
                    log_completed_trade(
                        Path("logs") / "trade_history.csv",
                        symbol=symbol,
                        side=state.entry_side,
                        quantity=qty,
                        entry_price=state.entry_price,
                        exit_price=mark,
                        entry_time=datetime.fromtimestamp(state.entry_time, tz=timezone.utc).isoformat(),
                        exit_time=_utc_now(),
                        pnl=(mark - state.entry_price) * qty if state.entry_side == "LONG" else (state.entry_price - mark) * qty,
                        roi_pct=((mark - state.entry_price) / state.entry_price * 100) if state.entry_side == "LONG" else ((state.entry_price - mark) / state.entry_price * 100),
                        exit_reason=exit_reason,
                    )
                else:
                    filled = place_market_order_and_wait(
                        client,
                        symbol=symbol,
                        side=close_side,
                        quantity=qty,
                        reduce_only=True,
                        use_filters=settings.trading_use_exchange_filters,
                    )
                    exit_price = float(filled.get("avgPrice") or filled.get("price") or mark)
                    
                    append_trade_log(
                        Path("logs") / "trades.csv",
                        symbol=symbol,
                        side=f"CLOSE_{close_side}",
                        quantity=qty,
                        price=exit_price,
                        order_id=filled.get("orderId"),
                        status=filled.get("status") or exit_reason,
                        metadata=exit_reason,
                    )
                    
                    # 상세 히스토리 기록
                    pnl_raw = (exit_price - state.entry_price) if state.entry_side == "LONG" else (state.entry_price - exit_price)
                    roi_pct = (pnl_raw / state.entry_price) * 100
                    pnl_usdt = pnl_raw * qty
                    
                    log_completed_trade(
                        Path("logs") / "trade_history.csv",
                        symbol=symbol,
                        side=state.entry_side,
                        quantity=qty,
                        entry_price=state.entry_price,
                        exit_price=exit_price,
                        entry_time=datetime.fromtimestamp(state.entry_time, tz=timezone.utc).isoformat(),
                        exit_time=_utc_now(),
                        pnl=pnl_usdt,
                        roi_pct=roi_pct,
                        exit_reason=exit_reason,
                    )
                    
                    # 수익/손실 여부에 따라 연속 손실 카운트 업데이트
                    if roi_pct < 0:
                        state.consecutive_losses += 1
                        print(f"[{_utc_now()}] 손실 발생({roi_pct:.2f}%). 연속 손실: {state.consecutive_losses}")
                    else:
                        state.consecutive_losses = 0
                        print(f"[{_utc_now()}] 수익 발생({roi_pct:.2f}%). 연속 손실 리셋.")

                # 상태 초기화
                state.in_position_symbol = None
                state.entry_price = None
                state.entry_time = None
                state.entry_side = None
                state.high_water_mark = None
                state.stop_loss_price = None
                state.take_profit_price = None
                state.last_trade_ts = now
                state.trades_done += 1

        else:
            # 포지션이 없으면 쿨다운 체크 후 진입 판단
            if settings.bot_max_trades and state.trades_done >= settings.bot_max_trades:
                print("BOT_MAX_TRADES 도달. 종료합니다.")
                return

            # 최대 동시 포지션 제한 체크
            if len(open_positions) >= settings.risk_max_open_positions:
                # 이미 최대 포지션이면 대기
                time.sleep(settings.bot_loop_seconds)
                continue

            if now - state.last_trade_ts < params.cooldown_seconds:
                time.sleep(settings.bot_loop_seconds)
                continue

            # 레버리지 설정(진입 전에만)
            if not settings.trading_dry_run:
                try:
                    set_futures_margin_type_isolated(client, symbol)
                    lev = params.leverage if settings.leverage_mode == "auto" else settings.trading_leverage
                    lev = max(settings.leverage_min, min(int(lev), settings.leverage_max))
                    set_futures_leverage(client, symbol, lev)
                except Exception:
                    pass

            df = fetch_futures_klines(
                client,
                symbol=symbol,
                interval=params.trading_interval,
                limit=settings.trading_limit,
            )
            signal, reason = breakout_volume_direction_signal(
                df,
                lookback=params.lookback,
                volume_mult=params.volume_mult,
                min_body_pct=params.min_body_pct,
                rsi_period=params.rsi_period,
                rsi_low=params.rsi_low,
                rsi_high=params.rsi_high,
                ema_fast_p=params.ema_fast,
                ema_slow_p=params.ema_slow,
                macd_fast=params.macd_fast,
                macd_slow=params.macd_slow,
                macd_signal=params.macd_signal,
            )

            if signal in {"LONG", "SHORT"}:
                side = "BUY" if signal == "LONG" else "SELL"
                mark = float(df["close"].iloc[-1])
                print(f"[{_utc_now()}] ENTER {symbol} {signal} qty={settings.trading_quantity} reason={reason}")

                if settings.trading_dry_run:
                    append_trade_log(
                        Path("logs") / "trades.csv",
                        symbol=symbol,
                        side=f"DRY_{side}",
                        quantity=settings.trading_quantity,
                        price=mark,
                        order_id=None,
                        status=reason,
                    )
                else:
                    filled = place_market_order_and_wait(
                        client,
                        symbol=symbol,
                        side=side,
                        quantity=settings.trading_quantity,
                        reduce_only=False,
                    )
                    entry_price = float(filled.get("avgPrice") or filled.get("price") or mark)
                    append_trade_log(
                        Path("logs") / "trades.csv",
                        symbol=symbol,
                        side=side,
                        quantity=settings.trading_quantity,
                        price=entry_price,
                        order_id=filled.get("orderId"),
                        status=filled.get("status") or "ENTER",
                        metadata=reason,
                    )

                    state.in_position_symbol = symbol
                    state.entry_price = entry_price
                    state.entry_time = now
                    state.entry_side = signal
                    state.high_water_mark = entry_price
                    
                    # ATR 기반 SL/TP 계산
                    atr_val = calculate_atr(df).iloc[-1]
                    if signal == "LONG":
                        state.stop_loss_price = entry_price - (atr_val * params.atr_multiplier_sl)
                        state.take_profit_price = entry_price + (atr_val * params.atr_multiplier_tp)
                    else:
                        state.stop_loss_price = entry_price + (atr_val * params.atr_multiplier_sl)
                        state.take_profit_price = entry_price - (atr_val * params.atr_multiplier_tp)
                    
                    print(f"[{_utc_now()}] ATR SL: {state.stop_loss_price:.2f}, TP: {state.take_profit_price:.2f}")

        time.sleep(settings.bot_loop_seconds)
