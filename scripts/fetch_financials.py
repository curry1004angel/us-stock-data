# yfinance(야후)로 미국 상장사 분기/연간 재무(매출·영업이익·순이익)를 수집해 Parquet으로 저장하는 스크립트
#
# 원래 SEC EDGAR를 쓰려 했으나 SEC가 GitHub Actions·자동화 IP를 모두 403 차단(Akamai)해 야후로 대체했다.
# 야후 분기 손익은 종료일 기준이라, 분기를 "종료일의 달력 분기"(1Q~4Q)로 라벨링한다.
# (선택적으로 SEC 벌크를 한 번 받아 과거 이력을 보강할 수 있다 — seed 스크립트 별도.)
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


def pick_row(df, names):
    for n in names:
        if n in df.index:
            return n
    return None


def extract(df, ticker, quarterly):
    # quarterly=True면 분기(달력 분기 라벨), False면 연간. 종료일(컬럼)에서 연도·분기를 뽑는다.
    rows = []
    if df is None or df.empty:
        return rows
    for account, names in ROW_MAP.items():
        r = pick_row(df, names)
        if r is None:
            continue
        for col, val in df.loc[r].items():
            if pd.isna(val):
                continue
            ts = pd.Timestamp(col)
            row = {"ticker": ticker, "year": int(ts.year), "account": account, "amount": int(val)}
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
