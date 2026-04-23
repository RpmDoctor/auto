from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from binance.client import Client

from config.settings import Settings
from core.analytics import add_derived_columns, build_close_events, futures_trades_to_df
from core.params import StrategyParams, save_params


UPDATES_LOG = Path("logs") / "param_updates.csv"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_update_log(before: StrategyParams, after: StrategyParams, reason: str) -> None:
    UPDATES_LOG.parent.mkdir(parents=True, exist_ok=True)
    write_header = not UPDATES_LOG.exists()
    with UPDATES_LOG.open("a", newline="", encoding="utf-8-sig") as f:
        fieldnames = ["timestamp", "reason", "before", "after"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            w.writeheader()
        w.writerow(
            {
                "timestamp": _utc_now(),
                "reason": reason,
                "before": str(asdict(before)),
                "after": str(asdict(after)),
            }
        )


def _fetch_recent_fills_df(client: Client, limit: int = 1000) -> pd.DataFrame:
    """
    계정 전체 체결내역(최근)에서 분석용 DataFrame 생성.
    """
    raw = client.futures_account_trades(limit=limit)
    df = futures_trades_to_df(raw if isinstance(raw, list) else [raw])
    return add_derived_columns(df)


def maybe_auto_tune_params(
    client: Client,
    settings: Settings,
    params: StrategyParams,
    *,
    min_closed_trades: int = 20,
    window: int = 50,
) -> StrategyParams:
    """
    데이터가 쌓이면(최근 청산 이벤트 min_closed_trades 이상) 아주 보수적으로 파라미터를 자동 조정합니다.

    목표:
    - 손실/슬리피지 큰 종목을 피하고, 신호를 조금 더/덜 까다롭게 조절
    - 큰 변화는 금지(소폭만)
    """
    try:
        fills_df = _fetch_recent_fills_df(client, limit=1000)
        closes = build_close_events(fills_df)
    except Exception:
        return params

    if closes.empty:
        return params

    closes = closes.head(window)
    if len(closes) < min_closed_trades:
        return params

    # 승률(순손익 > 0) 기준
    net = pd.to_numeric(closes.get("net_pnl"), errors="coerce").fillna(0.0)
    wins = int((net > 0).sum())
    total = int(len(net))
    win_rate = wins / total if total else 0.0

    new = StrategyParams(**asdict(params))
    reason_parts = []

    # 1) 진입 필터 조정 (거래량 및 몸통 크기)
    if win_rate < 0.40:
        new.volume_mult = min(new.volume_mult + 0.1, 3.0)
        new.min_body_pct = min(new.min_body_pct + 0.01, 0.5)
        reason_parts.append(f"win_rate={win_rate:.2f} 낮음 → 진입조건 강화")
    elif win_rate > 0.65:
        new.volume_mult = max(new.volume_mult - 0.05, 1.0)
        new.min_body_pct = max(new.min_body_pct - 0.005, 0.01)
        reason_parts.append(f"win_rate={win_rate:.2f} 높음 → 진입조건 완화")

    # 2) ATR 기반 SL/TP 비율 조정
    # 승률이 낮으면 SL을 좁히거나 TP를 넓히는 식의 조정 (여기선 보수적으로 TP/SL 비율 상향 시도)
    if win_rate < 0.45:
        # 손절은 조금 더 타이트하게, 익절은 조금 더 멀리
        new.atr_multiplier_sl = max(new.atr_multiplier_sl - 0.1, 1.0)
        new.atr_multiplier_tp = min(new.atr_multiplier_tp + 0.2, 5.0)
        reason_parts.append("SL 타이트/TP 확대")
    elif win_rate > 0.70:
        # 이미 잘 벌고 있으면 이익 실현을 더 빠르게
        new.atr_multiplier_tp = max(new.atr_multiplier_tp - 0.1, 1.5)
        reason_parts.append("익절 타겟 소폭 하향(안정화)")

    # 3) 레버리지 자동 조절
    if settings.leverage_mode == "auto":
        cur = int(new.leverage)
        cur = max(settings.leverage_min, min(cur, settings.leverage_max))

        last3 = net.head(3)
        last3_losses = int((last3 < 0).sum())

        if last3_losses >= 2 or win_rate < 0.40:
            nxt = max(settings.leverage_min, cur - 1)
            if nxt != cur:
                new.leverage = nxt
                reason_parts.append(f"leverage {cur}→{nxt}")
        elif win_rate > 0.60 and net.sum() > 0:
            nxt = min(settings.leverage_max, cur + 1)
            if nxt != cur:
                new.leverage = nxt
                reason_parts.append(f"leverage {cur}→{nxt}")

    # 변경 여부 확인 및 저장
    if reason_parts:
        reason = " | ".join(reason_parts)
        changed = (
            (new.volume_mult != params.volume_mult)
            or (new.min_body_pct != params.min_body_pct)
            or (new.atr_multiplier_sl != params.atr_multiplier_sl)
            or (new.atr_multiplier_tp != params.atr_multiplier_tp)
            or (new.leverage != params.leverage)
        )
        if changed:
            save_params(new)
            _append_update_log(params, new, reason)
            return new

    return params


def get_symbol_performance_score(symbol: str, window: int = 20) -> float:
    """
    특정 종목의 최근 성과 점수 계산 (백테스트/실거래 결과 반영)
    기본값 1.0, 승률/ROI에 따라 가감.
    """
    history_path = Path("logs") / "trade_history.csv"
    if not history_path.exists():
        return 1.0
    
    try:
        df = pd.read_csv(history_path)
        sym_df = df[df["symbol"] == symbol].head(window)
        if sym_df.empty:
            return 1.0
        
        roi = pd.to_numeric(sym_df["roi_pct"], errors="coerce").fillna(0.0)
        win_rate = (roi > 0).mean()
        avg_roi = roi.mean()
        
        # 점수 = 승률 * 0.7 + (평균ROI/10) * 0.3 (단순 예시)
        score = (win_rate * 1.5) + (avg_roi / 2.0)
        return max(0.1, score)
    except Exception:
        return 1.0
