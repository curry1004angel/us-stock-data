# SEC XBRL 전자공시에서 기본주당이익을 받아 재무 Parquet의 eps 행을 채우는 스크립트
#
# yfinance의 Basic EPS는 공시값이 없으면 Net Income ÷ Basic Average Shares로 자기가
# 만드는데 그 주식수가 틀릴 때가 있다(BKNG 2023: 야후 4.7468 대 공시 118.67, 25배).
# 공시값을 직접 받아 그 자리를 대신한다.
#
# 다루는 함정 넷.
#   1) 액면분할 — 10-K는 비교연도 2개만 다시 싣는다. 최신 제출본을 써도 그보다 오래된
#      연도는 분할 이전 기준이라 계열에 절벽이 생긴다. 제출본 간 값의 배수로 분할
#      비율을 잡아내 직접 조정한다.
#   2) 자릿수 오태깅 — 드물게 회사가 자릿수를 틀리게 태깅한다(ULBI 0.38을 38로).
#      net_income으로 주식수를 역산해 계열에서 튀는 연도를 버린다.
#   3) 태그 고르기 — 회사마다 쓰는 태그가 다르고, 한 회사도 시기별로 태그를
#      바꾼다. 후보를 다 받아 합치고 겹칠 때만 우선순위로 가른다(collect_facts).
#   4) 통화 — 해외 발행사는 EUR/BRL/MXN per share로 낸다. 그대로 받는다(pick_unit).
#
# 사용법:
#     python scripts/fetch_eps_sec.py            # 전 종목
#     python scripts/fetch_eps_sec.py --limit 50 # 표본
import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

DATA = Path("data")

# SEC는 연락처가 든 User-Agent를 요구한다. 없으면 403이다.
UA = "canslim-screener (contact: vkdlzlz11@gmail.com)"
HEADERS = {"User-Agent": UA, "Accept-Encoding": "gzip, deflate"}

# 후보 태그. 전부 받아서 합치고, 같은 기간이 겹칠 때만 이 순위로 가른다.
# 첫 번째로 값이 나오는 태그에서 멈추면 계열이 잘린다.
#   agilon health: EarningsPerShareBasic이 10-Q 6건뿐이고 진짜 계열은
#   IncomeLossFromContinuingOperationsPerBasicShare 56건에 있다.
# 두꺼운 태그 하나만 골라도 잘린다.
#   Ares: 유닛당 태그 114건이 2014~2019, 주당 태그가 2019~2025를 덮는다.
TAGS = [("us-gaap", "EarningsPerShareBasic"),
        ("us-gaap", "EarningsPerShareBasicAndDiluted"),
        ("us-gaap", "IncomeLossFromContinuingOperationsPerBasicShare"),
        ("us-gaap", "IncomeLossFromContinuingOperationsPerBasicAndDilutedShare"),
        ("ifrs-full", "BasicEarningsLossPerShare"),
        ("ifrs-full", "BasicAndDilutedEarningsLossPerShare"),
        ("ifrs-full", "BasicEarningsLossPerShareFromContinuingOperations"),
        # 합자회사는 주당이 아니라 유닛당으로 낸다(AB·ARES·ARLP·MMLP).
        ("us-gaap", "NetIncomeLossPerOutstandingLimitedPartnershipUnitBasicNetOfTax"),
        ("us-gaap", "IncomeLossFromContinuingOperationsPerOutstanding"
                    "LimitedPartnershipUnitBasicNetOfTax"),
        ("us-gaap", "NetIncomeLossPerOutstandingLimitedPartnershipAnd"
                    "GeneralPartnershipUnitBasicAndDiluted"),
        # 기본이 아예 없는 회사가 있다. 희석이라도 없는 것보다 낫다.
        ("us-gaap", "EarningsPerShareDiluted"),
        ("ifrs-full", "DilutedEarningsLossPerShare")]
UNIT_KEY = "USD/shares"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# 주당이익이 안 보일 때 "회사가 안 낸 것"인지 "이 창구로 안 보이는 것"인지 가른다.
# 기간이 있는 손익 태그라 10-K/10-Q를 내는 사업회사에만 있다.
OPERATING_TAGS = [("us-gaap", "NetIncomeLoss"),
                  ("us-gaap", "Revenues"),
                  ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
                  ("ifrs-full", "ProfitLoss")]

FORMS = ("10-K", "10-Q", "20-F", "40-F")
ANNUAL_DAYS = (330, 400)
QUARTER_DAYS = (75, 105)

# yfinance는 분사(spinoff) 조정도 splits에 섞어 넣는다(DELL의 1.806·1.973이
# VMware 분사다). SEC는 분사를 중단사업 소급으로 이미 처리하므로 그것까지 곱하면
# 이중 반영이다. 분모가 작은 분수에 딱 떨어지는 비율만 진짜 액면분할로 본다.
SPLIT_TOL = 0.003          # 0.3%. 1.998(2:1)은 통과, 1.973(분사)은 탈락한다
SPLIT_DENOMS = (1, 2, 4)

# 역산 주식수가 중앙값에서 이 배수를 넘게 벗어나면 자릿수 오태깅으로 보고 버린다.
SHARE_OUTLIER_MULT = 10.0
SHARE_GUARD_MIN_YEARS = 4  # 연도가 적으면 중앙값 자체를 못 믿는다

QUARTER_ORDER = {"1Q": 1, "2Q": 2, "3Q": 3, "4Q": 4}

# SEC 연간 계열의 최신 연도가 기존보다 이만큼 뒤처지면 갈아 끼우지 않는다.
# 한 해 차이는 제출 시기 문제라 정상이다. 여러 해가 비면 태깅이 끊긴 것이고
# (Ares는 2018년 법인 전환 뒤로 없다), 그걸로 덮으면 최신 연도가 사라져
# A항목이 판정 불가가 된다. 틀릴 수 있는 값이 못 쓰는 값보다 낫다.
STALE_YEARS = 2

SEC_SLEEP = 0.11           # SEC 권고 초당 10건
FACTS_WORKERS = 5          # 워커당 SEC_SLEEP*6이면 합쳐서 초당 8건 안쪽이다
FACTS_SLEEP = SEC_SLEEP * 6

# 야후 분할 이력. 6워커로 5,919종목을 86초에 긁었다가 4,365종목이 차단당했다
# (2026-08-24). 워커를 줄이고 간격을 두고 재시도한다.
SPLIT_WORKERS = 3
SPLIT_SLEEP = 0.2
SPLIT_RETRIES = 3
SPLIT_BACKOFF = 4.0        # 1초, 4초, 16초

SPLIT_RETRY_SLEEP = 0.5    # 실패분 재시도는 한 줄로 더 천천히 간다


def load_tickers(path):
    """종목 목록에서 쓸 수 있는 티커만 뽑는다.

    keep_default_na=False가 핵심이다. dtype=str를 줘도 pandas는 "NA"·"N/A"·"NULL"
    같은 문자열을 결측으로 바꿔버린다. Nano Labs Ltd의 티커가 실제로 "NA"라서
    NaN(float)이 되었고, 루프 한복판에서 tk.upper()가 AttributeError로 터졌다.
    저장이 루프 뒤에 있어 그때까지 받은 2,700종목이 통째로 날아갔다.
    결측으로 버리는 것도 답이 아니다 — 멀쩡한 종목 하나를 조용히 잃는다.
    """
    sl = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    return [t.strip() for t in sl["ticker"].tolist() if str(t).strip()]


def load_cik_map():
    """{티커: 10자리 CIK}. company_tickers.json은 dict 또는 list로 온다."""
    raw = json.loads((DATA / "company_tickers.json").read_text(encoding="utf-8"))
    src = raw.values() if isinstance(raw, dict) else raw
    return {str(v["ticker"]).upper(): f"{int(v['cik_str']):010d}" for v in src}


def pick_unit(units):
    """한 태그의 단위 중 쓸 것을 고른다. USD/shares가 있으면 그것, 없으면 최다.

    해외 발행사는 자국 통화로 낸다 — ASML은 EUR/shares, Ambev는 BRL/shares,
    América Móvil은 MXN/shares다. USD만 받으면 이들이 통째로 "미공시"가 되어
    기존 값까지 지워진다. 판정에 쓰는 것은 증가율이라 통화에 무관하고, 한
    종목 안에서는 단위가 일관되므로 그대로 쓴다.
    """
    if UNIT_KEY in units:
        return units[UNIT_KEY]
    per_share = [(len(v), k) for k, v in units.items() if k.endswith("/shares")]
    return units[max(per_share)[1]] if per_share else []


def collect_facts(facts):
    """모든 후보 태그의 사실을 합친다. (사실 목록, 태그를 하나라도 봤는가).

    태그 하나만 골라선 안 된다. 시기별로 태그가 갈리는 회사가 있다 — Ares는
    2018년에 합자회사에서 법인으로 바꿔서 유닛당 태그가 2014~2019, 주당 태그가
    2019~2025를 덮는다. 둘 중 두꺼운 쪽만 쓰면 나머지 기간이 통째로 사라진다.

    각 사실에 태그 순위를 붙여 둔다. 같은 기간을 여러 태그가 덮으면 by_period가
    순위가 앞선 쪽을 쓴다.

    "태그를 봤는가"를 따로 돌려준다. 태그는 있는데 쓸 만한 사실이 없는 경우
    (전부 8-K거나 기간이 없는 경우)를 "미공시"로 뭉개면 기존 값이 지워진다.
    """
    merged, found = [], False
    for rank, (taxonomy, tag) in enumerate(TAGS):
        items = pick_unit(facts.get(taxonomy, {}).get(tag, {}).get("units", {}))
        if not items:
            continue
        found = True
        merged += [{**f, "_rank": rank} for f in items if usable(f)]
    return merged, found


def files_operating_reports(facts):
    """주당이익은 안 보이지만 10-K/10-Q 손익을 내는 회사인가.

    다중 클래스 회사는 주당이익 사실에 ClassOfStockAxis가 붙는데,
    companyfacts와 companyconcept는 차원 없는 사실만 보여준다. 그래서
    Constellation Brands·Planet Fitness·Bel Fuse는 EarningsPerShareBasic이
    404다. 응답 자체는 온전하다 — STZ는 us-gaap 태그가 657개고 NetIncomeLoss도
    있다. 주당이익만 안 보인다.

    회사가 안 낸 것이 아니라 이 창구로 안 보이는 것이므로 기존 값을 지우면
    안 된다. 폐쇄형 펀드·SPAC은 여기서 걸러진다 — 10-K 손익 자체가 없다.
    """
    for taxonomy, tag in OPERATING_TAGS:
        units = facts.get(taxonomy, {}).get(tag, {}).get("units", {})
        for items in units.values():
            if any(usable(f) for f in items):
                return True
    return False


def fetch_facts(cik, session=None):
    """(사실 목록, 상태). 상태는 "ok" | "none" | "fail".

    "none"은 이 회사가 주당이익을 아예 공시하지 않는다는 뜻이고, 그때만 기존
    값을 지운다. 조회 실패("fail")와 섞으면 네트워크 문제로 멀쩡한 데이터가
    지워진다.

    companyfacts만 쓴다. companyconcept는 같은 태그를 두고 다른 답을 준다 —
    Abbott은 companyfacts에 EarningsPerShareBasic이 313건인데 companyconcept는
    HTTP 200에 0건을 돌려줬고, 그 때문에 2026-08-24에 ABT·ACHC의 EPS가 통째로
    지워졌다. 태그별로 따로 물어야 해서 어느 태그가 제일 두꺼운지 비교할 수도
    없다. 한 번에 다 받아 비교한다.
    """
    get = (session or requests).get
    try:
        r = get(FACTS_URL.format(cik=cik), headers=HEADERS, timeout=60)
    except Exception:  # noqa: BLE001
        return [], "fail"
    if r.status_code == 404:
        return [], "none"     # SEC에 제출 이력이 없다(펀드·SPAC 다수)
    if r.status_code != 200:
        return [], "fail"
    try:
        facts = r.json().get("facts", {})
    except ValueError:
        return [], "fail"
    items, found = collect_facts(facts)
    if items or found or files_operating_reports(facts):
        return items, "ok"
    return [], "none"


_local = threading.local()


def fetch_facts_threaded(cik):
    """워커에서 부르는 fetch_facts. 세션은 스레드마다 따로 둔다."""
    session = getattr(_local, "session", None)
    if session is None:
        session = _local.session = requests.Session()
    try:
        return fetch_facts(cik, session)
    finally:
        time.sleep(FACTS_SLEEP)


def usable(fact):
    """기간이 있고 정기보고서에서 온 사실만 쓴다. 8-K 예비치는 정정 전 값이다."""
    return bool(fact.get("start")) and str(fact.get("form", "")).startswith(FORMS)


def period_kind(start, end):
    """"annual" | "quarterly" | None. 반기·5주 같은 중간 구간은 버린다."""
    days = (pd.Timestamp(end) - pd.Timestamp(start)).days
    if ANNUAL_DAYS[0] < days < ANNUAL_DAYS[1]:
        return "annual"
    if QUARTER_DAYS[0] < days < QUARTER_DAYS[1]:
        return "quarterly"
    return None


def label(end):
    """(연도, 분기). fetch_financials.py와 같은 규칙 — 기간 종료일의 달력 연도·분기."""
    ts = pd.Timestamp(end)
    return int(ts.year), f"{(ts.month - 1) // 3 + 1}Q"


def by_period(facts):
    """{(start, end): [사실, ...]} — 제출일 오름차순.

    한 기간을 여러 태그가 덮으면 순위가 앞선 태그만 남긴다. 계속사업 기준과
    총액 기준을 같은 기간에 섞으면 그 해만 성격이 달라진다.
    """
    out = {}
    for f in facts:
        if not usable(f) or not period_kind(f["start"], f["end"]):
            continue
        out.setdefault((f["start"], f["end"]), []).append(f)
    for group in out.values():
        best = min(f.get("_rank", 0) for f in group)
        group[:] = sorted((f for f in group if f.get("_rank", 0) == best),
                          key=lambda f: str(f.get("filed", "")))
    return out


def is_split(ratio):
    """진짜 액면분할 비율인가. 분사 조정을 걸러낸다.

    yfinance는 분사도 splits에 넣는다. DELL의 1.973(VMware 분사)을 분할로 받으면,
    SEC가 이미 중단사업 소급으로 처리한 것을 한 번 더 곱하게 된다.
    """
    if ratio is None or ratio <= 0:
        return False
    r = ratio if ratio >= 1 else 1 / ratio
    if r < 1.2:
        return False
    return any(abs(r * d - round(r * d)) <= r * d * SPLIT_TOL for d in SPLIT_DENOMS)


def fetch_splits(ticker):
    """{권리락일(Timestamp, tz 없음): 비율}. 조회 실패면 None.

    제출본 사이 값의 배수로 분할을 역산해봤으나 시점을 충분히 좁힐 수 없었다.
    BKNG은 연간 값이 분할 전인지 후인지 EPS만으로 판별이 안 됐다. 무엇보다
    가격 계열이 yfinance 조정가라, 같은 소스를 써야 EPS와 축이 맞는다.

    "분할 없음"({})과 "조회 실패"(None)를 반드시 구분한다. 실패를 빈 값으로
    뭉개면 분할한 종목의 EPS가 조정 없이 그대로 저장돼, 틀린 값이 맞는 값처럼
    보인다 — 이 작업이 애초에 고치려는 문제다.
    """
    import yfinance as yf          # 테스트에서 import만 할 때의 부담을 줄인다

    raw = None
    for attempt in range(SPLIT_RETRIES):
        try:
            raw = yf.Ticker(str(ticker).replace(".", "-")).splits
            break
        except Exception:  # noqa: BLE001
            # 야후는 과하게 부르면 막는다. 한 번 실패했다고 포기하면 그 종목이
            # 통째로 빠지고, 갈아엎기와 겹치면 EPS가 사라진다.
            if attempt == SPLIT_RETRIES - 1:
                return None
            time.sleep(SPLIT_BACKOFF ** attempt)
    if raw is None:
        return None
    time.sleep(SPLIT_SLEEP)
    return {pd.Timestamp(d).tz_localize(None): float(r)
            for d, r in raw.items() if is_split(float(r))}


def split_factor(splits, filed):
    """이 제출일 뒤에 권리락된 분할 비율의 곱. 값을 이것으로 나눈다."""
    if not splits or not filed:
        return 1.0
    when = pd.Timestamp(filed)
    factor = 1.0
    for ex_date, ratio in splits.items():
        if ex_date > when:
            factor *= ratio
    return factor


def build_rows(ticker, facts, splits=None):
    """(분기 행, 연간 행). 최신 제출본을 쓰고 분할을 조정한다.

    최신 제출본을 쓰는 이유는 둘이다. 후속 제출본이 중단사업을 빼고 계속사업
    기준으로 소급하는데 오닐도 계속사업 기준을 쓰고, 비교연도 2개는 분할까지
    소급돼 있다. 그보다 오래된 연도는 소급이 안 닿으므로 splits로 직접 나눈다.
    """
    periods = by_period(facts)
    if not periods:
        return [], []

    q_rows, a_rows = [], []
    for (start, end), group in periods.items():
        latest = group[-1]
        value = latest.get("val")
        if value is None:
            continue
        adjusted = float(value) / split_factor(splits, latest.get("filed"))
        year, quarter = label(end)
        row = {"ticker": ticker, "year": year, "account": "eps", "amount": adjusted}
        if period_kind(start, end) == "annual":
            a_rows.append(row)
        else:
            q_rows.append({**row, "quarter": quarter})
    return q_rows, a_rows


def drop_scale_outliers(a_rows, net_income):
    """자릿수 오태깅 연도를 버린다. {연도: 순이익}으로 주식수를 역산해 비교한다.

    회사가 자릿수를 틀리게 태깅하는 일이 드물게 있다(ULBI 0.38을 38로). 그러면
    역산 주식수가 계열에서 100배씩 튄다. 분할은 앞 단계에서 이미 조정했으므로
    여기 남은 이상치는 진짜 오류다.
    """
    shares = {}
    for r in a_rows:
        ni = net_income.get(r["year"])
        if ni is None or pd.isna(ni) or abs(r["amount"]) < 1e-9:
            continue
        shares[r["year"]] = abs(ni / r["amount"])
    if len(shares) < SHARE_GUARD_MIN_YEARS:
        return a_rows, []
    median = float(pd.Series(list(shares.values())).median())
    if median <= 0:
        return a_rows, []
    bad = {y for y, s in shares.items()
           if s > median * SHARE_OUTLIER_MULT or s < median / SHARE_OUTLIER_MULT}
    return [r for r in a_rows if r["year"] not in bad], sorted(bad)


def newest_period(rows):
    """행 목록의 가장 최근 기간. 분기 행이면 (연도, 분기), 연간이면 (연도, 0)."""
    return max((r["year"], QUARTER_ORDER.get(r.get("quarter"), 0)) for r in rows)


def last_eps_period(df, ticker):
    """기존 파일에 든 이 종목 eps의 가장 최근 기간. 없으면 None."""
    if df is None or df.empty:
        return None
    d = df[(df["ticker"] == ticker) & (df["account"] == "eps")]
    if not len(d):
        return None
    if "quarter" in d.columns:
        return max((int(y), QUARTER_ORDER.get(q, 0))
                   for y, q in zip(d["year"], d["quarter"]))
    return (int(d["year"].max()), 0)


def annual_too_stale(rows, last):
    """연간 계열이 기존보다 STALE_YEARS 이상 뒤처지는가.

    한 해 차이는 건드린다. National Grid처럼 3월 결산이면 제출 시기 때문에
    기존이 한 해 앞설 수 있고 그건 정상이다.
    """
    if not rows or last is None:
        return False
    return newest_period(rows)[0] <= last[0] - STALE_YEARS


def quarterly_too_stale(rows, last):
    """분기 계열이 한 칸이라도 뒤로 밀리는가.

    연간과 달리 한 분기도 못 봐준다. C항목이 보는 것은 최근 분기 EPS
    증가율이라 최신 분기가 뒤로 밀리면 판정 자체가 낡는다.

    밀리는 이유는 둘이고 둘 다 우리가 손쓸 수 없다.
      - 회사가 그 분기에 EarningsPerShareBasic을 안 태깅한다. Northern Trust는
        2026년 2분기에 EarningsPerShareBasicUndistributed만 냈는데 그건 두 클래스
        배분용이라 총 EPS가 아니다.
      - companyfacts에 그 제출본이 아직 없다. Southern Company는 2026-06-08
        제출본이 마지막이다.
    둘 다 시간이 지나면 풀린다. 그때까지 기존 값을 지킨다.
    """
    if not rows or last is None:
        return False
    return newest_period(rows) < last


def net_income_by_year(annual_df, ticker):
    if annual_df is None or annual_df.empty:
        return {}
    d = annual_df[(annual_df["ticker"] == ticker) & (annual_df["account"] == "net_income")]
    return dict(zip(d["year"], d["amount"]))


def write_eps(path, new_rows, processed, keys):
    """처리한 종목의 eps만 새 값으로 갈아 끼운다.

    전체를 지우고 덮으면 이번에 못 받은 종목이 값 없음이 된다. 2026-08-24에
    야후 차단으로 실제로 그렇게 4,325종목의 EPS가 사라졌다. 처리하지 못한
    종목은 손대지 않는다 — 오래된 값이라도 없는 것보다 낫다.

    `processed`는 이 파일에 대해 SEC가 답을 준 종목이다. 주당이익을 아예 안
    내는 회사도 여기 들어가고, 그 종목의 기존 eps는 지워진다. 야후가 만든
    계산값을 남겨두면 그것이 그대로 판정에 쓰이기 때문이다.

    연간과 분기는 서로 다른 집합을 받는다. 캐나다·아일랜드 발행사는 40-F/20-F로
    연간만 내고 중간보고는 6-K라 SEC에 분기 주당이익이 없다. 한 집합으로 묶으면
    연간이 나왔다는 이유로 분기까지 지워진다(2026-08-24에 AEM·AER·AGI 등
    508종목이 그렇게 분기를 잃었다).
    """
    existing = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    if len(existing):
        stale = (existing["account"] == "eps") & existing["ticker"].isin(processed)
        kept = existing[~stale]
        replaced = int(stale.sum())
    else:
        kept, replaced = existing, 0

    new_df = pd.DataFrame(new_rows)
    if len(kept) and len(new_df):
        new_df = new_df[[c for c in new_df.columns if c in kept.columns]]
    combined = pd.concat([kept, new_df], ignore_index=True)
    combined = combined.drop_duplicates(subset=keys, keep="last")
    combined = combined.sort_values(keys).reset_index(drop=True)
    combined.to_parquet(path, index=False, compression="snappy")

    eps = combined[combined["account"] == "eps"]
    print(f"  {path.name}: 기존 eps {replaced}행 교체, 신규 {len(new_df)}행 "
          f"→ eps {eps['ticker'].nunique()}종목 / 총 {len(combined)}행", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N종목만 (표본 실행용)")
    args = ap.parse_args()

    tickers = load_tickers(DATA / "stock_list.csv")
    if args.limit:
        tickers = tickers[:args.limit]
    cik_map = load_cik_map()

    path_a = DATA / "financials/annual.parquet"
    path_q = DATA / "financials/quarterly.parquet"
    existing_annual = pd.read_parquet(path_a) if path_a.exists() else None
    existing_quarterly = pd.read_parquet(path_q) if path_q.exists() else None

    print(f"SEC 주당이익 수집: {len(tickers)}종목", flush=True)

    # 분할 이력을 먼저 병렬로 받는다. 종목당 약 1초라 직렬로는 5,500종목에
    # 90분이 넘고 SEC 수집까지 더하면 잡 제한시간(350분)을 위협한다. SEC는
    # 초당 10건 제한이 있어 같이 묶지 않고 따로 돌린다.
    print("  분할 이력 수집 중...", flush=True)
    splits_by_ticker = {}
    with ThreadPoolExecutor(max_workers=SPLIT_WORKERS) as pool:
        futures = {pool.submit(fetch_splits, tk): tk for tk in tickers}
        for n, fut in enumerate(as_completed(futures), 1):
            splits_by_ticker[futures[fut]] = fut.result()
            if n % 1000 == 0:
                print(f"    {n}/{len(tickers)}", flush=True)
    failed = [tk for tk, v in splits_by_ticker.items() if v is None]
    print(f"  분할 이력 1차 완료 (실패 {len(failed)}종목)", flush=True)
    # 실패분은 한 줄로 천천히 다시 받는다. 병렬로 몰아치다 막힌 것이라 혼자
    # 다시 물으면 대부분 온다. 여기서 건지지 못하면 그 종목은 손대지 않는다.
    if failed:
        print(f"  실패분 {len(failed)}종목 재시도 중...", flush=True)
        for n, tk in enumerate(failed, 1):
            splits_by_ticker[tk] = fetch_splits(tk)
            time.sleep(SPLIT_RETRY_SLEEP)
            if n % 200 == 0:
                print(f"    {n}/{len(failed)}", flush=True)
    split_fail = sum(1 for v in splits_by_ticker.values() if v is None)
    print(f"  분할 이력 완료 (최종 실패 {split_fail}종목)", flush=True)

    # SEC 사실도 병렬로 받는다. companyfacts는 응답이 중앙값 3.4MB라 직렬이면
    # 5,919종목에 70분이 넘는다. 초당 8건 안쪽을 지키며 5줄로 나눠 받는다.
    print("  SEC 사실 수집 중...", flush=True)
    facts_by_ticker = {}
    targets = [tk for tk in tickers if cik_map.get(tk.upper())]
    no_cik = len(tickers) - len(targets)
    with ThreadPoolExecutor(max_workers=FACTS_WORKERS) as pool:
        futures = {pool.submit(fetch_facts_threaded, cik_map[tk.upper()]): tk
                   for tk in targets}
        for n, fut in enumerate(as_completed(futures), 1):
            tk = futures[fut]
            try:
                facts_by_ticker[tk] = fut.result()
            except Exception as e:  # noqa: BLE001
                facts_by_ticker[tk] = ([], f"fail:{type(e).__name__}: {e}")
            if n % 1000 == 0:
                print(f"    {n}/{len(targets)}", flush=True)

    q_all, a_all = [], []
    ok = no_tag = no_splits = dropped = fetch_fail = no_rows = 0
    errors, dropped_detail, stale_a, stale_q = [], [], [], []
    # 연간과 분기를 따로 센다. SEC가 연간만 주는 종목의 분기까지 지우면 안 된다.
    done_a, done_q = set(), set()
    for i, tk in enumerate(targets, 1):
        # 종목 하나의 예외로 전체가 죽으면 여기까지 받은 수천 종목이 통째로
        # 버려진다(저장은 루프가 끝난 뒤에 한다). 티커와 예외는 반드시 남긴다.
        try:
            splits = splits_by_ticker.get(tk)
            if splits is None:
                # 분할 이력을 모르면 조정할 수 없다. 조정 없이 저장하느니 비워둔다.
                no_splits += 1
                continue
            facts, status = facts_by_ticker.get(tk, ([], "fail"))
            if status.startswith("fail"):
                fetch_fail += 1
                continue                  # 손대지 않는다. 기존 값이 남는다
            if status == "none":
                # 주당이익을 아예 공시하지 않는 회사다(펀드·SPAC 다수). 기존
                # 야후 값을 지운다 — 남겨두면 그 계산값이 그대로 판정에 쓰인다.
                no_tag += 1
                done_a.add(tk)
                done_q.add(tk)
                continue

            q_rows, a_rows = build_rows(tk, facts, splits)
            a_rows, bad = drop_scale_outliers(
                a_rows, net_income_by_year(existing_annual, tk))
            if bad:
                dropped += len(bad)
                dropped_detail.append((tk, bad))
            if annual_too_stale(a_rows, last_eps_period(existing_annual, tk)):
                stale_a.append(tk)
                a_rows = []
            if quarterly_too_stale(q_rows, last_eps_period(existing_quarterly, tk)):
                stale_q.append(tk)
                q_rows = []
            if not q_rows and not a_rows:
                # 사실은 받았는데 쓸 행이 안 나왔다. 회사가 안 낸 것이 아니라
                # 우리 추출이 못 건진 것이므로 기존 값을 지우면 안 된다.
                no_rows += 1
                continue
            ok += 1
            if a_rows:
                done_a.add(tk)
                a_all += a_rows
            if q_rows:
                done_q.add(tk)
                q_all += q_rows
        except Exception as e:  # noqa: BLE001
            errors.append((tk, f"{type(e).__name__}: {e}"))
        if i % 500 == 0:
            print(f"  {i}/{len(targets)} 처리 (수집 {ok}종목)", flush=True)

    if errors:
        print(f"경고: {len(errors)}종목에서 예외가 발생해 제외됨.", flush=True)
        for tk, msg in errors[:30]:
            print(f"  {tk}: {msg}", flush=True)

    print(f"수집 완료: {ok}종목 / CIK 없음 {no_cik} / 분할이력 실패 {no_splits} / "
          f"SEC 조회 실패 {fetch_fail} / 주당이익 미공시 {no_tag} / "
          f"사실은 있으나 행 없음 {no_rows} / 자릿수 폐기 {dropped}건 "
          f"({len(dropped_detail)}종목)", flush=True)
    print(f"  갱신 대상: 연간 {len(done_a)}종목 / 분기 {len(done_q)}종목", flush=True)
    print(f"  기존보다 뒤처져 건너뜀: 연간 {len(stale_a)}종목 {sorted(stale_a)[:20]} / "
          f"분기 {len(stale_q)}종목 {sorted(stale_q)[:20]}", flush=True)
    for tk, bad in dropped_detail[:40]:
        print(f"  폐기 {tk}: {bad}", flush=True)

    write_eps(path_q, q_all, done_q, ["ticker", "year", "quarter", "account"])
    write_eps(path_a, a_all, done_a, ["ticker", "year", "account"])


if __name__ == "__main__":
    main()
