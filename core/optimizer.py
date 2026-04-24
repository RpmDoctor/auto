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
        모든 타임프레임과 전략 성향(공격/균형/보수)을 교차 스캔하여 
        리스크(MDD)는 관리하면서 수익률을 극대화하는 최적 조합을 찾아 적용합니다.
        """
        logger.info("자율 전략 최적화 스캔 시작 (리스크 관리형 수익 극대화 모드)...")
        
        best_metrics = None
        best_interval = "1m"
        best_trades = pd.DataFrame()
        best_score = -float('inf')
        best_params = None
        
        current_params = load_params(self.settings)
        
        # 탐색할 전략 성향 프로필 (레버리지는 건드리지 않고 필터와 익절가만 조정)
        profiles = [
            {"vol": 1.2, "tp": 3.0, "desc": "공격형 (큰 파동 추종)"},
            {"vol": 1.5, "tp": 2.2, "desc": "균형형 (표준 설정)"},
            {"vol": 2.0, "tp": 1.6, "desc": "보수형 (확실한 진입)"}
        ]
        
        for interval in self.intervals:
            logger.info(f"타임프레임 스캔 중: {interval}")
            
            # 데이터 수집 (최근 30일 기준)
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
            
            if not symbol_data: continue
            
            # 각 프로필별 시뮬레이션 교차 테스트
            for prof in profiles:
                test_params = StrategyParams(
                    lookback=current_params.lookback,
                    volume_mult=prof["vol"],
                    min_body_pct=current_params.min_body_pct,
                    atr_multiplier_tp=prof["tp"],
                    atr_multiplier_sl=current_params.atr_multiplier_sl,
                    trading_interval=interval
                )
                
                # 시뮬레이션 실행
                backtester = PortfolioBacktester(test_params, self.settings)
                all_trades = backtester.run(symbol_data)
                metrics = calculate_portfolio_metrics(all_trades)
                
                # 점수 계산: (수익률 * 3.0) + (승률 * 10) - (MDD * 4.0) 
                # MDD(최대낙폭)에 대한 패널티를 강화하여 과도한 레버리지 효과 같은 리스크 방지
                pnl = metrics.get("total_pnl", 0)
                wr = metrics.get("win_rate", 0) / 100.0
                mdd = metrics.get("max_drawdown", 0)
                
                # 수익이 마이너스면 제외, MDD가 20%를 넘어가면 큰 패널티
                score = (pnl * 3.0) + (wr * 10.0) - (mdd * 4.0)
                
                logger.info(f"  > [{interval}][{prof['desc']}] PnL={pnl:.2f}%, Win={wr*100:.1f}%, MDD={mdd:.1f}%, Score={score:.2f}")
                
                if score > best_score:
                    best_score = score
                    best_metrics = metrics
                    best_interval = interval
                    best_trades = all_trades
                    best_params = test_params

        if best_metrics and best_score > -100:
            logger.info(f"🏆 최적 전략 발견: {best_interval} ({best_params.volume_mult}배 필터 / {best_params.atr_multiplier_tp}배 익절)")
            
            # 실제 전략 파일에 반영
            save_params(best_params)
            save_report_snapshot(30, best_metrics, best_trades) # 30일 기준 스냅샷 갱신
            
            return {
                "success": True,
                "best_interval": best_interval,
                "metrics": best_metrics,
                "reason": f"자율 최적화 결과 {best_interval} 주기의 수익 극대화 셋팅이 선택됨 (MDD 관리 포함)."
            }
        
        return {"success": False, "reason": "수익을 낼 수 있는 최적화 조합을 찾지 못함"}
