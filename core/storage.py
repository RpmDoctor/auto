from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd

SNAPSHOT_DIR = Path("data/snapshots")


def save_report_snapshot(days: int, metrics: Dict[str, Any], trades_df: pd.DataFrame) -> None:
    """
    성과 리포트의 지표와 거래 내역을 파일로 저장합니다.
    """
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 지표 저장 (JSON)
    metrics_path = SNAPSHOT_DIR / f"report_{days}d_metrics.json"
    with metrics_path.open("w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    
    # 거래 내역 저장 (CSV)
    trades_path = SNAPSHOT_DIR / f"report_{days}d_trades.csv"
    trades_df.to_csv(trades_path, index=False, encoding="utf-8-sig")


def load_report_snapshot(days: int) -> Tuple[Optional[Dict[str, Any]], pd.DataFrame]:
    """
    저장된 스냅샷을 불러옵니다. 없으면 (None, Empty DataFrame) 반환.
    """
    metrics_path = SNAPSHOT_DIR / f"report_{days}d_metrics.json"
    trades_path = SNAPSHOT_DIR / f"report_{days}d_trades.csv"
    
    metrics = None
    if metrics_path.exists():
        try:
            with metrics_path.open("r", encoding="utf-8") as f:
                metrics = json.load(f)
        except Exception:
            metrics = None
            
    trades_df = pd.DataFrame()
    if trades_path.exists():
        try:
            trades_df = pd.read_csv(trades_path)
        except Exception:
            trades_df = pd.DataFrame()
            
    return metrics, trades_df


def get_latest_snapshot_info() -> Dict[int, str]:
    """
    각 기간별 스냅샷의 마지막 업데이트 시간을 확인합니다.
    """
    info = {}
    for days in [7, 14, 30]:
        path = SNAPSHOT_DIR / f"report_{days}d_metrics.json"
        if path.exists():
            mtime = path.stat().st_mtime
            from datetime import datetime
            info[days] = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
    return info
