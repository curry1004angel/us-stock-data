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


def test_A_마지막_연도_EPS가_NaN이면_핵심1은_미계산():
    # NaN은 truthy라 first<=0 같은 단순 비교로는 걸러지지 않는다. 이 테스트는
    # 핵심1의 pd.isna(last) 가드(canslim_items.py:141)를 고정한다. 이 가드를 지우면
    # cum이 NaN이 되어 "미통과"로, 사유에는 "nan%"가 찍히며 조용히 오판정된다.
    b = 번들(annual={
        ("AAA", "eps"): 연간프레임([(2022, 1.0, None), (2023, 1.4, 40.0),
                                 (2024, 2.0, 42.9), (2025, float("nan"), 45.0)]),
    })
    r = ci.judge_a("AAA", b)
    assert r.core[0].passed is None
    assert "nan" not in r.core[0].detail.lower()


def test_A_자본총계가_NaN이면_ROE_부가는_미계산():
    # NaN은 truthy라 not eq_a["amount"]로는 걸러지지 않는다. 이 테스트는 부가1의
    # not(eq_a["amount"] and eq_a["amount"] > 0) 가드(canslim_items.py:158-159)를 고정한다.
    # 이 가드를 지우면 roe가 NaN이 되어 "미통과"로, 사유에는 "nan%"가 찍힌다.
    b = 번들(annual={
        ("AAA", "eps"): 연간프레임([(2022, 1.0, None), (2023, 1.4, 40.0),
                                 (2024, 2.0, 42.9), (2025, 2.9, 45.0)]),
        ("AAA", "net_income"): 연간프레임([(2025, 200.0, None)], "net_income"),
        ("AAA", "total_equity"): 연간프레임([(2025, float("nan"), None)], "total_equity"),
    })
    r = ci.judge_a("AAA", b)
    assert r.bonus[0].passed is None
    assert "nan" not in r.bonus[0].detail.lower()


def test_A_영업현금흐름이_NaN이면_현금흐름_부가는_미계산():
    # NaN은 truthy다. 이 테스트는 부가2의 pd.isna(ocf_a["amount"]) 가드
    # (canslim_items.py:168-169)를 고정한다. 이 가드를 지우면 ratio가 NaN이 되어
    # "미통과"로, 사유에는 "nan배"가 찍히며 조용히 오판정된다.
    b = 번들(annual={
        ("AAA", "eps"): 연간프레임([(2022, 1.0, None), (2023, 1.4, 40.0),
                                 (2024, 2.0, 42.9), (2025, 2.9, 45.0)]),
        ("AAA", "net_income"): 연간프레임([(2025, 100.0, None)], "net_income"),
        ("AAA", "operating_cashflow"): 연간프레임([(2025, float("nan"), None)], "operating_cashflow"),
    })
    r = ci.judge_a("AAA", b)
    assert r.bonus[1].passed is None
    assert "nan" not in r.bonus[1].detail.lower()


# ---------- N 항목 ----------

import canslim_bases as cb_


def 결과행(**kw):
    base = {"close": 100.0, "high_52w": 100.0, "volume": 1_000_000}
    base.update(kw)
    return pd.DataFrame([base], index=pd.Index(["AAA"], name="ticker"))


def test_N_신고점이고_돌파했으면_핵심_통과():
    b = 번들(results=결과행(close=100.0, high_52w=100.0))
    st = cb_.BaseState("1a차", 95.0, None, 0, False)
    r = ci.judge_n("AAA", b, st, 500_000)
    assert [c.passed for c in r.core] == [True, True]


def test_N_신고점_5퍼센트_이내면_핵심1_통과():
    b = 번들(results=결과행(close=96.0, high_52w=100.0))
    st = cb_.BaseState("1a차", 90.0, None, 0, False)
    r = ci.judge_n("AAA", b, st, 500_000)
    assert r.core[0].passed is True


def test_N_신고점에서_10퍼센트_아래면_핵심1_미통과():
    b = 번들(results=결과행(close=90.0, high_52w=100.0))
    st = cb_.BaseState("1a차", 85.0, None, 0, False)
    r = ci.judge_n("AAA", b, st, 500_000)
    assert r.core[0].passed is False


def test_N_베이스가_없으면_핵심2_미통과():
    b = 번들(results=결과행(close=100.0, high_52w=100.0))
    st = cb_.BaseState("베이스 없음", None, None, 0, False)
    r = ci.judge_n("AAA", b, st, 500_000)
    assert r.core[1].passed is False


def test_N_베이스_형성중이면_핵심2_미통과():
    b = 번들(results=결과행(close=90.0, high_52w=100.0))
    st = cb_.BaseState("2 형성중", 100.0, 92.0, 5, True)
    r = ci.judge_n("AAA", b, st, 500_000)
    assert r.core[1].passed is False


def test_N_거래량이_50일평균의_1_4배_이상이면_부가_통과():
    b = 번들(results=결과행(close=100.0, high_52w=100.0, volume=1_000_000))
    st = cb_.BaseState("1a차", 95.0, None, 0, False)
    r = ci.judge_n("AAA", b, st, 700_000)
    assert r.bonus[0].passed is True


def test_N_평균거래량이_없으면_부가는_미계산():
    b = 번들(results=결과행(close=100.0, high_52w=100.0))
    st = cb_.BaseState("1a차", 95.0, None, 0, False)
    r = ci.judge_n("AAA", b, st, None)
    assert r.bonus[0].passed is None


def test_N_종가와_베이스가_모두_없으면_데이터부족():
    # 베이스 상태가 None인 경우는 가격 이력이 60일 미만이라 계산 자체를 못 한 것이다.
    # "베이스 없음"(BaseState 객체)은 계산 결과이므로 미통과이지 데이터부족이 아니다.
    b = 번들(results=결과행(close=None, high_52w=None))
    r = ci.judge_n("AAA", b, None, None)
    assert r.grade == cs.INSUFFICIENT


def test_N_베이스_없음은_데이터부족이_아니라_미통과():
    b = 번들(results=결과행(close=100.0, high_52w=100.0))
    st = cb_.BaseState("베이스 없음", None, None, 0, False)
    r = ci.judge_n("AAA", b, st, None)
    assert r.core[1].passed is False
    assert r.grade != cs.INSUFFICIENT


def test_N_종가가_없으면_부가는_미계산_크래시아님():
    # breakout_confirmed(close, ...)는 close가 None이면 close > pivot 비교에서
    # TypeError로 죽는다. 종가는 results.csv, 베이스는 별도 일봉 계산에서 나오므로
    # 베이스와 평균거래량은 있는데 종가만 없는 조합이 실제로 생길 수 있다. 이 테스트는
    # judge_n이 breakout_confirmed 호출 전에 close is None을 걸러내는 가드를 고정한다.
    # 이 가드를 지우면 크래시가 난다.
    b = 번들(results=결과행(close=None, high_52w=None))
    st = cb_.BaseState("1a차", 90.0, None, 0, False)
    r = ci.judge_n("AAA", b, st, 500_000)
    assert r.bonus[0].passed is None


def test_N_평균거래량이_NaN이면_부가는_미계산():
    # vol_avg50은 시그니처상 float | None이지만 신규 상장주는 rolling(50).mean()이
    # NaN을 내놓을 수 있어 None이 아닌 NaN으로 들어올 수 있다. NaN은 truthy라
    # "vol_avg50 is None" 만으로는 걸러지지 않고 volume/vol_avg50 나눗셈에서
    # "nan배"가 사유 문자열에 찍힌다. 이 테스트는 pd.isna(vol_avg50) 가드를 고정한다.
    b = 번들(results=결과행(close=100.0, high_52w=100.0, volume=1_000_000))
    st = cb_.BaseState("1a차", 95.0, None, 0, False)
    r = ci.judge_n("AAA", b, st, float("nan"))
    assert r.bonus[0].passed is None
    assert "nan" not in r.bonus[0].detail.lower()


def test_N_평균거래량이_0이면_부가는_미계산():
    # 거래정지나 상장폐지 예정 종목은 50일 내내 거래량이 0이라 rolling 평균도 0이 된다.
    # volume이 넘파이 실수라 0으로 나눠도 ZeroDivisionError가 아니라 inf가 나오고,
    # breakout_confirmed의 volume >= 0 × 1.4가 무조건 참이 되어 거래량 확인 없이
    # 돌파 통과(passed=True, 사유 "inf배")로 둔갑한다. 이 테스트가 그 가드를 고정한다.
    b = 번들(results=결과행(close=100.0, high_52w=100.0, volume=1_000_000))
    st = cb_.BaseState("1a차", 95.0, None, 0, False)
    r = ci.judge_n("AAA", b, st, 0.0)
    assert r.bonus[0].passed is None
    assert "inf" not in r.bonus[0].detail.lower()


# ---------- S 항목 ----------

def test_S_거래량_급증일이_있으면_핵심_통과():
    b = 번들(results=결과행())
    r = ci.judge_s("AAA", b, 3)
    assert r.core[0].passed is True


def test_S_거래량_급증일이_없으면_핵심_미통과():
    b = 번들(results=결과행())
    r = ci.judge_s("AAA", b, 0)
    assert r.core[0].passed is False


def test_S_급증일수를_모르면_데이터부족():
    b = 번들(results=결과행())
    r = ci.judge_s("AAA", b, None)
    assert r.grade == cs.INSUFFICIENT


def test_S_부채비율이_낮고_3년간_감소하면_부가1_통과():
    b = 번들(results=결과행(), annual={
        ("AAA", "total_liabilities"): 연간프레임(
            [(2023, 600.0, None), (2024, 500.0, None), (2025, 400.0, None)], "total_liabilities"),
        ("AAA", "total_assets"): 연간프레임(
            [(2023, 1000.0, None), (2024, 1000.0, None), (2025, 1000.0, None)], "total_assets"),
    })
    r = ci.judge_s("AAA", b, 2)
    assert r.bonus[0].passed is True


def test_S_부채비율이_늘면_부가1_미통과():
    b = 번들(results=결과행(), annual={
        ("AAA", "total_liabilities"): 연간프레임(
            [(2023, 400.0, None), (2024, 500.0, None), (2025, 600.0, None)], "total_liabilities"),
        ("AAA", "total_assets"): 연간프레임(
            [(2023, 1000.0, None), (2024, 1000.0, None), (2025, 1000.0, None)], "total_assets"),
    })
    r = ci.judge_s("AAA", b, 2)
    assert r.bonus[0].passed is False


def test_S_최근_부채가_NaN이면_부가1은_미계산():
    # NaN은 truthy라 단순 비교로는 걸러지지 않는다. 가장 최근 연도의 부채가 NaN이면
    # ratios[-1]도 NaN이 되고, "NaN <= 0.5"와 "NaN < ratios[0]"이 둘 다 False라
    # bool(False and False)로 조용히 "미통과"가 찍힌다(사유에 "nan%"까지 남는다).
    # 이 테스트는 judge_s의 any(pd.isna(v) for v in ratios) 가드를 고정한다.
    b = 번들(results=결과행(), annual={
        ("AAA", "total_liabilities"): 연간프레임(
            [(2023, 600.0, None), (2024, 500.0, None), (2025, float("nan"), None)], "total_liabilities"),
        ("AAA", "total_assets"): 연간프레임(
            [(2023, 1000.0, None), (2024, 1000.0, None), (2025, 1000.0, None)], "total_assets"),
    })
    r = ci.judge_s("AAA", b, 2)
    assert r.bonus[0].passed is None
    assert "nan" not in r.bonus[0].detail.lower()


def test_S_주식수가_전년보다_줄면_부가2_통과():
    shares = pd.DataFrame({"shares_yoy": [-2.0], "float_shares": [1000.0], "market_cap": [1.0]},
                          index=pd.Index(["AAA"], name="ticker"))
    r = ci.judge_s("AAA", 번들(results=결과행(), shares=shares), 2)
    assert r.bonus[1].passed is True


def test_S_주식수_변화율이_없으면_부가2는_미계산():
    shares = pd.DataFrame({"shares_yoy": [None], "float_shares": [1000.0], "market_cap": [1.0]},
                          index=pd.Index(["AAA"], name="ticker"))
    r = ci.judge_s("AAA", 번들(results=결과행(), shares=shares), 2)
    assert r.bonus[1].passed is None


def test_S_경영진_지분이_크면_부가3_통과():
    analyst = pd.DataFrame({"held_pct_insiders": [0.12]}, index=pd.Index(["AAA"], name="ticker"))
    r = ci.judge_s("AAA", 번들(results=결과행(), analyst=analyst), 2)
    assert r.bonus[2].passed is True


# ---------- L 항목 ----------

def 업종_결과():
    res = pd.DataFrame({
        "close": [10.0, 20.0, 30.0, 40.0],
        "rs_rating": [95.0, 85.0, 75.0, 30.0],
    }, index=pd.Index(["AAA", "BBB", "CCC", "DDD"], name="ticker"))
    sl = pd.DataFrame({
        "ticker": ["AAA", "BBB", "CCC", "DDD"],
        "industry": ["반도체", "반도체", "반도체", "유틸리티"],
    })
    return res, sl


def test_업종통계는_평균RS와_순위를_낸다():
    res, sl = 업종_결과()
    st = ci.industry_stats(res, sl)
    assert st["반도체"]["mean_rs"] == pytest.approx(85.0)
    assert st["반도체"]["ranked"] == ["AAA", "BBB", "CCC"]


def test_L_RS가_70미만이면_즉시_F():
    res, sl = 업종_결과()
    b = 번들(results=res, stock_list=sl)
    r = ci.judge_l("DDD", b, ci.industry_stats(res, sl), None)
    assert r.grade == "F"
    assert r.coefficient == 0.0


def test_L_RS와_업종강도와_업종내순위를_모두_충족하면_핵심_통과():
    res, sl = 업종_결과()
    b = 번들(results=res, stock_list=sl)
    r = ci.judge_l("AAA", b, ci.industry_stats(res, sl), None)
    assert [c.passed for c in r.core] == [True, True, True]


def test_L_업종내_4위이하면_핵심3_미통과():
    res = pd.DataFrame({"rs_rating": [95.0, 94.0, 93.0, 92.0, 91.0]},
                       index=pd.Index(["A1", "A2", "A3", "A4", "A5"], name="ticker"))
    sl = pd.DataFrame({"ticker": ["A1", "A2", "A3", "A4", "A5"], "industry": ["반도체"] * 5})
    b = 번들(results=res, stock_list=sl)
    r = ci.judge_l("A4", b, ci.industry_stats(res, sl), None)
    assert r.core[2].passed is False


def test_L_업종이_비어있으면_핵심2와_3은_미계산():
    res = pd.DataFrame({"rs_rating": [95.0]}, index=pd.Index(["AAA"], name="ticker"))
    sl = pd.DataFrame({"ticker": ["AAA"], "industry": [""]})
    b = 번들(results=res, stock_list=sl)
    r = ci.judge_l("AAA", b, ci.industry_stats(res, sl), None)
    assert r.core[1].passed is None
    assert r.core[2].passed is None


def test_L_RS가_80이상이면_부가1_통과():
    res, sl = 업종_결과()
    b = 번들(results=res, stock_list=sl)
    r = ci.judge_l("AAA", b, ci.industry_stats(res, sl), None)
    assert r.bonus[0].passed is True


def test_L_조정구간_하락폭이_지수보다_작으면_부가2_통과():
    res, sl = 업종_결과()
    b = 번들(results=res, stock_list=sl)
    r = ci.judge_l("AAA", b, ci.industry_stats(res, sl), (-5.0, -9.0))
    assert r.bonus[1].passed is True


def test_L_조정구간_자료가_없으면_부가2는_미계산():
    res, sl = 업종_결과()
    b = 번들(results=res, stock_list=sl)
    r = ci.judge_l("AAA", b, ci.industry_stats(res, sl), None)
    assert r.bonus[1].passed is None


def test_L_RS등급이_없으면_데이터부족():
    res = pd.DataFrame({"rs_rating": [None]}, index=pd.Index(["AAA"], name="ticker"))
    sl = pd.DataFrame({"ticker": ["AAA"], "industry": [""]})
    b = 번들(results=res, stock_list=sl)
    r = ci.judge_l("AAA", b, ci.industry_stats(res, sl), None)
    assert r.grade == cs.INSUFFICIENT


def test_L_조정구간_하락률이_NaN이면_부가2는_미계산():
    # corr_drawdown은 엔트리포인트가 계산해 넘기는 (종목 하락률, 지수 하락률) 튜플이다.
    # 가격 이력이 부족하면 튜플 자체가 아니라 안의 값이 NaN으로 들어올 수 있다.
    # NaN은 truthy라 "stock_dd > index_dd" 비교가 조용히 False를 내고 사유에
    # "nan%"가 찍히며 미계산이 아니라 미통과로 둔갑한다. 이 테스트가 그 가드를 고정한다.
    res, sl = 업종_결과()
    b = 번들(results=res, stock_list=sl)
    r = ci.judge_l("AAA", b, ci.industry_stats(res, sl), (float("nan"), -9.0))
    assert r.bonus[1].passed is None
    assert "nan" not in r.bonus[1].detail.lower()


def test_L_자기_RS는_없어도_업종이_알려지면_하드F가_걸리지_않는다():
    # EEE는 자기 RS가 없다(c1=None). 하지만 같은 업종의 AAA·BBB가 유효한 RS를 갖고
    # 있어 industry_stats에는 "반도체"가 여전히 잡히므로 known=True다(핵심3의 대상인
    # ranked 목록에는 EEE 자신의 RS가 없어 빠지지만, 핵심2는 업종 평균만 보므로 계산된다).
    # 즉 core=[None, True, None]로 core_known이 비지 않아 grade_item의 INSUFFICIENT
    # 조기 반환을 피해가고 hard_fail 분기까지 실제로 도달한다. hard_fail은
    # "c1.passed is False"라 None은 걸리지 않아야 하며, 정상 등급(C)이 나와야 한다.
    # hard_fail을 "not c1.passed"로 바꾸면 None도 참이 되어 이 케이스가 조용히 F로
    # 떨어진다. 이 테스트가 그 회귀를 고정한다.
    res = pd.DataFrame({"rs_rating": [None, 95.0, 85.0]},
                       index=pd.Index(["EEE", "AAA", "BBB"], name="ticker"))
    sl = pd.DataFrame({"ticker": ["EEE", "AAA", "BBB"], "industry": ["반도체"] * 3})
    b = 번들(results=res, stock_list=sl)
    ind_stats = ci.industry_stats(res, sl)
    r = ci.judge_l("EEE", b, ind_stats, None)
    assert r.core[0].passed is None
    assert r.core[1].passed is True
    assert r.core[2].passed is None
    assert r.grade == "C"


# ---------- I 항목 ----------

def 기관이력(rows):
    # rows = [(asof, held_pct)]
    return pd.DataFrame([{"ticker": "AAA", "asof": a, "held_pct_institutions": p}
                         for a, p in rows])


def test_I_기관비중이_직전_스냅샷보다_늘면_핵심_통과():
    b = 번들(inst=기관이력([("2026-08-11", 0.60), ("2026-08-18", 0.65)]),
            analyst=pd.DataFrame({"held_pct_institutions": [0.65]},
                                 index=pd.Index(["AAA"], name="ticker")))
    r = ci.judge_i_us("AAA", b)
    assert r.core[0].passed is True


def test_I_기관비중이_줄면_핵심_미통과():
    b = 번들(inst=기관이력([("2026-08-11", 0.70), ("2026-08-18", 0.65)]),
            analyst=pd.DataFrame({"held_pct_institutions": [0.65]},
                                 index=pd.Index(["AAA"], name="ticker")))
    r = ci.judge_i_us("AAA", b)
    assert r.core[0].passed is False


def test_I_스냅샷이_하나뿐이면_데이터부족():
    b = 번들(inst=기관이력([("2026-08-18", 0.65)]),
            analyst=pd.DataFrame({"held_pct_institutions": [0.65]},
                                 index=pd.Index(["AAA"], name="ticker")))
    r = ci.judge_i_us("AAA", b)
    assert r.grade == cs.INSUFFICIENT


def test_I_이력이_아예_없으면_데이터부족():
    r = ci.judge_i_us("AAA", 번들())
    assert r.grade == cs.INSUFFICIENT


def test_I_기관비중이_90퍼센트_미만이면_부가_통과():
    b = 번들(inst=기관이력([("2026-08-11", 0.60), ("2026-08-18", 0.65)]),
            analyst=pd.DataFrame({"held_pct_institutions": [0.65]},
                                 index=pd.Index(["AAA"], name="ticker")))
    r = ci.judge_i_us("AAA", b)
    assert r.bonus[0].passed is True


def test_I_기관비중이_과도하면_부가_미통과():
    b = 번들(inst=기관이력([("2026-08-11", 0.90), ("2026-08-18", 0.95)]),
            analyst=pd.DataFrame({"held_pct_institutions": [0.95]},
                                 index=pd.Index(["AAA"], name="ticker")))
    r = ci.judge_i_us("AAA", b)
    assert r.bonus[0].passed is False


def test_I_다른_종목_이력만_있으면_데이터부족():
    # inst_history 자체는 비어있지 않지만("이력이 아예 없으면"과 다른 경로), 조회하는
    # 티커의 행이 하나도 없는 경우다. 티커 필터 후 빈 데이터프레임이 되는 경로를 검증한다.
    b = 번들(inst=기관이력([("2026-08-11", 0.60), ("2026-08-18", 0.65)]))
    r = ci.judge_i_us("BBB", b)
    assert r.grade == cs.INSUFFICIENT


def test_I_스냅샷이_세개_이상이면_최근_두개만_비교한다():
    # 스냅샷이 누적될수록(정상 운영 경로) series[-2], series[-1]이 항상 가장 최근
    # 두 개를 가리켜야 한다. 더 과거의 하락(0.80 -> 0.60)은 무시되고 최근 두 스냅샷
    # (0.60 -> 0.65)만으로 판정해야 한다.
    b = 번들(inst=기관이력([("2026-08-01", 0.80), ("2026-08-11", 0.60), ("2026-08-18", 0.65)]),
            analyst=pd.DataFrame({"held_pct_institutions": [0.65]},
                                 index=pd.Index(["AAA"], name="ticker")))
    r = ci.judge_i_us("AAA", b)
    assert r.core[0].passed is True
    assert "60.0% -> 65.0%" in r.core[0].detail


def test_I_같은_asof가_중복되면_한_스냅샷으로_취급되어_데이터부족():
    # 파이프라인이 같은 날짜의 스냅샷을 두 번 적재하면 asof가 중복된 행이 생길 수
    # 있다. 이걸 그대로 세면 "스냅샷 2개"로 착각해 비교 대상이 없는데도 핵심요소가
    # 계산된 것처럼 오판정된다. 이 테스트는 asof 중복 제거 가드를 고정한다.
    b = 번들(inst=기관이력([("2026-08-18", 0.60), ("2026-08-18", 0.65)]),
            analyst=pd.DataFrame({"held_pct_institutions": [0.65]},
                                 index=pd.Index(["AAA"], name="ticker")))
    r = ci.judge_i_us("AAA", b)
    assert r.grade == cs.INSUFFICIENT
