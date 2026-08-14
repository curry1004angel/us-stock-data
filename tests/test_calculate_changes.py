# calculate_changes의 누락 분기 보강 로직이 EPS를 건드리지 않는지 검증하는 테스트
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import calculate_changes as cc


def test_eps는_비가산_계정이다():
    # 연간 EPS는 분기 EPS의 정확한 합이 아니다(기중 주식수 변동). 또한 보강 로직이
    # int()로 캐스팅하므로 통과시키면 미국 EPS가 정수로 잘린다.
    assert "eps" in cc.NON_ADDITIVE_ACCOUNTS


def test_재무상태표_계정도_비가산이다():
    assert cc.BS_ACCOUNTS <= cc.NON_ADDITIVE_ACCOUNTS


def test_eps는_누락분기_보강에서_제외된다(tmp_path, monkeypatch):
    # eps 계정은 NON_ADDITIVE_ACCOUNTS에 있으므로, 1Q·2Q·3Q·연간이 주어져도 4Q를 보강하지 않는다.
    monkeypatch.chdir(tmp_path)

    # 테스트 디렉토리 구조 생성
    (tmp_path / "data" / "financials").mkdir(parents=True)

    # 분기 데이터: TEST 종목의 eps 1Q=1.0, 2Q=1.0, 3Q=1.0
    q_data = pd.DataFrame([
        {"ticker": "TEST", "year": 2024, "quarter": "1Q", "account": "eps", "amount": 1.0},
        {"ticker": "TEST", "year": 2024, "quarter": "2Q", "account": "eps", "amount": 1.0},
        {"ticker": "TEST", "year": 2024, "quarter": "3Q", "account": "eps", "amount": 1.0},
    ])
    q_data.to_parquet("data/financials/quarterly.parquet", index=False, compression="snappy")

    # 연간 데이터: TEST 종목의 eps 2024년 = 3.0 (투표 성립)
    a_data = pd.DataFrame([
        {"ticker": "TEST", "year": 2024, "account": "eps", "amount": 3.0},
    ])
    a_data.to_parquet("data/financials/annual.parquet", index=False, compression="snappy")

    # 보강 실행
    cc.fill_missing_quarters()

    # 보강 후 읽기
    q_result = pd.read_parquet("data/financials/quarterly.parquet")
    eps_data = q_result[q_result["account"] == "eps"]

    # eps 계정은 4Q가 보강되지 않았다
    assert len(eps_data) == 3, f"eps는 3개 행이어야 하는데 {len(eps_data)}개 있음"
    assert not any(eps_data["quarter"] == "4Q"), "eps는 4Q 행이 없어야 함"


def test_비eps_계정은_누락분기를_보강한다(tmp_path, monkeypatch):
    # net_income 계정은 NON_ADDITIVE_ACCOUNTS에 없으므로, 1Q·2Q·3Q가 주어졌을 때 4Q를 보강한다.
    monkeypatch.chdir(tmp_path)

    # 테스트 디렉토리 구조 생성
    (tmp_path / "data" / "financials").mkdir(parents=True)

    # 분기 데이터: TEST 종목의 net_income 1Q=100, 2Q=100, 3Q=100
    q_data = pd.DataFrame([
        {"ticker": "TEST", "year": 2024, "quarter": "1Q", "account": "net_income", "amount": 100.0},
        {"ticker": "TEST", "year": 2024, "quarter": "2Q", "account": "net_income", "amount": 100.0},
        {"ticker": "TEST", "year": 2024, "quarter": "3Q", "account": "net_income", "amount": 100.0},
    ])
    q_data.to_parquet("data/financials/quarterly.parquet", index=False, compression="snappy")

    # 연간 데이터: TEST 종목의 net_income 2024년 = 400 (투표 성립, 4Q = 400 - 300 = 100)
    a_data = pd.DataFrame([
        {"ticker": "TEST", "year": 2024, "account": "net_income", "amount": 400.0},
    ])
    a_data.to_parquet("data/financials/annual.parquet", index=False, compression="snappy")

    # 보강 실행
    cc.fill_missing_quarters()

    # 보강 후 읽기
    q_result = pd.read_parquet("data/financials/quarterly.parquet")
    ni_data = q_result[q_result["account"] == "net_income"]

    # net_income 계정은 4Q가 보강되었다
    assert len(ni_data) == 4, f"net_income은 4개 행이어야 하는데 {len(ni_data)}개 있음"
    q4_data = ni_data[ni_data["quarter"] == "4Q"]
    assert len(q4_data) == 1, "4Q 행이 정확히 1개여야 함"
    # 4Q = 연간 - (1Q+2Q+3Q) = 400 - 300 = 100
    assert q4_data.iloc[0]["amount"] == 100, f"4Q 금액은 100이어야 하는데 {q4_data.iloc[0]['amount']} 임"
