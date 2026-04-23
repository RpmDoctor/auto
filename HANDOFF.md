# 자동 매매프로그램 인수인계 로그 (대화 요약)

작성일: 2026-04-18 (KST)

> 주의: 이 파일에는 **API 키/시크릿을 절대** 적지 않습니다.  
> 현재 `.env`에는 키가 들어있을 수 있으니, 공유/업로드/커밋 전에 반드시 제거하세요. (`.gitignore`로 커밋은 막아둠)

## 1) 현재 목표
- Binance Futures **Testnet** 기반 자동매매 봇
- 로컬 Streamlit 대시보드로 계좌/포지션/청산내역/ROI/수수료/승률 확인
- 데이터가 쌓이면(청산 20건 이상) 파라미터를 아주 조금 자동 조정(보수적으로)

## 2) 실행 방법(가장 중요)
### 대시보드
- 실행: `.\\.venv\\Scripts\\python.exe run_dashboard.py`
  - 브라우저 자동 오픈
  - Streamlit 이메일 프롬프트 방지 옵션 적용됨
  - 1분마다 자동 새로고침(대시보드가 켜져 있을 때만)
  - 대시보드는 로컬 UI라 꺼도 됨(봇 실행과 별개)

### 봇(데이터 쌓기)
- `.env` 설정
  - `APP_MODE=bot`
  - `TRADING_ENABLE=true`
  - `TRADING_DRY_RUN=false`  (실제 체결이 히스토리에 남게)
- 실행: `.\\.venv\\Scripts\\python.exe main.py`

### 상태 점검(헬스체크)
- 실행: `$env:APP_MODE="check"; .\\.venv\\Scripts\\python.exe main.py`

## 3) 폴더/파일 구조(핵심만)
- `main.py`: 실행 엔트리, `APP_MODE`에 따라 `check/run/bot` 동작
- `config/settings.py`: `.env` 로드 + 모든 설정 파싱
- `core/client.py`: Testnet URL 설정 + Futures 서버시간 동기화(`timestamp_offset`)
- `core/data.py`: Futures klines → DataFrame
- `core/strategy.py`: 
  - `breakout_volume_direction_signal()` (돌파+거래량+방향성 캔들)
- `core/bot.py`: 자동 진입/청산 루프(1포지션만)
- `core/execution.py`: 시장가 주문 + 체결 대기(간헐 -2013 재시도)
- `core/analytics.py`: 체결내역 → 청산 이벤트 추정(entry/exit/수수료/ROI)
- `core/watchlist.py`: 자동 관심코인 선정(거래대금/변동성/블랙리스트)
- `core/params.py`: `config/auto_params.json` 로 파라미터 저장/로드
- `core/adaptive.py`: 데이터 기반 파라미터 미세 조정 + `logs/param_updates.csv` 기록
- `dashboard.py`: Streamlit 대시보드(한글, 원화(약) 보조표기, KST 컬럼)
- `run_dashboard.py`: Streamlit 런처(IDE ▶ 실행용)

## 4) 대시보드에서 보는 것(사용자 요구 반영)
- 계좌 요약(USDT) + 원화(약) 보조표기
- 현재 포지션(진입된 것만): 심볼/종목명/롱숏/수량/진입가/현재가/미실현손익
- 최근 청산 내역: 시간(UTC/KST), 심볼/종목명, 롱/숏, 진입평단(추정), 청산가, 수수료, 순손익, ROI%
- 탭 4개: `거래` / `포지션` / `상태/로그` / `진입/청산 조건`

## 5) 진입/청산 조건(현재 구현)
### 진입(3개 모두 만족 시)
- 돌파: 직전 `lookback` 봉의 고가/저가 돌파
- 거래량: 현재 거래량 > 평균 거래량 × `volume_mult`
- 방향성 캔들: 양봉/음봉 + 몸통비율 ≥ `min_body_pct`(%)

### 청산(아래 중 하나라도 만족 시)
- 손절: `stop_loss_pct`(%)
- 익절: `take_profit_pct`(%)
- 시간청산: `max_hold_seconds`(초)
- 쿨다운: 거래 후 `cooldown_seconds`(초)

### 자동 수정(데이터 기반)
- 최근 청산 데이터가 충분히 쌓이면(기본 20건) `volume_mult`를 아주 조금씩 조정
- 저장: `config/auto_params.json`
- 변경 로그: `logs/param_updates.csv`

## 6) 관심 코인(자동)
- `.env` 설정:
  - `WATCHLIST_MODE=auto`
  - `WATCHLIST_MIN_QUOTE_USDT` (24h 거래대금 최소)
  - `WATCHLIST_MAX_VOL_PCT_24H` (24h 변동률 상한)
  - `WATCHLIST_EXCLUDE_SYMBOLS`, `WATCHLIST_EXCLUDE_KEYWORDS` (밈/잡코인 제외)
- 대시보드 왼쪽에 자동 watchlist를 표시

## 7) 이미 겪은 이슈/해결
- `-1021 Timestamp ahead` → `core/client.py`에서 Futures 시간 동기화로 해결
- `WinError 10013` 소켓 차단 → 실행 권한/네트워크 환경 이슈(현재는 실행 가능 확인)
- Streamlit `Email:` 프롬프트 → `run_dashboard.py`에서 headless/옵션으로 차단
- 주문 직후 조회 `-2013 Order does not exist` → `core/execution.py`에서 재시도 처리

## 8) 운영시 주의(핵심)
- 너무 작은 익절/손절은 왕복 수수료+슬리피지 때문에 구조적으로 마이너스가 되기 쉬움
- 처음엔 `BOT_MAX_TRADES`를 1~5로 제한하고 안정화 후 0(무제한)
- 레버리지: `LEVERAGE_MODE=auto`일 때 최근 성과에 따라 1단계씩 자동 조절(범위 1~5x)

## 9) 대시보드 표시량
- `DASHBOARD_TRADES_LIMIT` : API에서 가져올 체결내역 수(권장 1000)
- `DASHBOARD_CLOSE_ROWS` : 최근 청산 테이블에 보여줄 행 수

## 9) 다음 할 일(추천 우선순위)
1) 봇 로그 강화: 진입/청산마다 `entry_price/exit_price/roi/fee`를 로컬에도 구조화 저장
2) 주문 안정화: 최소수량/스텝사이즈 반영, 재시도/부분체결 안전장치
3) 리스크 강화: 일 손실 제한, 연속 손실 제한, 동시 포지션 1개 확정, 심볼 전환 정책
