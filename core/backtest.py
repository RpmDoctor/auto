from __future__ import annotations

import pandas as pd
from typing import List, Dict, Any
from core.strategy import breakout_volume_direction_signal, calculate_atr
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
        trades = []
        in_pos = False
        side = None
        entry_p = 0.0
        high_water = 0.0
        entry_time = None
        entry_reason = ""

        sl_p = 0.0
        tp_p = 0.0
        
        # 수수료 설정
        fee_rate = 0.0005 # 편도 0.05%
        
        # 전략 파라미터
        ts = self.params.trailing_stop_pct / 100.0

        # 지표 계산을 위한 시작 인덱스
        start_idx = max(self.params.lookback, self.params.rsi_period, self.params.ema_slow) + 5
        
        if len(df) <= start_idx:
            return []

        for i in range(int(start_idx), len(df)):
            cur_df = df.iloc[: i + 1]
            cur_row = df.iloc[i]
            cur_p = float(cur_row["close"])
            cur_time = cur_row["open_time"]

            if not in_pos:
                sig, reason = breakout_volume_direction_signal(
                    cur_df,
                    lookback=self.params.lookback,
                    volume_mult=self.params.volume_mult,
                    min_body_pct=self.params.min_body_pct,
                    rsi_period=self.params.rsi_period,
                    rsi_low=self.params.rsi_low,
                    rsi_high=self.params.rsi_high,
                    ema_fast_p=self.params.ema_fast,
                    ema_slow_p=self.params.ema_slow,
                    macd_fast=self.params.macd_fast,
                    macd_slow=self.params.macd_slow,
                    macd_signal=self.params.macd_signal,
                )

                if sig in ["LONG", "SHORT"]:
                    in_pos = True
                    side = sig
                    entry_p = cur_p
                    high_water = cur_p
                    entry_time = cur_time
                    entry_reason = reason

                    # ATR 기반 SL/TP 계산
                    atr_series = calculate_atr(cur_df)
                    atr_val = float(atr_series.iloc[-1])
                    if sig == "LONG":
                        sl_p = entry_p - (atr_val * self.params.atr_multiplier_sl)
                        tp_p = entry_p + (atr_val * self.params.atr_multiplier_tp)
                    else:
                        sl_p = entry_p + (atr_val * self.params.atr_multiplier_sl)
                        tp_p = entry_p - (atr_val * self.params.atr_multiplier_tp)
            else:
                exit_reason = None
                pnl_pct = 0.0

                if side == "LONG":
                    high_water = max(high_water, cur_p)
                    if cur_p <= sl_p:
                        exit_reason = "ATR손절"
                    elif cur_p >= tp_p:
                        exit_reason = "ATR익절"
                    elif ts > 0 and cur_p <= high_water * (1 - ts) and cur_p > entry_p:
                        exit_reason = "트레일링"
                    
                    if exit_reason:
                        pnl_pct = (cur_p - entry_p) / entry_p - (fee_rate * 2)
                else: # SHORT
                    high_water = min(high_water, cur_p)
                    if cur_p >= sl_p:
                        exit_reason = "ATR손절"
                    elif cur_p <= tp_p:
                        exit_reason = "ATR익절"
                    elif ts > 0 and cur_p >= high_water * (1 + ts) and cur_p < entry_p:
                        exit_reason = "트레일링"
                    
                    if exit_reason:
                        pnl_pct = (entry_p - cur_p) / entry_p - (fee_rate * 2)

                if exit_reason:
                    trades.append({
                        "symbol": symbol,
                        "entry_time": entry_time,
                        "exit_time": cur_time,
                        "side": side,
                        "entry_p": entry_p,
                        "exit_p": cur_p,
                        "pnl_pct": pnl_pct * 100,
                        "reason": exit_reason,
                        "entry_reason": entry_reason
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

    return {
        "total_trades": total_trades,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_pnl": avg_pnl,
        "max_drawdown": max_dd,
        "profit_factor": profit_factor
    }
