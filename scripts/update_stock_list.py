# NASDAQ·NYSE 상장 종목 목록을 수집해 stock_list.csv로 저장하는 스크립트
import re
import FinanceDataReader as fdr
import pandas as pd
from pathlib import Path

# 보통주가 아닌 증권(우선주·워런트·라이트)을 이름으로 가려내는 정규식.
_NONCOMMON_NAME = re.compile(
    r"\b(?:Warrants?|Rights?|Pfd|Pref Shs|Preference Shares?|Preferred Stock|Preferred Shares?|Depositary|Depository)\b"
    r"|DS Repr|%.*Preferred|Cumulative.*Preferred",
    re.I,
)


def is_common_stock(ticker: str, name: str, tickers: set) -> bool:
    # 티커에 공백이 있으면 비보통주다(예: 'BAC PR L'=우선주, 'XXX RT'=라이트, 'XXX WI'=when-issued).
    if " " in ticker:
        return False
    if _NONCOMMON_NAME.search(name or ""):
        return False
    # SPAC/IPO 유닛: 티커가 U로 끝 + 이름에 Unit + U 뗀 보통주가 함께 상장(BTSGU→BTSG)이면 제외.
    if ticker.endswith("U") and re.search(r"\bUnits?\b", name or "", re.I) and ticker[:-1] in tickers:
        return False
    return True


def filter_common(out: pd.DataFrame) -> pd.DataFrame:
    tickers = set(out["ticker"].dropna())
    mask = out.apply(
        lambda r: isinstance(r["ticker"], str)
        and is_common_stock(r["ticker"], r["name"] if isinstance(r["name"], str) else "", tickers),
        axis=1,
    )
    removed = int((~mask).sum())
    if removed:
        print(f"  비보통주(우선주·워런트·라이트·유닛) {removed}개 제외")
    return out[mask].reset_index(drop=True)


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
    out = filter_common(out)
    Path("data").mkdir(exist_ok=True)
    out.to_csv("data/stock_list.csv", index=False, encoding="utf-8-sig")
    print(f"종목 {len(out)}개 저장 (NASDAQ+NYSE, 보통주만)")


if __name__ == "__main__":
    main()
