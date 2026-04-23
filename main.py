"""
Binance Futures Testnet 자동매매 프로젝트 (초기 뼈대)

실행 방법:
1) .env 파일에 API 키 입력
2) 의존성 설치: pip install -r requirements.txt
3) 실행: python main.py
"""

from __future__ import annotations

import logging
from pathlib import Path

from config.settings import get_settings
from core.client import create_binance_client
from core.data import fetch_futures_klines
from core.health import run_health_checks
from core.bot import run_bot
from core.params import load_params
from core.strategy import breakout_volume_direction_signal, simple_ma_cross_signal
from core.trade import append_trade_log, place_market_order, set_futures_leverage, set_futures_margin_type_isolated


def _configure_logging() -> None:
    Path("logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(Path("logs") / "app.log", encoding="utf-8"),
        ],
    )


def _print_check_results() -> bool:
    """
    헬스체크 결과를 출력하고, 전체 OK 여부를 반환합니다.
    """
    settings = get_settings()
    client = create_binance_client(settings)
    results = run_health_checks(client, settings)

    all_ok = True
    print("=== Health Check ===")
    for r in results:
        mark = "OK" if r.ok else "FAIL"
        print(f"[{mark}] {r.name}: {r.detail}")
        all_ok = all_ok and r.ok
    print()
    print(f"Overall: {'OK' if all_ok else 'FAIL'}")
    return all_ok


def main() -> None:
    _configure_logging()
    settings = get_settings()
    client = create_binance_client(settings)

    if settings.app_mode == "check":
        ok = _print_check_results()
        if not ok:
            raise SystemExit(1)
        return

    if settings.app_mode == "bot":
        if not settings.trading_enable:
            print("APP_MODE=bot 이지만 TRADING_ENABLE=false 입니다. .env에서 TRADING_ENABLE=true로 바꿔주세요.")
            return
        run_bot(client, settings)
        return

    # Futures 계좌 정보 조회 (Testnet)
    account = client.futures_account()

    # 초보자도 보기 쉽도록 자주 쓰는 항목만 요약 출력
    print("=== Futures Account Summary ===")
    print(f"canTrade: {account.get('canTrade')}")
    print(f"totalWalletBalance: {account.get('totalWalletBalance')}")
    print(f"totalUnrealizedProfit: {account.get('totalUnrealizedProfit')}")
    print(f"totalMarginBalance: {account.get('totalMarginBalance')}")
    print()

    # ===== 2단계: 데이터 수집 (캔들) =====
    symbol = settings.trading_symbol
    interval = settings.trading_interval
    limit = settings.trading_limit

    df = fetch_futures_klines(client, symbol=symbol, interval=interval, limit=limit)

    print(f"=== {symbol} {interval} klines (last 5) ===")
    print(df.tail(5).to_string(index=False))
    print()

    out_path = Path("logs") / f"klines_{symbol}_{interval}.csv"
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"Saved: {out_path}")
    print()

    # ===== 3단계: 전략 시그널 =====
    params = load_params(settings)
    signal, reason = breakout_volume_direction_signal(
        df,
        lookback=params.lookback,
        volume_mult=params.volume_mult,
        min_body_pct=params.min_body_pct,
    )
    last_close = float(df["close"].iloc[-1])
    print("=== Strategy Signal ===")
    print(f"last_close: {last_close}")
    print(f"signal: {signal}  (LONG/SHORT/HOLD)")
    print(f"reason: {reason}")
    print()

    # ===== 4단계: 주문 1회 테스트 (기본은 비활성) =====
    if not settings.trading_enable:
        print("=== Trading ===")
        print("TRADING_ENABLE=false (주문 비활성). .env에서 true로 바꾸면 주문이 실행됩니다.")
        print("다음 단계로 가려면: TRADING_ENABLE=true + 수량/레버리지 확인")
        return

    # ===== 테스트: 전략 무시 강제 주문(체결내역 생성용) =====
    if settings.test_force_action != "none":
        print("=== Trading (FORCE TEST) ===")
        print(f"TEST_FORCE_ACTION={settings.test_force_action}")

        if settings.trading_dry_run:
            print("TRADING_DRY_RUN=true 이므로 실제 주문은 나가지 않습니다.")
            print("DRY_RUN으로 trades.csv만 남기고 종료합니다.")
            append_trade_log(
                Path("logs") / "trades.csv",
                symbol=symbol,
                side=settings.test_force_action,
                quantity=settings.trading_quantity,
                price=last_close,
                order_id=None,
                status="DRY_RUN_FORCE",
            )
            return

        # 레버리지 설정(진입 테스트일 때만 의미 있음)
        try:
            set_futures_margin_type_isolated(client, symbol)
            leverage_resp = set_futures_leverage(client, symbol=symbol, leverage=settings.trading_leverage)
            print("Leverage:", leverage_resp)
        except Exception as e:
            print("레버리지 설정 실패(계속 진행):", e)

        if settings.test_force_action in {"open_long", "open_short"}:
            side = "BUY" if settings.test_force_action == "open_long" else "SELL"
            order = place_market_order(
                client,
                symbol=symbol,
                side=side,
                quantity=settings.trading_quantity,
            )
            print("Order:", order)
            append_trade_log(
                Path("logs") / "trades.csv",
                symbol=symbol,
                side=side,
                quantity=settings.trading_quantity,
                price=order.get("avgPrice") or order.get("price"),
                order_id=order.get("orderId"),
                status=order.get("status"),
            )
            print("Logged: logs/trades.csv")
            return

        if settings.test_force_action == "close":
            positions = client.futures_position_information()
            pos = next((p for p in positions if p.get("symbol") == symbol), None)
            if not pos:
                print("포지션 정보를 찾지 못했습니다.")
                return

            try:
                amt = float(pos.get("positionAmt", 0))
            except Exception:
                amt = 0.0

            if amt == 0.0:
                print("현재 포지션이 없습니다(청산할 것 없음).")
                return

            close_side = "SELL" if amt > 0 else "BUY"
            close_qty = abs(amt)
            order = client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type="MARKET",
                quantity=close_qty,
                reduceOnly=True,
            )
            print("Close order:", order)
            append_trade_log(
                Path("logs") / "trades.csv",
                symbol=symbol,
                side=f"CLOSE_{close_side}",
                quantity=close_qty,
                price=order.get("avgPrice") or order.get("price"),
                order_id=order.get("orderId"),
                status=order.get("status"),
            )
            print("Logged: logs/trades.csv")
            return

    if signal == "HOLD":
        print("=== Trading ===")
        print("signal=HOLD 이므로 주문을 넣지 않습니다.")
        return

    side = "BUY" if signal == "LONG" else "SELL"

    if settings.trading_dry_run:
        print("=== Trading (DRY RUN) ===")
        print("TRADING_DRY_RUN=true 이므로 실제 주문은 나가지 않습니다.")
        print(f"would_set_leverage: symbol={symbol}, leverage={settings.trading_leverage}")
        print(f"would_place_order: symbol={symbol}, side={side}, qty={settings.trading_quantity}")
        append_trade_log(
            Path("logs") / "trades.csv",
            symbol=symbol,
            side=side,
            quantity=settings.trading_quantity,
            price=last_close,
            order_id=None,
            status="DRY_RUN",
        )
        print("Logged (dry-run): logs/trades.csv")
        return

    # 레버리지 설정 (실패해도 주문은 시도 가능하지만, 초보자용으로 에러가 나면 중단)
    set_futures_margin_type_isolated(client, symbol)
    leverage_resp = set_futures_leverage(client, symbol=symbol, leverage=settings.trading_leverage)
    print("=== Leverage Set ===")
    print(leverage_resp)
    print()

    order = place_market_order(
        client,
        symbol=symbol,
        side=side,
        quantity=settings.trading_quantity,
    )
    print("=== Order Result ===")
    print(order)

    # 체결 로그 기록
    append_trade_log(
        Path("logs") / "trades.csv",
        symbol=symbol,
        side=side,
        quantity=settings.trading_quantity,
        price=order.get("avgPrice") or order.get("price"),
        order_id=order.get("orderId"),
        status=order.get("status"),
    )
    print("Logged: logs/trades.csv")

    # 필요하면 전체 딕셔너리도 확인 가능
    # print(account)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.exception("Unhandled error: %s", e)
        raise
