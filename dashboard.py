"""
RpmDoctor Bot Briefing Center - Premium Edition (23:44 Recovery)
"""

from __future__ import annotations

import json
import time
import threading
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# Core Imports
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
from core.data import fetch_futures_klines
from core.storage import load_report_snapshot
from core.backtest import calculate_portfolio_metrics
from core.optimizer import StrategyOptimizer

# UI Constants
_COIN_KR = {
    "BTC": "비트코인", "ETH": "이더리움", "BNB": "바이낸스코인", "XRP": "리플",
    "SOL": "솔라나", "ADA": "에이다", "DOGE": "도지코인", "AVAX": "아발란체",
    "DOT": "폴카닷", "LINK": "체인링크", "MATIC": "폴리곤", "TRX": "트론",
    "LTC": "라이트코인", "BCH": "비트코인캐시", "ATOM": "코스모스", "ETC": "이더리움클래식",
}

def _coin_name(symbol: str) -> str:
    s = (symbol or "").upper()
    base = s[:-4] if s.endswith("USDT") else s
    return _COIN_KR.get(base, base or "-")

def _fmt(value, decimals: int = 1, suffix: str = "") -> str:
    try:
        num = float(value)
        return f"{num:,.{decimals}f}{suffix}"
    except: return "-"

@st.cache_resource
def _get_client():
    return create_binance_client(get_settings())

@st.cache_data(ttl=10)
def _get_account_summary():
    return _get_client().futures_account()

@st.cache_data(ttl=10)
def _get_positions():
    return _get_client().futures_position_information()

@st.cache_data(ttl=10)
def _get_account_trades(symbol: str | None, limit: int):
    client = _get_client()
    params = {"limit": limit}
    if symbol: params["symbol"] = symbol
    trades = client.futures_account_trades(**params)
    df = futures_trades_to_df(trades)
    return add_derived_columns(df)

def _render_trade_summary(limit: int):
    st.subheader("📊 실거래 요약 및 내역")
    try:
        df = _get_account_trades(None, limit)
        if df.empty:
            st.info("실제 거래 내역이 아직 없습니다.")
            return
        summary = summarize_futures_trades(df)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("총 거래", f"{summary.trades_count}회")
        win_pct = (summary.win_rate * 100) if summary.win_rate is not None else 0
        c2.metric("승률", f"{win_pct:.1f}%")
        c3.metric("누적 수익(USDT)", f"{summary.realized_pnl:.2f}", delta=f"{summary.net_pnl:.2f} (Net)")
        c4.metric("수수료", f"{summary.commission_usdt:.2f}")
        
        st.markdown("#### 최근 체결 리스트")
        df_view = df.sort_values("time", ascending=False).copy()
        st.dataframe(df_view, width="stretch", height=400)
    except Exception as e: st.error(f"거래 로드 오류: {e}")

def _render_positions():
    st.subheader("🎯 현재 포지션 상황")
    try:
        all_p = _get_positions()
        active = [p for p in all_p if float(p.get("positionAmt", 0)) != 0]
        if not active:
            st.info("현재 진입된 포지션이 없습니다. (시그널 대기 중)")
            return
        for p in active:
            with st.container():
                c1, c2, c3, c4, c5 = st.columns(5)
                amt = float(p.get("positionAmt", 0))
                pnl = float(p.get("unrealizedProfit", 0))
                roe = pnl / float(p.get("isolatedWallet", 1)) * 100
                c1.metric("심볼/방향", f"{p['symbol']} ({'LONG' if amt > 0 else 'SHORT'})")
                c2.metric("수량", f"{abs(amt):g}")
                c3.metric("진입가", f"{float(p['entryPrice']):,.2f}")
                c4.metric("현재가", f"{float(p['markPrice']):,.2f}")
                c5.metric("수익률(ROE)", f"{roe:.1f}%", delta=f"{pnl:.2f} USDT")
                st.divider()
    except Exception as e: st.error(f"포지션 로드 오류: {e}")

@st.cache_data(ttl=300)
def _get_market_intel_data(symbols: list[str]):
    if not symbols: return None
    client = _get_client()
    settings = get_settings()
    params = load_params(settings)
    
    results = []
    # 15개 심볼까지만 분석하여 속도 확보
    for s in symbols[:15]:
        try:
            df = fetch_futures_klines(client, s, params.trading_interval, limit=100)
            if df.empty: continue
            sig = breakout_volume_direction_signal(df, params)
            rsi = calculate_rsi(df).iloc[-1]
            results.append({"symbol": s, "trend": sig, "rsi": rsi})
        except: continue
    return results

@st.cache_data(ttl=3600)
def _get_fear_and_greed():
    import requests
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=5)
        if r.status_code == 200:
            data = r.json()
            val = int(data["data"][0]["value"])
            cls = data["data"][0]["value_classification"]
            return val, cls
    except: pass
    return None, "N/A"

def _render_market_intelligence(symbols: list[str]):
    st.subheader("🌐 시장 인텔리전스 보고서")
    
    # 1. 공포/탐욕 지수
    fng_val, fng_cls = _get_fear_and_greed()
    if fng_val:
        st.markdown(f"#### 🎭 공포 & 탐욕 지수: **{fng_val} ({fng_cls})**")
        st.progress(fng_val / 100)
        
    intel = _get_market_intel_data(symbols)
    if not intel:
        st.info("시장 데이터를 수집 중입니다...")
        return
    
    c1, c2 = st.columns(2)
    with c1:
        longs = len([i for i in intel if i["trend"] == "LONG"])
        shorts = len([i for i in intel if i["trend"] == "SHORT"])
        total = len(intel)
        st.markdown("#### ⚖️ 시장 심리 (Long vs Short)")
        st.write(f"📈 롱 추세 종목: {longs}/{total} ({(longs/total*100):.1f}%)")
        st.write(f"📉 숏 추세 종목: {shorts}/{total} ({(shorts/total*100):.1f}%)")
        sentiment = "BULLISH" if longs > shorts else "BEARISH"
        st.success(f"현재 시장 주도권: **{sentiment}**")
        
    with c2:
        avg_rsi = sum([i["rsi"] for i in intel]) / len(intel)
        st.markdown("#### 🌡️ 시장 과열도 (Avg RSI)")
        st.metric("평균 RSI", f"{avg_rsi:.1f}", delta="과열" if avg_rsi > 70 else "침체" if avg_rsi < 30 else "중립")
        if avg_rsi > 60: st.warning("시장 전반이 과열 상태입니다. 눌림목 매수를 권장합니다.")
        elif avg_rsi < 40: st.info("시장 전반이 침체 상태입니다. 반등 매수를 고려해 보세요.")

def _render_rules():
    st.subheader("📏 현재 적용된 매매 전략 규칙 (Premium)")
    settings = get_settings()
    params = load_params(settings)
    
    # Optimizer 상태 가드 (AttributeError 방지)
    opt_status = getattr(settings, "optimizer_enable", False)
    st.markdown(f"### 🤖 지능형 자율 엔진 상태: **{'활성화' if opt_status else '비활성화'}**")
    
    st.info(f"현재 봇은 **{params.trading_interval}** 주기를 최적으로 판단하여 **{params.leverage}배** 레버리지를 사용 중입니다.")
    
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 📥 진입 조건 (Entry Logic)")
        st.write(f"🔹 **거래량 폭발**: 최근 {params.lookback}봉 평균 대비 **{params.volume_mult}배** 이상 시")
        st.write(f"🔹 **캔들 에너지**: 몸통 비율 **{params.min_body_pct}%** 이상의 장대봉 형성 시")
        st.write(f"🔹 **추세 필터**: EMA {params.ema_fast} / {params.ema_slow} 정배열(LONG) 또는 역배열(SHORT)")
    with c2:
        st.markdown("#### 📤 청산 조건 (Exit Logic)")
        st.write(f"🔸 **손절/익절**: ATR 기반 **{params.atr_multiplier_sl}x / {params.atr_multiplier_tp}x** 적용")
        st.write(f"🔸 **수익 보존**: 고점 대비 **{params.trailing_stop_pct}%** 트레일링 스탑")
        st.write(f"🔸 **시간 제한**: 진입 후 **{params.max_hold_seconds // 3600}시간** 경과 시 타임아웃")
    st.divider()
    st.caption("※ 위 규칙들은 봇이 24시간마다 시장 데이터를 학습하여 스스로 최적의 값으로 갱신합니다.")

def _render_health_and_logs():
    st.subheader("🏥 시스템 상태 및 로그")
    results = run_health_checks(_get_client(), get_settings())
    cols = st.columns(len(results))
    for i, r in enumerate(results):
        with cols[i]:
            if r.ok: st.success(f"**{r.name}**\n\nOK")
            else: st.error(f"**{r.name}**\n\nFAIL\n{r.detail}")
    
    st.divider()
    st.markdown("#### 최근 50개 실행 로그")
    log_path = Path("logs") / "app.log"
    if log_path.exists():
        logs = log_path.read_text(encoding="utf-8").splitlines()[-50:]
        st.code("\n".join(logs[::-1]), language="text")
    else: st.info("로그 파일이 없습니다.")

def format_duration(minutes: float) -> str:
    if minutes < 60: return f"{int(minutes)}m"
    h = int(minutes // 60)
    m = int(minutes % 60)
    return f"{h}h {m}m"

def main():
    st.set_page_config(page_title="RpmDoctor Bot Briefing Center", layout="wide")
    st.title("🛡️ Bot Briefing Center")
    st.caption("자율 전략 최적화 엔진이 탑재된 실시간 매매 모니터링 시스템 (v23.44 Recovery)")

    settings = get_settings()
    
    # Sidebar
    with st.sidebar:
        st.header("⚙️ 봇 상태 센터")
        st.write(f"🟢 **현재 상태**: {'작동 중' if settings.trading_enable else '대기 중'}")
        st.write(f"🏦 **모드**: {'테스트넷' if settings.use_testnet else '메인넷'}")
        st.write(f"🧪 **가상매매**: {'ON' if settings.trading_dry_run else 'OFF'}")
        st.divider()
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
        
        for s in watchlist: st.write(f"- {s} ({_coin_name(s)})")
        if st.button("새로고침 및 캐시 삭제"): st.cache_data.clear()

    # 계좌 요약
    try:
        acc = _get_account_summary()
        c1, c2, c3, c4 = st.columns(4)
        wallet = float(acc.get("totalWalletBalance", 0))
        margin = float(acc.get("totalMarginBalance", 0))
        unreal = float(acc.get("totalUnrealizedProfit", 0))
        c1.metric("지갑 잔고", f"{wallet:,.2f} USDT")
        c2.metric("마진 잔고", f"{margin:,.2f} USDT")
        c3.metric("미실현 손익", f"{unreal:,.2f} USDT", delta=f"{unreal:,.2f}")
        c4.metric("매매 가능 여부", "YES" if acc.get("canTrade") else "NO")
    except: st.error("계좌 정보를 가져올 수 없습니다.")

    # 6개 핵심 탭 구성 (복구 완료)
    tabs = st.tabs(["거래", "포지션", "시장 인텔리전스", "전략 성과 리포트", "상태/로그", "진입/청산 조건"])

    with tabs[0]: _render_trade_summary(settings.dashboard_trades_limit)
    with tabs[1]: _render_positions()
    with tabs[2]: _render_market_intelligence(watchlist)
    with tabs[3]:
        st.subheader("🤖 봇 자율 전략 분석 리포트")
        try:
            metrics_30, all_trades = load_report_snapshot(30)
            if metrics_30:
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("총 예상 수익", f"{metrics_30['total_pnl']:.2f}%")
                m2.metric("종합 승률", f"{metrics_30['win_rate']:.1f}%")
                m3.metric("최대 낙폭", f"{metrics_30['max_drawdown']:.2f}%")
                m4.metric("분석 거래수", f"{metrics_30['total_trades']}회")
                
                st.markdown("#### 전체 거래 내역 (레버리지/보유시간 반영)")
                view_trades = all_trades.copy()
                view_trades["보유시간"] = (view_trades["exit_time"] - view_trades["entry_time"]).apply(format_duration)
                view_trades["진입시간"] = pd.to_datetime(view_trades["entry_time"], unit='ms', utc=True).dt.strftime('%m-%d %H:%M')
                view_trades["청산시간"] = pd.to_datetime(view_trades["exit_time"], unit='ms', utc=True).dt.strftime('%m-%d %H:%M')
                view_trades["레버리지"] = view_trades.get("leverage", 1).apply(lambda x: f"{int(x)}x")
                
                cols = ["symbol", "side", "레버리지", "진입시간", "청산시간", "보유시간", "entry_p", "exit_p", "pnl_pct"]
                st.dataframe(view_trades[cols].sort_values("exit_time", ascending=False), width="stretch")
                
                st.markdown("#### 누적 수익 곡선")
                st.line_chart(all_trades.sort_values("exit_time")["pnl_pct"].cumsum())
            else: st.warning("성과 데이터를 분석 중입니다...")
        except Exception as e: st.error(f"성과 리포트 로드 오류: {e}")
        
    with tabs[4]: _render_health_and_logs()
    with tabs[5]: _render_rules()

if __name__ == "__main__":
    main()
