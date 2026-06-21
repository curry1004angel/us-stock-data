# 재무 Parquet에서 QoQ/YoY 변화율을 계산하여 덮어쓰는 스크립트
import pandas as pd
from pathlib import Path


QUARTER_ORDER = {"1Q": 1, "2Q": 2, "3Q": 3, "4Q": 4}


def pct_change(current, previous):
    if previous is None or previous == 0 or pd.isna(previous):
        return None
    return round((current - previous) / abs(previous) * 100, 2)


def fill_missing_q4():
    # 야후는 실제 4Q 값을 주므로 보존하고, 4Q가 없는 연도만 연간−(1Q+2Q+3Q)로 채운다.
    # (SEC 시드로 받은 과거 연도는 10-Q에 4Q 단독 3개월 값이 없어 이렇게 보강한다.)
    path_q = Path("data/financials/quarterly.parquet")
    path_a = Path("data/financials/annual.parquet")
    if not path_q.exists() or not path_a.exists():
        return

    q = pd.read_parquet(path_q)
    a = pd.read_parquet(path_a)

    have_q4 = set(map(tuple, q[q["quarter"] == "4Q"][["ticker", "year", "account"]].values))
    q123 = q[q["quarter"].isin(["1Q", "2Q", "3Q"])]
    # 1~3분기가 모두 있는 (종목·연도·계정)만 4Q 도출 대상
    full = (q123.groupby(["ticker", "year", "account"])["quarter"].nunique()
            .reset_index().query("quarter == 3")[["ticker", "year", "account"]])
    q123_sum = (q123.groupby(["ticker", "year", "account"])["amount"].sum()
                .reset_index().rename(columns={"amount": "q123_sum"}))
    cand = (a[["ticker", "year", "account", "amount"]]
            .merge(full, on=["ticker", "year", "account"])
            .merge(q123_sum, on=["ticker", "year", "account"]))
    cand["key"] = list(zip(cand["ticker"], cand["year"], cand["account"]))
    cand = cand[~cand["key"].isin(have_q4)]
    if cand.empty:
        print("보강할 4Q 없음")
        return
    cand["amount"] = (cand["amount"] - cand["q123_sum"]).astype("int64")
    cand["quarter"] = "4Q"
    combined = pd.concat([q, cand[["ticker", "year", "quarter", "account", "amount"]]], ignore_index=True)
    combined.sort_values(["ticker", "year", "quarter", "account"]).reset_index(drop=True).to_parquet(
        path_q, index=False, compression="snappy")
    print(f"4Q 보강: {len(cand)}행 추가 → 총 {len(combined)}행")


def process_quarterly():
    path = Path("data/financials/quarterly.parquet")
    if not path.exists():
        print("quarterly.parquet 없음, 건너뜀.")
        return

    df = pd.read_parquet(path)
    df["q_order"] = df["quarter"].map(QUARTER_ORDER)
    df = df.sort_values(["ticker", "account", "year", "q_order"]).reset_index(drop=True)

    grp = df.groupby(["ticker", "account"])
    df["prev_q_amount"] = grp["amount"].shift(1)
    df["prev_y_amount"] = df.groupby(["ticker", "account", "quarter"])["amount"].shift(1)

    df["qoq"] = df.apply(lambda r: pct_change(r["amount"], r["prev_q_amount"]), axis=1)
    df["yoy"] = df.apply(lambda r: pct_change(r["amount"], r["prev_y_amount"]), axis=1)

    df = df.drop(columns=["prev_q_amount", "prev_y_amount", "q_order"])
    df.to_parquet(path, index=False, compression="snappy")
    print(f"분기 QoQ/YoY 계산 완료: {len(df)}행")


def process_annual():
    path = Path("data/financials/annual.parquet")
    if not path.exists():
        print("annual.parquet 없음, 건너뜀.")
        return

    df = pd.read_parquet(path)
    df = df.sort_values(["ticker", "account", "year"]).reset_index(drop=True)

    df["prev_y_amount"] = df.groupby(["ticker", "account"])["amount"].shift(1)
    df["yoy"] = df.apply(lambda r: pct_change(r["amount"], r["prev_y_amount"]), axis=1)

    df = df.drop(columns=["prev_y_amount"])
    df.to_parquet(path, index=False, compression="snappy")
    print(f"연간 YoY 계산 완료: {len(df)}행")


if __name__ == "__main__":
    fill_missing_q4()
    process_quarterly()
    process_annual()
