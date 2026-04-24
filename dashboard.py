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
    st.subheader("진입/청산 조건(현재 적용값)")
    settings = get_settings()
    params = load_params(settings)

    st.write("진입(모든 필터 만족 시 진입)")
    st.write(
        f"- 돌파: 직전 {params.lookback}개 봉의 고가/저가 돌파\n"
        f"- 거래량: 현재 거래량 > 평균거래량 × {params.volume_mult}\n"
        f"- 방향성 캔들: 양봉/음봉 + 몸통비율 ≥ {params.min_body_pct}%\n"
        f"- 추세(EMA): 단기({params.ema_fast}) > 장기({params.ema_slow}) (LONG 기준)\n"
        f"- MACD: MACD선 > Signal선 (LONG 기준)\n"
        f"- RSI 필터: 과매수/과매도 구간 진입 전 체크"
    )

    st.write("청산(아래 중 하나라도 만족 시 청산)")
    st.write(
        f"- ATR 손절: 진입가 ± (ATR × {params.atr_multiplier_sl})\n"
        f"- ATR 익절: 진입가 ± (ATR × {params.atr_multiplier_tp})\n"
        f"- 트레일링 스탑: 고점대비 {params.trailing_stop_pct}%\n"
        f"- 시간청산: {params.max_hold_seconds}초\n"
        f"- 쿨다운: 거래 후 {params.cooldown_seconds}초"
    )

    st.write("자동 수정(데이터 기반)")
    st.write("- 최근 청산 데이터가 충분히 쌓이면(기본 20건) `거래량 배수`를 아주 조금씩 자동 조정합니다.")
    st.caption("저장: `config/auto_params.json`, 기록: `logs/param_updates.csv`")

    st.divider()
    st.subheader("레버리지 정책")
    if settings.leverage_mode == "auto":
        st.write(f"- 모드: 자동(auto)")
        st.write(f"- 현재 목표 레버리지: {params.leverage}x (범위 {settings.leverage_min}~{settings.leverage_max}x, 최대 5배 준수)")
        st.write("- 최근 성과가 불안하면 1단계 낮추고, 안정적이면 1단계 올립니다.")
    else:
        st.write(f"- 모드: 수동(manual)")
        st.write(f"- 고정 레버리지: {settings.trading_leverage}x")

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


def main() -> None:
    st.set_page_config(page_title="RpmDoctor Portfolio Bot", layout="wide", page_icon="📈")
    
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

    tabs = st.tabs(["거래", "포지션", "실시간 모니터링", "전략 성과 리포트", "상태/로그", "진입/청산 조건"])

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
        st.subheader("🤖 봇 자율 전략 분석 및 최적화 보고")
        st.caption("봇이 백그라운드에서 스스로 분석하고 갱신한 최신 전략 성과 리포트입니다.")
        
        settings = get_settings()
        params = load_params(settings)
        
        # 최신 14일 분석 결과 로드 (Optimizer가 저장한 값)
        metrics, all_trades = load_report_snapshot(14)
        
        if metrics:
            st.info(f"💡 현재 봇은 **{params.trading_interval}** 주기를 최적으로 판단하여 자율 매매 중입니다.")
            
            # 상단 지표 카드
            c1, c2, c3, c4, c5 = st.columns(5)
            with c1: st.metric("최근 14일 예상 수익", f"{metrics['total_pnl']:.2f}%")
            with c2: st.metric("시뮬레이션 횟수", f"{metrics['total_trades']}회")
            with c3: st.metric("종합 승률", f"{metrics['win_rate']:.1f}%")
            with c4: st.metric("평균 보유 시간", f"{metrics.get('avg_hold_duration', 0):.1f}분")
            with c5: st.metric("최대 리스크(MDD)", f"{metrics['max_drawdown']:.2f}%")

            # 수익률 차트
            st.subheader("자율 최적화 모델 수익률 추이")
            all_trades = all_trades.sort_values("exit_time")
            all_trades["cum_pnl"] = all_trades["pnl_pct"].cumsum()
            all_trades["exit_time_dt"] = pd.to_datetime(all_trades["exit_time"], unit='ms', utc=True).dt.tz_convert("Asia/Seoul")
            st.line_chart(all_trades.set_index("exit_time_dt")["cum_pnl"])

            with st.expander("세부 종목별 성과 및 거래 내역"):
                symbol_summary = all_trades.groupby("symbol")["pnl_pct"].agg(["count", "sum", "mean"]).reset_index()
                symbol_summary.columns = ["심볼", "거래횟수", "누적수익률(%)", "평균수익률(%)"]
                st.dataframe(symbol_summary.sort_values("누적수익률(%)", ascending=False), width="stretch")
        else:
            st.warning("아직 자율 최적화 분석 데이터가 없습니다. 봇을 실행하면 백그라운드에서 첫 분석을 시작합니다.")

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

    with tabs[4]:
        _render_health()
        _render_logs()

    with tabs[5]:
        _render_rules()


if __name__ == "__main__":
    main()
