# 종목별 추정 베이스 라벨(1a차·2 형성중 등)을 미리 계산해 results.csv에 base_label 컬럼으로 박는 스크립트
import pandas as pd
from pathlib import Path
from datetime import datetime

PRICES_DIR = Path("data/prices")
RESULTS = Path("data/screener/results.csv")


def base_label(daily: pd.DataFrame) -> str:
    # 앱 detect_bases와 동일 알고리즘의 라벨 전용 버전. 주봉 접근만 groupby로 바꿔 대량 처리에 맞춘다.
    d = daily.set_index("date").sort_index()
    c = d["close"]
    ma50, ma150, ma200 = c.rolling(50).mean(), c.rolling(150).mean(), c.rolling(200).mean()
    ma200_up = ma200 > ma200.shift(20)
    low52, high52 = d["low"].rolling(252).min(), d["high"].rolling(252).max()
    stage2 = ((c > ma150) & (c > ma200) & (ma150 > ma200) & ma200_up &
              (ma50 > ma150) & (ma50 > ma200) & (c >= low52 * 1.25) & (c >= high52 * 0.75))
    d["ma200"] = ma200
    d["vol_avg"] = d["volume"].rolling(50).mean()

    s2 = d.index[stage2.fillna(False)]
    if len(s2) == 0:
        return "Stage2 아님"
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
                continue
            bo = wd[(wd["close"] > pivot) & (wd["volume"] >= wd["vol_avg"] * 1.4)]  # 거래량 동반 돌파
            if not bo.empty:
                if count == 0:
                    count, sub = 1, "a"
                else:
                    rise = (pivot - last_pivot) / last_pivot if last_pivot else 1
                    count, sub = (count + 1, "a") if rise >= 0.20 else (count, chr(ord(sub) + 1))
                last_pivot, in_base, peak = pivot, False, wk_close

    if in_base:
        return f"{count + 1} 형성중"
    return f"{count}{sub}차" if count else "베이스 없음"


def main():
    this = datetime.today().year
    frames = []
    for y in range(this - 4, this + 1):                              # 베이스 카운트용 최근 5년
        p = PRICES_DIR / f"{y}.parquet"
        if p.exists():
            frames.append(pd.read_parquet(p, columns=["date", "ticker", "high", "low", "close", "volume"]))
    if not frames:
        raise RuntimeError("가격 데이터가 없습니다. data/prices/ 를 확인하세요.")
    px = pd.concat(frames, ignore_index=True)
    px["date"] = pd.to_datetime(px["date"].astype(str), format="%Y%m%d")
    px = px[(px[["high", "low", "close"]] > 0).all(axis=1)].sort_values(["ticker", "date"])

    res = pd.read_csv(RESULTS, dtype={"ticker": str})
    targets = set(res["ticker"])
    labels = {tk: base_label(g) for tk, g in px.groupby("ticker", sort=False) if tk in targets}

    res["base_label"] = res["ticker"].map(labels).fillna("-")
    res.to_csv(RESULTS, index=False, encoding="utf-8-sig")
    print(f"추정 베이스 계산 완료: {len(labels)}종목 -> {RESULTS}")


if __name__ == "__main__":
    main()
