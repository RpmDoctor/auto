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
        
        # Strictly Sequential Loop: 시그널은 i-1까지의 데이터로, 진입은 i의 시가로.
        for i in range(start_idx, len(df)):
            cur_open = df["open"].iloc[i]
            cur_time = df["open_time"].iloc[i]
            
            # i-1 시점의 데이터 (진입 판단용)
            idx_prev = i - 1
            
            if not in_pos:
                # i-1 봉이 마감된 시점의 지표들
                p_rsi = rsi.iloc[idx_prev]
                p_ema_f = ema_fast.iloc[idx_prev]
                p_ema_s = ema_slow.iloc[idx_prev]
                p_macd = macd.iloc[idx_prev]
                p_macd_s = macd_signal.iloc[idx_prev]
                
                p_close = close_series.iloc[idx_prev]
                p_open = df["open"].iloc[idx_prev]
                p_high = df["high"].iloc[idx_prev]
                p_low = df["low"].iloc[idx_prev]
                p_vol = vol_series.iloc[idx_prev]
                
                p_db_high = prev_high.iloc[idx_prev]
                p_db_low = prev_low.iloc[idx_prev]
                p_vol_avg = avg_vol.iloc[idx_prev]
                
                body_pct = abs(p_close - p_open) / p_open * 100.0 if p_open > 0 else 0
                vol_ok = p_vol > (p_vol_avg * self.params.volume_mult) if p_vol_avg > 0 else False
                
                is_bull = p_close > p_open
                is_bear = p_close < p_open
                
                # 시그널 판단 (i-1 봉 마감 기준)
                long_cond = (p_high > p_db_high) and vol_ok and is_bull and (body_pct >= self.params.min_body_pct) and (p_ema_f > p_ema_s) and (p_rsi < self.params.rsi_high) and (p_macd > p_macd_s)
                short_cond = (p_low < p_db_low) and vol_ok and is_bear and (body_pct >= self.params.min_body_pct) and (p_ema_f < p_ema_s) and (p_rsi > self.params.rsi_low) and (p_macd < p_macd_s)

                if long_cond:
                    in_pos, side = True, "LONG"
                elif short_cond:
                    in_pos, side = True, "SHORT"

                if in_pos:
                    # 진입은 i 봉의 시가(Open)에 수행 (미래 데이터 참조 0%)
                    entry_p = cur_open
                    high_water = entry_p
                    entry_time = cur_time
                    
                    c_atr = atr.iloc[idx_prev]
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
                    # 레버리지가 반영된 수익률 계산
                    leveraged_pnl = pnl_pct * self.params.leverage * 100
                    
                    trades.append({
                        "symbol": symbol,
                        "entry_time": entry_time,
                        "exit_time": cur_time,
                        "side": side,
                        "entry_p": entry_p,
                        "exit_p": cur_p,
                        "pnl_pct": leveraged_pnl,
                        "leverage": self.params.leverage,
                        "reason": exit_reason
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
