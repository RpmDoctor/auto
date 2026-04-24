"""
RpmDoctor Automated Trading Dashboard - Premium Recovery Edition
"""

from __future__ import annotations

import json
import time
import threading
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

# Core Imports
from config.settings import get_settings
from core.analytics import (
    add_derived_columns,
    futures_trades_to_df,
    summarize_futures_trades,
)
from core.client import create_binance_client
from core.health import run_health_checks
from core.watchlist import build_auto_watchlist
from core.params import load_params
from core.strategy import breakout_volume_direction_signal, calculate_rsi, calculate_atr
from core.data import fetch_futures_klines
from core.storage import load_report_snapshot

# --- PREMIUM UI ENHANCEMENT ---
def _apply_premium_style():
    st.markdown("""
        <style>
        .main {
            background-color: #0E1117;
        }
        .stMetric {
            background: rgba(255, 255, 255, 0.05);
            padding: 20px;
            border-radius: 12px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        .stTabs [data-baseweb="tab"] {
            font-weight: 700;
            font-size: 1.1rem;
            color: #E0E0E0;
        }
        .title-container {
            background: linear-gradient(135deg, #00FFC2 0%, #FFD700 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-size: 3rem;
            font-weight: 900;
            padding-bottom: 10px;
        }
        </style>
    """, unsafe_allow_html=True)

def _coin_name(symbol: str) -> str:
    COIN_KR = {
        "BTC": "비트코인", "ETH": "이더리움", "BNB": "바이낸스", "XRP": "리플",
        "SOL": "솔라나", "ADA": "에이다", "DOGE": "도지코인", "AVAX": "아발란체",
    }
    s = (symbol or "").upper()
    base = s[:-4] if s.endswith("USDT") else s
    return COIN_KR.get(base, base)

def format_duration(minutes: float) -> str:
    if minutes < 60: return f"{int(minutes)}m"
    h, m = int(minutes // 60), int(minutes % 60)
    return f"{h}h {m}m"

@st.cache_resource
def _get_client():
    return create_binance_client(get_settings())

# --- RENDERERS ---
def _render_trade_summary(settings):
    st.markdown("### 📊 최근 거래 요약")
    try:
        client = _get_client()
        trades_raw = client.futures_account_trades(limit=settings.dashboard_trades_limit)
        df = futures_trades_to_df(trades_raw)
        if df.empty:
            st.info("거래 내역이 없습니다.")
            return
        summary = summarize_futures_trades(add_derived_columns(df))
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("총 거래", f"{summary.trades_count}회")
        c2.metric("실현 수익", f"{summary.realized_pnl:.2f} USDT")
        c3.metric("승률", f"{(summary.win_rate*100):.1f}%")
        c4.metric("수수료", f"{summary.commission_usdt:.2f}")
        st.dataframe(df.sort_values("time", ascending=False), use_container_width=True)
    except Exception as e: st.error(f"거래 로드 실패: {e}")

def _render_positions():
    st.markdown("### 🎯 현재 포지션")
    try:
        client = _get_client()
        pos = [p for p in client.futures_position_information() if float(p.get("positionAmt", 0)) != 0]
        if not pos:
            st.info("진입한 포지션이 없습니다.")
            return
        for p in pos:
            amt = float(p.get("positionAmt", 0))
            pnl = float(p.get("unrealizedProfit", 0))
            c1, c2, c3 = st.columns(3)
            c1.metric(f"{p['symbol']} ({'LONG' if amt > 0 else 'SHORT'})", f"{abs(amt):g}")
            c2.metric("진입가", f"{float(p['entryPrice']):,.2f}")
            c3.metric("미실현 손익", f"{pnl:.2f} USDT", delta=f"{(pnl/float(p.get('isolatedWallet', 1))*100):.1f}%")
            st.divider()
    except Exception as e: st.error(f"포지션 로드 실패: {e}")

def _render_market_intel(watchlist):
    st.markdown("### 🌐 시장 인텔리전스")
    try:
        import requests
        r = requests.get("https://api.alternative.me/fng/", timeout=3).json()
        f_val, f_cls = r["data"][0]["value"], r["data"][0]["value_classification"]
        st.metric("공포/탐욕 지수", f"{f_val} ({f_cls})")
        st.progress(int(f_val)/100)
    except: pass
    st.markdown("#### 실시간 감시 종목")
    cols = st.columns(4)
    for i, s in enumerate(watchlist):
        cols[i % 4].write(f"- {s} ({_coin_name(s)})")

def _render_performance_report():
    st.markdown("### 🤖 전략 성과 리포트")
    try:
        metrics, trades = load_report_snapshot(30)
        if metrics:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("총 예상 수익", f"{metrics['total_pnl']:.2f}%")
            c2.metric("종합 승률", f"{metrics['win_rate']:.1f}%")
            c3.metric("최대 낙폭", f"{metrics['max_drawdown']:.2f}%")
            c4.metric("평균 보유시간", format_duration(metrics.get('avg_hold_duration', 0)))
            
            df_view = trades.copy()
            df_view["보유시간"] = (df_view["exit_time"] - df_view["entry_time"]).apply(format_duration)
            df_view["레버리지"] = df_view.get("leverage", 1).apply(lambda x: f"{int(x)}x")
            df_view["진입시간"] = pd.to_datetime(df_view["entry_time"], unit='ms', utc=True).dt.strftime('%m-%d %H:%M')
            
            st.dataframe(df_view[["symbol", "side", "레버리지", "진입시간", "보유시간", "entry_p", "exit_p", "pnl_pct", "reason"]].sort_values("진입시간", ascending=False), use_container_width=True)
            st.line_chart(trades.sort_values("exit_time")["pnl_pct"].cumsum())
    except Exception as e: st.error(f"리포트 로드 실패: {e}")

def main():
    st.set_page_config(page_title="RpmDoctor Dashboard", layout="wide", page_icon="🛡️")
    _apply_premium_style()
    
    st.markdown('<div class="title-container">RpmDoctor Automated Trading Dashboard</div>', unsafe_allow_html=True)
    st.caption("Advanced Autonomous Strategy Engine v23.44 Stable")
    
    settings = get_settings()
    
    # Error-safe watchlist
    watchlist = []
    try:
        if settings.watchlist_mode == "auto":
            wl_items = build_auto_watchlist(
                _get_client(),
                size=settings.watchlist_size,
                min_quote_volume_usdt=settings.watchlist_min_quote_usdt,
                max_volatility_pct_24h=settings.watchlist_max_vol_pct_24h,
                exclude_symbols=settings.watchlist_exclude_symbols,
                exclude_keywords=settings.watchlist_exclude_keywords
            )
            watchlist = [i.symbol for i in wl_items]
        else:
            watchlist = list(settings.watchlist_symbols)
    except Exception as e:
        st.warning(f"관심종목 로드 중 오류: {e}")
        watchlist = list(settings.watchlist_symbols)

    st.divider()
    
    # THE TABS (Crucial Re-entry)
    tabs = st.tabs(["거래", "포지션", "시장 인텔리전스", "전략 성과 리포트", "상태/로그", "매매 규칙"])
    
    with tabs[0]: _render_trade_summary(settings)
    with tabs[1]: _render_positions()
    with tabs[2]: _render_market_intel(watchlist)
    with tabs[3]: _render_performance_report()
    with tabs[4]:
        st.subheader("🏥 시스템 상태")
        log_path = Path("logs") / "app.log"
        if log_path.exists():
            st.code("\n".join(log_path.read_text(encoding="utf-8").splitlines()[-50:][::-1]))
    with tabs[5]:
        params = load_params(settings)
        st.markdown("### 현재 적용된 파라미터")
        st.json(vars(params))

if __name__ == "__main__":
    main()
