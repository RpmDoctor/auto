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
    reason = None

    # 간단 규칙(보수적인 스텝)
    # - 승률 낮으면: 진입을 더 까다롭게(거래량 배수↑)
    # - 승률 높으면: 진입을 조금 완화(거래량 배수↓)
    if win_rate < 0.45:
        new.volume_mult = min(new.volume_mult + 0.1, 3.0)
        reason = f"win_rate={win_rate:.2f} 낮음 → STRAT_VOLUME_MULT +0.1"
    elif win_rate > 0.60:
        new.volume_mult = max(new.volume_mult - 0.05, 1.0)
        reason = f"win_rate={win_rate:.2f} 높음 → STRAT_VOLUME_MULT -0.05"

    # 레버리지 자동 조절(요구사항: 최대 5배, 불안하면 낮추기)
    if settings.leverage_mode == "auto":
        cur = int(new.leverage)
        cur = max(settings.leverage_min, min(cur, settings.leverage_max))

        last3 = net.head(3)  # closes는 최신순(이미 head(window) 후) 가정
        last3_losses = int((last3 < 0).sum())

        lev_reason = None
        if last3_losses >= 2 or win_rate < 0.45:
            nxt = max(settings.leverage_min, cur - 1)
            if nxt != cur:
                new.leverage = nxt
                lev_reason = f"최근손실/승률↓ → leverage {cur}→{nxt}"
        elif win_rate > 0.60 and net.sum() > 0:
            nxt = min(settings.leverage_max, cur + 1)
            if nxt != cur:
                new.leverage = nxt
                lev_reason = f"승률/수익↑ → leverage {cur}→{nxt}"

        if lev_reason:
            reason = f"{reason} | {lev_reason}" if reason else lev_reason

    # 너무 잦은 업데이트 방지: 값이 바뀐 경우만 기록/저장
    changed = (
        (new.volume_mult != params.volume_mult)
        or (new.leverage != params.leverage)
    )
    if reason and changed:
        save_params(new)
        _append_update_log(params, new, reason)
        return new

    return params
