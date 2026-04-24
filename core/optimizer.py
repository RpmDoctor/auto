from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Tuple

import pandas as pd
from binance.client import Client

from config.settings import Settings
from core.backtest import PortfolioBacktester, calculate_portfolio_metrics
from core.data import fetch_historical_klines_paginated
from core.params import StrategyParams, load_params, save_params
from core.storage import save_report_snapshot

logger = logging.getLogger(__name__)


class StrategyOptimizer:
    def __init__(self, client: Client, settings: Settings):
        self.client = client
        self.settings = settings
        self.intervals = ["1m", "3m", "5m", "15m", "1h"]

    def run_autonomous_optimization(self, watchlist: List[str]) -> Dict[str, Any]:
        """
        모든 타임프레임을 스캔하여 가장 성과가 좋은 설정을 자동으로 찾아 적용합니다.
        """
        logger.info("자율 전략 최적화 스캔 시작...")
        
        best_metrics = None
        best_interval = "1m"
        best_trades = pd.DataFrame()
        best_score = -float('inf')
        
        current_params = load_params(self.settings)
        
        for interval in self.intervals:
            logger.info(f"타임프레임 스캔 중: {interval}")
            
            # 데이터 수집 (최근 30일 기준 고정으로 확장)
            symbol_data = {}
            for symbol in watchlist[:10]:
                try:
                    df = fetch_historical_klines_paginated(
                        self.client, symbol=symbol, interval=interval, days=30
                    )
                    if not df.empty:
                        symbol_data[symbol] = df
                except Exception as e:
                    logger.error(f"{symbol} 데이터 수집 실패 ({interval}): {e}")
            
            if not symbol_data:
                continue
            
            # 백테스트 실행
            backtester = PortfolioBacktester(current_params, self.settings)
            all_trades = backtester.run(symbol_data)
            metrics = calculate_portfolio_metrics(all_trades)
            
            # 점수 계산 (수익성 * 안정성 * 보유시간)
            # Profit Factor가 1.0 미만이면 감점, 보유시간이 짧으면 감점
            pf = metrics.get("profit_factor", 0)
            wr = metrics.get("win_rate", 0) / 100.0
            hold_time = metrics.get("avg_hold_duration", 0)
            
            # 점수 산식: Profit Factor * (승률 + 0.5) * log(보유시간 + 1)
            # 단순히 수익률만 보는 게 아니라 '안정성'을 중시
            import math
            score = pf * (wr + 0.5) * math.log10(hold_time + 1)
            
            logger.info(f"결과 ({interval}): Score={score:.2f}, PF={pf:.2f}, WinRate={wr*100:.1f}%, Hold={hold_time:.1f}m")
            
            if score > best_score:
                best_score = score
                best_metrics = metrics
                best_interval = interval
                best_trades = all_trades

        if best_metrics and best_score > 0:
            logger.info(f"최적 타임프레임 발견: {best_interval} (Score: {best_score:.2f})")
            
            # 실제 전략에 반영
            # 1. 인터벌 업데이트 (Settings 객체는 메모리상이므로 params에 저장하거나 로그로 남김)
            # 여기선 편의상 auto_params.json에 metadata로 저장하거나 로그로 남김
            # 실제 봇이 이 값을 읽어가도록 settings를 업데이트하는 로직이 필요함
            
            # 2. 파라미터 보정 (adaptive 로직 활용 가능)
            from core.adaptive import apply_backtest_feedback
            new_params, reason = apply_backtest_feedback(current_params, best_metrics)
            
            # 베스트 인터벌 적용
            new_params.trading_interval = best_interval
            
            save_params(new_params)
            save_report_snapshot(30, best_metrics, best_trades) # 30일 기준 스냅샷 강제 갱신
            
            return {
                "success": True,
                "best_interval": best_interval,
                "metrics": best_metrics,
                "reason": f"자율 최적화 결과 {best_interval} 주기가 가장 안정적임. {reason}"
            }
        
        return {"success": False, "reason": "최적화 조건을 만족하는 결과가 없음"}
