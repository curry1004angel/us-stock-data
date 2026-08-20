# CANSLIM 판정에 필요한 데이터 로드를 검증하는 테스트
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import canslim_loaders as cl


def 데이터셋_만들기(root: Path):
    (root / "financials").mkdir(parents=True)
    (root / "screener").mkdir(parents=True)
    (root / "prices").mkdir(parents=True)
    (root / "indices").mkdir(parents=True)
    (root / "analyst").mkdir(parents=True)

    pd.DataFrame({
        "ticker": ["AAA", "AAA", "NA"],
        "year": [2025, 2026, 2026],
        "quarter": ["4Q", "1Q", "1Q"],
        "account": ["eps", "eps", "eps"],
        "amount": [1.0, 2.0, 3.0],
        "qoq": [None, 100.0, None],
        "yoy": [None, 50.0, None],
    }).to_parquet(root / "financials/quarterly.parquet", index=False)

    pd.DataFrame({
        "ticker": ["AAA", "AAA"], "year": [2024, 2025],
        "account": ["eps", "eps"], "amount": [3.0, 4.0], "yoy": [None, 33.3],
    }).to_parquet(root / "financials/annual.parquet", index=False)

    pd.DataFrame({
        "ticker": ["AAA", "NA"], "name": ["에이", "나노"], "market": ["NASDAQ", "NASDAQ"],
        "close": [10.0, 20.0], "rs_rating": [80.0, 40.0], "high_52w": [11.0, 30.0],
        "ma50": [9.0, 21.0], "ma200": [8.0, 22.0], "base_label": ["1a차", "베이스 없음"],
        "date": ["20260817", "20260817"],
    }).to_csv(root / "screener/results.csv", index=False, encoding="utf-8-sig")

    pd.DataFrame({
        "ticker": ["AAA", "NA"], "name": ["에이", "나노"],
        "market": ["NASDAQ", "NASDAQ"], "industry": ["반도체", ""],
    }).to_csv(root / "stock_list.csv", index=False, encoding="utf-8-sig")

    pd.DataFrame({
        "date": ["20260817", "20260817"], "ticker": ["AAA", "NA"],
        "market": ["NASDAQ", "NASDAQ"], "open": [9.0, 19.0], "high": [10.5, 20.5],
        "low": [8.5, 18.5], "close": [10.0, 20.0], "volume": [1000, 2000],
    }).to_parquet(root / f"prices/{pd.Timestamp.today().year}.parquet", index=False)

    pd.DataFrame({
        "ticker": ["AAA", "NA"], "asof": ["20260818", "20260818"],
        "shares": [100.0, 200.0], "float_shares": [90.0, 180.0],
        "market_cap": [1000.0, 4000.0], "shares_yoy": [-1.0, 5.0],
    }).to_parquet(root / "screener/shares_snapshot.parquet", index=False)

    pd.DataFrame({
        "ticker": ["AAA"], "asof": ["2026-08-18"], "held_pct_institutions": [0.7],
        "held_pct_insiders": [0.05], "eps_0y_current": [1.0], "eps_1y_current": [1.5],
    }).to_parquet(root / "analyst/snapshot.parquet", index=False)

    pd.DataFrame({
        "ticker": ["AAA", "AAA"], "asof": ["2026-08-11", "2026-08-18"],
        "held_pct_institutions": [0.6, 0.7],
    }).to_parquet(root / "analyst/institution_history.parquet", index=False)

    pd.DataFrame({
        "date": ["20260816", "20260817"], "open": [1.0, 2.0], "high": [1.0, 2.0],
        "low": [1.0, 2.0], "close": [1.0, 2.0], "volume": [10, 20],
    }).to_parquet(root / "indices/US500.parquet", index=False)


def test_티커_NA가_결측값으로_사라지지_않는다(tmp_path):
    데이터셋_만들기(tmp_path)
    b = cl.load_all(tmp_path)
    assert "NA" in set(b.stock_list["ticker"])
    assert "NA" in set(b.results.index)


def test_분기_재무는_티커와_계정으로_인덱싱된다(tmp_path):
    데이터셋_만들기(tmp_path)
    b = cl.load_all(tmp_path)
    d = b.quarterly[("AAA", "eps")]
    assert list(d["amount"]) == [1.0, 2.0]


def test_분기_재무는_시간순으로_정렬된다(tmp_path):
    데이터셋_만들기(tmp_path)
    b = cl.load_all(tmp_path)
    d = b.quarterly[("AAA", "eps")]
    assert list(zip(d["year"], d["quarter"])) == [(2025, "4Q"), (2026, "1Q")]


def test_없는_계정은_키가_없다(tmp_path):
    데이터셋_만들기(tmp_path)
    b = cl.load_all(tmp_path)
    assert ("AAA", "revenue") not in b.quarterly


def test_지수는_코드별로_나뉜다(tmp_path):
    데이터셋_만들기(tmp_path)
    b = cl.load_all(tmp_path)
    assert "US500" in b.indices
    assert len(b.indices["US500"]) == 2


def test_애널리스트_파일이_없어도_빈_프레임으로_로드된다(tmp_path):
    데이터셋_만들기(tmp_path)
    (tmp_path / "analyst/snapshot.parquet").unlink()
    b = cl.load_all(tmp_path)
    assert len(b.analyst) == 0


def test_분기키는_연도와_분기를_정렬가능한_정수로_만든다():
    assert cl.quarter_key(2026, "1Q") < cl.quarter_key(2026, "2Q")
    assert cl.quarter_key(2025, "4Q") < cl.quarter_key(2026, "1Q")


def test_필수_파일이_없으면_명확한_오류로_멈춘다(tmp_path):
    # 종목 모집단 파일이 없을 때 빈 프레임을 돌려주면 빈 결과가 커밋돼 직전 결과를 덮어쓴다.
    데이터셋_만들기(tmp_path)
    (tmp_path / "screener/results.csv").unlink()
    with pytest.raises(FileNotFoundError, match="반드시 필요한 파일"):
        cl.load_all(tmp_path)
