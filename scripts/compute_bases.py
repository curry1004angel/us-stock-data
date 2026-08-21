# 종목별 추정 베이스 라벨(1a차·2 형성중 등)을 미리 계산해 results.csv에 base_label 컬럼으로 박는 스크립트
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canslim_bases import detect_base
from canslim_loaders import BASE_INSUFFICIENT_LABEL, MIN_BASE_ROWS, load_prices

RESULTS = Path("data/screener/results.csv")


def base_label(daily: pd.DataFrame) -> str:
    # 감지 로직은 canslim_bases.detect_base에 있다. 여기서는 라벨만 쓴다.
    # 문턱 미만이면 detect_base를 부르지 않는다. 짧은 시계열에서 나오는
    # "Stage2 아님"은 근거 없는 판정이라 "자료 부족"이라고 말하는 편이 정직하다.
    if len(daily) < MIN_BASE_ROWS:
        return BASE_INSUFFICIENT_LABEL
    return detect_base(daily).label


def main():
    # 연도 창·행 필터(load_prices)도 최소 길이 문턱·라벨(MIN_BASE_ROWS,
    # BASE_INSUFFICIENT_LABEL)도 canslim_loaders 한 곳에만 둔다. canslim.py가 같은
    # 것을 읽으므로 두 CSV의 base_label이 갈라질 수 있는 축이 남아 있지 않다.
    px = load_prices()
    if not len(px):
        raise RuntimeError("가격 데이터가 없습니다. data/prices/ 를 확인하세요.")

    res = pd.read_csv(RESULTS, dtype={"ticker": str})
    targets = set(res["ticker"])
    labels = {tk: base_label(g) for tk, g in px.groupby("ticker", sort=False) if tk in targets}

    res["base_label"] = res["ticker"].map(labels).fillna("-")
    res.to_csv(RESULTS, index=False, encoding="utf-8-sig")
    print(f"추정 베이스 계산 완료: {len(labels)}종목 -> {RESULTS}")


if __name__ == "__main__":
    main()
