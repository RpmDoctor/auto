from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class PnlSummary:
    realized_pnl: float
    commission_usdt: float
    net_pnl: float
    win_rate: float | None
    trades_count: int
    realized_trades_count: int


def futures_trades_to_df(trades: list[dict[str, Any]]) -> pd.DataFrame:
    """
    Binance Futures account trades(list[dict]) → DataFrame 변환.
    """
    if not trades:
        return pd.DataFrame()

    df = pd.DataFrame(trades)

    # 숫자 컬럼들(문자열로 오는 경우가 많음)
    for col in ["price", "qty", "realizedPnl", "commission"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], unit="ms", errors="coerce")

    return df


def summarize_futures_trades(df: pd.DataFrame) -> PnlSummary:
    """
    체결내역 기반 요약(로컬 계산):
    - realizedPnl 합계
    - commission(USDT만) 합계
    - net = realized - commission_usdt
    - win_rate: realizedPnl != 0 인 건들 중 realizedPnl > 0 비율
    """
    if df.empty:
        return PnlSummary(
            realized_pnl=0.0,
            commission_usdt=0.0,
            net_pnl=0.0,
            win_rate=None,
            trades_count=0,
            realized_trades_count=0,
        )

    realized = float(df.get("realizedPnl", pd.Series(dtype=float)).fillna(0).sum())

    commission_usdt = 0.0
    if "commission" in df.columns and "commissionAsset" in df.columns:
        commission_usdt = float(
            df.loc[df["commissionAsset"] == "USDT", "commission"].fillna(0).sum()
        )

    realized_mask = df.get("realizedPnl", pd.Series(dtype=float)).fillna(0) != 0
    realized_count = int(realized_mask.sum()) if hasattr(realized_mask, "sum") else 0

    win_rate: float | None = None
    if realized_count > 0:
        wins = int((df.loc[realized_mask, "realizedPnl"] > 0).sum())
        win_rate = wins / realized_count

    return PnlSummary(
        realized_pnl=realized,
        commission_usdt=commission_usdt,
        net_pnl=realized - commission_usdt,
        win_rate=win_rate,
        trades_count=int(len(df)),
        realized_trades_count=realized_count,
    )


def add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    대시보드용 파생 컬럼 추가.
    - commission_usdt: USDT 수수료만
    - net_pnl: realizedPnl - commission_usdt (근사)
    - pnl_pct_of_notional: realizedPnl / (abs(qty)*price) * 100 (근사)
    """
    if df.empty:
        return df

    out = df.copy()

    if "commission" in out.columns and "commissionAsset" in out.columns:
        out["commission_usdt"] = 0.0
        out.loc[out["commissionAsset"] == "USDT", "commission_usdt"] = out.loc[
            out["commissionAsset"] == "USDT", "commission"
        ].fillna(0)
    else:
        out["commission_usdt"] = 0.0

    realized = out.get("realizedPnl", pd.Series([0.0] * len(out))).fillna(0)
    out["net_pnl"] = realized - out["commission_usdt"].fillna(0)

    # 근사 수익률(포지션 단위가 아니라 '해당 체결 notional' 대비)
    if {"qty", "price"}.issubset(set(out.columns)):
        notional = (out["qty"].abs() * out["price"]).replace(0, pd.NA)
        out["pnl_pct_of_notional"] = (realized / notional) * 100
    else:
        out["pnl_pct_of_notional"] = pd.NA

    return out


def build_close_events(df: pd.DataFrame) -> pd.DataFrame:
    """
    체결(fills) 리스트를 시간순으로 훑으면서 "청산(포지션 감소)" 이벤트를 뽑아냅니다.

    목적:
    - 사용자가 원하는 "언제/어떤 종목/롱숏/얼마에 들어가서(평단)/얼마에 나왔는지/수수료/수익률"
      를 로컬에서 추정해서 보기 쉽게 제공합니다.

    주의:
    - Binance가 제공하는 realizedPnl은 포지션 정리 시점의 실현손익이며,
      여기서는 entry/exit 가격 기반의 추정치도 같이 제공합니다.
    - 수수료는 commissionAsset이 USDT인 것만 합산(근사).
    """
    if df.empty:
        return pd.DataFrame()

    required = {"symbol", "side", "qty", "price", "time"}
    if not required.issubset(set(df.columns)):
        return pd.DataFrame()

    # 정렬 및 결측 처리
    fills = df.copy()
    fills = fills.sort_values("time", ascending=True)
    fills["qty"] = pd.to_numeric(fills["qty"], errors="coerce").fillna(0.0)
    fills["price"] = pd.to_numeric(fills["price"], errors="coerce").fillna(0.0)
    if "realizedPnl" in fills.columns:
        fills["realizedPnl"] = pd.to_numeric(fills["realizedPnl"], errors="coerce").fillna(0.0)
    else:
        fills["realizedPnl"] = 0.0

    if "commission_usdt" not in fills.columns:
        fills = add_derived_columns(fills)

    # 상태: (symbol, position_key) -> (qty_signed, avg_entry_price)
    # position_key는 hedge모드의 positionSide가 있으면 그걸 쓰고, 없으면 "BOTH".
    state: dict[tuple[str, str], dict[str, float]] = {}
    events: list[dict[str, Any]] = []

    def _pos_key(row: pd.Series) -> str:
        ps = row.get("positionSide")
        if isinstance(ps, str) and ps.strip():
            return ps.strip().upper()
        return "BOTH"

    def _signed_qty(row: pd.Series, pos_key: str) -> float:
        side = str(row["side"]).upper()
        qty = float(row["qty"])
        # one-way(BOTH): BUY=+ , SELL=-
        if pos_key == "BOTH":
            return qty if side == "BUY" else -qty
        # hedge: LONG 포지션은 BUY로 증가, SELL로 감소 / SHORT 포지션은 SELL로 증가(음수), BUY로 감소
        if pos_key == "LONG":
            return qty if side == "BUY" else -qty
        if pos_key == "SHORT":
            return -qty if side == "SELL" else qty
        return qty if side == "BUY" else -qty

    for _, row in fills.iterrows():
        symbol = str(row["symbol"]).upper()
        pos_key = _pos_key(row)
        key = (symbol, pos_key)
        st = state.setdefault(key, {"qty": 0.0, "avg": 0.0})

        s_qty = _signed_qty(row, pos_key)
        price = float(row["price"])
        if price <= 0 or s_qty == 0:
            continue

        prev_qty = float(st["qty"])
        prev_avg = float(st["avg"])

        # 포지션이 없으면 새로 열림(평단 설정)
        if prev_qty == 0:
            st["qty"] = s_qty
            st["avg"] = price
            continue

        # 같은 방향으로 증가 → 가중평균 평단 업데이트
        if (prev_qty > 0 and s_qty > 0) or (prev_qty < 0 and s_qty < 0):
            new_qty = prev_qty + s_qty
            st["avg"] = (abs(prev_qty) * prev_avg + abs(s_qty) * price) / abs(new_qty)
            st["qty"] = new_qty
            continue

        # 반대 방향 → 청산(포지션 감소) 발생
        close_qty = min(abs(prev_qty), abs(s_qty))
        direction = "LONG" if prev_qty > 0 else "SHORT"
        entry_price = prev_avg
        exit_price = price

        # 추정 PnL(수수료 제외)
        est_pnl = (exit_price - entry_price) * close_qty if direction == "LONG" else (entry_price - exit_price) * close_qty

        api_realized = float(row.get("realizedPnl", 0.0))
        fee = float(row.get("commission_usdt", 0.0))
        net = api_realized - fee

        notional = abs(entry_price) * close_qty
        roi_pct = (net / notional * 100.0) if notional > 0 else None

        events.append(
            {
                "time": row.get("time"),
                "symbol": symbol,
                "position_side": direction if pos_key == "BOTH" else pos_key,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "qty": close_qty,
                "commission_usdt": fee,
                "realized_pnl_api": api_realized,
                "net_pnl": net,
                "roi_pct": roi_pct,
                "est_pnl_from_prices": est_pnl,
                "order_id": row.get("orderId"),
                "side": row.get("side"),
            }
        )

        # 상태 업데이트: 포지션 감소/반전
        remaining = prev_qty + s_qty  # prev_qty와 반대 부호로 감소
        if remaining == 0:
            st["qty"] = 0.0
            st["avg"] = 0.0
        else:
            # 반전으로 넘어간 경우, 남은 수량은 새 포지션 방향이므로 평단은 exit 가격으로 시작
            if (prev_qty > 0 and remaining < 0) or (prev_qty < 0 and remaining > 0):
                st["qty"] = remaining
                st["avg"] = exit_price
            else:
                st["qty"] = remaining
                # 같은 방향이 남는 경우 평단 유지
                st["avg"] = prev_avg

    if not events:
        return pd.DataFrame()

    out = pd.DataFrame(events)
    # time 컬럼을 datetime으로 정리
    if "time" in out.columns:
        out["time"] = pd.to_datetime(out["time"], errors="coerce")
    return out.sort_values("time", ascending=False)
