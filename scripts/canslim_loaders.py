# CANSLIM 판정에 필요한 Parquet·CSV를 한 번에 읽어 종목별 조회 인덱스로 만드는 모듈
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

QUARTER_ORDER = {"1Q": 1, "2Q": 2, "3Q": 3, "4Q": 4}
# 베이스 카운트에 맞춘 창이다. Stage2 상승은 2년을 넘겨 이어지는 일이 흔해서 2년치로는
# 상승 시작일이 창 밖에 있고 베이스가 조직적으로 적게 세어진다. 50·200일선과 52주 고저는
# 훨씬 짧은 창으로도 충분하지만, 짧은 쪽에 맞추면 compute_bases.py와 창이 갈라져
# 두 CSV의 base_label이 어긋난다.
PRICE_YEARS = 5
INDEX_CODES = ["US500", "IXIC", "DJI"]


def quarter_key(year, quarter):
    return int(year) * 10 + QUARTER_ORDER.get(quarter, 0)


@dataclass
class Bundle:
    results: pd.DataFrame
    stock_list: pd.DataFrame
    quarterly: dict = field(default_factory=dict)
    annual: dict = field(default_factory=dict)
    prices: pd.DataFrame = field(default_factory=pd.DataFrame)
    shares: pd.DataFrame = field(default_factory=pd.DataFrame)
    analyst: pd.DataFrame = field(default_factory=pd.DataFrame)
    inst_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    # 한국 수급 데이터. 미국에서는 늘 비어 있지만 canslim_items.py를 두 레포에서
    # 동일하게 유지하려면 judge_i_kr이 읽는 필드가 양쪽 Bundle에 다 있어야 한다.
    flows: pd.DataFrame = field(default_factory=pd.DataFrame)
    indices: dict = field(default_factory=dict)


def _index_financials(df, has_quarter):
    if has_quarter:
        df = df.assign(_k=[quarter_key(y, q) for y, q in zip(df["year"], df["quarter"])])
    else:
        df = df.assign(_k=df["year"].astype(int))
    df = df.sort_values("_k")
    return {key: g.drop(columns="_k").reset_index(drop=True)
            for key, g in df.groupby(["ticker", "account"], sort=False)}


def _read_parquet(path, columns=None):
    if not path.exists():
        return pd.DataFrame(columns=columns or [])
    return pd.read_parquet(path)


def load_prices(data_dir=Path("data")):
    """최근 PRICE_YEARS년치 일봉을 한 프레임으로 읽어 필터·정렬해 돌려준다.

    canslim.py와 compute_bases.py가 같은 detect_base를 부르므로 두 스크립트가 읽는
    연도 창과 행 필터도 여기 한 곳에만 둔다. 각자 읽으면 창이 갈라져 같은 종목의
    base_label이 두 CSV에서 달라진다.
    """
    data_dir = Path(data_dir)
    frames = []
    this_year = date.today().year
    for y in range(this_year - PRICE_YEARS + 1, this_year + 1):
        p = data_dir / f"prices/{y}.parquet"
        if p.exists():
            frames.append(pd.read_parquet(
                p, columns=["date", "ticker", "high", "low", "close", "volume"]))
    prices = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["date", "ticker", "high", "low", "close", "volume"])
    if len(prices):
        prices["date"] = pd.to_datetime(prices["date"].astype(str), format="%Y%m%d")
        # 거래량 0인 행은 거래일이 아니라 거래정지일이다. 남겨두면 50일 평균 거래량이
        # 실제 거래를 실제보다 낮게 말하고, 정지가 풀린 첫날의 평범한 거래량이
        # 거짓 급증으로 읽힌다.
        prices = prices[(prices[["high", "low", "close"]] > 0).all(axis=1)
                        & (prices["volume"] > 0)]
        prices = prices.sort_values(["ticker", "date"]).reset_index(drop=True)
    return prices


def load_all(data_dir=Path("data")):
    data_dir = Path(data_dir)

    # stock_list.csv와 results.csv는 종목 모집단 자체다. 없을 때 빈 프레임을 돌려주면
    # canslim.py가 0종목을 순회해 빈 결과 CSV를 만들고, 일일 워크플로가 그것을 커밋해
    # 직전의 정상 결과를 덮어쓴다. 나머지 여덟 개 입력과 달리 여기서는 크게 실패한다.
    for required in (data_dir / "stock_list.csv", data_dir / "screener/results.csv"):
        if not required.exists():
            raise FileNotFoundError(f"CANSLIM 판정에 반드시 필요한 파일이 없습니다: {required}")

    # 티커 "NA"(Nano Labs Ltd)를 pandas가 결측값으로 파싱하지 않도록 기본 해석을 끈다.
    sl = pd.read_csv(data_dir / "stock_list.csv", dtype=str,
                     encoding="utf-8-sig", keep_default_na=False)

    res = pd.read_csv(data_dir / "screener/results.csv", dtype={"ticker": str},
                      keep_default_na=False, na_values=[""])
    res = res.set_index("ticker")

    q = _read_parquet(data_dir / "financials/quarterly.parquet")
    a = _read_parquet(data_dir / "financials/annual.parquet")

    prices = load_prices(data_dir)

    shares = _read_parquet(data_dir / "screener/shares_snapshot.parquet")
    if len(shares):
        shares = shares.drop_duplicates("ticker", keep="last").set_index("ticker")

    analyst = _read_parquet(data_dir / "analyst/snapshot.parquet")
    if len(analyst):
        analyst = analyst.drop_duplicates("ticker", keep="last").set_index("ticker")

    inst = _read_parquet(data_dir / "analyst/institution_history.parquet",
                         ["ticker", "asof", "held_pct_institutions"])

    indices = {}
    for code in INDEX_CODES:
        p = data_dir / f"indices/{code}.parquet"
        if p.exists():
            indices[code] = pd.read_parquet(p).sort_values("date").reset_index(drop=True)

    return Bundle(
        results=res, stock_list=sl,
        quarterly=_index_financials(q, True) if len(q) else {},
        annual=_index_financials(a, False) if len(a) else {},
        prices=prices, shares=shares, analyst=analyst, inst_history=inst, indices=indices,
    )
