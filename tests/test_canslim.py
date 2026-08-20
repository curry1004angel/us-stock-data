# canslim.py 엔트리포인트의 종목 루프 밖 순수 로직(지수 조정 구간, 실패율 임계값)을 검증하는 테스트
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import canslim as cm


def 지수프레임(closes):
    n = len(closes)
    dates = [f"2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}" for i in range(n)]
    return pd.DataFrame({"date": dates, "close": closes})


# ---------- _index_drawdown ----------

def test_고점_이후_저점까지의_하락률을_계산한다():
    closes = [100.0] * 30 + [110.0] + [90.0] * 29
    dd = cm._index_drawdown(지수프레임(closes))
    assert dd is not None
    assert dd[2] == pytest.approx((90.0 - 110.0) / 110.0 * 100)


def test_60일_미만이면_None():
    assert cm._index_drawdown(지수프레임([100.0] * 59)) is None


def test_index_df가_None이면_None():
    assert cm._index_drawdown(None) is None


def test_구간에_NaN_종가가_섞여도_죽지_않고_유효한_값만으로_계산한다():
    # 지수 로딩 경로는 개별 종목 가격과 달리 결측·0 필터가 없다(canslim_loaders.py).
    # NaN이 섞여도 idxmax/idxmin이 ValueError로 죽지 않고 유효한 종가만으로 계산해야 한다.
    closes = [100.0] * 20 + [float("nan")] * 5 + [110.0] + [90.0] * 34
    dd = cm._index_drawdown(지수프레임(closes))
    assert dd is not None
    assert dd[2] == pytest.approx((90.0 - 110.0) / 110.0 * 100)


def test_최근구간_종가가_전부_NaN이면_크래시_없이_None():
    # 이 함수는 종목 루프보다 앞서 돈다. 예전 코드는 idxmax()가 전부 NaN인 Series에서
    # ValueError("Encountered all NA values")를 던져 한 종목도 처리하기 전에 전체
    # 실행을 죽였다.
    closes = [float("nan")] * 60
    assert cm._index_drawdown(지수프레임(closes)) is None


def test_최근구간_종가가_전부_0이면_None():
    closes = [0.0] * 60
    assert cm._index_drawdown(지수프레임(closes)) is None


def test_유효한_종가가_하나뿐이면_None():
    closes = [float("nan")] * 59 + [100.0]
    assert cm._index_drawdown(지수프레임(closes)) is None


# ---------- _check_failure_rate ----------

def test_실패가_없으면_아무것도_하지_않는다(capsys):
    cm._check_failure_rate(0, 5840)
    assert capsys.readouterr().out == ""


def test_실패율이_임계값_이하이면_경고만_출력한다(capsys):
    cm._check_failure_rate(100, 5840)  # 100/5840 ≈ 1.7% < 5%
    out = capsys.readouterr().out
    assert "::warning::" in out
    assert "100" in out and "5840" in out


def test_실패율이_임계값을_넘으면_예외를_던진다():
    with pytest.raises(RuntimeError):
        cm._check_failure_rate(500, 5840)  # 500/5840 ≈ 8.6% > 5%


def test_실패율_경계값_5퍼센트는_넘지_않은_것으로_취급한다():
    # fail_rate > MAX_FAILURE_RATE 이므로 정확히 5%는 아직 통과다.
    cm._check_failure_rate(292, 5840)  # 292/5840 = 5.0%
