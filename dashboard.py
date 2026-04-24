"""
RpmDoctor™ Autonomous Strategy Dashboard - The Definitive Restoration (v23.44.Full)
"""

from __future__ import annotations

import json
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
from core.watchlist import build_auto_watchlist
from core.params import load_params
from core.storage import load_report_snapshot

# Constants
USD_KRW = 1350.0  # KRW Conversion Rate

# Helper functions
def _coin_name(symbol: str) -> str:
    COIN_KR = {
        "BTC": "비트코인", "ETH": "이더리움", "BNB": "바이낸스", "XRP": "리플",
        "SOL": "솔라나", "ADA": "에이다", "DOGE": "도지코인", "AVAX": "아발란체",
    }
    s = (symbol or "").upper()
    base = s[:-4] if s.endswith("USDT") else s
    return COIN_KR.get(base, base)

def format_duration(minutes: float) -> str:
    if minutes < 60: return f"{minutes:.1f}분"
    h, m = int(minutes // 60), int(minutes % 60)
    return f"{h}시간 {m}분"

@st.cache_resource
def _get_client():
    return create_binance_client(get_settings())

# --- UI COMPONENTS ---

def _render_header_krw(acc):
    try:
        balance = float(acc.get('totalWalletBalance', 0))
        margin = float(acc.get('totalMarginBalance', 0))
        unrealized = float(acc.get('totalUnrealizedProfit', 0))
        
        c1, c2, c3 = st.columns([1, 1, 1])
        with st.container():
            st.markdown(
                f"<div style='text-align: right; font-size: 0.8rem; color: #888;'>"
                f"약 ₩{balance*USD_KRW:,.0f} &nbsp;&nbsp;&nbsp;&nbsp;&nbsp; "
                f"약 ₩{margin*USD_KRW:,.0f} &nbsp;&nbsp;&nbsp;&nbsp;&nbsp; "
                f"약 ₩{unrealized*USD_KRW:,.0f}</div>", 
                unsafe_allow_html=True
            )
    except: pass

def _render_trade_summary(settings):
    st.subheader("📊 전체 거래 내역 (진입/청산/포지션)")
    try:
        client = _get_client()
        trades_raw = client.futures_account_trades(limit=settings.dashboard_trades_limit)
        df = futures_trades_to_df(trades_raw)
        if df.empty:
            st.info("표시할 거래 내역이 없습니다.")
            return
        
        df = add_derived_columns(df)
        df_view = df.sort_values("time", ascending=False).copy()
        
        # 캡처본 컬럼 명칭 적용
        df_view.rename(columns={
            "symbol": "symbol", "side": "방향", "qty": "수량", 
            "price": "체결가", "realizedPnl": "실현손익", "time": "시간"
        }, inplace=True)
        
        st.dataframe(df_view, use_container_width=True)
    except Exception as e: st.error(f"거래 로드 실패: {e}")

def _render_performance_report(params):
    st.subheader("🤖 봇 자율 전략 분석 및 최적화 보고")
    st.markdown(f"봇이 백그라운드에서 스스로 분석하고 갱신한 최신 전략 성과 리포트입니다.")
    
    # 지능형 안내 박스 (캡처본 재현)
    st.info(f"💡 현재 봇은 {params.trading_interval} 주기를 최적으로 판단하여 자율 매매 중입니다.")
    
    try:
        # 멀티 기간 리포트 구현 (7, 14, 30일)
        periods = [7, 14, 30]
        rows = []
        last_trades = None
        
        for p in periods:
            m, t = load_report_snapshot(p)
            if m:
                rows.append({
                    "기간": f"최근 {p}일 성과",
                    "예상 수익": f"{m['total_pnl']:.2f}%",
                    "거래 횟수": f"{m['total_trades']}회",
                    "종합 승률": f"{m['win_rate']:.1f}%",
                    "평균 보유": f"{m.get('avg_hold_duration', 0):.1f}분",
                    "최대 리스크": f"{m['max_drawdown']:.2f}%"
                })
                if p == 30: last_trades = t
        
        if rows:
            # 상단 요약 메트릭 (14일 기준 캡처본 재현)
            m14, _ = load_report_snapshot(14)
            if m14:
                c1, c2, c3 = st.columns(3)
                c1.metric("최근 14일 예상 수익", f"{m14['total_pnl']:.2f}%")
                c2.metric("시뮬레이션 횟수", f"{m14['total_trades']}회")
                c3.metric("종합 승률", f"{m14['win_rate']:.1f}%")
            
            st.divider()
            st.markdown("#### 기간별 전략 성과 비교")
            st.table(pd.DataFrame(rows))
            
            if last_trades is not None and not last_trades.empty:
                st.markdown("#### 자율 최적화 모델 수익률 추이")
                st.line_chart(last_trades.sort_values("exit_time")["pnl_pct"].cumsum())
                
                st.markdown("#### 상세 매매 기록 (레버리지 반영)")
                df_trades = last_trades.copy()
                df_trades["보유시간"] = (df_trades["exit_time"] - df_trades["entry_time"]).apply(lambda x: f"{x:.1f}분")
                df_trades["진입시간"] = pd.to_datetime(df_trades["entry_time"], unit='ms', utc=True).dt.strftime('%m-%d %H:%M')
                df_trades["수익률(%)"] = df_trades["pnl_pct"].apply(lambda x: f"{x:.2f}%")
                df_trades["레버리지"] = df_trades.get("leverage", 1).apply(lambda x: f"{int(x)}x")
                
                cols = ["symbol", "side", "레버리지", "진입시간", "보유시간", "entry_p", "exit_p", "수익률(%)", "reason"]
                st.dataframe(df_trades[cols].sort_values("진입시간", ascending=False), use_container_width=True)
                
        else: st.warning("성과 데이터를 분석 중입니다...")
    except Exception as e: st.error(f"리포트 구성 실패: {e}")

def main():
    st.set_page_config(page_title="RpmDoctor Intelligence", layout="wide")
    settings = get_settings()
    params = load_params(settings)
    
    # 상단 KRW 표시 및 타이틀
    acc_info = {}
    try: acc_info = _get_client().futures_account()
    except: pass
    
    _render_header_krw(acc_info)
    
    st.title("🤖 봇 자율 전략 분석 및 최적화 보고")
    
    # 상단 계좌 메트릭
    if acc_info:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("지갑 잔고", f"{float(acc_info.get('totalWalletBalance', 0)):,.2f} USDT")
        c2.metric("마진 잔고", f"{float(acc_info.get('totalMarginBalance', 0)):,.2f} USDT")
        c3.metric("미실현 손익", f"{float(acc_info.get('totalUnrealizedProfit', 0)):,.2f} USDT")
        c4.metric("매매 가능", "YES" if acc_info.get("canTrade") else "NO")

    st.divider()

    # 관심 종목
    watchlist = []
    try:
        if settings.watchlist_mode == "auto":
            wl_items = build_auto_watchlist(_get_client(), size=settings.watchlist_size)
            watchlist = [i.symbol for i in wl_items]
        else: watchlist = list(settings.watchlist_symbols)
    except: watchlist = list(settings.watchlist_symbols)

    # 7개 탭 구성
    tabs = st.tabs(["거래", "포지션", "실시간 모니터링", "시장 인텔리전스", "전략 성과 리포트", "상태/로그", "진입/청산 조건"])
    
    with tabs[0]: _render_trade_summary(settings)
    with tabs[1]:
        st.subheader("🎯 현재 라이브 포지션")
        try:
            pos = [p for p in _get_client().futures_position_information() if float(p.get("positionAmt", 0)) != 0]
            if not pos: st.info("현재 오픈된 포지션이 없습니다.")
            for p in pos:
                st.write(f"**{p['symbol']}** | ROI: {float(p['unrealizedProfit']):.2f} USDT")
        except: pass
    with tabs[2]:
        st.subheader("📈 실시간 모니터링")
        st.info("전략 엔진이 감시 종목들의 실시간 시그널을 분석 중입니다.")
    with tabs[3]:
        st.subheader("🌐 시장 인텔리전스")
        cols = st.columns(4)
        for i, s in enumerate(watchlist): cols[i % 4].write(f"- {s} ({_coin_name(s)})")
    with tabs[4]: _render_performance_report(params)
    with tabs[5]:
        st.subheader("🏥 시스템 로그")
        log_path = Path("logs") / "app.log"
        if log_path.exists():
            st.code("\n".join(log_path.read_text(encoding="utf-8").splitlines()[-50:][::-1]))
    with tabs[6]:
        st.subheader("📏 진입/청산 조건")
        st.json(vars(params))

if __name__ == "__main__":
    main()
