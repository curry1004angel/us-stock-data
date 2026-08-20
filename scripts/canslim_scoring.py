# CANSLIM 항목별 판정 결과를 등급·계수로 환산하고 종목 총점을 내는 모듈
from dataclasses import dataclass, field

# 스펙 5.1. L을 가장 무겁게 둔다(오닐이 "부진 종목을 피하라"를 가장 강하게 반복한다).
# M은 시장 전체에 하나뿐인 값이라 종목 간 변별력이 없어 총점에 넣지 않는다.
WEIGHTS = {"C": 20, "A": 20, "N": 15, "S": 10, "L": 25, "I": 10}
GRADE_COEF = {"A": 1.00, "B": 0.85, "C": 0.70, "D": 0.40, "F": 0.00}

INSUFFICIENT = "데이터부족"
# 데이터부족 항목이 이 수 이상이면 종목 전체를 판정 불가로 본다 (스펙 5.9).
MAX_INSUFFICIENT = 3


@dataclass
class Criterion:
    # passed가 None이면 "데이터부족"이다. False(미통과)와 반드시 구분한다.
    name: str
    passed: bool | None
    detail: str = ""


@dataclass
class ItemResult:
    key: str
    grade: str
    coefficient: float | None
    core: list = field(default_factory=list)
    bonus: list = field(default_factory=list)
    reason: str = ""


def _summarize(core, bonus, unknown_core):
    parts = []
    for c in core:
        mark = {True: "통과", False: "미통과", None: "미계산"}[c.passed]
        parts.append(f"{c.name} {mark}" + (f"({c.detail})" if c.detail else ""))
    for b in bonus:
        if b.passed:
            parts.append(f"[가점] {b.name}" + (f"({b.detail})" if b.detail else ""))
    if unknown_core:
        parts.append(f"핵심요소 미계산 {unknown_core}건으로 A등급 제외")
    return " / ".join(parts)


def grade_item(key, core, bonus, hard_fail=False):
    """핵심요소·부가요소 판정 목록을 등급으로 환산한다.

    hard_fail은 L 항목의 RS 70 미만처럼 다른 요소와 무관하게 즉시 F인 경우에 쓴다.
    """
    core_known = [c for c in core if c.passed is not None]
    unknown_core = len(core) - len(core_known)

    if not core_known:
        return ItemResult(key, INSUFFICIENT, None, core, bonus,
                          "핵심요소를 계산할 데이터가 없다")

    if hard_fail:
        return ItemResult(key, "F", GRADE_COEF["F"], core, bonus,
                          _summarize(core, bonus, 0))

    passed = [c for c in core_known if c.passed]
    if not passed:
        grade = "F"
    elif len(passed) < len(core_known):
        grade = "D"
    else:
        bonus_known = [b for b in bonus if b.passed is not None]
        n_bonus_pass = sum(1 for b in bonus_known if b.passed)
        if bonus_known and n_bonus_pass == len(bonus_known):
            grade = "A"
        elif n_bonus_pass:
            grade = "B"
        else:
            grade = "C"
        # 미계산 핵심요소가 있으면 "전부 통과"를 증명할 수 없으므로 A를 주지 않는다.
        if grade == "A" and unknown_core:
            grade = "B"

    return ItemResult(key, grade, GRADE_COEF[grade], core, bonus,
                      _summarize(core, bonus, unknown_core))


def total_score(items):
    """항목 결과 목록에서 총점을 낸다. 데이터부족 항목은 분모에서 뺀다."""
    insufficient = [i.key for i in items if i.coefficient is None]
    used = [i for i in items if i.coefficient is not None]
    denom = sum(WEIGHTS[i.key] for i in used)
    numer = sum(i.coefficient * WEIGHTS[i.key] for i in used)

    score = round(numer / denom * 100, 2) if denom else None
    verdict = "판정불가" if len(insufficient) >= MAX_INSUFFICIENT else "정상"
    return {"score": score, "verdict": verdict, "used_weight": denom,
            "insufficient": insufficient}
