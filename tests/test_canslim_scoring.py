# CANSLIM 등급 산출과 총점 재정규화를 검증하는 테스트
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import canslim_scoring as cs


def crit(passed, name="요소"):
    return cs.Criterion(name=name, passed=passed)


def test_핵심전부통과_부가전부통과면_A():
    r = cs.grade_item("C", [crit(True), crit(True)], [crit(True)])
    assert r.grade == "A"
    assert r.coefficient == 1.00


def test_핵심전부통과_부가일부통과면_B():
    r = cs.grade_item("C", [crit(True)], [crit(True), crit(False)])
    assert r.grade == "B"
    assert r.coefficient == 0.85


def test_핵심전부통과_부가0개통과면_C():
    r = cs.grade_item("C", [crit(True)], [crit(False), crit(False)])
    assert r.grade == "C"
    assert r.coefficient == 0.70


def test_핵심일부만_통과면_D():
    r = cs.grade_item("C", [crit(True), crit(False)], [crit(True)])
    assert r.grade == "D"
    assert r.coefficient == 0.40


def test_핵심전부실패면_F():
    r = cs.grade_item("C", [crit(False), crit(False)], [crit(True)])
    assert r.grade == "F"
    assert r.coefficient == 0.00


def test_핵심이_전부_미계산이면_데이터부족():
    r = cs.grade_item("C", [crit(None), crit(None)], [crit(True)])
    assert r.grade == cs.INSUFFICIENT
    assert r.coefficient is None


def test_핵심이_일부_미계산이면_A를_주지_않는다():
    # "핵심요소 전부 통과"를 증명할 수 없으므로 상한은 B다.
    r = cs.grade_item("C", [crit(True), crit(None)], [crit(True)])
    assert r.grade == "B"
    assert "미계산" in r.reason


def test_부가가_전부_미계산이면_C():
    r = cs.grade_item("C", [crit(True)], [crit(None), crit(None)])
    assert r.grade == "C"


def test_부가요소가_아예_없으면_C():
    r = cs.grade_item("S", [crit(True)], [])
    assert r.grade == "C"


def test_hard_fail이면_핵심통과여부와_무관하게_F():
    # L 항목의 RS 70 미만은 즉시 F다 (스펙 5.6).
    r = cs.grade_item("L", [crit(False), crit(True)], [crit(True)], hard_fail=True)
    assert r.grade == "F"
    assert r.coefficient == 0.00


def test_총점은_가중평균():
    items = [
        cs.grade_item("C", [crit(True)], [crit(True)]),   # A 1.00 × 20
        cs.grade_item("A", [crit(True)], [crit(True)]),   # A 1.00 × 20
        cs.grade_item("N", [crit(True)], [crit(True)]),   # A 1.00 × 15
        cs.grade_item("S", [crit(True)], [crit(True)]),   # A 1.00 × 10
        cs.grade_item("L", [crit(True)], [crit(True)]),   # A 1.00 × 25
        cs.grade_item("I", [crit(True)], [crit(True)]),   # A 1.00 × 10
    ]
    out = cs.total_score(items)
    assert out["score"] == pytest.approx(100.0)
    assert out["used_weight"] == 100
    assert out["verdict"] == "정상"


def test_데이터부족_항목은_분모에서_빠지고_재정규화된다():
    items = [
        cs.grade_item("C", [crit(True)], [crit(True)]),    # A 1.00 × 20
        cs.grade_item("A", [crit(None)], [crit(True)]),    # 데이터부족 → 제외
        cs.grade_item("N", [crit(False)], [crit(True)]),   # F 0.00 × 15
        cs.grade_item("S", [crit(True)], [crit(True)]),    # A 1.00 × 10
        cs.grade_item("L", [crit(True)], [crit(True)]),    # A 1.00 × 25
        cs.grade_item("I", [crit(True)], [crit(True)]),    # A 1.00 × 10
    ]
    out = cs.total_score(items)
    # 분모 80, 분자 20 + 0 + 10 + 25 + 10 = 65
    assert out["used_weight"] == 80
    assert out["score"] == pytest.approx(65 / 80 * 100)
    assert out["insufficient"] == ["A"]


def test_데이터부족이_3개_이상이면_판정불가():
    items = [
        cs.grade_item("C", [crit(None)], []),
        cs.grade_item("A", [crit(None)], []),
        cs.grade_item("N", [crit(None)], []),
        cs.grade_item("S", [crit(True)], []),
        cs.grade_item("L", [crit(True)], []),
        cs.grade_item("I", [crit(True)], []),
    ]
    out = cs.total_score(items)
    assert out["verdict"] == "판정불가"


def test_모든_항목이_데이터부족이면_점수가_None():
    items = [cs.grade_item(k, [crit(None)], []) for k in ("C", "A", "N", "S", "L", "I")]
    out = cs.total_score(items)
    assert out["score"] is None
    assert out["used_weight"] == 0
    assert out["verdict"] == "판정불가"
