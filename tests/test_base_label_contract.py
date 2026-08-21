# canslim.py와 compute_bases.py가 같은 최소 길이 문턱·같은 라벨을 쓰는지 검사하는 테스트
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import canslim_loaders as cl
import compute_bases as cb

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def 일봉(n):
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n, freq="D"),
        "ticker": ["005930"] * n,
        "high": [101.0] * n, "low": [99.0] * n, "close": [100.0] * n,
        "volume": [1000.0] * n,
    })


def test_문턱_미만이면_자료부족_라벨이_나온다():
    # 짧은 시계열에서는 rolling(252) 구간이 전부 NaN이라 detect_base가 늘
    # "Stage2 아님"을 돌려준다. 그건 판정이 아니라 거짓이다. 진실은 "알 수 없다"다.
    assert cb.base_label(일봉(cl.MIN_BASE_ROWS - 1)) == cl.BASE_INSUFFICIENT_LABEL


def test_문턱_이상이면_실제_판정_라벨이_나온다():
    # 가드가 과하게 걸려 정상 종목까지 "자료 부족"으로 보내면 안 된다.
    assert cb.base_label(일봉(cl.MIN_BASE_ROWS)) != cl.BASE_INSUFFICIENT_LABEL


def test_두_스크립트가_문턱을_공유_상수로_읽는다():
    # 문턱을 복제하면 두 CSV의 base_label이 또 갈라진다. PRICE_YEARS가 정확히 그렇게
    # 갈라져 종목의 37~39%가 어긋났고, 그 다음엔 이 최소 길이 조건이 같은 방식으로
    # 갈라져 신규 상장 종목이 어긋났다. 세 번째는 없어야 한다.
    for name in ("canslim.py", "compute_bases.py"):
        본문 = [l for l in (SCRIPTS / name).read_text(encoding="utf-8").splitlines()
                if not l.lstrip().startswith("#")]
        assert any("MIN_BASE_ROWS" in l for l in 본문), f"{name}이 공유 문턱을 안 쓴다"
        # detect_base를 부르는 자리 주변에 길이 리터럴이 박혀 있으면 안 된다.
        for i, line in enumerate(본문):
            if "detect_base(" not in line:
                continue
            근처 = "\n".join(본문[max(0, i - 3):i + 1])
            assert not re.search(r"len\([^)]*\)\s*[<>]=?\s*\d", 근처), \
                f"{name}의 detect_base 호출 주변에 길이 리터럴이 박혀 있다: {line.strip()}"


def test_자료부족_라벨이_가격없음과_다르다():
    # canslim.py는 가격 데이터가 아예 없으면 "-"를 쓴다. 두 경우가 같은 기호로
    # 뭉개지면 "자료가 없다"와 "자료가 짧다"를 되짚을 수 없다.
    assert cl.BASE_INSUFFICIENT_LABEL != "-"
