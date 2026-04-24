"""
Streamlit 대시보드 (로컬)

실행:
  .\.venv\Scripts\python.exe -m streamlit run dashboard.py
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import streamlit as st
import threading
import time
import streamlit.components.v1 as components

from config.settings import get_settings
from core.analytics import (
    add_derived_columns,
    build_close_events,
    futures_trades_to_df,
    summarize_futures_trades,
)
from core.client import create_binance_client
from core.health import run_health_checks
from core.watchlist import build_auto_watchlist
from core.params import load_params
from core.strategy import breakout_volume_direction_signal, calculate_rsi, calculate_ema, calculate_atr
from core.data import fetch_futures_klines, fetch_historical_klines_paginated
from core.backtest import PortfolioBacktester, calculate_portfolio_metrics
from core.storage import save_report_snapshot, load_report_snapshot, get_latest_snapshot_info
from core.optimizer import StrategyOptimizer
from core.adaptive import apply_backtest_feedback
from core.params import save_params


_COIN_KR = {
    "BTC": "비트코인",
    "ETH": "이더리움",
    "BNB": "바이낸스코인",
    "XRP": "리플",
    "SOL": "솔라나",
    "ADA": "에이다",
    "DOGE": "도지코인",
    "AVAX": "아발란체",
    "DOT": "폴카닷",
    "LINK": "체인링크",
    "MATIC": "폴리곤",
    "TRX": "트론",
    "LTC": "라이트코인",
    "BCH": "비트코인캐시",
    "ATOM": "코스모스",
    "ETC": "이더리움클래식",
    "APT": "앱토스",
    "SUI": "수이",
    "OP": "옵티미즘",
    "AR": "아르위브",
}


def _coin_name(symbol: str) -> str:
    s = (symbol or "").upper()
    base = s[:-4] if s.endswith("USDT") else s
    return _COIN_KR.get(base, base or "-")


def _to_float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _fmt(value, decimals: int = 1, suffix: str = "") -> str:
    num = _to_float(value)
    if num is None:
        return "-"
    return f"{num:,.{decimals}f}{suffix}"


@dataclass(frozen=True)
class _FmtSpec:
    decimals: int
    suffix: str = ""


def _format_df(df: pd.DataFrame, specs: dict[str, _FmtSpec]) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    for col, spec in specs.items():
        if col in out.columns:
            out[col] = out[col].map(lambda v: _fmt(v, decimals=spec.decimals, suffix=spec.suffix))
    return out


def _fmt_krw_from_usdt(usdt_value, usdt_krw_rate: float | None) -> str | None:
    """
    참고용 원화 표기(약).
    """
    if not usdt_krw_rate or usdt_krw_rate <= 0:
        return None
    usdt = _to_float(usdt_value)
    if usdt is None:
        return None
    krw = usdt * usdt_krw_rate
    return f"약 ₩{krw:,.0f}"


@st.cache_resource
def _get_client():
    settings = get_settings()
    return create_binance_client(settings)


@st.cache_data(ttl=10)
def _get_account_summary() -> dict:
    return _get_client().futures_account()


@st.cache_data(ttl=10)
def _get_positions() -> list[dict]:
    return _get_client().futures_position_information()


@st.cache_data(ttl=10)
def _get_open_orders(symbol: str | None) -> list[dict]:
    client = _get_client()
    if symbol:
        return client.futures_get_open_orders(symbol=symbol)
    return client.futures_get_open_orders()


@st.cache_data(ttl=10)
def _get_account_trades(symbol: str | None, limit: int) -> pd.DataFrame:
    client = _get_client()
    params: dict = {"limit": limit}
    if symbol:
        params["symbol"] = symbol
    trades = client.futures_account_trades(**params)
    df = futures_trades_to_df(trades)
    return add_derived_columns(df)


@st.cache_data(ttl=60)
def _get_auto_watchlist_symbols() -> list[str]:
    settings = get_settings()
    if settings.watchlist_mode != "auto":
        return []
    items = build_auto_watchlist(
        _get_client(),
        size=settings.watchlist_size,
        min_quote_volume_usdt=settings.watchlist_min_quote_usdt,
        max_volatility_pct_24h=settings.watchlist_max_vol_pct_24h,
        exclude_symbols=settings.watchlist_exclude_symbols,
        exclude_keywords=settings.watchlist_exclude_keywords,
    )
    return [i.symbol for i in items]


@st.cache_data(ttl=10)
def _get_live_signals(symbols: list[str]) -> pd.DataFrame:
    client = _get_client()
    settings = get_settings()
    params = load_params(settings)
    
    results = []
    for s in symbols:
        try:
            df = fetch_futures_klines(client, symbol=s, interval=settings.trading_interval, limit=settings.trading_limit)
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
            
            close_series = df["close"].astype(float)
            rsi = calculate_rsi(close_series, params.rsi_period).iloc[-1]
            ema_fast = calculate_ema(close_series, params.ema_fast).iloc[-1]
            ema_slow = calculate_ema(close_series, params.ema_slow).iloc[-1]
            
            results.append({
                "심볼": s,
                "현재가": float(df["close"].iloc[-1]),
                "시그널": signal,
                "이유": reason,
                "RSI": round(rsi, 2),
                "EMA(단기/장기)": f"{ema_fast:.1f}/{ema_slow:.1f}",
                "추세": "정배열" if ema_fast > ema_slow else "역배열" if ema_fast < ema_slow else "혼조"
            })
        except Exception:
            continue
    return pd.DataFrame(results)


@st.cache_data(ttl=3600)
def _get_fear_and_greed():
    import requests
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=5)
        if r.status_code == 200:
            data = r.json()
            val = int(data["data"][0]["value"])
            classification = data["data"][0]["value_classification"]
            return val, classification
    except Exception:
        pass
    return 50, "Neutral"

@st.cache_data(ttl=300)
def _get_market_intel_data(symbols: list[str]):
    if not symbols: return None
    client = _get_client()
    settings = get_settings()
    params = load_params(settings)
    
    results = []
    for s in symbols[:15]: 
        try:
            df_1h = fetch_futures_klines(client, symbol=s, interval="1h", limit=100)
            df_4h = fetch_futures_klines(client, symbol=s, interval="4h", limit=100)
            
            if df_1h.empty or df_4h.empty: continue
            
            c_1h = df_1h["close"].astype(float)
            ema_fast_1h = calculate_ema(c_1h, params.ema_fast).iloc[-1]
            ema_slow_1h = calculate_ema(c_1h, params.ema_slow).iloc[-1]
            rsi_1h = calculate_rsi(c_1h, params.rsi_period).iloc[-1]
            
            c_4h = df_4h["close"].astype(float)
            ema_fast_4h = calculate_ema(c_4h, params.ema_fast).iloc[-1]
            ema_slow_4h = calculate_ema(c_4h, params.ema_slow).iloc[-1]
            
            atr = calculate_atr(df_1h, 14).iloc[-1]
            curr_price = c_1h.iloc[-1]
            
            results.append({
                "symbol": s,
                "trend_1h": "BULL" if ema_fast_1h > ema_slow_1h else "BEAR",
                "trend_4h": "BULL" if ema_fast_4h > ema_slow_4h else "BEAR",
                "rsi": rsi_1h,
                "vol_ratio": (atr / curr_price) * 100,
                "change_pct": ((curr_price - c_1h.iloc[0]) / c_1h.iloc[0]) * 100
            })
        except Exception: continue
    return results

def _render_market_intelligence(symbols: list[str]) -> None:
    st.subheader("🌐 미래 지향적 시장 전망 보고서")
    
    fng_val, fng_class = _get_fear_and_greed()
    
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Fear & Greed Index", f"{fng_val}", fng_class)
    
    with st.spinner("다각도 시장 분석 중..."):
        data = _get_market_intel_data(symbols)
    
    if not data:
        st.warning("분석 데이터 로딩 중...")
        return
    
    df = pd.DataFrame(data)
    bull_ratio_1h = (len(df[df["trend_1h"] == "BULL"]) / len(df)) * 100
    bull_ratio_4h = (len(df[df["trend_4h"] == "BULL"]) / len(df)) * 100

    with c2:
        st.metric("단기 심리 (1h)", f"{bull_ratio_1h:.0f}% Bull", "상승세" if bull_ratio_1h > 50 else "하락세")
    with c3:
        st.metric("중기 추세 (4h)", f"{bull_ratio_4h:.0f}% Bull", "안정적" if bull_ratio_4h > 50 else "불안정")
    with c4:
        avg_vol = df["vol_ratio"].mean()
        st.metric("변동성 (ATR)", f"{avg_vol:.2f}%", "폭발적" if avg_vol > 2 else "안정")

    st.divider()
    st.markdown("### 🕵️ 봇의 인공지능 분석 가이드")
    if bull_ratio_1h > 60 and bull_ratio_4h > 60:
        st.success("✅ **추세 완전 정렬 (Bull)**: 단기/중기가 모두 상승세입니다. 미래 전망이 매우 밝으며, 눌림목마다 매수(Long)가 유리한 구간입니다.")
    elif bull_ratio_1h < 40 and bull_ratio_4h < 40:
        st.error("🚨 **강한 하락 압력 (Bear)**: 단기/중기가 모두 꺾였습니다. 신규 진입에 매우 신중해야 하며, 당분간 하락세가 지속될 가능성이 높습니다.")
    elif bull_ratio_1h > 60 and bull_ratio_4h < 40:
        st.warning("⚠️ **기술적 반등 구간**: 중기 추세는 하락이나 단기적으로만 오르고 있습니다. '가짜 반등(Dead Cat)'일 확률이 있으니 추격 매수는 금물입니다.")
    elif bull_ratio_1h < 40 and bull_ratio_4h > 60:
        st.info("📉 **건전한 조정 구간**: 대세는 상승이나 단기적으로 과열을 식히는 중입니다. 주요 지지선에서 매수 기회를 엿볼 수 있는 미래 지향적 타점입니다.")
    else:
        st.write("⚖️ **방향성 탐색 중**: 현재 시장은 뚜렷한 방향 없이 힘을 모으는 중입니다. 큰 베팅보다는 짧은 매매로 대응하세요.")

def _render_forward_test() -> None:
    st.subheader("🧪 실시간 가상 시뮬레이션 (Forward Test)")
    st.caption("봇이 실시간 타임라인을 따라가며 가상으로 매매한 내역입니다.")
    
    import json
    v_pos_path = Path("data") / "virtual_position.json"
    history_path = Path("logs") / "trade_history.csv"
    
    if v_pos_path.exists():
        try:
            v_pos = json.loads(v_pos_path.read_text(encoding="utf-8"))
            if v_pos.get("symbol"):
                st.markdown("### 📡 현재 오픈된 가상 포지션")
                c1, c2, c3, c4, c5 = st.columns(5)
                entry = v_pos["entry_price"]
                mark = v_pos["mark_price"]
                side = v_pos["side"]
                pnl_pct = (mark - entry) / entry * 100 if side == "LONG" else (entry - mark) / entry * 100
                with c1: st.metric("심볼", v_pos["symbol"])
                with c2: st.metric("방향", side)
                with c3: st.metric("진입가", f"{entry:,.4f}")
                with c4: st.metric("현재가", f"{mark:,.4f}")
                with c5: st.metric("평가손익(%)", f"{pnl_pct:.2f}%", delta=f"{pnl_pct:.2f}%")
                st.divider()
        except Exception: pass
    
    if history_path.exists():
        try:
            df = pd.read_csv(history_path)
            if not df.empty:
                st.markdown("### 📜 실시간 가상 매매 히스토리")
                win_rate = (len(df[df["roi_pct"] > 0]) / len(df) * 100) if len(df) > 0 else 0
                m1, m2, m3 = st.columns(3)
                m1.metric("총 거래", f"{len(df)}회")
                m2.metric("승률", f"{win_rate:.1f}%")
                m3.metric("누적 수익률", f"{df['roi_pct'].sum():.2f}%")
                st.dataframe(df.iloc[::-1], width="stretch")
                st.markdown("### 📈 실시간 가상 수익 곡선")
                st.line_chart(df["roi_pct"].cumsum())
        except Exception: pass

def _render_trade_summary(symbol: str | None, limit: int) -> None:
    st.subheader("최근 거래 내역")
    try:
        df = _get_account_trades(symbol=symbol, limit=limit)
        if df.empty:
            st.info("거래 내역이 없습니다.")
            return
        
        summary = summarize_futures_trades(df)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("총 거래", f"{summary['total_count']}회")
        c2.metric("승률", f"{summary['win_rate']:.1f}%")
        c3.metric("총 실현익(USDT)", f"{summary['total_pnl']:.2f}")
        c4.metric("수수료(USDT)", f"{summary['total_commission']:.2f}")

        st.dataframe(df.sort_values("time", ascending=False), width="stretch")
    except Exception as e:
        st.error(f"거래 내역 조회 중 오류: {e}")

def _render_positions(symbol: str | None) -> None:
    st.subheader("현재 포지션(진입된 것만 표시)")
    try:
        all_pos = _get_positions()
        filtered = [p for p in all_pos if float(p.get("positionAmt", 0)) != 0]
        if symbol:
            filtered = [p for p in filtered if p.get("symbol") == symbol]
        
        if not filtered:
            st.info("포지션 정보가 없습니다.")
            return

        for p in filtered:
            with st.container():
                c1, c2, c3, c4 = st.columns(4)
                amt = float(p.get("positionAmt", 0))
                side = "LONG" if amt > 0 else "SHORT"
                pnl = float(p.get("unrealizedProfit", 0))
                roe = float(p.get("unrealizedProfit", 0)) / float(p.get("isolatedWallet", 1)) * 100
                c1.metric(f"{p['symbol']} ({side})", f"{abs(amt):g}")
                c2.metric("진입가", f"{float(p['entryPrice']):,.2f}")
                c3.metric("현재가", f"{float(p['markPrice']):,.2f}")
                c4.metric("미실현손익", f"{pnl:.2f}", f"{roe:.1f}%")
                st.divider()
    except Exception as e:
        st.error(f"포지션 조회 중 오류: {e}")

def _render_health() -> None:
    st.subheader("상태 점검")
    results = run_health_checks(_get_client(), get_settings())
    cols = st.columns(len(results))
    for i, r in enumerate(results):
        with cols[i]:
            if r.ok: st.success(f"**{r.name}**\n\nOK")
            else: st.error(f"**{r.name}**\n\nFAIL\n{r.detail}")

def _render_logs() -> None:
    st.subheader("최근 실행 로그")
    log_path = Path("logs") / "app.log"
    if log_path.exists():
        logs = log_path.read_text(encoding="utf-8").splitlines()[-100:]
        st.code("\n".join(logs[::-1]))

def _render_rules() -> None:
    st.subheader("📏 현재 적용된 매매 전략 규칙")
    settings = get_settings()
    params = load_params(settings)
    st.markdown(f"### 🤖 지능형 자율 엔진 상태: **{'활성화' if settings.optimizer_enable else '비활성화'}**")
    st.write(f"현재 봇은 **{params.trading_interval}** 주기를 기준으로 **{params.leverage}배** 레버리지를 사용 중입니다.")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 📥 진입 조건 (Entry)")
        st.write(f"- 거래량 필터: {params.volume_mult}배")
        st.write(f"- 캔들 몸통: {params.min_body_pct}%")
    with c2:
        st.markdown("#### 📤 청산 조건 (Exit)")
        st.write(f"- 손절/익절: ATR {params.atr_multiplier_sl}x / {params.atr_multiplier_tp}x")
        st.write(f"- 트레일링: {params.trailing_stop_pct}%")

def main() -> None:
    st.set_page_config(page_title="RpmDoctor Bot Briefing Center", layout="wide")
    st.title("🛡️ Bot Briefing Center")
    
    settings = get_settings()
    watchlist = _get_auto_watchlist_symbols() if settings.watchlist_mode == "auto" else list(settings.watchlist_symbols)
    
    tabs = st.tabs(["거래", "포지션", "실시간 모니터링", "시장 인텔리전스", "실시간 시뮬레이션", "전략 성과 리포트", "상태/로그", "진입/청산 조건"])

    with tabs[0]: _render_trade_summary(None, settings.dashboard_trades_limit)
    with tabs[1]: _render_positions(None)
    with tabs[2]:
        st.subheader("관심 종목 실시간 시그널 상태")
        if watchlist: st.dataframe(_get_live_signals(watchlist), width="stretch")
    with tabs[3]: _render_market_intelligence(watchlist)
    with tabs[4]: _render_forward_test()
    with tabs[5]:
        st.subheader("🤖 봇 자율 전략 분석 및 최적화 보고")
        try:
            metrics_30, all_trades = load_report_snapshot(30)
            if metrics_30:
                st.info(f"💡 최적 판단 주기: **{load_params(settings).trading_interval}**")
                st.metric("예상 수익", f"{metrics_30['total_pnl']:.2f}%")
                st.line_chart(all_trades.sort_values("exit_time")["pnl_pct"].cumsum())
        except Exception: st.warning("성과 데이터를 분석 중입니다...")
    with tabs[6]:
        _render_health()
        _render_logs()
    with tabs[7]: _render_rules()

if __name__ == "__main__":
    main()
