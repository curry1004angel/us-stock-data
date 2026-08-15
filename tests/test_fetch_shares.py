# 주식수 스냅샷 변환 로직을 검증하는 테스트 (미국)
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_shares as fs


def test_info에서_필드를_뽑는다():
    info = {"sharesOutstanding": 24220525225, "floatShares": 23000000000, "marketCap": 4000000000000}
    row = fs.from_info(info, "NVDA", "20260814")
    assert row["shares"] == 24220525225
    assert row["float_shares"] == 23000000000
    assert row["market_cap"] == 4000000000000
    assert row["ticker"] == "NVDA"
    assert row["asof"] == "20260814"


def test_빈_info는_None으로_채운다():
    row = fs.from_info({}, "NVDA", "20260814")
    assert row["shares"] is None
    assert row["market_cap"] is None


def test_None_info도_크래시하지_않는다():
    row = fs.from_info(None, "NVDA", "20260814")
    assert row["shares"] is None


def test_shares_qoq는_잔액표가_없으면_None():
    assert fs.shares_qoq_from_balance(None) is None
    assert fs.shares_qoq_from_balance(pd.DataFrame()) is None


def test_shares_qoq는_직전분기_대비_퍼센트():
    bs = pd.DataFrame(
        [[24220525225.0, 24304000000.0]],
        index=["Ordinary Shares Number"],
        columns=[pd.Timestamp("2026-04-30"), pd.Timestamp("2026-01-31")],
    )
    assert fs.shares_qoq_from_balance(bs) == pytest.approx(-0.34, abs=0.01)


def test_주식수_행이_없으면_None():
    bs = pd.DataFrame([[1.0, 2.0]], index=["Total Assets"],
                      columns=[pd.Timestamp("2026-04-30"), pd.Timestamp("2026-01-31")])
    assert fs.shares_qoq_from_balance(bs) is None
