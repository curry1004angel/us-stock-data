# 미국 주식 최근 N년 가격을 수정주가로 백필하는 스크립트 (재무는 fetch_financials.py에서 별도 처리)
import FinanceDataReader as fdr
import pandas as pd
from pathlib import Path
import sys
import time
from datetime import datetime


def main():
    args = [a for a in sys.argv[1:] if a.isdigit()]
    this_year = datetime.today().year
    start_year = int(args[0]) if args else this_year - 4
    end_year = int(args[1]) if len(args) > 1 else this_year
    start, end = f"{start_year}-01-01", f"{end_year}-12-31"

    stock_list = pd.read_csv("data/stock_list.csv", dtype=str)
    total = len(stock_list)
    print(f"가격 백필: {total}종목 {start_year}~{end_year}")

    all_rows = []
    for i, (_, row) in enumerate(stock_list.iterrows(), 1):
        ticker, market = row["ticker"], row["market"]
        try:
            df = fdr.DataReader(ticker, start, end)
            if df.empty:
                continue
            df = df.reset_index()
            date_col = next((c for c in df.columns if "date" in c.lower()), df.columns[0])
            df["date"] = pd.to_datetime(df[date_col]).dt.strftime("%Y%m%d")
            df["ticker"] = ticker
            df["market"] = market
            df = df.rename(columns={"Open": "open", "High": "high", "Low": "low",
                                    "Close": "close", "Volume": "volume"})
            for c in ("open", "high", "low", "close"):
                df[c] = df[c].round(4)
            all_rows.append(df[["date", "ticker", "market", "open", "high", "low", "close", "volume"]])
        except Exception as e:
            print(f"  {ticker} 오류: {e}")
        time.sleep(0.1)
        if i % 200 == 0:
            print(f"  {i}/{total} 완료중...")

    if not all_rows:
        print("수집된 가격 데이터 없음")
        return
    combined = pd.concat(all_rows, ignore_index=True)
    combined["year"] = combined["date"].str[:4]
    for year, grp in combined.groupby("year"):
        path = Path(f"data/prices/{year}.parquet")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = grp.drop(columns="year")
        if path.exists():
            existing = pd.read_parquet(path)
            data = pd.concat([existing, data], ignore_index=True).drop_duplicates(
                subset=["date", "ticker"], keep="last")
        data.sort_values(["date", "ticker"]).reset_index(drop=True).to_parquet(
            path, index=False, compression="snappy")
        print(f"  {year}.parquet 저장: {len(data)}건")


if __name__ == "__main__":
    main()
