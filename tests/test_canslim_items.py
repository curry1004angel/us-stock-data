# CANSLIM 여섯 항목 판정 함수를 검증하는 테스트
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import canslim_items as ci
import canslim_loaders as cl
import canslim_scoring as cs


def 분기프레임(rows):
    # rows = [(year, quarter, amount, yoy)]
    return pd.DataFrame(
        [{"ticker": "AAA", "year": y, "quarter": q, "account": "x",
          "amount": a, "qoq": None, "yoy": v} for y, q, a, v in rows]
    )


def 번들(quarterly=None, annual=None, results=None, shares=None,
        analyst=None, inst=None, prices=None, indices=None, stock_list=None):
    return cl.Bundle(
        results=results if results is not None else pd.DataFrame().set_index(pd.Index([], name="ticker")),
        stock_list=stock_list if stock_list is not None else pd.DataFrame(columns=["ticker", "industry"]),
        quarterly=quarterly or {}, annual=annual or {},
        prices=prices if prices is not None else pd.DataFrame(),
        shares=shares if shares is not None else pd.DataFrame(),
        analyst=analyst if analyst is not None else pd.DataFrame(),
        inst_history=inst if inst is not None else pd.DataFrame(),
        indices=indices or {},
    )


# ---------- C 항목 ----------

def test_최근_YoY_두개를_뽑는다():
    df = 분기프레임([(2025, "1Q", 1.0, None), (2025, "3Q", 2.0, 10.0), (2026, "1Q", 3.0, 30.0)])
    cur, prev = ci.latest_two_yoy(df)
    assert (cur["year"], cur["quarter"], cur["yoy"]) == (2026, "1Q", 30.0)
    assert (prev["year"], prev["quarter"], prev["yoy"]) == (2025, "3Q", 10.0)


def test_YoY가_하나뿐이면_직전은_None():
    df = 분기프레임([(2026, "1Q", 3.0, 30.0)])
    cur, prev = ci.latest_two_yoy(df)
    assert cur["yoy"] == 30.0
    assert prev is None


def test_YoY가_없으면_둘다_None():
    df = 분기프레임([(2026, "1Q", 3.0, None)])
    assert ci.latest_two_yoy(df) == (None, None)


def test_C_증가율과_가속을_충족하면_핵심_통과():
    b = 번들(quarterly={
        ("AAA", "eps"): 분기프레임([(2025, "1Q", 1.0, 10.0), (2026, "1Q", 2.0, 40.0)]),
        ("AAA", "net_income"): 분기프레임([(2026, "1Q", 100.0, 40.0)]),
        ("AAA", "operating_profit"): 분기프레임([(2026, "1Q", 80.0, 30.0)]),
        ("AAA", "revenue"): 분기프레임([(2024, "1Q", 10.0, 30.0), (2025, "1Q", 20.0, 30.0),
                                    (2026, "1Q", 30.0, 30.0)]),
    })
    r = ci.judge_c("AAA", b, {})
    assert [c.passed for c in r.core] == [True, True, True]
    assert r.grade in ("A", "B", "C")


def test_C_증가율이_20퍼센트_미만이면_핵심1_미통과():
    b = 번들(quarterly={
        ("AAA", "eps"): 분기프레임([(2025, "1Q", 1.0, 5.0), (2026, "1Q", 2.0, 15.0)]),
    })
    r = ci.judge_c("AAA", b, {})
    assert r.core[0].passed is False


def test_C_일회성이익이_의심되면_핵심1을_미통과로_되돌린다():
    # 순이익이 영업이익의 3배를 넘으면 일회성 의심이다 (스펙 5.2 핵심요소 2).
    b = 번들(quarterly={
        ("AAA", "eps"): 분기프레임([(2025, "1Q", 1.0, 10.0), (2026, "1Q", 2.0, 90.0)]),
        ("AAA", "net_income"): 분기프레임([(2026, "1Q", 400.0, 90.0)]),
        ("AAA", "operating_profit"): 분기프레임([(2026, "1Q", 100.0, 10.0)]),
    })
    r = ci.judge_c("AAA", b, {})
    assert r.core[0].passed is False
    assert r.core[1].passed is False
    assert "일회성" in r.core[1].detail


def test_C_가속이_없으면_핵심3_미통과():
    b = 번들(quarterly={
        ("AAA", "eps"): 분기프레임([(2025, "1Q", 1.0, 60.0), (2026, "1Q", 2.0, 30.0)]),
    })
    r = ci.judge_c("AAA", b, {})
    assert r.core[2].passed is False


def test_C_직전분기_YoY가_없으면_핵심3은_미계산():
    b = 번들(quarterly={
        ("AAA", "eps"): 분기프레임([(2026, "1Q", 2.0, 30.0)]),
    })
    r = ci.judge_c("AAA", b, {})
    assert r.core[2].passed is None


def test_C_인접하지_않은_분기_비교는_사유에_적는다():
    b = 번들(quarterly={
        ("AAA", "eps"): 분기프레임([(2025, "1Q", 1.0, 10.0), (2026, "1Q", 2.0, 40.0)]),
    })
    r = ci.judge_c("AAA", b, {})
    assert "인접" in r.core[2].detail


def test_C_EPS가_아예_없으면_데이터부족():
    r = ci.judge_c("AAA", 번들(), {})
    assert r.grade == cs.INSUFFICIENT


def test_C_같은업종_강한매출종목이_있으면_부가2_통과():
    b = 번들(quarterly={
        ("AAA", "eps"): 분기프레임([(2025, "1Q", 1.0, 10.0), (2026, "1Q", 2.0, 40.0)]),
    }, stock_list=pd.DataFrame({"ticker": ["AAA"], "industry": ["반도체"]}))
    r = ci.judge_c("AAA", b, {"반도체": [30.0, 5.0]})
    assert r.bonus[1].passed is True
