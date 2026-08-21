# 종목별 추정 베이스 라벨(1a차·2 형성중 등)을 미리 계산해 results.csv에 base_label 컬럼으로 박는 스크립트
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canslim_bases import detect_base
from canslim_loaders import load_prices

RESULTS = Path("data/screener/results.csv")


def base_label(daily: pd.DataFrame) -> str:
    # 감지 로직은 canslim_bases.detect_base에 있다. 여기서는 라벨만 쓴다.
    return detect_base(daily).label


def main():
    # 연도 창과 행 필터는 canslim_loaders.load_prices 한 곳에만 둔다. canslim.py도
    # 같은 함수로 읽으므로 창이 갈라져 base_label이 어긋나는 일은 없다.
    # 다만 최소 길이 조건은 아직 두 곳이 다르다. canslim.py는 len(g) >= 60일 때만
    # detect_base를 부르고(아니면 "-"), 여기서는 길이와 무관하게 부른다. 그래서
    # 일봉이 60개 미만인 신규 상장 종목은 두 CSV의 라벨이 여전히 다르다.
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
