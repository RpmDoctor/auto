"""
RpmDoctor™ Intelligence Autonomous Dashboard - Premium Edition (v23.44 Stable Core)
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

# --- PREMIUM CSS STYLING ---
def _apply_premium_style():
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=JetBrains+Mono:wght@400;700&display=swap');
        
        :root {
            --primary: #00FFC2;
            --secondary: #FFD700;
            --bg-dark: #0E1117;
            --card-bg: #1A1C24;
            --text: #E0E0E0;
        }
        
        .main {
            background-color: var(--bg-dark);
            font-family: 'Inter', sans-serif;
        }
        
        .stMetric {
            background: rgba(255, 255, 255, 0.03);
            padding: 15px;
            border-radius: 10px;
            border: 1px solid rgba(255, 255, 255, 0.05);
        }
        
        h1, h2, h3 {
            font-weight: 800 !important;
            letter-spacing: -0.5px;
        }
        
        .brand-title {
            background: linear-gradient(90deg, #00FFC2, #FFD700);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-size: 2.5rem;
            font-weight: 900;
            margin-bottom: 0.5rem;
        }
        
        .status-badge {
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.8rem;
            font-weight: 700;
            text-transform: uppercase;
        }
        
        .trade-row {
            padding: 10px;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }
        
        /* Streamlit Tab Customization */
        .stTabs [data-baseweb="tab-list"] {
            gap: 20px;
        }
        .stTabs [data-baseweb="tab"] {
            height: 50px;
            white-space: pre-wrap;
            background-color: transparent;
            border-radius: 4px 4px 0px 0px;
            gap: 1px;
            padding-top: 10px;
            padding-bottom: 10px;
            font-weight: 600;
        }
        </style>
    """, unsafe_allow_html=True)

# Helper functions
def _coin_name(symbol: str) -> str:
    COIN_KR = {
        "BTC": "비트코인", "ETH": "이더리움", "BNB": "바이낸스", "XRP": "리플",
        "SOL": "솔라나", "ADA": "에이다", "DOGE": "도지코인", "AVAX": "아발란체",
    }
    s = (symbol or "").upper()
    base = s[:-4] if s.endswith("USDT") else s
    return COIN_KR.get(base, base)

@st.cache_resource
def _get_client():
    return create_binance_client(get_settings())

def format_duration(minutes: float) -> str:
    if minutes < 60: return f"{int(minutes)}m"
    h, m = int(minutes // 60), int(minutes % 60)
    return f"{h}h {m}m"

# --- RENDER FUNCTIONS ---
def _render_header(settings):
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown('<p class="brand-title">RpmDoctor™ Intelligence</p>', unsafe_allow_html=True)
        st.markdown("### 💠 Autonomous Trading Protocol v2.3.44")
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        status = "🟢 ACTIVE" if settings.trading_enable else "🟡 STANDBY"
        st.markdown(f"**STATUS:** `{status}`")
        st.markdown(f"**NETWORK:** `{'TESTNET' if settings.use_testnet else 'MAINNET'}`")

def _render_trade_summary(settings):
    st.markdown("## 📊 실거래 모니터링")
    try:
        client = _get_client()
        trades_raw = client.futures_account_trades(limit=settings.dashboard_trades_limit)
        df = futures_trades_to_df(trades_raw)
        df = add_derived_columns(df)
        
        if df.empty:
            st.info("현재 기록된 실거래 데이터가 없습니다.")
            return
            
        summary = summarize_futures_trades(df)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("총 거래수", f"{summary.trades_count}회")
        m2.metric("실현 수익", f"{summary.realized_pnl:,.2f} USDT")
        m3.metric("승률", f"{(summary.win_rate*100):.1f}%")
        m4.metric("평균 수익률", f"{(summary.avg_pnl_pct*100):.2f}%")
        
        st.dataframe(df.sort_values("time", ascending=False), use_container_width=True)
    except Exception as e: st.error(f"거래 로드 오류: {e}")

def _render_positions():
    st.markdown("## 🎯 라이브 포지션")
    try:
        client = _get_client()
        pos_info = client.futures_position_information()
        active = [p for p in pos_info if float(p.get("positionAmt", 0)) != 0]
        
        if not active:
            st.info("현재 진입한 포지션이 없습니다. 최적의 시그널을 탐색 중입니다.")
            return
            
        for p in active:
            amt = float(p.get("positionAmt", 0))
            side = "LONG" if amt > 0 else "SHORT"
            pnl = float(p.get("unrealizedProfit", 0))
            roe = pnl / float(p.get("isolatedWallet", 1)) * 100
            
            with st.container():
                c1, c2, c3, c4 = st.columns(4)
                c1.markdown(f"#### {p['symbol']}\n`{side}`")
                c2.metric("포지션 규모", f"{abs(amt):g}")
                c3.metric("진입/현재가", f"{float(p['entryPrice']):,.2f}", delta=f"{float(p['markPrice']):,.2f}")
                c4.metric("수익률 (ROE)", f"{roe:.2f}%", delta=f"{pnl:,.2f} USDT")
                st.divider()
    except Exception as e: st.error(f"포지션 로드 오류: {e}")

@st.cache_data(ttl=3600)
def _get_fear_and_greed():
    import requests
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=5)
        return r.json()["data"][0]["value"], r.json()["data"][0]["value_classification"]
    except: return None, "N/A"

def _render_market_intel(watchlist):
    st.markdown("## 🌐 마켓 인텔리전스 센터")
    f_val, f_cls = _get_fear_and_greed()
    
    col1, col2 = st.columns([1, 2])
    with col1:
        if f_val:
            st.metric("공포/탐욕 지수", f"{f_val}", delta=f_cls)
            st.progress(int(f_val)/100)
    with col2:
        st.markdown("#### 🔍 실시간 감시 리스트")
        st.caption("자율 엔진이 선정한 고변동성/고유동성 종목군")
        cols = st.columns(3)
        for idx, s in enumerate(watchlist):
            cols[idx % 3].markdown(f"- **{s}** ({_coin_name(s)})")

def _render_performance_report():
    st.markdown("## 🤖 전략 시뮬레이션 성과 보고서")
    try:
        metrics, trades = load_report_snapshot(30)
        if not metrics or trades.empty:
            st.warning("데이터 분석이 진행 중입니다. 잠시만 기다려 주세요.")
            return
            
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("예상 수익률", f"{metrics['total_pnl']:.2f}%")
        c2.metric("종합 승률", f"{metrics['win_rate']:.1f}%")
        c3.metric("Profit Factor", f"{metrics.get('profit_factor', 0):.2f}")
        c4.metric("최대 낙폭(MDD)", f"{metrics['max_drawdown']:.2f}%")
        
        st.markdown("#### 📜 상세 거래 이력")
        df_view = trades.copy()
        df_view["보유시간"] = (df_view["exit_time"] - df_view["entry_time"]).apply(format_duration)
        df_view["진입시간"] = pd.to_datetime(df_view["entry_time"], unit='ms', utc=True).dt.strftime('%m-%d %H:%M')
        df_view["레버리지"] = df_view.get("leverage", 1).apply(lambda x: f"{int(x)}x")
        
        display_cols = ["symbol", "side", "레버리지", "진입시간", "보유시간", "entry_p", "exit_p", "pnl_pct", "reason"]
        st.dataframe(df_view[display_cols].sort_values("진입시간", ascending=False), use_container_width=True)
        
        st.markdown("#### 📈 성과 곡선")
        st.line_chart(trades.sort_values("exit_time")["pnl_pct"].cumsum())
    except Exception as e: st.error(f"리포트 로드 오류: {e}")

def main():
    st.set_page_config(page_title="RpmDoctor™ Intelligence Dashboard", layout="wide", page_icon="🛡️")
    _apply_premium_style()
    
    settings = get_settings()
    _render_header(settings)
    
    # Watchlist logic
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
    
    st.divider()
    
    # Premium Tab Layout
    tabs = st.tabs(["📊 거래 데스크", "🎯 라이브 포지션", "🌐 마켓 인텔리전스", "🤖 전략 성과 리포트", "🏥 시스템 로그", "📏 매매 규칙"])
    
    with tabs[0]: _render_trade_summary(settings)
    with tabs[1]: _render_positions()
    with tabs[2]: _render_market_intel(watchlist)
    with tabs[3]: _render_performance_report()
    with tabs[4]:
        st.subheader("🏥 시스템 헬스 및 실시간 로그")
        # 로그 렌더링 로직 (v23.44 복구)
        log_path = Path("logs") / "app.log"
        if log_path.exists():
            logs = log_path.read_text(encoding="utf-8").splitlines()[-50:]
            st.code("\n".join(logs[::-1]), language="text")
    with tabs[5]:
        params = load_params(settings)
        st.markdown(f"### 📏 Active Trading Rules ({params.trading_interval})")
        st.info(f"봇이 현재 시장을 **{params.trading_interval}** 주기로 분석하며, **{params.leverage}x** 레버리지를 사용하도록 설정되었습니다.")
        st.json(vars(params))

if __name__ == "__main__":
    main()
