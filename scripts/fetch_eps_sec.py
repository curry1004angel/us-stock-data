# SEC XBRL 전자공시에서 기본주당이익을 받아 재무 Parquet의 eps 행을 채우는 스크립트
#
# yfinance의 Basic EPS는 공시값이 없으면 Net Income ÷ Basic Average Shares로 자기가
# 만드는데 그 주식수가 틀릴 때가 있다(BKNG 2023: 야후 4.7468 대 공시 118.67, 25배).
# 공시값을 직접 받아 그 자리를 대신한다.
#
# 다루는 함정 둘.
#   1) 액면분할 — 10-K는 비교연도 2개만 다시 싣는다. 최신 제출본을 써도 그보다 오래된
#      연도는 분할 이전 기준이라 계열에 절벽이 생긴다. 제출본 간 값의 배수로 분할
#      비율을 잡아내 직접 조정한다.
#   2) 자릿수 오태깅 — 드물게 회사가 자릿수를 틀리게 태깅한다(ULBI 0.38을 38로).
#      net_income으로 주식수를 역산해 계열에서 튀는 연도를 버린다.
#
# 사용법:
#     python scripts/fetch_eps_sec.py            # 전 종목
#     python scripts/fetch_eps_sec.py --limit 50 # 표본
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_financials import update_parquet  # noqa: E402

DATA = Path("data")
SEC_BASE = "https://data.sec.gov/api/xbrl/companyconcept"

# SEC는 연락처가 든 User-Agent를 요구한다. 없으면 403이다.
UA = "canslim-screener (contact: vkdlzlz11@gmail.com)"
HEADERS = {"User-Agent": UA, "Accept-Encoding": "gzip, deflate"}

# 우선순위대로 폴백한다. 하나라도 값이 나오면 거기서 멈춘다.
TAGS = [("us-gaap", "EarningsPerShareBasic"),
        ("us-gaap", "EarningsPerShareBasicAndDiluted"),
        ("ifrs-full", "BasicEarningsLossPerShare")]

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

SEC_SLEEP = 0.11           # SEC 권고 초당 10건

# 야후 분할 이력. 6워커로 5,919종목을 86초에 긁었다가 4,365종목이 차단당했다
# (2026-08-24). 워커를 줄이고 간격을 두고 재시도한다.
SPLIT_WORKERS = 3
SPLIT_SLEEP = 0.2
SPLIT_RETRIES = 3
SPLIT_BACKOFF = 4.0        # 1초, 4초, 16초

# 분할 이력을 못 받은 종목은 건너뛰므로, 실패가 많으면 커버리지가 통째로 무너진다.
# 조용히 진행해 지우고 덮느니 소리내어 실패한다.
SPLIT_FAIL_MAX = 0.05
# 갈아엎기 전 확인. 새로 받은 종목 수가 기존의 이 비율에 못 미치면 아무것도 안 지운다.
PURGE_MIN_COVERAGE = 0.80


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


def fetch_facts(cik, session=None):
    """첫 번째로 값이 나오는 태그의 사실 목록. 없으면 빈 리스트."""
    get = (session or requests).get
    for taxonomy, tag in TAGS:
        url = f"{SEC_BASE}/CIK{cik}/{taxonomy}/{tag}.json"
        try:
            r = get(url, headers=HEADERS, timeout=30)
        except Exception:  # noqa: BLE001
            continue
        time.sleep(SEC_SLEEP)
        if r.status_code != 200:
            continue
        facts = r.json().get("units", {}).get("USD/shares", [])
        if facts:
            return facts
    return []


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
    """{(start, end): [사실, ...]} — 제출일 오름차순."""
    out = {}
    for f in facts:
        if not usable(f) or not period_kind(f["start"], f["end"]):
            continue
        out.setdefault((f["start"], f["end"]), []).append(f)
    for k in out:
        out[k].sort(key=lambda f: str(f.get("filed", "")))
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


def net_income_by_year(annual_df, ticker):
    if annual_df is None or annual_df.empty:
        return {}
    d = annual_df[(annual_df["ticker"] == ticker) & (annual_df["account"] == "net_income")]
    return dict(zip(d["year"], d["amount"]))


def purge_eps(path, new_tickers):
    """기존 eps 행을 지운다. 새 데이터의 커버리지가 부족하면 지우지 않는다.

    야후 폴백을 두지 않기로 했으므로(틀린 값이 맞는 값처럼 보이는 것이 이 문제의
    핵심이었다), SEC가 못 채운 자리는 비어 있어야 한다. 남겨두면 야후가 만든
    계산값이 그대로 판정에 쓰인다. 전 종목 실행에서만 부른다 — 표본 실행에서
    지우면 건드리지 않은 종목의 EPS까지 날아간다.

    커버리지 확인이 핵심이다. 2026-08-24에 야후 차단으로 1,223종목만 수집된
    채로 지우기가 실행돼 4,325종목의 EPS가 사라졌다. 수집이 부실하면 기존
    데이터가 오래됐을지언정 없는 것보다 낫다.
    """
    if not path.exists():
        return 0
    df = pd.read_parquet(path)
    old = df.loc[df["account"] == "eps", "ticker"].nunique()
    if old and len(new_tickers) < old * PURGE_MIN_COVERAGE:
        raise SystemExit(
            f"{path.name}: 새로 받은 {len(new_tickers)}종목이 기존 {old}종목의 "
            f"{PURGE_MIN_COVERAGE:.0%}에 못 미친다. 지우지 않고 멈춘다.")
    before = len(df)
    df = df[df["account"] != "eps"]
    removed = before - len(df)
    df.to_parquet(path, index=False, compression="snappy")
    print(f"  {path.name}: 기존 eps {removed}행 제거 "
          f"(기존 {old}종목 → 신규 {len(new_tickers)}종목)", flush=True)
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N종목만 (표본 실행용)")
    args = ap.parse_args()

    tickers = load_tickers(DATA / "stock_list.csv")
    if args.limit:
        tickers = tickers[:args.limit]
    cik_map = load_cik_map()

    path_a = DATA / "financials/annual.parquet"
    existing_annual = pd.read_parquet(path_a) if path_a.exists() else None

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
    split_fail = sum(1 for v in splits_by_ticker.values() if v is None)
    print(f"  분할 이력 완료 (조회 실패 {split_fail}종목)", flush=True)
    if split_fail > len(tickers) * SPLIT_FAIL_MAX:
        # 여기서 계속 가면 실패한 종목을 전부 건너뛴 채 기존 EPS를 지우고 덮는다.
        # 2026-08-24에 실제로 그렇게 4,325종목의 EPS가 사라졌다.
        raise SystemExit(
            f"분할 이력 조회 실패가 {split_fail}/{len(tickers)}종목으로 너무 많다 "
            f"(허용 {SPLIT_FAIL_MAX:.0%}). 야후가 막은 것으로 보인다. "
            f"아무것도 쓰지 않고 멈춘다.")

    session = requests.Session()
    q_all, a_all = [], []
    ok = no_cik = no_tag = no_splits = dropped = 0
    errors, dropped_detail = [], []
    for i, tk in enumerate(tickers, 1):
        # 종목 하나의 예외로 전체가 죽으면 여기까지 받은 수천 종목이 통째로
        # 버려진다(저장은 루프가 끝난 뒤에 한다). 티커와 예외는 반드시 남긴다.
        try:
            cik = cik_map.get(tk.upper())
            if not cik:
                no_cik += 1
                continue
            splits = splits_by_ticker.get(tk)
            if splits is None:
                # 분할 이력을 모르면 조정할 수 없다. 조정 없이 저장하느니 비워둔다.
                no_splits += 1
                continue
            facts = fetch_facts(cik, session)
            if not facts:
                no_tag += 1
                continue
            q_rows, a_rows = build_rows(tk, facts, splits)
            a_rows, bad = drop_scale_outliers(
                a_rows, net_income_by_year(existing_annual, tk))
            if bad:
                dropped += len(bad)
                dropped_detail.append((tk, bad))
            if q_rows or a_rows:
                ok += 1
            q_all += q_rows
            a_all += a_rows
        except Exception as e:  # noqa: BLE001
            errors.append((tk, f"{type(e).__name__}: {e}"))
        if i % 500 == 0:
            print(f"  {i}/{len(tickers)} 처리 (수집 {ok}종목)", flush=True)

    if errors:
        print(f"경고: {len(errors)}종목에서 예외가 발생해 제외됨.", flush=True)
        for tk, msg in errors[:30]:
            print(f"  {tk}: {msg}", flush=True)

    print(f"수집 완료: {ok}종목 / CIK 없음 {no_cik} / 분할이력 실패 {no_splits} / "
          f"EPS 태그 없음 {no_tag} / 자릿수 폐기 {dropped}건 "
          f"({len(dropped_detail)}종목)", flush=True)
    for tk, bad in dropped_detail[:40]:
        print(f"  폐기 {tk}: {bad}", flush=True)

    path_q = DATA / "financials/quarterly.parquet"
    if not args.limit:
        # 전 종목 실행일 때만 갈아엎는다. 야후가 남긴 계산값이 섞여 있으면
        # SEC가 못 채운 종목이 여전히 틀린 값으로 판정된다.
        purge_eps(path_q, {r["ticker"] for r in q_all})
        purge_eps(path_a, {r["ticker"] for r in a_all})
    if q_all:
        update_parquet(path_q, pd.DataFrame(q_all),
                       ["ticker", "year", "quarter", "account"])
    if a_all:
        update_parquet(path_a, pd.DataFrame(a_all), ["ticker", "year", "account"])


if __name__ == "__main__":
    main()
