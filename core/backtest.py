from __future__ import annotations

import pandas as pd
from typing import List, Dict, Any
from core.strategy import (
    breakout_volume_direction_signal,
    calculate_atr,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
)
from core.params import StrategyParams
from config.settings import Settings


class PortfolioBacktester:
    """
    여러 종목의 데이터를 시간순으로 시뮬레이션하여 통합 성과를 계산합니다.
    """

    def __init__(self, params: StrategyParams, settings: Settings):
        self.params = params
        self.settings = settings
        self.max_open_positions = settings.risk_max_open_positions

    def run(self, symbol_data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        각 종목별로 독립적인 시뮬레이션을 돌린 후, 전체 거래를 시간순으로 정렬하여 반환합니다.
        (심화: 동시 진입 제한 로직은 일단 단순화하여 모든 시그널을 기록하되, 
        나중에 자산 배분 시뮬레이션을 위해 확장 가능하게 구성)
        """
        all_trades = []
        for symbol, df in symbol_data.items():
            if df.empty or len(df) < 50:
                continue
            trades = self._run_single_backtest(symbol, df)
            all_trades.extend(trades)

        if not all_trades:
            return pd.DataFrame()

        result_df = pd.DataFrame(all_trades)
        # 진입 시간 순으로 정렬
        result_df = result_df.sort_values("entry_time")
        return result_df

    def _run_single_backtest(self, symbol: str, df: pd.DataFrame) -> List[Dict[str, Any]]:
        if df.empty:
            return []

        # 지표 선계산 (Vectorized)
        close_series = df["close"].astype(float)
        high_series = df["high"].astype(float)
        low_series = df["low"].astype(float)
        vol_series = df["volume"].astype(float)

        rsi = calculate_rsi(close_series, self.params.rsi_period)
        ema_fast = calculate_ema(close_series, self.params.ema_fast)
        ema_slow = calculate_ema(close_series, self.params.ema_slow)
        macd, macd_signal = calculate_macd(close_series, self.params.macd_fast, self.params.macd_slow, self.params.macd_signal)
        atr = calculate_atr(df, 14) # Default period 14
        
        # Donchian Channels (Breakout)
        lookback = self.params.lookback
        prev_high = high_series.shift(1).rolling(window=lookback).max()
        prev_low = low_series.shift(1).rolling(window=lookback).min()
        avg_vol = vol_series.shift(1).rolling(window=lookback).mean()

        trades = []
        in_pos = False
        side = None
        entry_p = 0.0
        high_water = 0.0
        entry_time = None
        
        sl_p = 0.0
        tp_p = 0.0
        fee_rate = 0.0006 # 바이낸스 시장가 수수료 약 0.05%~0.06%
        slippage = 0.0005 # 시장가 슬리피지 약 0.05% 가정
        total_cost_rate = (fee_rate + slippage) * 2 # 왕복 비용 약 0.22%
        ts_pct = self.params.trailing_stop_pct / 100.0

        # 루프 시작점
        start_idx = int(max(lookback, self.params.rsi_period, self.params.ema_slow, 30))
        
        # 최적화된 루프
        for i in range(start_idx, len(df)):
            cur_p = close_series.iloc[i]
            cur_time = df["open_time"].iloc[i]

            if not in_pos:
                # 진입 조건 체크 (Pre-calculated values 사용)
                c_rsi = rsi.iloc[i]
                c_ema_f = ema_fast.iloc[i]
                c_ema_s = ema_slow.iloc[i]
                c_macd = macd.iloc[i]
                c_macd_s = macd_signal.iloc[i]
                
                c_open = df["open"].iloc[i]
                c_high = df["high"].iloc[i]
                c_low = df["low"].iloc[i]
                c_vol = vol_series.iloc[i]
                
                p_high = prev_high.iloc[i]
                p_low = prev_low.iloc[i]
                p_vol_avg = avg_vol.iloc[i]
                
                body_pct = abs(cur_p - c_open) / c_open * 100.0 if c_open > 0 else 0
                vol_ok = c_vol > (p_vol_avg * self.params.volume_mult) if p_vol_avg > 0 else False
                
                # Signal Logic (breakout_volume_direction_signal의 단순화 버전)
                is_bull = cur_p > c_open
                is_bear = cur_p < c_open
                
                long_cond = (c_high > p_high) and vol_ok and is_bull and (body_pct >= self.params.min_body_pct) and (c_ema_f > c_ema_s) and (c_rsi < self.params.rsi_high) and (c_macd > c_macd_s)
                short_cond = (c_low < p_low) and vol_ok and is_bear and (body_pct >= self.params.min_body_pct) and (c_ema_f < c_ema_s) and (c_rsi > self.params.rsi_low) and (c_macd < c_macd_s)

                if long_cond:
                    in_pos, side = True, "LONG"
                elif short_cond:
                    in_pos, side = True, "SHORT"

                if in_pos:
                    entry_p, high_water, entry_time = cur_p, cur_p, cur_time
                    c_atr = atr.iloc[i]
                    if side == "LONG":
                        sl_p = entry_p - (c_atr * self.params.atr_multiplier_sl)
                        tp_p = entry_p + (c_atr * self.params.atr_multiplier_tp)
                    else:
                        sl_p = entry_p + (c_atr * self.params.atr_multiplier_sl)
                        tp_p = entry_p - (c_atr * self.params.atr_multiplier_tp)
            else:
                # 청산 조건 체크
                exit_reason = None
                if side == "LONG":
                    high_water = max(high_water, cur_p)
                    if cur_p <= sl_p: exit_reason = "ATR손절"
                    elif cur_p >= tp_p: exit_reason = "ATR익절"
                    elif ts_pct > 0 and cur_p <= high_water * (1 - ts_pct) and cur_p > entry_p: exit_reason = "트레일링"
                    if exit_reason: pnl_pct = (cur_p - entry_p) / entry_p - total_cost_rate
                else: # SHORT
                    high_water = min(high_water, cur_p)
                    if cur_p >= sl_p: exit_reason = "ATR손절"
                    elif cur_p <= tp_p: exit_reason = "ATR익절"
                    elif ts_pct > 0 and cur_p >= high_water * (1 + ts_pct) and cur_p < entry_p: exit_reason = "트레일링"
                    if exit_reason: pnl_pct = (entry_p - cur_p) / entry_p - total_cost_rate

                if exit_reason:
                    trades.append({
                        "symbol": symbol, "entry_time": entry_time, "exit_time": cur_time,
                        "side": side, "entry_p": entry_p, "exit_p": cur_p,
                        "pnl_pct": pnl_pct * 100, "reason": exit_reason
                    })
                    in_pos = False
        return trades


def calculate_portfolio_metrics(trades_df: pd.DataFrame) -> Dict[str, Any]:
    """
    통합 거래 내역을 바탕으로 성과 지표를 계산합니다.
    """
    if trades_df.empty:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "max_drawdown": 0.0,
            "profit_factor": 0.0
        }

    total_trades = len(trades_df)
    wins = trades_df[trades_df["pnl_pct"] > 0]
    losses = trades_df[trades_df["pnl_pct"] <= 0]
    
    win_rate = (len(wins) / total_trades) * 100 if total_trades > 0 else 0
    total_pnl = trades_df["pnl_pct"].sum()
    avg_pnl = trades_df["pnl_pct"].mean()
    
    # MDD 계산 (단순 누적 수익률 기준)
    trades_df = trades_df.sort_values("exit_time")
    cum_pnl = trades_df["pnl_pct"].cumsum()
    peak = cum_pnl.expanding().max()
    drawdown = peak - cum_pnl
    max_dd = drawdown.max()
    
    # Profit Factor
    gross_profit = wins["pnl_pct"].sum()
    gross_loss = abs(losses["pnl_pct"].sum())
    profit_factor = gross_profit / gross_loss if gross_loss != 0 else float('inf')

    # 보유 기간 계산 (분 단위)
    trades_df["hold_duration"] = (trades_df["exit_time"] - trades_df["entry_time"]) / (1000 * 60)
    avg_hold_duration = trades_df["hold_duration"].mean()

    return {
        "total_trades": total_trades,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_pnl": avg_pnl,
        "max_drawdown": max_dd,
        "profit_factor": profit_factor,
        "avg_hold_duration": avg_hold_duration
    }
