# 주봉 베이스를 감지해 라벨·피봇가·매수범위·현재 상태를 산출하는 모듈
#
# 알고리즘은 compute_bases.py의 base_label()을 그대로 옮긴 것이다. 매일 도는
# results.csv의 base_label이 달라지면 안 되므로 판정 로직은 건드리지 않고
# 반환값만 넓혔다. compute_bases.py는 이 모듈의 detect_base를 import해 쓴다.
from dataclasses import dataclass

import pandas as pd

BUY_RANGE_PCT = 0.05          # 매수범위 = 피봇 ~ 피봇 × 1.05 (스펙 5.10)
BREAKOUT_VOL_MULT = 1.4       # 돌파 확인 거래량 배수


@dataclass
class BaseState:
    label: str
    pivot: float | None
    base_low: float | None
    weeks: int
    in_base: bool


def detect_base(daily: pd.DataFrame) -> BaseState:
    d = daily.set_index("date").sort_index()
    c = d["close"]
    ma50, ma150, ma200 = c.rolling(50).mean(), c.rolling(150).mean(), c.rolling(200).mean()
    ma200_up = ma200 > ma200.shift(20)
    low52, high52 = d["low"].rolling(252).min(), d["high"].rolling(252).max()
    stage2 = ((c > ma150) & (c > ma200) & (ma150 > ma200) & ma200_up &
              (ma50 > ma150) & (ma50 > ma200) & (c >= low52 * 1.25) & (c >= high52 * 0.75))
    d = d.copy()
    d["ma200"] = ma200
    d["vol_avg"] = d["volume"].rolling(50).mean()

    s2 = d.index[stage2.fillna(False)]
    if len(s2) == 0:
        return BaseState("Stage2 아님", None, None, 0, False)
    start = s2[0]
    d["wk"] = d.index.to_period("W")

    count, sub, last_pivot = 0, "a", None
    peak, in_base = None, False
    pivot = base_low = None
    wk_cnt = 0

    for _, wd in d.groupby("wk", sort=True):
        if wd.index.max() < start:                                   # Stage2 시작 전 주봉은 건너뜀
            continue
        wk_close = wd["close"].iloc[-1]
        wk_low, wk_ma200 = wd["low"].min(), wd["ma200"].iloc[-1]
        if peak is None:
            peak = wk_close
        if in_base and pd.notna(wk_ma200) and wk_close < wk_ma200:   # 200일선 이탈 → 리셋
            count, sub, in_base, last_pivot = 0, "a", False, None
            peak = wk_close
            pivot = base_low = None
            continue
        if not in_base:
            if wk_close > peak:
                peak = wk_close
            if (peak - wk_close) / peak >= 0.08:                     # 8% 조정 → 베이스 시작
                in_base, pivot, base_low, wk_cnt = True, peak, wk_low, 1
        else:
            wk_cnt += 1
            base_low = min(base_low, wk_low)
            depth = (pivot - base_low) / pivot
            if depth > 0.50 or wk_cnt > 26:                          # 깊이/26주 리셋
                count, sub, in_base, last_pivot = 0, "a", False, None
                peak = wk_close
                pivot = base_low = None
                continue
            bo = wd[(wd["close"] > pivot) & (wd["volume"] >= wd["vol_avg"] * BREAKOUT_VOL_MULT)]
            if not bo.empty:
                if count == 0:
                    count, sub = 1, "a"
                else:
                    rise = (pivot - last_pivot) / last_pivot if last_pivot else 1
                    count, sub = (count + 1, "a") if rise >= 0.20 else (count, chr(ord(sub) + 1))
                last_pivot, in_base, peak = pivot, False, wk_close

    if in_base:
        return BaseState(f"{count + 1} 형성중", pivot, base_low, wk_cnt, True)
    label = f"{count}{sub}차" if count else "베이스 없음"
    # 돌파가 끝난 뒤에는 마지막 돌파의 피봇이 현재 유효한 매수 기준선이다.
    return BaseState(label, last_pivot, None, 0, False)


def buy_range(pivot):
    if pivot is None:
        return None
    return (pivot, pivot * (1 + BUY_RANGE_PCT))


def base_status(close, pivot, in_base):
    if pivot is None:
        return "베이스 없음"
    if in_base:
        return "베이스 형성중"
    low, high = buy_range(pivot)
    if close < low:
        return "피봇 아래"
    if close <= high:
        return "매수범위 내"
    return "매수범위 초과(추격 금지)"


def breakout_confirmed(close, volume, pivot, vol_avg50):
    if pivot is None or vol_avg50 is None or pd.isna(vol_avg50):
        return None
    return bool(close > pivot and volume >= vol_avg50 * BREAKOUT_VOL_MULT)
