# 베이스 감지와 피봇·매수범위 계산을 검증하는 테스트
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import canslim_bases as cb


def 상승후_조정_일봉():
    # 300거래일: 앞 250일 꾸준한 상승으로 Stage2를 만들고, 뒤 50일에 10% 조정을 넣는다.
    dates = pd.bdate_range("2024-01-01", periods=300)
    close = [50 + i * 0.4 for i in range(250)]
    peak = close[-1]
    close += [peak * (1 - 0.10 * (i + 1) / 50) for i in range(50)]
    return pd.DataFrame({
        "date": dates,
        "high": [c * 1.01 for c in close],
        "low": [c * 0.99 for c in close],
        "close": close,
        "volume": [1_000_000] * 300,
    })


def test_Stage2가_아니면_라벨이_Stage2_아님():
    dates = pd.bdate_range("2024-01-01", periods=300)
    close = [100 - i * 0.2 for i in range(300)]           # 계속 하락
    daily = pd.DataFrame({"date": dates, "high": close, "low": close,
                          "close": close, "volume": [1_000_000] * 300})
    st = cb.detect_base(daily)
    assert st.label == "Stage2 아님"
    assert st.pivot is None


def test_조정중이면_형성중이고_피봇은_직전_고점():
    st = cb.detect_base(상승후_조정_일봉())
    assert st.in_base is True
    assert "형성중" in st.label
    assert st.pivot == pytest.approx(149.6, abs=2.0)      # 250일차 종가 근처(rolling(252) 워밍업 때문에 최대 5거래일 지연)


def test_데이터가_짧으면_Stage2_아님():
    dates = pd.bdate_range("2026-01-01", periods=30)
    daily = pd.DataFrame({"date": dates, "high": [10.0] * 30, "low": [10.0] * 30,
                          "close": [10.0] * 30, "volume": [1000] * 30})
    st = cb.detect_base(daily)
    assert st.label == "Stage2 아님"


def test_매수범위는_피봇에서_5퍼센트():
    assert cb.buy_range(100.0) == (100.0, 105.0)
    assert cb.buy_range(None) is None


def test_상태는_종가와_피봇_관계로_결정된다():
    assert cb.base_status(100.0, None, False) == "베이스 없음"
    assert cb.base_status(90.0, 100.0, True) == "베이스 형성중"
    assert cb.base_status(102.0, 100.0, False) == "매수범위 내"
    assert cb.base_status(100.0, 100.0, False) == "매수범위 내"
    assert cb.base_status(105.0, 100.0, False) == "매수범위 내"
    assert cb.base_status(105.01, 100.0, False) == "매수범위 초과(추격 금지)"
    assert cb.base_status(99.0, 100.0, False) == "피봇 아래"


def test_돌파확인은_종가와_거래량을_같이_본다():
    # 스펙 5.10. 종가 > 피봇 그리고 거래량 >= 50일 평균 × 1.4
    assert cb.breakout_confirmed(101.0, 1_500_000, 100.0, 1_000_000) is True
    assert cb.breakout_confirmed(101.0, 1_300_000, 100.0, 1_000_000) is False
    assert cb.breakout_confirmed(99.0, 2_000_000, 100.0, 1_000_000) is False


def test_돌파확인은_피봇이나_평균거래량이_없으면_None():
    assert cb.breakout_confirmed(101.0, 1_500_000, None, 1_000_000) is None
    assert cb.breakout_confirmed(101.0, 1_500_000, 100.0, None) is None
