# SEC EDGAR companyfacts API로 미국 상장사 분기/연간 재무(매출·영업이익·순이익)를 수집해 Parquet으로 저장하는 스크립트
#
# SEC는 인증키 없이 무료지만 User-Agent에 연락 가능한 이메일을 요구한다(GitHub Secret SEC_USER_AGENT).
# companyfacts는 한 종목의 전체 과거 재무를 한 번에 돌려주므로 백필과 정기 갱신을 겸한다.
import os
import time
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime

DATA = Path("data")
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# 시크릿 미등록 시 환경변수가 빈 문자열("")로 들어올 수 있어, or로 기본값을 보장한다.
# (빈 User-Agent는 SEC가 "Undeclared Automated Tool"로 403 차단한다.)
USER_AGENT = (os.environ.get("SEC_USER_AGENT") or "").strip() or "trend-template-us soso9717@naver.com"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json, text/plain, */*",
}

# 매출은 회계기준 변경(ASC606) 등으로 태그가 여러 개라 우선순위대로 폴백한다.
CONCEPTS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ],
    "operating_profit": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
}

FP_TO_Q = {"Q1": "1Q", "Q2": "2Q", "Q3": "3Q"}


def get_cik_map():
    print(f"  사용 User-Agent: '{USER_AGENT}'")  # 빈 값이면 시크릿 미등록 신호
    for attempt in range(4):
        resp = requests.get(TICKERS_URL, headers=HEADERS, timeout=60)
        if resp.status_code == 200:
            data = resp.json()
            # {idx: {cik_str, ticker, title}} 구조 → {티커: CIK}
            return {row["ticker"].upper(): int(row["cik_str"]) for row in data.values()}
        print(f"  company_tickers.json {resp.status_code} 응답 (시도 {attempt + 1}/4)")
        time.sleep(3)
    resp.raise_for_status()


def fetch_company_facts(cik):
    url = FACTS_URL.format(cik=cik)
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == 2:
                print(f"  CIK {cik} 조회 실패: {e}")
                return None
            time.sleep(1)


def extract_account(usgaap, tags, min_year):
    # period_key((연도, 기간)) -> (태그우선순위, 제출일, 금액).
    # 상위 태그·최신 제출분을 우선해 정정공시·비교공시 중복을 정리한다.
    best = {}
    for pi, tag in enumerate(tags):
        node = usgaap.get(tag)
        if not node:
            continue
        for p in node.get("units", {}).get("USD", []):
            start, end, val = p.get("start"), p.get("end"), p.get("val")
            fy, fp, filed = p.get("fy"), p.get("fp"), p.get("filed", "")
            if not (start and end and val is not None and fy and fp):
                continue
            if int(fy) < min_year:
                continue
            try:
                days = (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days
            except ValueError:
                continue
            if fp in FP_TO_Q and 80 <= days <= 100:
                key = (int(fy), FP_TO_Q[fp])
            elif fp == "FY" and 350 <= days <= 380:
                key = (int(fy), "annual")
            else:
                continue
            cur = best.get(key)
            if cur is None or pi < cur[0] or (pi == cur[0] and filed > cur[1]):
                best[key] = (pi, filed, int(val))
    return {k: v[2] for k, v in best.items()}


def update_parquet(path, new_df, key_cols):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df
    combined = combined.drop_duplicates(subset=key_cols, keep="last")
    combined = combined.sort_values(key_cols).reset_index(drop=True)
    combined.to_parquet(path, index=False, compression="snappy")
    print(f"  {path.name}: {len(combined)}행 저장")


def main():
    print("CIK 매핑 로드 중...")
    cik_map = get_cik_map()
    print(f"  SEC 등록 종목 {len(cik_map)}개")

    stock_list = pd.read_csv(DATA / "stock_list.csv", dtype=str, encoding="utf-8-sig")
    tickers = stock_list["ticker"].tolist()
    min_year = datetime.today().year - 4

    q_rows, a_rows = [], []
    matched = 0
    for i, ticker in enumerate(tickers, 1):
        cik = cik_map.get(ticker.upper())
        if cik is None:
            continue
        facts = fetch_company_facts(cik)
        time.sleep(0.12)  # SEC 초당 10요청 제한 준수
        if not facts:
            continue
        usgaap = facts.get("facts", {}).get("us-gaap", {})
        if not usgaap:
            continue
        matched += 1
        for account, tags in CONCEPTS.items():
            for (year, period), val in extract_account(usgaap, tags, min_year).items():
                if period == "annual":
                    a_rows.append({"ticker": ticker, "year": year, "account": account, "amount": val})
                else:
                    q_rows.append({"ticker": ticker, "year": year, "quarter": period,
                                   "account": account, "amount": val})
        if i % 200 == 0:
            print(f"  {i}/{len(tickers)} 처리 (재무 매칭 {matched}종목)")

    print(f"재무 매칭 완료: {matched}종목")
    if q_rows:
        update_parquet(DATA / "financials/quarterly.parquet", pd.DataFrame(q_rows),
                       ["ticker", "year", "quarter", "account"])
    if a_rows:
        update_parquet(DATA / "financials/annual.parquet", pd.DataFrame(a_rows),
                       ["ticker", "year", "account"])


if __name__ == "__main__":
    main()
