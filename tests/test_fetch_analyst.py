# 재개 가능한 대상 선별 로직을 검증하는 테스트
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_analyst as fa


def existing():
    # asof는 fetch_analyst가 저장하는 형식 그대로 YYYY-MM-DD다.
    return pd.DataFrame({"ticker": ["AAPL", "NVDA"], "asof": ["2026-08-10", "2026-01-01"]})


def test_없는_종목은_대상이다():
    todo = fa.pending_tickers(["AAPL", "NVDA", "MSFT"], existing(), 30, "2026-08-14")
    assert "MSFT" in todo


def test_최근_수집된_종목은_제외된다():
    todo = fa.pending_tickers(["AAPL", "NVDA", "MSFT"], existing(), 30, "2026-08-14")
    assert "AAPL" not in todo


def test_오래된_종목은_다시_대상이다():
    todo = fa.pending_tickers(["AAPL", "NVDA", "MSFT"], existing(), 30, "2026-08-14")
    assert "NVDA" in todo


def test_기존_파일이_비어도_전부_대상():
    empty = pd.DataFrame(columns=["ticker", "asof"])
    todo = fa.pending_tickers(["AAPL", "NVDA"], empty, 30, "2026-08-14")
    assert todo == ["AAPL", "NVDA"]


def test_None이면_전부_대상():
    assert fa.pending_tickers(["AAPL"], None, 30, "2026-08-14") == ["AAPL"]


def test_입력_순서가_유지된다():
    todo = fa.pending_tickers(["MSFT", "NVDA", "GOOG"], existing(), 30, "2026-08-14")
    assert todo == ["MSFT", "NVDA", "GOOG"]


def test_깨진_asof는_대상으로_남긴다():
    df = pd.DataFrame({"ticker": ["AAPL"], "asof": ["없음"]})
    assert fa.pending_tickers(["AAPL"], df, 30, "2026-08-14") == ["AAPL"]


def test_history_rows는_세_컬럼만_뽑는다():
    rows = [{"ticker": "AAPL", "asof": "2026-08-10", "held_pct_institutions": 0.62,
             "rec_mean": 1.8, "n_analysts": 30}]
    hist = fa.history_rows(rows)
    assert hist == [{"ticker": "AAPL", "asof": "2026-08-10", "held_pct_institutions": 0.62}]


def test_보유비중이_None이어도_행이_살아남는다():
    rows = [{"ticker": "NVDA", "asof": "2026-08-10", "held_pct_institutions": None}]
    hist = fa.history_rows(rows)
    assert len(hist) == 1
    assert hist[0]["held_pct_institutions"] is None


def test_컬럼_자체가_없어도_행이_빠지지_않는다():
    rows = [{"ticker": "MSFT", "asof": "2026-08-10"}]
    hist = fa.history_rows(rows)
    assert len(hist) == 1
    assert hist[0]["held_pct_institutions"] is None


def test_여러_행이_모두_보존된다():
    rows = [
        {"ticker": "AAPL", "asof": "2026-08-10", "held_pct_institutions": 0.62},
        {"ticker": "NVDA", "asof": "2026-08-10", "held_pct_institutions": None},
    ]
    hist = fa.history_rows(rows)
    assert len(hist) == 2
