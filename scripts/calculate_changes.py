# 재무 Parquet에서 QoQ/YoY 변화율을 계산하여 덮어쓰는 스크립트
from collections import Counter
from pathlib import Path

import pandas as pd


QUARTER_ORDER = {"1Q": 1, "2Q": 2, "3Q": 3, "4Q": 4}


def pct_change(current, previous):
    if previous is None or previous == 0 or pd.isna(previous):
        return None
    return round((current - previous) / abs(previous) * 100, 2)


def fill_missing_quarters():
    # 누락 분기를 연간−(나머지 3분기 합)으로 채운다. 결산월이 12월이 아닌 기업(예: NVDA 1월 결산)은
    # 회계 4분기가 달력 1~3Q에 떨어지는데 야후·SEC 모두 그 분기의 3개월 단독 값을 잘 안 줘서
    # 매년 같은 달력 분기에 구멍이 난다(NVDA는 매년 1Q 누락 → YoY·streak 끊김).
    # 종목별 결산 분기(e)를 "연속 4분기 합 = 연간" 검산 투표로 확정한 뒤, 그 구성에서
    # 정확히 3개만 있는 회계연도의 누락분을 도출한다. 구성 미확정 종목은 기존 동작대로
    # 12월 결산으로 간주하되 4Q 누락만 보강한다(검증 없는 확장 방지).
    path_q = Path("data/financials/quarterly.parquet")
    path_a = Path("data/financials/annual.parquet")
    if not path_q.exists() or not path_a.exists():
        return

    q = pd.read_parquet(path_q)
    a = pd.read_parquet(path_a)
    seq = q["year"] * 4 + q["quarter"].map(QUARTER_ORDER)  # 연속 분기 인덱스(year*4+분기)
    amounts = dict(zip(zip(q["ticker"], q["account"], seq), q["amount"]))
    ann = list(zip(a["ticker"], a["account"], a["year"], a["amount"]))

    # 1) 결산 분기 투표: 연간 Y가 (Y,e)로 끝나는 연속 4분기 합과 맞으면 e에 한 표
    votes = {}
    for t, acct, y, amt in ann:
        for e in (1, 2, 3, 4):
            vals = [amounts.get((t, acct, y * 4 + e - k)) for k in range(4)]
            if all(v is not None for v in vals) and abs(sum(vals) - amt) <= max(abs(amt) * 0.02, 2e6):
                votes.setdefault(t, Counter())[e] += 1
    fiscal_end = {t: c.most_common(1)[0][0] for t, c in votes.items()}

    # 2) 확정 구성에서 4분기 중 3개만 있으면 누락분 = 연간 − 3분기 합
    fills = []
    for t, acct, y, amt in ann:
        e = fiscal_end.get(t, 4)
        seqs = [y * 4 + e - k for k in range(4)]
        known = [amounts[(t, acct, s)] for s in seqs if (t, acct, s) in amounts]
        if len(known) != 3:
            continue
        (miss,) = [s for s in seqs if (t, acct, s) not in amounts]
        qo = ((miss - 1) % 4) + 1
        if t not in fiscal_end and qo != 4:  # 미확정 종목은 4Q만(기존 동작 유지)
            continue
        fills.append({"ticker": t, "year": (miss - qo) // 4, "quarter": f"{qo}Q",
                      "account": acct, "amount": int(amt - sum(known))})
    if not fills:
        print("보강할 누락 분기 없음")
        return
    combined = pd.concat([q, pd.DataFrame(fills)], ignore_index=True)
    combined.sort_values(["ticker", "year", "quarter", "account"]).reset_index(drop=True).to_parquet(
        path_q, index=False, compression="snappy")
    dist = dict(Counter(f["quarter"] for f in fills).most_common())
    print(f"누락 분기 보강: {len(fills)}행 추가 (분포 {dist}) → 총 {len(combined)}행")


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
    fill_missing_quarters()
    process_quarterly()
    process_annual()
