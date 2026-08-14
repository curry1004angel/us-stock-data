# calculate_changes의 누락 분기 보강 로직이 EPS를 건드리지 않는지 검증하는 테스트
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import calculate_changes as cc


def test_eps는_비가산_계정이다():
    # 연간 EPS는 분기 EPS의 정확한 합이 아니다(기중 주식수 변동). 또한 보강 로직이
    # int()로 캐스팅하므로 통과시키면 미국 EPS가 정수로 잘린다.
    assert "eps" in cc.NON_ADDITIVE_ACCOUNTS


def test_재무상태표_계정도_비가산이다():
    assert cc.BS_ACCOUNTS <= cc.NON_ADDITIVE_ACCOUNTS
