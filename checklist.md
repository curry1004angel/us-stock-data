# EPS 원천을 yfinance에서 SEC XBRL로 교체

## 배경

yfinance `income_stmt`의 `Basic EPS`는 공시 EPS가 없으면 `Net Income ÷ Basic
Average Shares`로 자기가 만든다. 그 주식수가 틀릴 때가 있다.

```
DELL 2026   yfinance 9.107482  SEC 공시 8.79   (가중평균 대신 기말 주식수)
```

(BKNG을 25배 오차로 본 첫 진단은 틀렸다. 2026-04-06 25:1 분할이었고 yfinance
값이 맞았다. context-notes.md 참고.)

annual 5,500종목 중 1,569종목(28.5%), quarterly 4,932종목 중 880종목(17.8%)에
계산값(소수 3자리 이상)이 들어 있다.

## 할 일

- [x] SEC companyconcept API가 GitHub Actions에서 되는지 확인 (4/4 HTTP 200)
- [x] 라벨 규칙 확정 — `year`=종료일 연도, `quarter`=종료일 달력분기 (116일치/1어긋남)
- [x] 최신 제출본 방식의 액면분할 소급 한계 확인 (2년치까지만 닿음)
- [x] `scripts/fetch_eps_sec.py` 작성
- [x] 테스트 작성 (`tests/test_fetch_eps_sec.py`, 21건)
- [x] `fetch_financials.py`에서 eps 제거 + 낡은 주석 정정
- [x] DELL·BKNG·NVDA·AAPL로 실제 API 검증
- [ ] 워크플로 추가 (주간 갱신 + 전체 백필 dispatch)
- [ ] 백필 실행
- [ ] `calculate_changes.py` 재실행 후 판정 재계산

## 검증 기준 (전부 통과)

| 종목 | 연도 | 기대값 | 근거 |
|---|---|---|---|
| DELL | 2026 | 8.79 | 분할 없음, 공시값 그대로 |
| BKNG | 2023 | 4.7468 | 118.67 ÷ 25 (2026-04-06 분할) |
| NVDA | 2022 | 0.391 | 3.91 ÷ 10 (2024-06-10 분할) |
| AAPL | 2025 | 7.49 | 분할 후 제출, 조정 없음 |
