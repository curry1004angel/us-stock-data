# 종목별 추정 베이스 라벨(1a차·2 형성중 등)을 미리 계산해 results.csv에 base_label 컬럼으로 박는 스크립트
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canslim_bases import detect_base

PRICES_DIR = Path("data/prices")
RESULTS = Path("data/screener/results.csv")


def base_label(daily: pd.DataFrame) -> str:
    # 감지 로직은 canslim_bases.detect_base에 있다. 여기서는 라벨만 쓴다.
    return detect_base(daily).label


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
