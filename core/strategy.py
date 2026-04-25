"""
전략 모듈

여기에는:
- 진입/청산 시그널 생성
- 지표 계산 (예: 이동평균, RSI 등)
을 구현합니다.
"""

from __future__ import annotations

import pandas as pd
import numpy as np


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    RSI(Relative Strength Index) 계산
    """
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """
    EMA(Exponential Moving Average) 계산
    """
    return series.ewm(span=period, adjust=False).mean()


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    ATR(Average True Range) 계산
    """
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    prev_close = df["close"].astype(float).shift(1)
    
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean() # Wilder's can be used, but simple mean is fine for start
    return atr


def calculate_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series]:
    """
    MACD 및 Signal Line 계산
    """
    exp1 = series.ewm(span=fast, adjust=False).mean()
    exp2 = series.ewm(span=slow, adjust=False).mean()
    macd = exp1 - exp2
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return macd, signal_line


def simple_ma_cross_signal(
    df: pd.DataFrame,
    fast_window: int = 5,
    slow_window: int = 20,
) -> str:
    """
    아주 단순한 이동평균 골든/데드크로스 예시.

    반환:
    - "LONG": 매수(롱) 시그널
    - "SHORT": 매도(숏) 시그널
    - "HOLD": 대기
    """
    if len(df) < slow_window:
        return "HOLD"

    close = df["close"]
    fast = close.rolling(fast_window).mean()
    slow = close.rolling(slow_window).mean()

    # 직전/현재 값 비교로 크로스 판단
    if fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1]:
        return "LONG"
    if fast.iloc[-2] >= slow.iloc[-2] and fast.iloc[-1] < slow.iloc[-1]:
        return "SHORT"

    return "HOLD"


def breakout_volume_direction_signal(
    df: pd.DataFrame,
    *,
    lookback: int = 20,
    volume_mult: float = 1.5,
    min_body_pct: float = 0.05,
    rsi_period: int = 14,
    rsi_low: float = 30.0,
    rsi_high: float = 70.0,
    ema_fast: int = 20,
    ema_slow: int = 50,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    atr_period: int = 14,
    **kwargs,
) -> tuple[str, str]:
    """
    고도화된 돌파 진입 조건:
    1) 돌파: 직전 lookback개 구간의 고가/저가 돌파
    2) 거래량 증가: 현재 봉 거래량 > 평균 거래량 * volume_mult
    3) 방향성 캔들: 양봉/음봉 + 몸통 비율(min_body_pct%) 이상
    4) RSI 필터: 롱 진입 시 RSI < rsi_high (과매수 방지), 숏 진입 시 RSI > rsi_low (과매도 방지)
    5) EMA 추세 필터: 롱은 단기 EMA > 장기 EMA (정배열), 숏은 단기 EMA < 장기 EMA (역배열)

    반환:
    - ("LONG"|"SHORT"|"HOLD", "사유")
    """
    min_req = max(lookback, rsi_period, ema_slow, macd_slow + macd_signal, atr_period) + 2
    if df.empty or len(df) < min_req:
        return "HOLD", "데이터 부족"

    close_series = df["close"].astype(float)
    
    # 지표 계산
    rsi = calculate_rsi(close_series, rsi_period).iloc[-1]
    ema_fast = calculate_ema(close_series, ema_fast).iloc[-1]
    ema_slow = calculate_ema(close_series, ema_slow).iloc[-1]
    macd, macd_signal_line = calculate_macd(close_series, macd_fast, macd_slow, macd_signal)
    cur_macd = macd.iloc[-1]
    cur_macd_signal = macd_signal_line.iloc[-1]
    atr = calculate_atr(df, atr_period).iloc[-1]

    cur = df.iloc[-1]
    prev_range = df.iloc[-(lookback + 1) : -1]

    open_p = float(cur["open"])
    high_p = float(cur["high"])
    low_p = float(cur["low"])
    close_p = float(cur["close"])
    vol = float(cur["volume"])

    if open_p <= 0:
        return "HOLD", "가격 이상"

    body_pct = abs(close_p - open_p) / open_p * 100.0
    is_bull = close_p > open_p
    is_bear = close_p < open_p

    avg_vol = float(prev_range["volume"].astype(float).mean())
    vol_ok = vol > (avg_vol * volume_mult) if avg_vol > 0 else False

    prev_high = float(prev_range["high"].astype(float).max())
    prev_low = float(prev_range["low"].astype(float).min())

    breakout_up = high_p > prev_high
    breakout_down = low_p < prev_low

    body_ok = body_pct >= float(min_body_pct)
    
    # 추세 및 RSI, MACD 필터
    trend_up = ema_fast > ema_slow
    trend_down = ema_fast < ema_slow
    rsi_long_ok = rsi < rsi_high
    rsi_short_ok = rsi > rsi_low
    macd_long_ok = cur_macd > cur_macd_signal
    macd_short_ok = cur_macd < cur_macd_signal

    if breakout_up and vol_ok and is_bull and body_ok and trend_up and rsi_long_ok and macd_long_ok:
        return "LONG", f"돌파↑+거래량+양봉+정배열+MACD골든+RSI({rsi:.1f})+ATR({atr:.2f})"
    
    if breakout_down and vol_ok and is_bear and body_ok and trend_down and rsi_short_ok and macd_short_ok:
        return "SHORT", f"돌파↓+거래량+음봉+역배열+MACD데드+RSI({rsi:.1f})+ATR({atr:.2f})"

    reason = []
    if not (breakout_up or breakout_down): reason.append("돌파X")
    if not vol_ok: reason.append("거래량X")
    if not body_ok: reason.append("몸통X")
    if not (trend_up or trend_down): reason.append("추세혼조")
    if not (macd_long_ok or macd_short_ok): reason.append("MACD방향X")
    if (breakout_up and not trend_up): reason.append("추세불일치(LONG)")
    if (breakout_down and not trend_down): reason.append("추세불일치(SHORT)")
    if (breakout_up and not macd_long_ok): reason.append("MACD불일치(LONG)")
    if (breakout_down and not macd_short_ok): reason.append("MACD불일치(SHORT)")
    if (breakout_up and not rsi_long_ok): reason.append(f"RSI과매수({rsi:.1f})")
    if (breakout_down and not rsi_short_ok): reason.append(f"RSI과매도({rsi:.1f})")

    return "HOLD", ", ".join(reason) if reason else "조건 미흡"
