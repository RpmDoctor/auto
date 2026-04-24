"""
RpmDoctor™ Bot 자율 전략 분석 및 최적화 보고 시스템 (v23.44 원본 디자인 복원)
"""

from __future__ import annotations

import json
import time
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
from core.strategy import breakout_volume_direction_signal
from core.storage import load_report_snapshot

# Helper functions
def _coin_name(symbol: str) -> str:
    COIN_KR = {
        "BTC": "비트코인", "ETH": "이더리움", "BNB": "바이낸스", "XRP": "리플",
        "SOL": "솔라나", "ADA": "에이다", "DOGE": "도지코인", "AVAX": "아발란체",
        "DOT": "폴카닷", "LINK": "체인링크", "MATIC": "폴리곤", "TRX": "트론",
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

# --- RENDER FUNCTIONS ---

def _render_trade_summary(settings):
    st.subheader("📊 실거래 요약 및 내역")
    try:
        client = _get_client()
        trades_raw = client.futures_account_trades(limit=settings.dashboard_trades_limit)
        df = futures_trades_to_df(trades_raw)
        if df.empty:
            st.info("실제 거래 내역이 아직 없습니다.")
            return
        
        summary = summarize_futures_trades(add_derived_columns(df))
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("총 거래", f"{summary.trades_count}회")
        c2.metric("누적 수익", f"{summary.realized_pnl:.2f} USDT")
        c3.metric("승률", f"{(summary.win_rate*100):.1f}%")
        c4.metric("수수료", f"{summary.commission_usdt:.2f}")
        
        st.markdown("#### 최근 체결 리스트")
        df_view = df.sort_values("time", ascending=False).copy()
        df_view.rename(columns={
            "symbol": "심볼", "side": "방향", "qty": "수량", 
            "price": "체결가", "realizedPnl": "실현손익", "time": "시간"
        }, inplace=True)
        st.dataframe(df_view, use_container_width=True)
    except Exception as e: st.error(f"거래 로드 오류: {e}")

def _render_positions():
    st.subheader("🎯 현재 포지션 상황")
    try:
        client = _get_client()
        pos_info = client.futures_position_information()
        active = [p for p in pos_info if float(p.get("positionAmt", 0)) != 0]
        
        if not active:
            st.info("현재 진입된 포지션이 없습니다. (시그널 대기 중)")
            return
            
        for p in active:
            amt = float(p.get("positionAmt", 0))
            pnl = float(p.get("unrealizedProfit", 0))
            roe = pnl / float(p.get("isolatedWallet", 1)) * 100
            
            with st.container():
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("심볼/방향", f"{p['symbol']} ({'LONG' if amt > 0 else 'SHORT'})")
                c2.metric("수량", f"{abs(amt):g}")
                c3.metric("진입/현재가", f"{float(p['entryPrice']):,.2f}", delta=f"{float(p['markPrice']):,.2f}")
                c4.metric("수익률(ROE)", f"{roe:.2f}%", delta=f"{pnl:.2f} USDT")
                st.divider()
    except Exception as e: st.error(f"포지션 로드 오류: {e}")

def _render_market_intel(watchlist):
    st.subheader("🌐 시장 인텔리전스 보고서")
    try:
        import requests
        r = requests.get("https://api.alternative.me/fng/", timeout=3).json()
        f_val, f_cls = r["data"][0]["value"], r["data"][0]["value_classification"]
        st.markdown(f"#### 🎭 공포 & 탐욕 지수: **{f_val} ({f_cls})**")
        st.progress(int(f_val)/100)
    except: pass
    
    st.divider()
    st.markdown("#### 🔍 실시간 감시 종목 리스트")
    cols = st.columns(4)
    for i, s in enumerate(watchlist):
        cols[i % 4].write(f"- {s} ({_coin_name(s)})")

def _render_performance_report():
    st.subheader("🤖 봇 자율 전략 분석 및 최적화 보고")
    try:
        metrics, trades = load_report_snapshot(30)
        if metrics:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("총 예상 수익", f"{metrics['total_pnl']:.2f}%")
            m2.metric("종합 승률", f"{metrics['win_rate']:.1f}%")
            m3.metric("최대 낙폭", f"{metrics['max_drawdown']:.2f}%")
            m4.metric("평균 보유시간", format_duration(metrics.get('avg_hold_duration', 0)))
            
            st.markdown("#### 전체 거래 내역 (진입/청산/포지션)")
            df_view = trades.copy()
            df_view["보유시간"] = (df_view["exit_time"] - df_view["entry_time"]).apply(format_duration)
            df_view["레버리지"] = df_view.get("leverage", 1).apply(lambda x: f"{int(x)}x")
            df_view["진입시간"] = pd.to_datetime(df_view["entry_time"], unit='ms', utc=True).dt.strftime('%m-%d %H:%M')
            df_view["청산시간"] = pd.to_datetime(df_view["exit_time"], unit='ms', utc=True).dt.strftime('%m-%d %H:%M')
            
            # 캡처본과 동일한 컬럼 명칭 및 순서
            df_view.rename(columns={
                "symbol": "심볼", "side": "방향", "entry_p": "entry_p", 
                "exit_p": "exit_p", "pnl_pct": "수익률(%)", "reason": "사유"
            }, inplace=True)
            
            cols_order = ["심볼", "방향", "레버리지", "진입시간", "청산시간", "보유시간", "entry_p", "exit_p", "수익률(%)", "사유"]
            st.dataframe(df_view[cols_order].sort_values("진입시간", ascending=False), use_container_width=True)
            
            st.markdown("#### 누적 수익 곡선")
            st.line_chart(trades.sort_values("exit_time")["pnl_pct"].cumsum())
    except Exception as e: st.error(f"성과 리포트 로드 오류: {e}")

def main():
    st.set_page_config(page_title="RpmDoctor Bot Briefing Center", layout="wide")
    
    st.title("🤖 봇 자율 전략 분석 및 최적화 보고")
    st.caption("RpmDoctor™ 자율 전략 최적화 엔진이 탑재된 실시간 매매 모니터링 시스템")
    
    settings = get_settings()
    
    # 사이드바
    with st.sidebar:
        st.header("⚙️ 봇 상태 센터")
        st.write(f"🟢 **현재 상태**: {'작동 중' if settings.trading_enable else '대기 중'}")
        st.write(f"🏦 **모드**: {'테스트넷' if settings.use_testnet else '메인넷'}")
        st.write(f"🧪 **가상매매**: {'ON' if settings.trading_dry_run else 'OFF'}")
        st.divider()
        if st.button("새로고침 및 캐시 삭제"): st.cache_data.clear()

    # 계좌 정보 요약 (Metric 상단 배치)
    try:
        acc = _get_client().futures_account()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("지갑 잔고", f"{float(acc.get('totalWalletBalance', 0)):,.2f} USDT")
        c2.metric("마진 잔고", f"{float(acc.get('totalMarginBalance', 0)):,.2f} USDT")
        c3.metric("미실현 손익", f"{float(acc.get('totalUnrealizedProfit', 0)):,.2f} USDT")
        c4.metric("매매 가능", "YES" if acc.get("canTrade") else "NO")
    except: pass

    st.divider()

    # 관심 종목 로직
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
    except: watchlist = list(settings.watchlist_symbols)

    # 7개 탭 구성 (원본 복원)
    tabs = st.tabs(["거래", "포지션", "실시간 모니터링", "시장 인텔리전스", "전략 성과 리포트", "상태/로그", "진입/청산 조건"])
    
    with tabs[0]: _render_trade_summary(settings)
    with tabs[1]: _render_positions()
    with tabs[2]:
        st.subheader("📈 실시간 모니터링 시그널")
        st.info("전략 엔진이 감시 종목들의 실시간 시그널을 분석 중입니다.")
    with tabs[3]: _render_market_intel(watchlist)
    with tabs[4]: _render_performance_report()
    with tabs[5]:
        st.subheader("🏥 시스템 상태 및 로그")
        log_path = Path("logs") / "app.log"
        if log_path.exists():
            st.code("\n".join(log_path.read_text(encoding="utf-8").splitlines()[-50:][::-1]))
    with tabs[6]:
        st.subheader("📏 진입/청산 조건 (Rules)")
        params = load_params(settings)
        st.json(vars(params))

if __name__ == "__main__":
    main()
