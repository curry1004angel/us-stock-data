# 지수 일봉 정규화 로직을 검증하는 테스트
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_indices as fi


def fdr_like():
    # FDR DataReader 출력 모양. 인덱스=DatetimeIndex, 컬럼=Open/High/Low/Close/Volume.
    idx = pd.DatetimeIndex(["2026-08-12", "2026-08-13"], name="Date")
    return pd.DataFrame(
        {"Open": [2500.0, 2510.0], "High": [2520.0, 2530.0], "Low": [2490.0, 2505.0],
         "Close": [2515.0, 2525.0], "Volume": [400000.0, 410000.0]},
        index=idx,
    )


def test_날짜가_YYYYMMDD_문자열이_된다():
    out = fi.normalize(fdr_like())
    assert list(out["date"]) == ["20260812", "20260813"]
    # pandas 3.0.3에서 dt.strftime은 StringDtype 또는 object를 반환할 수 있다
    assert str(out["date"].dtype) in ("object", "string", "str")


def test_컬럼이_소문자로_통일된다():
    out = fi.normalize(fdr_like())
    assert list(out.columns) == ["date", "open", "high", "low", "close", "volume"]


def test_거래량이_없는_지수는_0으로_채운다():
    df = fdr_like().drop(columns=["Volume"])
    out = fi.normalize(df)
    assert list(out["volume"]) == [0, 0]


def test_날짜순으로_정렬된다():
    df = fdr_like().iloc[::-1]
    out = fi.normalize(df)
    assert list(out["date"]) == ["20260812", "20260813"]


def test_빈_입력은_빈_결과():
    out = fi.normalize(pd.DataFrame())
    assert len(out) == 0
    assert list(out.columns) == ["date", "open", "high", "low", "close", "volume"]
