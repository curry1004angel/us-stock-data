# SEC 시드의 EPS 태그·단위 처리를 검증하는 테스트
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import seed_financials_from_sec as seed


def facts(unit, val, start="2025-01-01", end="2025-03-31"):
    # companyfacts의 us-gaap 노드 모양. 한 태그에 units 딕셔너리가 달린다.
    return {
        "EarningsPerShareBasic": {
            "units": {unit: [{"start": start, "end": end, "val": val, "filed": "2025-05-01"}]}
        }
    }


def test_CONCEPTS에_eps가_있다():
    assert "eps" in seed.CONCEPTS
    assert "EarningsPerShareBasic" in seed.CONCEPTS["eps"]


def test_계정별_단위가_정의돼_있다():
    assert seed.UNIT_FOR["eps"] == "USD/shares"
    assert seed.UNIT_FOR["revenue"] == "USD"


def test_EPS는_주당단위에서_소수로_읽는다():
    got = seed.extract_account(facts("USD/shares", 0.76), seed.CONCEPTS["eps"],
                               unit="USD/shares", cast=float)
    assert got == {(2025, "1Q"): 0.76}


def test_금액계정은_기본값으로_USD_정수를_읽는다():
    node = {"Revenues": {"units": {"USD": [
        {"start": "2025-01-01", "end": "2025-03-31", "val": 1000, "filed": "2025-05-01"}]}}}
    got = seed.extract_account(node, ["Revenues"])
    assert got == {(2025, "1Q"): 1000}


def test_단위가_없으면_빈_결과():
    got = seed.extract_account(facts("USD", 0.76), seed.CONCEPTS["eps"],
                               unit="USD/shares", cast=float)
    assert got == {}


def test_연간_기간도_잡는다():
    got = seed.extract_account(facts("USD/shares", 4.93, "2025-01-01", "2025-12-31"),
                               seed.CONCEPTS["eps"], unit="USD/shares", cast=float)
    assert got == {(2025, "annual"): 4.93}
