# fetch_financials.extract()의 순수 변환 로직을 검증하는 테스트
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_financials as ff


def make_stmt(row_name, values):
    # 야후 손익계산서 모양. 행=계정명, 열=기간 종료일(최신이 왼쪽).
    cols = [pd.Timestamp("2026-03-31"), pd.Timestamp("2025-12-31")]
    return pd.DataFrame([values], index=[row_name], columns=cols)


def test_소수_금액이_잘리지_않는다():
    df = make_stmt("Basic EPS", [4.93, 2.97])
    rows = ff.extract(df, "NVDA", True, {"eps": ["Basic EPS"]})
    amounts = sorted(r["amount"] for r in rows)
    assert amounts == [2.97, 4.93]


def test_정수_금액은_그대로_유지된다():
    df = make_stmt("Total Revenue", [1000000, 900000])
    rows = ff.extract(df, "NVDA", True, {"revenue": ["Total Revenue"]})
    assert sorted(r["amount"] for r in rows) == [900000.0, 1000000.0]


def test_capex는_부호가_뒤집힌다():
    df = make_stmt("Capital Expenditure", [-500.0, -400.0])
    rows = ff.extract(df, "NVDA", True, {"capex": ["Capital Expenditure"]})
    assert sorted(r["amount"] for r in rows) == [400.0, 500.0]


def test_분기_라벨은_종료일의_달력분기다():
    df = make_stmt("Basic EPS", [4.93, 2.97])
    rows = ff.extract(df, "NVDA", True, {"eps": ["Basic EPS"]})
    by_year = {(r["year"], r["quarter"]) for r in rows}
    assert by_year == {(2026, "1Q"), (2025, "4Q")}


def test_NaN_값은_행을_만들지_않는다():
    df = make_stmt("Basic EPS", [4.93, float("nan")])
    rows = ff.extract(df, "NVDA", True, {"eps": ["Basic EPS"]})
    assert len(rows) == 1
