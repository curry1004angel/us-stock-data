# 시장 동향(M) 신호 계산을 검증하는 테스트
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import canslim_market as cm


def 지수(closes, volumes=None):
    n = len(closes)
    volumes = volumes or [1_000_000] * n
    dates = [f"2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}" for i in range(n)]
    return pd.DataFrame({
        "date": dates, "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": volumes,
    })


def 상승지수(n=300):
    return 지수([100 + i * 0.3 for i in range(n)])


def test_상승추세_지수는_이평선_조건을_모두_만족():
    s = cm.index_signal(상승지수())
    assert s["above_ma50"] is True
    assert s["above_ma200"] is True
    assert s["ma200_rising"] is True


def test_하락추세_지수는_이평선_조건을_깬다():
    s = cm.index_signal(지수([200 - i * 0.3 for i in range(300)]))
    assert s["above_ma200"] is False
    assert s["ma200_rising"] is False


def test_분산일은_하락과_거래량증가가_동시에_있는_날만_센다():
    closes = [100 + i * 0.3 for i in range(275)]
    vols = [1_000_000] * 275
    # 마지막 25거래일 중 3일을 분산일로 만든다.
    for k in (270, 272, 274):
        closes[k] = closes[k - 1] * 0.99      # 1% 하락
        vols[k] = vols[k - 1] * 1.2           # 거래량 증가
    s = cm.index_signal(지수(closes, vols))
    assert s["distribution_days"] == 3


def test_하락했어도_거래량이_줄면_분산일이_아니다():
    closes = [100 + i * 0.3 for i in range(275)]
    vols = [1_000_000] * 275
    closes[274] = closes[273] * 0.99
    vols[274] = vols[273] * 0.8
    s = cm.index_signal(지수(closes, vols))
    assert s["distribution_days"] == 0


def test_고점신호는_변동이_작은데_거래량만_큰_날():
    closes = [100 + i * 0.3 for i in range(275)]
    vols = [1_000_000] * 275
    closes[274] = closes[273] * 1.001         # 변동 0.1%
    vols[274] = 2_000_000                     # 50일 평균의 2배
    s = cm.index_signal(지수(closes, vols))
    assert len(s["stall_days"]) == 1


def test_신호등은_분산일_수로_갈린다():
    assert cm.signal_level(0, True)[0] == "공격"
    assert cm.signal_level(2, True)[0] == "공격"
    assert cm.signal_level(3, True)[0] == "주의"
    assert cm.signal_level(4, True)[0] == "주의"
    assert cm.signal_level(5, True)[0] == "방어"


def test_이평선이_깨지면_분산일이_없어도_최소_주의():
    assert cm.signal_level(0, False)[0] == "주의"
    assert cm.signal_level(5, False)[0] == "방어"


def test_방어_권고에_현금_문구가_들어간다():
    assert "현금" in cm.signal_level(5, True)[1]


def test_시장신호는_가장_방어적인_지수를_따른다():
    up = 상승지수()
    down = 지수([200 - i * 0.3 for i in range(300)])
    out = cm.market_signal({"US500": up, "IXIC": down})
    assert out["ma_ok"] is False
    assert out["signal"] in ("주의", "방어")
    assert set(out["per_index"]) == {"US500", "IXIC"}


def test_지수가_하나도_없으면_신호가_None():
    out = cm.market_signal({})
    assert out["signal"] is None


# ---------- 결측·0 함정 회귀 테스트 ----------

def test_거래량이_전부_0이면_분산일은_0이_아니라_None():
    # 과거 지수 수집이 거래량 없는 소스로 폴백하면 volume이 전부 0으로 채워진다.
    # 이때 분산일 0건("시장이 건강하다")을 보고하면 안 되고 셀 수 없음(None)이어야 한다.
    closes = [100 + i * 0.3 for i in range(275)]
    vols = [0] * 275
    s = cm.index_signal(지수(closes, vols))
    assert s["distribution_days"] is None
    assert s["stall_days"] == []


def test_최근창에_거래량_결측일이_하루만_섞여도_분산일은_None():
    closes = [100 + i * 0.3 for i in range(275)]
    vols = [1_000_000] * 275
    vols[260] = float("nan")  # 최근 26거래일(DIST_LOOKBACK) 안쪽
    s = cm.index_signal(지수(closes, vols))
    assert s["distribution_days"] is None


def test_거래량이_오래된_결측이면_분산일_계산에_영향없다():
    closes = [100 + i * 0.3 for i in range(275)]
    vols = [1_000_000] * 275
    vols[0] = 0  # 최근 26거래일 밖(맨 앞)이라 계산 창에 들어오지 않는다.
    s = cm.index_signal(지수(closes, vols))
    assert s["distribution_days"] == 0


def test_시장신호는_분산일을_셀_수_있는_지수만으로_합산한다():
    # 한 지수는 거래량이 통째로 0(수집 실패), 다른 지수는 정상.
    up = 상승지수()
    broken = 지수([100 + i * 0.3 for i in range(300)], volumes=[0] * 300)
    out = cm.market_signal({"US500": up, "IXIC": broken})
    assert out["per_index"]["IXIC"]["distribution_days"] is None
    assert out["distribution_days"] == 0     # 셀 수 있는 US500만으로 합산
    assert out["signal"] == "공격"


def test_모든_지수가_거래량_결측이면_신호_전체가_None():
    broken1 = 지수([100 + i * 0.3 for i in range(300)], volumes=[0] * 300)
    broken2 = 지수([200 - i * 0.3 for i in range(300)], volumes=[0] * 300)
    out = cm.market_signal({"US500": broken1, "IXIC": broken2})
    assert out["distribution_days"] is None
    assert out["signal"] is None
    assert out["advice"] is None


def test_이평선_이력이_모자란_지수가_있어도_시장이_부당하게_강등되지_않는다():
    # US500은 200일치가 다 있어 이평선이 전부 정상. IXIC는 100거래일뿐이라
    # above_ma200·ma200_rising을 판정할 수 없다(None). 이 None을 False로 뭉개면
    # 확실한 이평선 붕괴가 하나도 없는데도 시장이 "주의"로 강등된다.
    healthy = 상승지수(n=300)
    short = 지수([100 + i * 0.3 for i in range(100)])
    out = cm.market_signal({"US500": healthy, "IXIC": short})
    assert out["per_index"]["IXIC"]["above_ma200"] is None
    assert out["ma_ok"] is None            # False가 아니라 "불명"이어야 한다.
    assert out["signal"] == "공격"          # 확실한 붕괴가 없으므로 부당하게 강등되지 않는다.


def test_한_지수가_완전히_비어도_시장신호는_나머지로_계산된다():
    up = 상승지수()
    empty = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    out = cm.market_signal({"US500": up, "IXIC": empty})
    assert set(out["per_index"]) == {"US500"}
    assert out["signal"] == "공격"


def test_분산일이_다른_두_지수는_평균이_아니라_최댓값을_따른다():
    # US500은 분산일 2일, IXIC는 분산일 5일. 평균(3.5)이나 합(7)이 아니라
    # 더 방어적인 5(IXIC 값)를 시장 전체 분산일로 써야 한다.
    closes2 = [100 + i * 0.3 for i in range(275)]
    vols2 = [1_000_000] * 275
    for k in (271, 273):
        closes2[k] = closes2[k - 1] * 0.99
        vols2[k] = vols2[k - 1] * 1.2
    two = 지수(closes2, vols2)

    closes5 = [100 + i * 0.3 for i in range(275)]
    vols5 = [1_000_000] * 275
    for k in (265, 267, 269, 271, 273):
        closes5[k] = closes5[k - 1] * 0.99
        vols5[k] = vols5[k - 1] * 1.2
    five = 지수(closes5, vols5)

    assert cm.index_signal(two)["distribution_days"] == 2
    assert cm.index_signal(five)["distribution_days"] == 5

    out = cm.market_signal({"US500": two, "IXIC": five})
    assert out["distribution_days"] == 5
    assert out["signal"] == "방어"
