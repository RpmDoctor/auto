import json
import os
from pathlib import Path
from dataclasses import asdict, is_dataclass
from typing import Any

STATE_PATH = Path("data") / "bot_state.json"

def save_bot_state(state: Any) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if is_dataclass(state):
        data = asdict(state)
        # JSON 직렬화 불가능한 필드 처리 (None 등은 괜찮음)
        STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

def load_bot_state(default_state_factory) -> Any:
    if not STATE_PATH.exists():
        return default_state_factory()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        state = default_state_factory()
        for k, v in data.items():
            if hasattr(state, k):
                setattr(state, k, v)
        return state
    except Exception:
        return default_state_factory()

def save_virtual_position(symbol: str | None, side: str | None, entry_price: float | None, mark_price: float | None, qty: float | None):
    """대시보드 표시용 실시간 가상 포지션 정보 저장"""
    path = Path("data") / "virtual_position.json"
    data = {
        "symbol": symbol,
        "side": side,
        "entry_price": entry_price,
        "mark_price": mark_price,
        "quantity": qty,
        "updated_at": json.dumps(None) # placeholder for actual time
    }
    # 실제로는 datetime을 처리해야 함
    import datetime
    data["updated_at"] = datetime.datetime.now().isoformat()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
