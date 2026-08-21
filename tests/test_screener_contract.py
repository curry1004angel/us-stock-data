# 스크리너가 내보내는 열과 CANSLIM 판정이 읽는 열이 어긋나지 않는지 검사하는 테스트
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import trend_template as tt

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def 판정이_읽는_열():
    """canslim_items.py가 _res로 results에서 읽는 열 이름을 소스에서 뽑아낸다."""
    src = (SCRIPTS / "canslim_items.py").read_text(encoding="utf-8")
    return set(re.findall(r'_res\(b, ticker, "([a-z_0-9]+)"', src))


def test_판정이_읽는_열이_전부_스크리너_출력에_있다():
    # 이 계약이 깨지면 해당 항목의 요소가 조용히 미계산으로 떨어진다. 예외도 경고도
    # 나지 않고, _summarize가 통과한 부가요소만 사유에 넣으므로 사유 문자열에도 안 뜬다.
    # 실제로 volume이 빠져 있어 N 항목 부가요소가 전 종목 미계산이었고, 그 결과
    # N 등급이 A·B를 하나도 내지 못하고 C에 상한이 걸려 있었다.
    빠진_열 = 판정이_읽는_열() - set(tt.OUT_COLS)
    assert 빠진_열 == set(), f"스크리너 출력에 없는 열을 판정이 읽는다: {sorted(빠진_열)}"


def test_거래량이_스크리너_출력에_있다():
    # 위 테스트가 소스 파싱에 의존하므로, 정규식이 깨져도 volume만은 잡히게 따로 고정한다.
    assert "volume" in tt.OUT_COLS
