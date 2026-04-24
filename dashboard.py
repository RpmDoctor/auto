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
    # symbol 미지정이면 전체 조회 (지원되는 경우)
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
def _get_trading_symbols() -> list[str]:
    """
    선물 거래 가능한 심볼 목록(USDT 무기한 위주)을 가져옵니다.
    실패하면 빈 리스트를 반환하고, UI에서 수동 입력으로 대체합니다.
    """
    try:
        info = _get_client().futures_exchange_info()
        symbols: list[str] = []
        for s in info.get("symbols", []):
            if s.get("status") != "TRADING":
                continue
            if s.get("quoteAsset") != "USDT":
                continue
            if s.get("contractType") and s.get("contractType") != "PERPETUAL":
                continue
            symbols.append(str(s.get("symbol")))
        symbols = sorted(set(symbols))
        return symbols
    except Exception:
        return []


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
            # 단기(1h)와 중기(4h) 데이터를 동시에 가져옴
            df_1h = fetch_futures_klines(client, symbol=s, interval="1h", limit=100)
            df_4h = fetch_futures_klines(client, symbol=s, interval="4h", limit=100)
            
            if df_1h.empty or df_4h.empty: continue
            
            # 1h 분석
            c_1h = df_1h["close"].astype(float)
            ema_fast_1h = calculate_ema(c_1h, params.ema_fast).iloc[-1]
            ema_slow_1h = calculate_ema(c_1h, params.ema_slow).iloc[-1]
            rsi_1h = calculate_rsi(c_1h, params.rsi_period).iloc[-1]
            
            # 4h 분석 (장기 추세)
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
    
    # 상단 요약 카드 (공포지수 포함)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Fear & Greed Index", f"{fng_val}", fng_class)
    
    with st.spinner("다각도 시장 분석 중..."):
        data = _get_market_intel_data(symbols)
    
    if not data:
        st.warning("분석 데이터 로딩 중...")
        return
    
    df = pd.DataFrame(data)
    
    bull_1h = len(df[df["trend_1h"] == "BULL"])
    bull_4h = len(df[df["trend_4h"] == "BULL"])
    bull_ratio_1h = (bull_1h / len(df)) * 100
    bull_ratio_4h = (bull_4h / len(df)) * 100

    with c2:
        st.metric("단기 심리 (1h)", f"{bull_ratio_1h:.0f}% Bull", "상승세" if bull_ratio_1h > 50 else "하락세")
    with c3:
        st.metric("중기 추세 (4h)", f"{bull_ratio_4h:.0f}% Bull", "안정적" if bull_ratio_4h > 50 else "불안정")
    with c4:
        avg_vol = df["vol_ratio"].mean()
        st.metric("변동성 (ATR)", f"{avg_vol:.2f}%", "폭발적" if avg_vol > 2 else "안정")

    st.divider()
    
    # 지능형 코멘트 (미래 가이드)
    st.markdown("### 🕵️ 봇의 인공지능 분석 가이드")
    
    # 추세 정렬 분석
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

    st.divider()
    # TOP 3 요약
    col_l, col_r = st.columns(2)
    with col_l:
        st.markdown("📈 **주도주 (Leading Symbols)**")
        sorted_df = df.sort_values("change_pct", ascending=False)
        for _, row in sorted_df.head(3).iterrows():
            st.write(f"- {row['symbol']}: +{row['change_pct']:.2f}% (1h: {row['trend_1h']})")
    with col_r:
        st.markdown("📉 **낙폭주 (Lagging Symbols)**")
        for _, row in sorted_df.tail(3).iloc[::-1].iterrows():
            st.write(f"- {row['symbol']}: {row['change_pct']:.2f}% (1h: {row['trend_1h']})")

def _render_health() -> None:
    st.subheader("상태 점검")
    st.caption("지금 API 연결/권한/조회가 정상인지 확인하는 검사입니다.")

    settings = get_settings()
    results = run_health_checks(_get_client(), settings)

    all_ok = True
    for r in results:
        all_ok = all_ok and r.ok
        if r.ok:
            st.success(f"{r.name}: {r.detail}")
        else:
            st.error(f"{r.name}: {r.detail}")

    st.write(f"전체 결과: {'정상' if all_ok else '실패'}")


def _render_trade_summary(symbol: str | None, limit: int) -> None:
    st.subheader("거래 요약(선물)")
    st.caption("단위는 대부분 USDT(테더)입니다. (테스트넷도 동일)")

    settings = get_settings()
    st.caption(f"기본 레버리지(설정): {settings.trading_leverage}x / 마진모드: 격리(ISOLATED)")
    try:
        df = _get_account_trades(symbol=symbol, limit=limit)
    except Exception as e:
        st.error(str(e))
        return

    if df.empty:
        st.info("체결 내역이 없습니다.")
        return

    summary = summarize_futures_trades(df)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("실현손익 합(USDT)", _fmt(summary.realized_pnl, decimals=2))
        hint = _fmt_krw_from_usdt(summary.realized_pnl, settings.usdt_krw_rate)
        if hint:
            st.caption(hint)
    with c2:
        st.metric("수수료 합(USDT)", _fmt(summary.commission_usdt, decimals=2))
        hint = _fmt_krw_from_usdt(summary.commission_usdt, settings.usdt_krw_rate)
        if hint:
            st.caption(hint)
    with c3:
        st.metric("순손익(근사, USDT)", _fmt(summary.net_pnl, decimals=2))
        hint = _fmt_krw_from_usdt(summary.net_pnl, settings.usdt_krw_rate)
        if hint:
            st.caption(hint)
    with c4:
        st.metric("승률(실현손익≠0 기준)", "-" if summary.win_rate is None else f"{summary.win_rate * 100:.2f}%")

    closes = build_close_events(df)
    st.subheader("최근 청산 내역(체결 기반)")
    with st.expander("청산 내역 계산 방식(중요)", expanded=False):
        st.write(
            "- `진입평단`은 체결을 시간순으로 추적해서 포지션 평단을 로컬에서 계산한 값(추정)입니다.\n"
            "- `청산가`는 포지션을 줄이는(반대 방향) 체결의 가격입니다.\n"
            "- `순손익`은 `realizedPnl - (USDT 수수료)` 근사치입니다. (수수료 자산이 USDT가 아니면 완전 정확하진 않습니다.)"
        )

    if closes.empty:
        st.info("아직 '청산(포지션 감소)' 이벤트가 없습니다. (열기만 했거나, 아직 닫지 않은 상태)")
        return

    closes = closes.copy()
    # 시간은 기본적으로 UTC 기준으로 들어오는 경우가 많아, 한국시간(KST) 컬럼을 같이 제공합니다.
    if "time" in closes.columns:
        t_utc = pd.to_datetime(closes["time"], utc=True, errors="coerce")
        closes["time_utc"] = t_utc.dt.strftime("%Y-%m-%d %H:%M:%S")
        closes["time_kst"] = t_utc.dt.tz_convert("Asia/Seoul").dt.strftime("%Y-%m-%d %H:%M:%S")
    closes["coin_kr"] = closes["symbol"].map(_coin_name)

    # 과거 청산내역에 '당시 레버리지'는 API에서 직접 제공되지 않는 경우가 많아,
    # 일단 "설정 레버리지"와 "마진모드(격리)"를 같이 표기합니다.
    closes["leverage_setting"] = settings.trading_leverage
    closes["margin_mode"] = "ISOLATED"

    close_rows = max(1, int(settings.dashboard_close_rows))
    view = closes[
        [
            c
            for c in [
                "time_utc",
                "time_kst",
                "symbol",
                "coin_kr",
                "position_side",
                "leverage_setting",
                "margin_mode",
                "entry_price",
                "exit_price",
                "qty",
                "commission_usdt",
                "realized_pnl_api",
                "net_pnl",
                "roi_pct",
            ]
            if c in closes.columns
        ]
    ].head(close_rows)

    view = view.rename(
        columns={
            "time_utc": "시간(UTC)",
            "time_kst": "시간(한국)",
            "symbol": "심볼",
            "coin_kr": "종목명",
            "position_side": "롱/숏",
            "leverage_setting": "레버리지(설정)",
            "margin_mode": "마진모드",
            "entry_price": "진입평단",
            "exit_price": "청산가",
            "qty": "수량",
            "commission_usdt": "수수료(USDT)",
            "realized_pnl_api": "실현손익(USDT)",
            "net_pnl": "순손익(USDT)",
            "roi_pct": "수익률(%)",
        }
    )

    view = _format_df(
        view,
        specs={
            "진입평단": _FmtSpec(decimals=1),
            "청산가": _FmtSpec(decimals=1),
            "수량": _FmtSpec(decimals=3),
            "수수료(USDT)": _FmtSpec(decimals=2),
            "실현손익(USDT)": _FmtSpec(decimals=2),
            "순손익(USDT)": _FmtSpec(decimals=2),
            "수익률(%)": _FmtSpec(decimals=2),
        },
    )
    st.dataframe(view, width="stretch", height=360)


def _render_rules() -> None:
    st.subheader("🤖 봇 자율 전략 매뉴얼 (실시간 최적화 적용 중)")
    st.caption("아래 조건들은 봇이 30일치 데이터를 자가 학습하여 갱신한 최신 규칙입니다.")
    
    settings = get_settings()
    params = load_params(settings)

    # 현재 전략 성향 판별 로직
    profile_name = "균형형"
    profile_color = "blue"
    profile_desc = "표준적인 위험과 수익을 추구합니다."
    
    if params.volume_mult <= 1.3 and params.atr_multiplier_tp >= 2.5:
        profile_name = "공격형 (추세 추종)"
        profile_color = "red"
        profile_desc = "낮은 진입 장벽과 높은 익절가로 큰 추세를 먹으려 노력합니다."
    elif params.volume_mult >= 1.8:
        profile_name = "보수형 (확실한 타점)"
        profile_color = "green"
        profile_desc = "거래량이 크게 터지는 확실한 순간에만 진입하여 승률을 관리합니다."

    st.info(f"📍 **현재 전략 성향: :{profile_color}[{profile_name}]**\n\n{profile_desc}")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### 📥 진입 조건 (Entry)")
        st.write(f"**1. 매매 주기**: `{params.trading_interval}` (현재 최적 주기)")
        st.write(f"**2. 돌파 필터**: 직전 `{params.lookback}`개 봉의 고점/저점 돌파")
        st.write(f"**3. 거래량 필터**: 평균 거래량의 `{params.volume_mult:.2f}`배 이상 폭발 시")
        st.write(f"**4. 캔들 몸통**: 캔들 전체 대비 `{params.min_body_pct}%` 이상의 실체 확인")
        st.write(f"**5. 보조지표**: EMA 정배열(추세) 및 MACD 시그널 교차 확인")

    with col2:
        st.markdown("### 📤 청산 조건 (Exit)")
        st.write(f"**1. 목표 수익(TP)**: 진입가 ± (ATR × `{params.atr_multiplier_tp:.2f}`배)")
        st.write(f"**2. 손절 라인(SL)**: 진입가 ± (ATR × `{params.atr_multiplier_sl:.2f}`배)")
        st.write(f"**3. 트레일링 스탑**: 고점 대비 `{params.trailing_stop_pct}%` 하락 시 익절 보존")
        st.write(f"**4. 최대 보유 시간**: `{params.max_hold_seconds // 3600}`시간 (이후 자동 시장가 종료)")
        st.write(f"**5. 쿨다운**: 매매 종료 후 `{params.cooldown_seconds // 60}`분간 재진입 금지")

    st.divider()
    st.subheader("⚙️ 자가 발전 시스템 (Self-Learning)")
    st.write("- **데이터 기반 갱신**: 봇은 매일 30일치 데이터를 전수 조사하여 위 수치들을 자동으로 보정합니다.")
    st.write(f"- **현재 학습 기록**: `logs/param_updates.csv`에 총 `{len(pd.read_csv(Path('logs/param_updates.csv'))) if Path('logs/param_updates.csv').exists() else 0}`회의 지능 업데이트 기록이 있습니다.")
    st.caption(f"파라미터 저장소: `config/auto_params.json` (마지막 수정: {datetime.fromtimestamp(Path('config/auto_params.json').stat().st_mtime).strftime('%H:%M:%S') if Path('config/auto_params.json').exists() else 'N/A'})")

    st.divider()
    st.subheader("수수료/손익 가이드(중요)")
    st.caption("시장가(테이커) 위주면 왕복 수수료가 커서, 너무 작은 익절/손절은 구조적으로 마이너스가 되기 쉽습니다.")

    # 현재 심볼 기준 수수료(가능한 경우)
    base_symbol = settings.trading_symbol
    try:
        fee = _get_client().futures_commission_rate(symbol=base_symbol)
        maker = _to_float(fee.get("makerCommissionRate"))
        taker = _to_float(fee.get("takerCommissionRate"))
    except Exception:
        maker = None
        taker = None

    if taker is not None:
        round_trip_fee_pct = taker * 2 * 100
        st.write(f"- 테이커 수수료(편도): {taker * 100:.3f}%")
        st.write(f"- 테이커 왕복(매수+매도) 수수료: 약 {round_trip_fee_pct:.3f}%")
        st.write("- 경험상 슬리피지/스프레드까지 고려하면, 익절 목표는 왕복 수수료보다 충분히 커야 합니다.")
    else:
        st.write("- 수수료율을 API에서 가져오지 못했습니다. (네트워크/권한 이슈 가능)")


def _render_positions(symbol: str | None) -> None:
    st.subheader("현재 포지션(진입된 것만 표시)")
    st.caption("레버리지/마진모드는 바이낸스 포지션 정보 기준으로 표시됩니다.")
    settings = get_settings()
    st.caption(f"기본 레버리지(설정): {settings.trading_leverage}x / 마진모드: 격리(ISOLATED)")

    try:
        positions = _get_positions()
        pos_df = pd.DataFrame(positions)
        if pos_df.empty:
            st.info("포지션 정보가 없습니다.")
            return

        if "positionAmt" in pos_df.columns:
            pos_df["positionAmt"] = pd.to_numeric(pos_df["positionAmt"], errors="coerce")
            pos_df = pos_df[pos_df["positionAmt"].fillna(0.0) != 0.0]  # 진입된 것만

        if pos_df.empty:
            st.info("현재 진입된 포지션이 없습니다.")
            return

        pos_df = pos_df.copy()
        if "symbol" in pos_df.columns:
            pos_df["coin_kr"] = pos_df["symbol"].map(_coin_name)

        # 선물 정보(레버리지/마진타입/격리마진)
        want = [
            c
            for c in [
                "symbol",
                "coin_kr",
                "positionSide",
                "positionAmt",
                "entryPrice",
                "markPrice",
                "unRealizedProfit",
                "leverage",
                "marginType",
                "isolatedMargin",
                "isolatedWallet",
                "liquidationPrice",
            ]
            if c in pos_df.columns
        ]
        view = pos_df[want].rename(
            columns={
                "symbol": "심볼",
                "coin_kr": "종목명",
                "positionSide": "롱/숏",
                "positionAmt": "수량",
                "entryPrice": "진입가",
                "markPrice": "현재가",
                "unRealizedProfit": "미실현손익",
                "leverage": "레버리지",
                "marginType": "마진모드",
                "isolatedMargin": "격리마진",
                "isolatedWallet": "격리지갑",
                "liquidationPrice": "강제청산가",
            }
        )
        view = _format_df(
            view,
            specs={
                "수량": _FmtSpec(decimals=3),
                "진입가": _FmtSpec(decimals=1),
                "현재가": _FmtSpec(decimals=1),
                "미실현손익": _FmtSpec(decimals=2),
                "격리마진": _FmtSpec(decimals=2),
                "격리지갑": _FmtSpec(decimals=2),
                "강제청산가": _FmtSpec(decimals=1),
            },
        )
        st.dataframe(view, width="stretch", height=300)
    except Exception as e:
        st.error(f"positions: {e}")

    st.subheader("미체결 주문")
    try:
        orders = _get_open_orders(symbol=symbol)
        odf = pd.DataFrame(orders)
        if odf.empty:
            st.info("미체결 주문 없음")
            return

        want = [c for c in ["symbol", "side", "type", "origQty", "price", "status", "updateTime", "orderId"] if c in odf.columns]
        st.dataframe(odf[want], width="stretch", height=260)
    except Exception as e:
        st.error(f"open_orders: {e}")


def _render_logs() -> None:
    st.subheader("앱 로그(tail)")
    app_log = Path("logs") / "app.log"
    if not app_log.exists():
        st.info("`logs/app.log` 없음")
        return

    try:
        lines = app_log.read_text(encoding="utf-8", errors="ignore").splitlines()[-200:]
        st.code("\n".join(lines))
    except Exception:
        st.info("로그를 읽을 수 없습니다.")


@st.cache_data(ttl=60) # 1분간 캐시 유지하여 중복 로드 방지
def get_cached_report_snapshot(days: int):
    return load_report_snapshot(days)

def main() -> None:
    st.set_page_config(page_title="RpmDoctor Bot Briefing Center", layout="wide")
    
    # 상단 헤더
    st.title("🛡️ Bot Briefing Center")
    st.caption("자율 전략 최적화 엔진이 탑재된 실시간 매매 모니터링 시스템")
    
    # 자율 최적화 엔진 시작 (대시보드에서도 백그라운드 구동)
    if "optimizer_started" not in st.session_state:
        settings = get_settings()
        client = _get_client()
        if client:
            def _run_optimizer_in_bg():
                optimizer = StrategyOptimizer(client, settings)
                while True:
                    try:
                        wl = build_auto_watchlist(client, size=20)
                        symbols = [item.symbol for item in wl]
                        if symbols:
                            optimizer.run_autonomous_optimization(symbols)
                    except Exception:
                        pass
                    time.sleep(86400) # 24시간
            
            thread = threading.Thread(target=_run_optimizer_in_bg, daemon=True)
            thread.start()
            st.session_state.optimizer_started = True
    
    settings = get_settings()
    st.title("자동매매 대시보드 (바이낸스 선물 테스트넷)")

    # 기본: 1분마다 자동 새로고침 (추가 패키지 없이 동작)
    components.html(
        "<script>setTimeout(() => window.location.reload(), 60000);</script>",
        height=0,
    )

    settings = get_settings()
    # 대시보드가 "최신 코드"로 떠 있는지 확인용(캐시/재시작 이슈 진단)
    try:
        mtime = datetime.fromtimestamp(Path(__file__).stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        st.caption(f"대시보드 코드 업데이트 시간: {mtime}")
    except Exception:
        pass

    with st.sidebar:
        st.header("설정")
        st.caption("`.env` 기반 (대시보드는 주문 실행하지 않음)")
        st.subheader("관심 코인")
        if settings.watchlist_mode == "auto":
            st.caption(
                f"자동 선정: 거래대금(24h)↑ / 변동률(24h)≤{settings.watchlist_max_vol_pct_24h:g}% / 밈·잡코인 제외"
            )
            watchlist = _get_auto_watchlist_symbols()
        else:
            st.caption("수동 설정(WATCHLIST_SYMBOLS)")
            watchlist = list(getattr(settings, "watchlist_symbols", ()))

        if watchlist:
            for s in watchlist:
                st.write(f"- {s} ({_coin_name(s)})")
            
            st.divider()
            if st.button("실시간 시그널 새로고침"):
                st.cache_data.clear()
        else:
            st.info("관심 코인 목록이 비어있습니다.")

        st.divider()
        st.caption("필터/슬라이더 없이 전체 기준으로 보여줍니다.")

    # UI에서 조회 심볼/조회 개수를 제거했습니다.
    symbol = None
    trades_limit = int(settings.dashboard_trades_limit)

    # ===== 맨 위: 계좌 요약 =====
    st.subheader("계좌 요약")
    with st.expander("용어 설명(지갑/마진/미실현)", expanded=False):
        st.write(
            "- **단위**: 대부분 `USDT(테더)` 입니다. (달러에 1:1로 붙도록 설계된 코인)\n"
            "- `지갑 잔고(Wallet)` : 선물 계정의 기본 잔고(대체로 미실현 손익 제외)\n"
            "- `마진 잔고(Margin)` : 포지션 평가까지 반영된 현재 증거금(대체로 Wallet + 미실현 손익)\n"
            "- `미실현 손익` : 진입은 했지만 아직 청산 전인 상태의 평가손익"
        )
        st.write("- **마진모드 권장**: 격리(ISOLATED). (이 프로젝트는 주문 전에 격리로 강제 설정)")

    try:
        acc = _get_account_summary()
        c1, c2, c3, c4 = st.columns(4)
        wallet = acc.get("totalWalletBalance")
        margin = acc.get("totalMarginBalance")
        unreal = acc.get("totalUnrealizedProfit")
        with c1:
            st.metric("지갑 잔고(USDT)", _fmt(wallet, decimals=1))
            hint = _fmt_krw_from_usdt(wallet, settings.usdt_krw_rate)
            if hint:
                st.caption(hint)
        with c2:
            st.metric("마진 잔고(USDT)", _fmt(margin, decimals=1))
            hint = _fmt_krw_from_usdt(margin, settings.usdt_krw_rate)
            if hint:
                st.caption(hint)
        with c3:
            st.metric("미실현 손익(USDT)", _fmt(unreal, decimals=1))
            hint = _fmt_krw_from_usdt(unreal, settings.usdt_krw_rate)
            if hint:
                st.caption(hint)
        with c4:
            st.metric("거래 가능", "가능" if acc.get("canTrade") else "불가")
    except Exception as e:
        st.error(str(e))
        st.stop()

    tabs = st.tabs(["거래", "포지션", "실시간 모니터링", "시장 인텔리전스", "전략 성과 리포트", "상태/로그", "진입/청산 조건"])

    with tabs[0]:
        _render_trade_summary(symbol=symbol, limit=trades_limit)

    with tabs[1]:
        _render_positions(symbol=symbol)

    with tabs[2]:
        st.subheader("관심 종목 실시간 시그널 상태")
        st.caption("봇이 진입 기회를 엿보고 있는 종목들의 현재 지표 상태입니다.")
        if watchlist:
            sig_df = _get_live_signals(watchlist)
            if not sig_df.empty:
                st.dataframe(sig_df, width="stretch", height=400)
            else:
                st.info("시그널 데이터를 가져오지 못했습니다.")
        else:
            st.info("관심 종목이 없습니다.")

    with tabs[3]:
        _render_market_intelligence(watchlist)

    with tabs[4]:
        st.subheader("🤖 봇 자율 전략 분석 및 최적화 보고")
        st.caption("봇이 백그라운드에서 스스로 분석하고 갱신한 최신 전략 성과 리포트입니다.")
        
        settings = get_settings()
        params = load_params(settings)
        
        try:
            # 최신 30일 분석 결과 로드 (Optimizer가 저장한 값)
            metrics_30, all_trades = load_report_snapshot(30)
            
            # 데이터가 아예 없는 경우 14일치라도 시도
            if metrics_30 is None or all_trades.empty:
                metrics_30, all_trades = load_report_snapshot(14)

            # 필수 컬럼 존재 여부 및 데이터 유효성 최종 확인
            required_cols = ["exit_time", "pnl_pct", "symbol", "side"]
            is_valid = (all_trades is not None and 
                        not all_trades.empty and 
                        all(col in all_trades.columns for col in required_cols))

            if metrics_30 is not None and is_valid:
                st.info(f"💡 현재 봇은 **{params.trading_interval}** 주기를 최적으로 판단하여 자율 매매 중입니다.")
                
                # 기간별 데이터 분리 및 메트릭 계산 (7일, 14일, 30일)
                now_ts = all_trades["exit_time"].max()
                
                # 안전한 필터링 및 메트릭 계산 함수
                def get_period_metrics(days):
                    mask = all_trades["exit_time"] >= (now_ts - days * 86400 * 1000)
                    period_trades = all_trades[mask]
                    if period_trades.empty: return None
                    return calculate_portfolio_metrics(period_trades)

                metrics_14 = get_period_metrics(14)
                metrics_7 = get_period_metrics(7)

                def display_metric_row(label, m):
                    if not m or m.get("total_trades", 0) == 0:
                        st.write(f"**{label}**: 분석 데이터 생성 중...")
                        return
                    cols = st.columns([1.5, 2, 2, 2, 2, 2])
                    cols[0].write(f"**{label}**")
                    cols[1].metric("예상 수익", f"{m['total_pnl']:.2f}%")
                    cols[2].metric("거래 횟수", f"{m['total_trades']}회")
                    cols[3].metric("종합 승률", f"{m['win_rate']:.1f}%")
                    cols[4].metric("평균 보유", f"{m.get('avg_hold_duration', 0):.1f}분")
                    cols[5].metric("최대 리스크", f"{m['max_drawdown']:.2f}%")

                st.divider()
                display_metric_row("최근 7일 성과", metrics_7)
                display_metric_row("최근 14일 성과", metrics_14)
                display_metric_row("최근 30일 성과", metrics_30)
                st.divider()

                # 수익률 차트
                st.subheader("자율 최적화 모델 수익률 추이")
                all_trades_sorted = all_trades.sort_values("exit_time")
                all_trades_sorted["cum_pnl"] = all_trades_sorted["pnl_pct"].cumsum()
                all_trades_sorted["exit_time_dt"] = pd.to_datetime(all_trades_sorted["exit_time"], unit='ms', utc=True).dt.tz_convert("Asia/Seoul")
                st.line_chart(all_trades_sorted.set_index("exit_time_dt")["cum_pnl"])

                with st.expander("📊 세부 종목별 성과 및 거래 내역 확인"):
                    st.subheader("종목별 요약")
                    symbol_summary = all_trades.groupby("symbol")["pnl_pct"].agg(["count", "sum", "mean"]).reset_index()
                    symbol_summary.columns = ["심볼", "거래횟수", "누적수익률(%)", "평균수익률(%)"]
                    st.dataframe(symbol_summary.sort_values("누적수익률(%)", ascending=False), width="stretch")
                    
                    st.subheader("전체 거래 내역 (진입/청산/포지션)")
                    view_trades = all_trades.copy()
                    
                    # 보유 시간 계산 (분 단위 -> 포맷팅)
                    def format_duration(ms):
                        minutes = int(ms / 60000)
                        if minutes < 60:
                            return f"{minutes}m"
                        hours = minutes // 60
                        mins = minutes % 60
                        return f"{hours}h {mins}m"
                    
                    view_trades["보유시간"] = (view_trades["exit_time"] - view_trades["entry_time"]).apply(format_duration)
                    view_trades["진입시간"] = pd.to_datetime(view_trades["entry_time"], unit='ms', utc=True).dt.tz_convert("Asia/Seoul").dt.strftime('%m-%d %H:%M')
                    view_trades["청산시간"] = pd.to_datetime(view_trades["exit_time"], unit='ms', utc=True).dt.tz_convert("Asia/Seoul").dt.strftime('%m-%d %H:%M')
                    view_trades["방향"] = view_trades["side"]
                    view_trades["레버리지"] = view_trades.get("leverage", 1).apply(lambda x: f"{int(x)}x")
                    view_trades["수익률(%)"] = view_trades["pnl_pct"].round(2)
                    
                    display_cols = ["symbol", "방향", "레버리지", "진입시간", "청산시간", "보유시간", "entry_p", "exit_p", "수익률(%)", "reason"]
                    # 정렬 먼저 하고 컬럼 슬라이싱
                    sorted_trades = view_trades.sort_values("exit_time", ascending=False)
                    st.dataframe(sorted_trades[display_cols], width="stretch", height=500)
            else:
                st.warning("🤖 봇이 현재 30일치 데이터를 정밀 분석 중입니다. (약 2~3분 소요)")
                st.caption("분석이 완료되면 자동으로 성과 리포트가 갱신됩니다.")
        except Exception as e:
            st.error(f"리포트 구성 중 오류 발생: {e}")
            st.info("데이터를 갱신하는 중입니다. 잠시 후 다시 확인해 주세요.")

        st.divider()
        st.subheader("🔄 전략 자가 학습 및 갱신 기록")
        update_log_path = Path("logs") / "param_updates.csv"
        if update_log_path.exists():
            try:
                log_df = pd.read_csv(update_log_path)
                # 최근 기록이 위로 오게
                st.dataframe(log_df.iloc[::-1], width="stretch")
            except Exception:
                st.info("학습 기록을 불러오는 중입니다...")
        else:
            st.info("아직 전략 변경 기록이 없습니다.")

    with tabs[5]:
        _render_health()
        _render_logs()

    with tabs[6]:
        _render_rules()


if __name__ == "__main__":
    main()
