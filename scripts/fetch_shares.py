# 미국 상장사의 시가총액·유통주식수 스냅샷과 주식수 분기 변화율을 수집하는 스크립트
#
# EPS 계산에는 쓰지 않는다(fetch_financials.py가 공시 EPS를 직접 받는다).
# S 항목의 규모 표시와 자사주 매입 판정(주식수 감소)에만 쓴다.
import time
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

OUT = Path("data/screener/shares_snapshot.parquet")
HISTORY = Path("data/screener/shares_history.parquet")
COLUMNS = ["ticker", "asof", "shares", "float_shares", "market_cap", "shares_qoq"]
SHARE_ROWS = ["Ordinary Shares Number", "Share Issued"]


def from_info(info: dict, ticker: str, asof: str) -> dict:
    info = info or {}
    return {
        "ticker": ticker,
        "asof": asof,
        "shares": info.get("sharesOutstanding"),
        "float_shares": info.get("floatShares"),
        "market_cap": info.get("marketCap"),
        "shares_qoq": None,
    }


def shares_qoq_from_balance(bs):
    # 야후 분기 재무상태표에서 최근 두 분기의 주식수를 비교한다. 열은 최신이 왼쪽이다.
    if bs is None or len(bs) == 0:
        return None
    row = next((r for r in SHARE_ROWS if r in bs.index), None)
    if row is None:
        return None
    vals = bs.loc[row].tolist()[:2]
    if len(vals) < 2 or any(pd.isna(v) for v in vals) or vals[1] == 0:
        return None
    return round((vals[0] - vals[1]) / abs(vals[1]) * 100, 2)


def update_history(snap: pd.DataFrame, path: Path = HISTORY) -> int:
    # 주식수 이력을 종목×일자로 누적한다. 같은 (ticker, asof)는 최신 값으로 갈아끼운다.
    new = snap[["ticker", "asof", "shares", "market_cap"]].copy()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = pd.read_parquet(path)
        combined = pd.concat([old, new], ignore_index=True)
    else:
        combined = new
    combined = combined.drop_duplicates(subset=["ticker", "asof"], keep="last")
    combined = combined.sort_values(["ticker", "asof"]).reset_index(drop=True)
    combined.to_parquet(path, index=False, compression="snappy")
    return len(combined)


def load_tickers(path="data/stock_list.csv"):
    # keep_default_na=False가 없으면 티커 "NA"(Nano Labs Ltd)를 pandas가 결측값으로
    # 해석해 float NaN으로 만든다. dtype=str로도 막히지 않아 이후 문자열 연산에서 죽는다.
    # 결측 해석을 끄면 빈 셀이 ""로 오므로 그것만 걸러낸다.
    sl = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    return [t for t in sl["ticker"] if t.strip()]


def main():
    asof = date.today().strftime("%Y%m%d")
    tickers = load_tickers()
    print(f"주식수 스냅샷: {len(tickers)}종목")

    rows = []
    for i, tk in enumerate(tickers, 1):
        # 야후는 복수클래스 보통주에 점이 아닌 대시를 쓴다(BRK.B→BRK-B). 저장은 원본 티커로.
        t = yf.Ticker(tk.replace(".", "-"))
        try:
            row = from_info(t.info, tk, asof)
        except Exception:  # noqa: BLE001
            row = from_info({}, tk, asof)
        try:
            row["shares_qoq"] = shares_qoq_from_balance(t.quarterly_balance_sheet)
        except Exception:  # noqa: BLE001
            row["shares_qoq"] = None
        rows.append(row)
        time.sleep(0.1)
        if i % 200 == 0:
            got = sum(1 for r in rows if r["market_cap"] is not None)
            print(f"  {i}/{len(tickers)} 처리 (시총 확보 {got}종목)")

    out = pd.DataFrame(rows)[COLUMNS]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT, index=False, compression="snappy")
    print(f"저장 완료: {len(out)}행 → {OUT}")

    n = update_history(out)
    print(f"이력 저장 완료: {n}행 → {HISTORY}")


if __name__ == "__main__":
    main()
