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
| `financials.yml` | 토 UTC 02:00 | yfinance 재무 수집(최근 분기) + QoQ/YoY 계산 |
| `monthly_stock_list.yml` | 매월 1일 | 종목 목록 갱신 |
| `monthly_resync.yml` | 매월 1일 | 최근 3년 주가 재동기화(분할 박제 보정) |
| `backfill.yml` | 수동 | 과거 가격 백필 + 재무 + 스크리닝 (최초 1회) |

## 재무 데이터 전략 (하이브리드)

SEC EDGAR가 자동화·CI IP를 모두 403 차단(Akamai)해 직접 자동 수집이 불가능하다. 그래서.

- **최근 분기 (~5개)** — `fetch_financials.py`가 **yfinance(야후)** 로 자동 수집. CI에서 막히지 않는다.
- **과거 이력 (고정)** — `seed_financials_from_sec.py`로 **SEC 벌크를 한 번만** 보강한다.
  과거 실적은 변하지 않으므로 시드는 1회면 충분하고, 이후 새 분기는 yfinance가 덧붙인다.
  - yfinance만으로는 분기 이력이 ~5개뿐이라 영업이익 YoY가 최근 1분기만 계산돼
    '3종 연속 가속(streak)'을 판정하기 어렵다. 시드로 과거를 채우면 streak이 살아난다.

## 최초 설정

1. **백필 실행** — Actions → `과거 데이터 백필` → Run workflow.
   기본값 `2021~2026`이면 가격 백필(수천 종목, 1~3시간) + yfinance 재무 + 스크리닝까지 수행한다.
2. **(선택) SEC 과거 시드** — 분기 streak 판정을 깊게 하려면 한 번만.
   - 브라우저로 `https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip`(~1.5GB)을 받는다.
   - 로컬에서 `python scripts/seed_financials_from_sec.py <받은 zip 경로>` 실행.
   - 이어서 `python scripts/calculate_changes.py` 실행 후 `data/financials/` 를 커밋·푸시한다.
3. 이후 정기 워크플로들이 자동으로 최신 상태를 유지한다(시드 재실행 불필요).

## 분할·수정주가 메모

fdr(야후)의 OHLC는 이미 분할조정돼 있어 별도 조정 로직이 없다. 다만 일별 수집은
수집 시점 가격으로 박제되므로, 분할 발생 시 과거 구간과 단차가 생긴다. 이를
`monthly_resync.yml`가 매월 최근 3년치를 다시 받아 덮어써(`keep=last`) 바로잡는다.
