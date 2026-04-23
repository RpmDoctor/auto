"""
헬스체크(정상 동작 점검) 모듈

목표:
- 초보자도 "지금 뭐가 되고/안 되는지" 바로 알 수 있게 PASS/FAIL 체크를 제공합니다.
"""

from __future__ import annotations

from dataclasses import dataclass

from binance.client import Client

from config.settings import Settings
from core.data import fetch_futures_klines


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def run_health_checks(client: Client, settings: Settings) -> list[CheckResult]:
    results: list[CheckResult] = []

    # 1) 모드/환경 확인
    results.append(
        CheckResult(
            name="ENV",
            ok=True,
            detail=f"use_testnet={settings.use_testnet}, app_mode={settings.app_mode}",
        )
    )

    # 2) 서버 시간/연결 확인 (서명 없는 엔드포인트)
    try:
        t = client.futures_time()
        server_time = t.get("serverTime")
        results.append(CheckResult(name="FUTURES_TIME", ok=True, detail=f"serverTime={server_time}"))
    except Exception as e:
        results.append(CheckResult(name="FUTURES_TIME", ok=False, detail=str(e)))
        return results  # 시간부터 막히면 이후도 대부분 실패

    # 3) 계좌 조회(서명 필요)
    try:
        account = client.futures_account()
        wallet = account.get("totalWalletBalance")
        results.append(CheckResult(name="FUTURES_ACCOUNT", ok=True, detail=f"wallet={wallet}"))
    except Exception as e:
        results.append(CheckResult(name="FUTURES_ACCOUNT", ok=False, detail=str(e)))
        return results

    # 4) 캔들 수집
    try:
        df = fetch_futures_klines(
            client,
            symbol=settings.trading_symbol,
            interval=settings.trading_interval,
            limit=settings.trading_limit,
        )
        last_close = float(df["close"].iloc[-1])
        results.append(
            CheckResult(
                name="KLINES",
                ok=len(df) > 0,
                detail=f"rows={len(df)}, last_close={last_close}",
            )
        )
    except Exception as e:
        results.append(CheckResult(name="KLINES", ok=False, detail=str(e)))

    return results

