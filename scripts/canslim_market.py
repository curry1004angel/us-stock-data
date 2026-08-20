# 지수 일봉에서 분산일·고점신호를 세어 시장 동향(M) 신호등을 내는 모듈
import pandas as pd

DIST_WINDOW = 25              # 스펙 5.8. 분산일을 세는 최근 거래일 수
DIST_LOOKBACK = DIST_WINDOW + 1  # 분산일 판정에 전일 거래량 비교가 필요해 창보다 하루 더 본다
DIST_DROP_PCT = 0.2           # 종가 하락 0.2% 이상
STALL_MOVE_PCT = 0.5          # 고점신호. 일간 변동률 절대값 0.5% 미만
STALL_VOL_MULT = 1.5          # 고점신호. 거래량 50일 평균의 1.5배 초과
MA200_TREND_DAYS = 22         # 200일선 상승 추세 판정 간격

ADVICE = {
    "공격": "정상 매수",
    "주의": "신규 진입 축소",
    "방어": "25% 이상 현금 확보",
}


def index_signal(df):
    d = df.sort_values("date").reset_index(drop=True)
    close, vol = d["close"], d["volume"]
    ma50, ma200 = close.rolling(50).mean(), close.rolling(200).mean()
    vol_avg50 = vol.rolling(50).mean()

    last = len(d) - 1
    last_close = close.iloc[last]
    # 종가가 결측이거나 0 이하이면 그날 값을 신뢰할 수 없다. 지수 로딩 경로(canslim_loaders.py)는
    # 개별 종목 가격(prices)과 달리 양수 필터가 없어 이런 값이 그대로 들어올 수 있다.
    # 0은 이동평균 계산에서 NaN처럼 전파되지 않아 above_ma50=False, change_pct=-100%처럼
    # 그럴듯하지만 거짓인 값을 만들므로 별도로 걸러야 한다.
    last_close_valid = not pd.isna(last_close) and last_close > 0

    above_ma50 = (bool(last_close > ma50.iloc[last])
                  if last_close_valid and not pd.isna(ma50.iloc[last]) else None)
    above_ma200 = (bool(last_close > ma200.iloc[last])
                   if last_close_valid and not pd.isna(ma200.iloc[last]) else None)
    prev200 = ma200.shift(MA200_TREND_DAYS).iloc[last]
    ma200_rising = (bool(ma200.iloc[last] > prev200)
                    if not (pd.isna(ma200.iloc[last]) or pd.isna(prev200)) else None)

    change = close.pct_change() * 100

    # 거래량이 없거나(과거 수집이 거래량 없는 소스로 폴백한 이력이 있다) 0으로 채워진
    # 날이 최근 창에 섞이면 분산일을 셀 수 없다. 셀 수 없음을 0건으로 보고하면
    # "시장이 건강하다"는 정반대 결론이 나오므로 그 경우 None으로 남긴다.
    recent_vol = vol.iloc[-DIST_LOOKBACK:]
    vol_countable = len(recent_vol) > 0 and bool((recent_vol.notna() & (recent_vol > 0)).all())

    if vol_countable:
        vol_up = vol > vol.shift(1)
        is_dist = (change <= -DIST_DROP_PCT) & vol_up
        dist_days = int(is_dist.iloc[-DIST_WINDOW:].fillna(False).sum())

        window = d.iloc[-DIST_WINDOW:]
        is_stall = (change.abs() < STALL_MOVE_PCT) & (vol > vol_avg50 * STALL_VOL_MULT)
        stall_days = window.loc[is_stall.iloc[-DIST_WINDOW:].fillna(False), "date"].tolist()
    else:
        dist_days = None
        # 분산일과 같은 이유로 고점신호도 0건("고점신호 없음")과 셀 수 없음을 구분해야 한다.
        stall_days = None

    return {
        "close": float(last_close) if last_close_valid else None,
        "change_pct": (None if not last_close_valid or pd.isna(change.iloc[last])
                       else round(float(change.iloc[last]), 2)),
        "above_ma50": above_ma50,
        "above_ma200": above_ma200,
        "ma200_rising": ma200_rising,
        "distribution_days": dist_days,
        "stall_days": None if stall_days is None else [str(x) for x in stall_days],
    }


def signal_level(distribution_days, ma_ok):
    if distribution_days >= 5:
        level = "방어"
    elif distribution_days >= 3:
        level = "주의"
    else:
        level = "공격"
    # 지수가 50일선·200일선 아래이거나 200일선이 하락 추세면 최소 "주의"로 강등한다.
    if not ma_ok and level == "공격":
        level = "주의"
    return level, ADVICE[level]


def _ma_status(s):
    # 세 이평선 신호 중 하나라도 확실히 깨졌으면 False, 데이터 부족으로 판정
    # 자체가 안 되면 None(불명)이다. 불명을 False로 뭉개면 이력이 짧은 지수 때문에
    # 시장 전체가 부당하게 강등된다.
    flags = (s["above_ma50"], s["above_ma200"], s["ma200_rising"])
    if any(f is False for f in flags):
        return False
    if any(f is None for f in flags):
        return None
    return True


def market_signal(indices):
    empty = {"per_index": {}, "distribution_days": None, "ma_ok": None,
             "signal": None, "advice": None}
    if not indices:
        return empty

    per = {code: index_signal(df) for code, df in indices.items() if len(df)}
    if not per:
        return empty

    # 여러 지수 중 가장 방어적인 쪽을 따른다. 분산일은 셀 수 있는 지수 중 최댓값을,
    # 이평선은 하나라도 확실히 깨지면 실패를 따른다. 분산일을 셀 수 없는 지수를
    # 0건으로 취급해 최댓값 계산에서 시장을 유리하게 만들지 않는다.
    known_dist = [s["distribution_days"] for s in per.values() if s["distribution_days"] is not None]
    dist = max(known_dist) if known_dist else None

    statuses = [_ma_status(s) for s in per.values()]
    if any(st is False for st in statuses):
        ma_ok = False
    elif any(st is None for st in statuses):
        ma_ok = None
    else:
        ma_ok = True

    if dist is None:
        # 어느 지수의 분산일도 셀 수 없으면 신호등 자체를 매길 수 없다.
        level, advice = None, None
    else:
        # 이평선이 불명(None)인 것은 확실한 실패(False)가 아니므로 강등 트리거로 쓰지 않는다.
        level, advice = signal_level(dist, ma_ok is not False)

    return {"per_index": per, "distribution_days": dist, "ma_ok": ma_ok,
            "signal": level, "advice": advice}
