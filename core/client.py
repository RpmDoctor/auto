"""
Binance Client 생성 모듈

목표:
- main.py에서 이 모듈만 import해서 클라이언트를 쉽게 생성할 수 있도록 합니다.
"""

from __future__ import annotations

import time

from binance.client import Client

from config.settings import Settings


FUTURES_TESTNET_URL = "https://testnet.binancefuture.com/fapi"


def _sync_futures_time(client: Client) -> None:
    """
    서명된 요청에 쓰이는 timestamp 보정을 합니다.

    - 로컬 PC 시간이 서버보다 조금이라도 빠르면(-1021) 에러가 날 수 있어 보정합니다.
    """
    try:
        server_time_ms = int(client.futures_time()["serverTime"])
        local_time_ms = int(time.time() * 1000)
        client.timestamp_offset = server_time_ms - local_time_ms
    except Exception:
        # 시간 동기화가 실패해도, 다음 요청에서 정상 동작할 수 있어 그대로 진행합니다.
        return


def create_binance_client(settings: Settings) -> Client:
    """
    Binance Futures 클라이언트를 생성합니다.

    - settings.use_testnet 이 True면 Futures Testnet URL로 연결합니다.
    """
    # python-binance는 Client 생성 시 기본적으로 spot ping(api.binance.com)을 수행합니다.
    # Testnet 프로젝트에서는 production spot 도메인을 치지 않도록 testnet/ping 옵션을 조절합니다.
    client = Client(
        settings.binance_api_key,
        settings.binance_api_secret,
        testnet=settings.use_testnet,
        ping=not settings.use_testnet,
    )

    # python-binance는 Futures Testnet용 별도 URL 설정이 필요합니다.
    if settings.use_testnet:
        client.FUTURES_URL = FUTURES_TESTNET_URL

    # Testnet/실서버 모두 서명 요청 전에 시간 동기화 권장
    _sync_futures_time(client)

    return client
