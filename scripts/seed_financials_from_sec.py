# SEC 벌크 companyfacts.zip을 한 번 받아 과거 분기/연간 재무를 보강하는 로컬 1회용 스크립트
#
# SEC가 자동화 IP를 모두 403 차단하므로, 브라우저로 받은 companyfacts.zip을 로컬에서 처리한다.
# 과거 실적은 변하지 않으니 시드는 한 번이면 충분하고, 최근 분기는 yfinance(fetch_financials.py)가 갱신한다.
# 분기 라벨은 yfinance와 맞추기 위해 "종료일의 달력 분기"(1Q~4Q)로 통일한다.
#
# 사용법: python scripts/seed_financials_from_sec.py [companyfacts.zip 경로]
#   이후 python scripts/calculate_changes.py 를 실행하고 data/financials/ 를 커밋한다.
import sys
import json
import zipfile
import pandas as pd
from pathlib import Path
from datetime import datetime

DATA = Path("data")
ZIP_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("companyfacts.zip")
LOCAL_TICKERS = DATA / "company_tickers.json"
MIN_YEAR = datetime.today().year - 4

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


def cik_to_ticker():
    data = json.loads(LOCAL_TICKERS.read_text(encoding="utf-8"))
    return {int(r["cik_str"]): r["ticker"].upper() for r in data.values()}


def extract_account(usgaap, tags):
    # period_key((연도, 기간)) -> (태그우선순위, 제출일, 금액). 기간은 종료일 달력 분기/연간.
    # 상위 태그·최신 제출분을 우선해 정정공시·비교공시 중복을 정리한다.
    best = {}
    for pi, tag in enumerate(tags):
        node = usgaap.get(tag)
        if not node:
            continue
        for p in node.get("units", {}).get("USD", []):
            start, end, val = p.get("start"), p.get("end"), p.get("val")
            filed = p.get("filed", "")
            if not (start and end and val is not None):
                continue
            try:
                d0 = datetime.strptime(start, "%Y-%m-%d")
                d1 = datetime.strptime(end, "%Y-%m-%d")
            except ValueError:
                continue
            if d1.year < MIN_YEAR:
                continue
            days = (d1 - d0).days
            if 80 <= days <= 100:                       # 3개월 = 분기 (종료일 달력 분기)
                key = (d1.year, f"{(d1.month - 1) // 3 + 1}Q")
            elif 350 <= days <= 380:                    # 12개월 = 연간
                key = (d1.year, "annual")
            else:
                continue                                # YTD(6·9개월) 등은 제외
            cur = best.get(key)
            if cur is None or pi < cur[0] or (pi == cur[0] and filed > cur[1]):
                best[key] = (pi, filed, int(val))
    return {k: v[2] for k, v in best.items()}


def merge_seed(path, seed_df, keys):
    if seed_df.empty:
        print(f"  {path.name}: 시드 데이터 없음")
        return
    if path.exists():
        existing = pd.read_parquet(path)
        existing = existing[[c for c in existing.columns if c in seed_df.columns]]
        # 기존(yfinance 최근분기)을 뒤에 둬 keep='last'로 우선시키고, 시드는 빈 과거만 채운다.
        combined = pd.concat([seed_df, existing], ignore_index=True).drop_duplicates(subset=keys, keep="last")
    else:
        combined = seed_df
    path.parent.mkdir(parents=True, exist_ok=True)
    combined.sort_values(keys).reset_index(drop=True).to_parquet(path, index=False, compression="snappy")
    print(f"  {path.name}: {len(combined)}행 (시드 병합 후)")


def main():
    if not ZIP_PATH.exists():
        sys.exit(f"companyfacts.zip을 찾을 수 없습니다: {ZIP_PATH}\n"
                 "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip 을 브라우저로 받아 경로를 지정하세요.")

    cik_map = cik_to_ticker()
    want = set(pd.read_csv(DATA / "stock_list.csv", dtype=str, encoding="utf-8-sig")["ticker"].str.upper())
    print(f"대상 종목 {len(want)}개, zip 처리 시작: {ZIP_PATH}")

    q_rows, a_rows = [], []
    matched = 0
    with zipfile.ZipFile(ZIP_PATH) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".json")]
        print(f"  zip 내 JSON {len(names)}개")
        for i, name in enumerate(names, 1):
            try:
                cik = int("".join(ch for ch in Path(name).stem if ch.isdigit()))
            except ValueError:
                continue
            ticker = cik_map.get(cik)
            if not ticker or ticker not in want:
                continue
            try:
                usgaap = json.loads(zf.read(name)).get("facts", {}).get("us-gaap", {})
            except Exception:
                continue
            if not usgaap:
                continue
            matched += 1
            for account, tags in CONCEPTS.items():
                for (year, period), val in extract_account(usgaap, tags).items():
                    if period == "annual":
                        a_rows.append({"ticker": ticker, "year": year, "account": account, "amount": val})
                    else:
                        q_rows.append({"ticker": ticker, "year": year, "quarter": period,
                                       "account": account, "amount": val})
            if i % 2000 == 0:
                print(f"  {i}/{len(names)} 처리 (매칭 {matched}종목)")

    print(f"시드 매칭 완료: {matched}종목")
    merge_seed(DATA / "financials/quarterly.parquet", pd.DataFrame(q_rows),
               ["ticker", "year", "quarter", "account"])
    merge_seed(DATA / "financials/annual.parquet", pd.DataFrame(a_rows),
               ["ticker", "year", "account"])
    print("완료. 이제 `python scripts/calculate_changes.py` 실행 후 data/financials/ 를 커밋하세요.")


if __name__ == "__main__":
    main()
