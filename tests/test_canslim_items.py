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


def test_C_영업이익이_NaN이면_일회성_판정은_미계산():
    # NaN은 0이나 음수와 달리 truthy라 단순 가드를 그냥 통과한다. 통과하면
    # ratio가 NaN이 되고 "일회성 아님"으로 조용히 오판정된다.
    b = 번들(quarterly={
        ("AAA", "eps"): 분기프레임([(2025, "1Q", 1.0, 10.0), (2026, "1Q", 2.0, 90.0)]),
        ("AAA", "net_income"): 분기프레임([(2026, "1Q", 400.0, 90.0)]),
        ("AAA", "operating_profit"): 분기프레임([(2026, "1Q", float("nan"), 10.0)]),
    })
    r = ci.judge_c("AAA", b, {})
    assert r.core[1].passed is None
    assert "nan" not in r.core[1].detail.lower()


# ---------- A 항목 ----------

def 연간프레임(rows, account="eps"):
    # rows = [(year, amount, yoy)]
    return pd.DataFrame(
        [{"ticker": "AAA", "year": y, "account": account, "amount": a, "yoy": v}
         for y, a, v in rows]
    )


def test_A_3년_누적증가율과_매해증가를_충족하면_핵심_통과():
    b = 번들(annual={
        ("AAA", "eps"): 연간프레임([(2022, 1.0, None), (2023, 1.4, 40.0),
                                 (2024, 2.0, 42.9), (2025, 2.9, 45.0)]),
    })
    r = ci.judge_a("AAA", b)
    assert [c.passed for c in r.core] == [True, True]


def test_A_누적증가율이_25퍼센트_미만이면_핵심1_미통과():
    b = 번들(annual={
        ("AAA", "eps"): 연간프레임([(2022, 1.0, None), (2023, 1.02, 2.0),
                                 (2024, 1.04, 2.0), (2025, 1.06, 1.9)]),
    })
    r = ci.judge_a("AAA", b)
    assert r.core[0].passed is False


def test_A_한_해라도_감소하면_핵심2_미통과():
    b = 번들(annual={
        ("AAA", "eps"): 연간프레임([(2022, 1.0, None), (2023, 2.0, 100.0),
                                 (2024, 1.5, -25.0), (2025, 3.0, 100.0)]),
    })
    r = ci.judge_a("AAA", b)
    assert r.core[1].passed is False


def test_A_연도가_4개_미만이면_데이터부족():
    b = 번들(annual={("AAA", "eps"): 연간프레임([(2024, 1.0, None), (2025, 2.0, 100.0)])})
    r = ci.judge_a("AAA", b)
    assert r.grade == cs.INSUFFICIENT


def test_A_ROE가_17퍼센트_이상이면_부가1_통과():
    b = 번들(annual={
        ("AAA", "eps"): 연간프레임([(2022, 1.0, None), (2023, 1.4, 40.0),
                                 (2024, 2.0, 42.9), (2025, 2.9, 45.0)]),
        ("AAA", "net_income"): 연간프레임([(2025, 200.0, None)], "net_income"),
        ("AAA", "total_equity"): 연간프레임([(2025, 1000.0, None)], "total_equity"),
    })
    r = ci.judge_a("AAA", b)
    assert r.bonus[0].passed is True
    assert "20.0%" in r.bonus[0].detail


def test_A_현금흐름이_순이익의_1_2배_이상이면_부가2_통과():
    b = 번들(annual={
        ("AAA", "eps"): 연간프레임([(2022, 1.0, None), (2023, 1.4, 40.0),
                                 (2024, 2.0, 42.9), (2025, 2.9, 45.0)]),
        ("AAA", "net_income"): 연간프레임([(2025, 100.0, None)], "net_income"),
        ("AAA", "operating_cashflow"): 연간프레임([(2025, 130.0, None)], "operating_cashflow"),
    })
    r = ci.judge_a("AAA", b)
    assert r.bonus[1].passed is True


def test_A_순이익이_0이하면_현금흐름_부가는_미계산():
    b = 번들(annual={
        ("AAA", "eps"): 연간프레임([(2022, 1.0, None), (2023, 1.4, 40.0),
                                 (2024, 2.0, 42.9), (2025, 2.9, 45.0)]),
        ("AAA", "net_income"): 연간프레임([(2025, -50.0, None)], "net_income"),
        ("AAA", "operating_cashflow"): 연간프레임([(2025, 130.0, None)], "operating_cashflow"),
    })
    r = ci.judge_a("AAA", b)
    assert r.bonus[1].passed is None


def test_A_증가율_표준편차가_평균의_절반_이하면_부가3_통과():
    b = 번들(annual={
        ("AAA", "eps"): 연간프레임([(2022, 1.0, None), (2023, 1.4, 40.0),
                                 (2024, 2.0, 42.0), (2025, 2.9, 44.0)]),
    })
    r = ci.judge_a("AAA", b)
    assert r.bonus[2].passed is True


def test_A_내년_추정이익이_올해보다_많으면_부가4_통과():
    analyst = pd.DataFrame({"eps_0y_current": [1.0], "eps_1y_current": [1.5]},
                           index=pd.Index(["AAA"], name="ticker"))
    b = 번들(annual={
        ("AAA", "eps"): 연간프레임([(2022, 1.0, None), (2023, 1.4, 40.0),
                                 (2024, 2.0, 42.9), (2025, 2.9, 45.0)]),
    }, analyst=analyst)
    r = ci.judge_a("AAA", b)
    assert r.bonus[3].passed is True


def test_A_애널리스트_커버가_없으면_부가4는_미계산():
    b = 번들(annual={
        ("AAA", "eps"): 연간프레임([(2022, 1.0, None), (2023, 1.4, 40.0),
                                 (2024, 2.0, 42.9), (2025, 2.9, 45.0)]),
    })
    r = ci.judge_a("AAA", b)
    assert r.bonus[3].passed is None
