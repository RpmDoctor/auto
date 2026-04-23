"""
리스크 관리 모듈 (초기 스켈레톤)

여기에는:
- 포지션 사이징(수량 계산)
- 손절/익절 규칙
- 최대 손실 제한
등을 구현할 예정입니다.
"""

from __future__ import annotations


def calc_position_size_by_fixed_usdt(
    usdt_amount: float,
    price: float,
    step_size: float = 0.001,
) -> float:
    """
    고정 USDT 금액 기준으로 수량을 계산하는 아주 단순한 예시.

    - 실제 거래에서는 심볼별 최소수량/스텝사이즈를 exchangeInfo로 가져와 맞춰야 합니다.
    """
    if price <= 0:
        raise ValueError("price는 0보다 커야 합니다.")
    raw_qty = usdt_amount / price

    # step_size에 맞춰 내림 처리 (초보자용 단순 구현)
    steps = int(raw_qty / step_size)
    return steps * step_size

