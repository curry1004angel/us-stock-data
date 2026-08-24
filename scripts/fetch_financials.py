# yfinance(야후)로 미국 상장사 분기/연간 재무(매출·영업이익·순이익)를 수집해 Parquet으로 저장하는 스크립트
#
# 야후 분기 손익은 종료일 기준이라, 분기를 "종료일의 달력 분기"(1Q~4Q)로 라벨링한다.
#
# 주당이익(eps)은 여기서 받지 않는다. 야후는 공시 EPS가 없으면 Net Income ÷ Basic
# Average Shares로 자기가 만드는데 그 주식수가 틀릴 때가 있다(BKNG 2023: 야후 4.7468
# 대 공시 118.67, 25배). fetch_eps_sec.py가 SEC 공시값을 직접 받아 채운다.
#
# 예전 주석에 "SEC가 GitHub Actions·자동화 IP를 모두 403 차단(Akamai)한다"고 적혀
# 있었으나 2026-08-23 실측으로 틀렸음을 확인했다. 403은 www.sec.gov의 companyfacts.zip
# 대량 다운로드에만 해당하고, data.sec.gov의 종목별 API는 CI에서 정상 동작한다.
import time
import pandas as pd
from pathlib import Path
import yfinance as yf

DATA = Path("data")

# 야후 손익계산서 행 이름(종목·업종에 따라 달라 우선순위대로 폴백)
ROW_MAP = {
    "revenue": ["Total Revenue", "Operating Revenue"],
    "operating_profit": ["Operating Income", "Operating Income Or Loss"],
    "net_income": ["Net Income", "Net Income Common Stockholders", "Net Income Continuous Operations"],
}

# 재무상태표 — 잔액(시점) 값. 은행 등은 유동/비유동 구분이 없어 current_*가 비는데 정상(NaN 처리).
BS_ROW_MAP = {
    "total_assets": ["Total Assets"],
    "total_liabilities": ["Total Liabilities Net Minority Interest", "Total Liabilities"],
    "total_equity": ["Stockholders Equity", "Total Equity Gross Minority Interest", "Common Stock Equity"],
    "current_assets": ["Current Assets", "Total Current Assets"],
    "current_liabilities": ["Current Liabilities", "Total Current Liabilities"],
}

# 현금흐름표 — capex는 야후가 음수(지출)로 주므로 한국(DART 취득액 양수)과 맞춰 부호를 뒤집어 저장
CF_ROW_MAP = {
    "operating_cashflow": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities",
                           "Total Cash From Operating Activities"],
    "capex": ["Capital Expenditure", "Capital Expenditures"],
}
NEGATE_ACCOUNTS = {"capex"}


def pick_row(df, names):
    for n in names:
        if n in df.index:
            return n
    return None


def extract(df, ticker, quarterly, row_map=ROW_MAP):
    # quarterly=True면 분기(달력 분기 라벨), False면 연간. 종료일(컬럼)에서 연도·분기를 뽑는다.
    rows = []
    if df is None or df.empty:
        return rows
    for account, names in row_map.items():
        r = pick_row(df, names)
        if r is None:
            continue
        sign = -1 if account in NEGATE_ACCOUNTS else 1
        for col, val in df.loc[r].items():
            if pd.isna(val):
                continue
            ts = pd.Timestamp(col)
            # 전 계정을 float로 통일한다. int 캐스팅은 소수 계정의 값을 파괴한다.
            row = {"ticker": ticker, "year": int(ts.year), "account": account, "amount": sign * float(val)}
            if quarterly:
                row["quarter"] = f"{(ts.month - 1) // 3 + 1}Q"
            rows.append(row)
    return rows


def update_parquet(path, new_df, key_cols):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = pd.read_parquet(path)
        # 변화율 컬럼(yoy/qoq)은 calculate_changes에서 다시 만들므로 원본 컬럼만 합친다.
        existing = existing[[c for c in existing.columns if c in new_df.columns]]
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df
    combined = combined.drop_duplicates(subset=key_cols, keep="last")
    combined = combined.sort_values(key_cols).reset_index(drop=True)
    combined.to_parquet(path, index=False, compression="snappy")
    print(f"  {path.name}: {len(combined)}행 저장")


def main():
    sl = pd.read_csv(DATA / "stock_list.csv", dtype=str, encoding="utf-8-sig")
    tickers = sl["ticker"].tolist()
    print(f"yfinance 재무 수집: {len(tickers)}종목")

    q_rows, a_rows = [], []
    ok = 0
    for i, tk in enumerate(tickers, 1):
        try:
            # 야후는 복수클래스 보통주에 점이 아닌 대시를 쓴다(BRK.B→BRK-B). 저장은 원본 티커로.
            t = yf.Ticker(tk.replace(".", "-"))
            qr = extract(t.quarterly_income_stmt, tk, True)
            ar = extract(t.income_stmt, tk, False)
            qr += extract(t.quarterly_balance_sheet, tk, True, BS_ROW_MAP)
            ar += extract(t.balance_sheet, tk, False, BS_ROW_MAP)
            qr += extract(t.quarterly_cashflow, tk, True, CF_ROW_MAP)
            ar += extract(t.cashflow, tk, False, CF_ROW_MAP)
            if qr or ar:
                ok += 1
            q_rows += qr
            a_rows += ar
        except Exception:
            pass
        time.sleep(0.1)
        if i % 200 == 0:
            print(f"  {i}/{len(tickers)} 처리 (수집 {ok}종목)")

    print(f"재무 수집 완료: {ok}종목")
    if q_rows:
        update_parquet(DATA / "financials/quarterly.parquet", pd.DataFrame(q_rows),
                       ["ticker", "year", "quarter", "account"])
    if a_rows:
        update_parquet(DATA / "financials/annual.parquet", pd.DataFrame(a_rows),
                       ["ticker", "year", "account"])


if __name__ == "__main__":
    main()
