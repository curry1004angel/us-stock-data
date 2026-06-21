# 미너비니 트렌드 템플릿 8개 조건과 RS 등급으로 나스닥·뉴욕 전 종목을 스크리닝하는 스크립트
#
# 8개 조건(저자의 "협상 불가" 기준):
#   1. 종가 > 150일선 그리고 종가 > 200일선
#   2. 150일선 > 200일선
#   3. 200일선이 최소 1개월(약 22거래일) 상승 추세
#   4. 50일선 > 150일선 그리고 50일선 > 200일선
#   5. 종가가 52주 저점 대비 25% 이상 높음
#   6. 종가가 52주 고점 대비 25% 이내(고점에 가까움)
#   7. RS 등급(지수 대비 상대강도 백분위) >= 70
#   8. 종가 > 50일선
#
# RS 등급: IBD 표준 근사식으로 산출한다.
#   raw = 0.4*(C/C63) + 0.2*(C/C126) + 0.2*(C/C189) + 0.2*(C/C252)
#   (C63 = 63거래일=약 3개월 전 종가) → 전 종목 백분위로 변환해 1~99 등급 부여.
# RS 선 추세(조건 7의 참고사항): 종목/S&P500 비율이 6주(30거래일)·13주(65거래일)
#   전보다 높은지를 보조 컬럼(rs_line_up)으로 제공한다. 통과 필수 조건은 아니다.
#
# 한국판과 달리 data_warning 플래그가 없다. 미국은 가격제한폭이 없고 fdr의 OHLC가
# 이미 분할조정되어 있어, ±35% 급변을 분할 신호로 단정할 수 없기 때문이다.
# (분할 후 과거 가격이 박제되는 문제는 monthly_resync 워크플로의 재동기화로 바로잡는다.)

import datetime
from pathlib import Path

import pandas as pd
import FinanceDataReader as fdr

DATA = Path(__file__).resolve().parent.parent / "data"
PRICES_DIR = DATA / "prices"
OUT_DIR = DATA / "screener"

CONDS = ["cond1", "cond2", "cond3", "cond4", "cond5", "cond6", "cond7", "cond8"]


def load_prices() -> pd.DataFrame:
    # 이동평균(200일)·52주 고저 계산에 1년 이상 필요하므로 최근 3개 연도 파일을 로드한다.
    this_year = datetime.date.today().year
    frames = []
    for y in (this_year - 2, this_year - 1, this_year):
        p = PRICES_DIR / f"{y}.parquet"
        if p.exists():
            frames.append(pd.read_parquet(p))
    if not frames:
        raise RuntimeError("가격 데이터가 없습니다. data/prices/ 를 확인하세요.")
    df = pd.concat(frames, ignore_index=True)
    # 거래정지·결측일은 OHLC가 0으로 저장돼 52주 고저를 왜곡하므로 제외한다.
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    return df.sort_values(["ticker", "date"]).reset_index(drop=True)


def load_benchmark(start: str, end: str):
    # RS 선 추세(보조 지표)용 S&P500. 지수 심볼이 환경마다 달라 후보를 차례로 시도한다.
    s = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
    e = f"{end[:4]}-{end[4:6]}-{end[6:8]}"
    for symbol in ("US500", "SPY", "^GSPC"):
        try:
            k = fdr.DataReader(symbol, s, e).reset_index()
            if k.empty:
                continue
            date_col = next((c for c in k.columns if "date" in c.lower()), k.columns[0])
            k["date"] = pd.to_datetime(k[date_col]).dt.strftime("%Y%m%d")
            return k[["date", "Close"]].rename(columns={"Close": "bench"})
        except Exception as ex:
            print(f"  벤치마크 {symbol} 로드 실패: {ex}")
    print("S&P500 지수 로드 실패(RS 선 추세 생략).")
    return None


def main():
    df = load_prices()
    grp = df.groupby("ticker", sort=False)

    # 이동평균
    df["ma50"] = grp["close"].transform(lambda x: x.rolling(50, min_periods=50).mean())
    df["ma150"] = grp["close"].transform(lambda x: x.rolling(150, min_periods=150).mean())
    df["ma200"] = grp["close"].transform(lambda x: x.rolling(200, min_periods=200).mean())
    # 200일선 1개월(22거래일) 전 값 → 추세 판정용
    df["ma200_prev"] = grp["ma200"].shift(22)
    # 52주(252거래일) 고저
    df["low_52w"] = grp["low"].transform(lambda x: x.rolling(252, min_periods=200).min())
    df["high_52w"] = grp["high"].transform(lambda x: x.rolling(252, min_periods=200).max())
    # RS 등급 근사식용 과거 종가
    for n in (63, 126, 189, 252):
        df[f"c{n}"] = grp["close"].shift(n)

    # RS 선(종목/S&P500) 추세
    bench = load_benchmark(df["date"].min(), df["date"].max())
    has_rs_line = bench is not None
    if has_rs_line:
        df = df.merge(bench, on="date", how="left")
        df["rs_line"] = df["close"] / df["bench"]
        df["rs_line_30"] = df.groupby("ticker", sort=False)["rs_line"].shift(30)
        df["rs_line_65"] = df.groupby("ticker", sort=False)["rs_line"].shift(65)

    # 종목별 최신 거래일 한 줄만 추출
    latest = df.drop_duplicates("ticker", keep="last").copy()

    # RS 등급: IBD 근사식 → 전 종목 백분위(1~99)
    latest["rs_raw"] = (
        0.4 * latest["close"] / latest["c63"]
        + 0.2 * latest["close"] / latest["c126"]
        + 0.2 * latest["close"] / latest["c189"]
        + 0.2 * latest["close"] / latest["c252"]
    )
    latest["rs_rating"] = (latest["rs_raw"].rank(pct=True) * 99).round().astype("Int64")

    # 8개 조건
    latest["cond1"] = (latest["close"] > latest["ma150"]) & (latest["close"] > latest["ma200"])
    latest["cond2"] = latest["ma150"] > latest["ma200"]
    latest["cond3"] = latest["ma200"] > latest["ma200_prev"]
    latest["cond4"] = (latest["ma50"] > latest["ma150"]) & (latest["ma50"] > latest["ma200"])
    latest["cond5"] = latest["close"] >= latest["low_52w"] * 1.25
    latest["cond6"] = latest["close"] >= latest["high_52w"] * 0.75
    latest["cond7"] = latest["rs_rating"] >= 70
    latest["cond8"] = latest["close"] > latest["ma50"]

    latest[CONDS] = latest[CONDS].fillna(False)
    latest["pass_count"] = latest[CONDS].sum(axis=1)
    latest["pass_all"] = latest["pass_count"] == 8

    # RS 선 추세(보조)
    if has_rs_line:
        latest["rs_line_up"] = (latest["rs_line"] > latest["rs_line_30"]) & (
            latest["rs_line"] > latest["rs_line_65"]
        )
        latest["rs_line_up"] = latest["rs_line_up"].fillna(False)
    else:
        latest["rs_line_up"] = pd.NA

    # 보기 좋은 파생 지표
    latest["pct_above_low"] = ((latest["close"] / latest["low_52w"] - 1) * 100).round(1)
    latest["pct_below_high"] = ((1 - latest["close"] / latest["high_52w"]) * 100).round(1)

    # 종목명 결합
    sl = pd.read_csv(DATA / "stock_list.csv", dtype=str, encoding="utf-8-sig")
    latest = latest.merge(sl[["ticker", "name"]], on="ticker", how="left")

    for col in ("ma50", "ma150", "ma200", "low_52w", "high_52w"):
        latest[col] = latest[col].round(2)

    out_cols = [
        "date", "ticker", "name", "market", "close",
        "ma50", "ma150", "ma200", "low_52w", "high_52w",
        "pct_above_low", "pct_below_high", "rs_rating", "rs_line_up",
        *CONDS, "pass_count", "pass_all",
    ]
    # 통과 우선 → RS 높은 순
    result = latest[out_cols].sort_values(
        ["pass_all", "rs_rating"], ascending=[False, False]
    ).reset_index(drop=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "results.csv"
    result.to_csv(out_path, index=False, encoding="utf-8-sig")

    as_of = result["date"].iloc[0] if len(result) else "?"
    n_pass = int(result["pass_all"].sum())
    print(f"기준일 {as_of}: 전체 {len(result)}종목 중 8개 조건 통과 {n_pass}종목 -> {out_path}")


if __name__ == "__main__":
    main()
