# 🛡️ RpmDoctor Recovery & Optimization Log

이 문서는 시스템을 가장 안정적인 상태(23:44)로 복구하고, 요청된 핵심 기능을 추가한 내역을 기록합니다. 향후 문제가 발생할 경우 이 로그를 참조하여 해당 시점으로 복구할 수 있습니다.

## 📅 최종 업데이트 시점
- **시간**: 2026-04-25 00:25 (KST)
- **상태**: 안정 (Stable) / 레버리지 기능 추가 완료

## 🔍 복구 및 변경 내역

### 1. Dashboard 복구 및 개선 (`dashboard.py`)
- **원본 복구**: 2026-04-24 23:44 당시의 안정 버전(`462ad66`)으로 100% 원복 완료.
- **기능 추가**: '전략 성과 리포트' 내 상세 거래 내역 표에 **[레버리지]** 컬럼 추가.
- **안정성 확보**: `leverage` 데이터가 없는 과거 기록도 에러 없이 `1x`로 표시되도록 예외 처리 완료.
- **멀티 리포트**: 7일 / 14일 / 30일 기간별 성과 비교 테이블 정상 작동 확인.

### 2. 백테스트 엔진 최적화 (`core/backtest.py`)
- **ROI 계산 방식 변경**: `pnl_pct` 계산 시 레버리지를 곱하여 **최종 ROI(%)**가 저장되도록 수정.
  - 공식: `leveraged_pnl = raw_pnl * leverage * 100`
- **데이터 구조**: 각 거래 기록에 `leverage` 필드를 포함하여 대시보드와 연동.

### 3. 실시간 거래 로깅 (`core/trade.py`)
- **로그 확장**: `log_completed_trade` 함수에 `leverage` 인자를 추가하여, 실거래 발생 시에도 레버리지 정보가 `trade_history.csv`에 기록되도록 보완.

## ⏪ 복구 가이드 (Emergency Restore)

만약 대시보드 코드가 다시 꼬이거나 에러가 발생할 경우, 터미널에서 아래 명령어를 실행하여 현재의 안정 버전으로 즉시 되돌릴 수 있습니다.

```bash
# dashboard.py만 현재의 안정 상태로 복구 (Git 관리 기준)
git restore --source HEAD -- dashboard.py

# 또는 23:44 순수 원본으로 복구하고 싶을 때
git restore --source 462ad66 -- dashboard.py
```

## ⚠️ 주의 사항
- **데이터 정합성**: 현재 `pnl_pct`는 레버리지가 이미 반영된 값입니다. 대시보드에서 별도로 레버리지를 다시 곱하지 않도록 주의하십시오.
- **문법 검사**: 파일 수정 후에는 항상 `python -m py_compile dashboard.py`로 검증을 마쳤습니다.

---
**RpmDoctor™ Intelligence System**
