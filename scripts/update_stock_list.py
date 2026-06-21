# NASDAQ·NYSE 상장 종목 목록을 수집해 stock_list.csv로 저장하는 스크립트
import FinanceDataReader as fdr
import pandas as pd
from pathlib import Path


def main():
    frames = []
    for market in ("NASDAQ", "NYSE"):
        df = fdr.StockListing(market)
        df = df.rename(columns={"Symbol": "ticker", "Name": "name", "Industry": "industry"})
        df["market"] = market
        frames.append(df[["ticker", "name", "market", "industry"]])
    out = pd.concat(frames, ignore_index=True)
    out = out[out["ticker"].notna()]
    out = out[~out["ticker"].str.contains(r"[\^/]", na=False)]
    out = out.drop_duplicates(subset="ticker", keep="first").reset_index(drop=True)
    Path("data").mkdir(exist_ok=True)
    out.to_csv("data/stock_list.csv", index=False, encoding="utf-8-sig")
    print(f"종목 {len(out)}개 저장 (NASDAQ+NYSE, 중복 제거)")


if __name__ == "__main__":
    main()
