# us-stock-data

미국 주식(NASDAQ·NYSE) 전 종목의 가격·재무 데이터를 수집해 미너비니 트렌드 템플릿
스크리닝 결과로 가공하는 데이터 레포. 웹앱 `screener-app-us`가 이 레포의 raw URL을 읽는다.

## 데이터 구조

```
data/
  stock_list.csv                 종목 목록 (ticker, name, market, industry)
  prices/<연도>.parquet          일별 OHLCV (date, ticker, market, open~close, volume)
  financials/quarterly.parquet   분기 재무 (ticker, year, quarter, account, amount, yoy, qoq)
  financials/annual.parquet      연간 재무 (ticker, year, account, amount, yoy)
  screener/results.csv           트렌드 템플릿 8조건 + RS 등급 스크리닝 결과
```

`account`은 `revenue`(매출)·`operating_profit`(영업이익)·`net_income`(순이익) 세 가지다.

## 자동화 (GitHub Actions)

| 워크플로 | 일정 | 하는 일 |
|---|---|---|
| `daily_prices.yml` | 평일 UTC 23:00 | 당일 주가 수집 + 트렌드 템플릿 재계산 |
| `financials.yml` | 토 UTC 02:00 | SEC EDGAR 재무 수집 + QoQ/YoY 계산 |
| `monthly_stock_list.yml` | 매월 1일 | 종목 목록 갱신 |
| `monthly_resync.yml` | 매월 1일 | 최근 3년 주가 재동기화(분할 박제 보정) |
| `backfill.yml` | 수동 | 과거 가격 백필 + 재무 + 스크리닝 (최초 1회) |

## 최초 설정

1. **SEC User-Agent 시크릿 등록** — Settings → Secrets and variables → Actions →
   New repository secret. 이름 `SEC_USER_AGENT`, 값은 연락 가능한 이메일을 포함한 문자열
   (예: `trend-template-us soso9717@naver.com`). SEC EDGAR는 인증키는 없지만 User-Agent에
   이메일을 요구한다. 미설정 시 스크립트의 기본값으로 동작하지만 명시 권장.
2. **백필 실행** — Actions → `과거 데이터 백필` → Run workflow.
   기본값 `2021~2026`이면 가격 백필(수천 종목, 1~3시간) + SEC 재무 + 스크리닝까지 한 번에 수행한다.
3. 백필 완료 후에는 위 정기 워크플로들이 자동으로 최신 상태를 유지한다.

## 분할·수정주가 메모

fdr(야후)의 OHLC는 이미 분할조정돼 있어 별도 조정 로직이 없다. 다만 일별 수집은
수집 시점 가격으로 박제되므로, 분할 발생 시 과거 구간과 단차가 생긴다. 이를
`monthly_resync.yml`가 매월 최근 3년치를 다시 받아 덮어써(`keep=last`) 바로잡는다.
