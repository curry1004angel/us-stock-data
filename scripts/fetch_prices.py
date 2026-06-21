# 미국 주식(NASDAQ·NYSE) 일별 OHLCV를 Parquet으로 저장하는 스크립트 (fdr의 Close는 이미 분할조정됨)
import FinanceDataReader as fdr
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import sys
import time


def get_last_trading_date():
    df = fdr.DataReader("AAPL", (datetime.today() - timedelta(days=10)).strftime("%Y-%m-%d"))
    if df.empty:
        raise RuntimeError("데이터를 가져올 수 없습니다. 네트워크를 확인하세요.")
    return df.index[-1].strftime("%Y%m%d")


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else get_last_trading_date()
    date_fdr = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    year = date_str[:4]
    path = Path(f"data/prices/{year}.parquet")
    path.parent.mkdir(parents=True, exist_ok=True)

    stock_list = pd.read_csv("data/stock_list.csv", dtype=str)
    rows = []
    for i, (_, row) in enumerate(stock_list.iterrows(), 1):
        ticker, market = row["ticker"], row["market"]
        try:
            df = fdr.DataReader(ticker, date_fdr, date_fdr)
            if df.empty:
                continue
            r = df.iloc[0]
            rows.append({
                "date": date_str, "ticker": ticker, "market": market,
                "open": round(float(r.get("Open", 0)), 4),
                "high": round(float(r.get("High", 0)), 4),
                "low": round(float(r.get("Low", 0)), 4),
                "close": round(float(r.get("Close", 0)), 4),
                "volume": int(r.get("Volume", 0)),
            })
        except Exception as e:
            print(f"  {ticker} 오류: {e}")
        time.sleep(0.1)
        if i % 300 == 0:
            print(f"  {i}/{len(stock_list)} 처리 중...")

    if not rows:
        print(f"{date_str}: 데이터 없음 (휴장일?)")
        return
    new_df = pd.DataFrame(rows)
    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["date", "ticker"], keep="last")
    else:
        combined = new_df
    combined = combined.sort_values(["date", "ticker"]).reset_index(drop=True)
    combined.to_parquet(path, index=False, compression="snappy")
    print(f"{date_str}: {len(rows)}건 수집 완료 -> {path}")


if __name__ == "__main__":
    main()
