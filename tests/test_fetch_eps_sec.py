# SEC 주당이익 수집의 기간 분류·분할 조정·자릿수 가드를 검증하는 테스트
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_eps_sec as F


def fact(start, end, val, filed, form="10-K"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}


# --- 기간 분류와 라벨 ---

def test_연간과_분기를_기간_길이로_가른다():
    assert F.period_kind("2024-02-01", "2025-01-31") == "annual"
    assert F.period_kind("2025-02-01", "2025-05-02") == "quarterly"


def test_반기와_누적_구간은_버린다():
    # SEC는 3Q 보고서에 9개월 누적도 함께 싣는다. 그걸 분기로 받으면 값이 부풀어 오른다.
    assert F.period_kind("2025-02-01", "2025-08-01") is None      # 6개월
    assert F.period_kind("2025-02-01", "2025-10-31") is None      # 9개월


def test_라벨은_종료일의_달력_연도와_분기다():
    # fetch_financials.py와 같은 규칙이어야 기존 계열과 안 어긋난다.
    assert F.label("2026-01-30") == (2026, "1Q")
    assert F.label("2025-05-02") == (2025, "2Q")
    assert F.label("2025-09-30") == (2025, "3Q")
    assert F.label("2025-12-31") == (2025, "4Q")


def test_정기보고서가_아니면_안_쓴다():
    assert F.usable(fact("2025-01-01", "2025-03-31", 1.0, "2025-04-01", "8-K")) is False
    assert F.usable(fact("2025-01-01", "2025-03-31", 1.0, "2025-04-01", "10-Q")) is True


def test_기간이_없는_사실은_안_쓴다():
    # 재무상태표성 사실은 start가 없다.
    assert F.usable({"end": "2025-03-31", "val": 1.0, "filed": "x", "form": "10-K"}) is False


# --- 액면분할 ---

def split(pairs):
    return {pd.Timestamp(d): r for d, r in pairs.items()}


def test_깔끔한_분할_비율만_인정한다():
    # yfinance는 분사 조정도 splits에 넣는다. DELL의 1.973이 VMware 분사다.
    # SEC가 중단사업 소급으로 이미 처리한 것을 또 곱하면 이중 반영이다.
    assert F.is_split(10.0) is True
    assert F.is_split(25.0) is True
    assert F.is_split(1.998) is True       # GOOGL 2:1
    assert F.is_split(1.5) is True         # 3:2
    assert F.is_split(1.973) is False      # DELL VMware 분사
    assert F.is_split(1.806) is False      # DELL 2018 분사
    assert F.is_split(1.0) is False


def test_역분할도_인정한다():
    assert F.is_split(0.1) is True
    assert F.is_split(0.05) is True


def test_제출일_뒤의_분할만_나눈다():
    s = split({"2024-06-10": 10.0})
    assert F.split_factor(s, "2024-02-21") == 10.0     # 분할 전 제출
    assert F.split_factor(s, "2025-02-26") == 1.0      # 분할 후 제출
    assert F.split_factor(s, "2024-06-10") == 1.0      # 권리락 당일은 이미 반영


def test_분할이_두_번이면_곱한다():
    # NVDA 실측: 2021-07-20 4:1, 2024-06-10 10:1
    s = split({"2021-07-20": 4.0, "2024-06-10": 10.0})
    assert F.split_factor(s, "2021-02-26") == 40.0
    assert F.split_factor(s, "2023-02-24") == 10.0
    assert F.split_factor(s, "2025-02-26") == 1.0


def test_분할이_없으면_배수가_1이다():
    assert F.split_factor({}, "2024-02-21") == 1.0
    assert F.split_factor(None, "2024-02-21") == 1.0


def test_오래된_연도가_분할_조정된다():
    # NVDA FY2022는 제출 2024-02-21에 3.91(분할 전)이다. 10:1 뒤 0.391이어야 한다.
    # FY2023은 제출 2025-02-26에 0.18(분할 후)이라 그대로 둔다.
    facts = [
        fact("2021-02-01", "2022-01-30", 3.91, "2024-02-21"),
        fact("2022-01-31", "2023-01-29", 1.76, "2024-02-21"),
        fact("2022-01-31", "2023-01-29", 0.18, "2025-02-26"),
    ]
    _, annual = F.build_rows("NVDA", facts, split({"2024-06-10": 10.0}))
    got = {r["year"]: round(r["amount"], 4) for r in annual}
    assert got[2022] == 0.391
    assert got[2023] == 0.18


def test_최신_제출본_값을_쓴다():
    facts = [fact("2024-02-01", "2025-01-31", 6.51, "2025-03-01"),
             fact("2024-02-01", "2025-01-31", 6.60, "2026-03-01")]
    _, annual = F.build_rows("X", facts)
    assert [r["amount"] for r in annual] == [6.60]


# --- 자릿수 가드 ---

def test_자릿수가_튀는_연도를_버린다():
    # ULBI 실측: 나머지가 0.05~1.57인데 2024년만 38이다. 100배다.
    rows = [{"ticker": "ULBI", "year": y, "account": "eps", "amount": v}
            for y, v in [(2021, 0.05), (2022, 0.05), (2023, 0.44), (2024, 38.0)]]
    ni = {2021: 800_000, 2022: 800_000, 2023: 7_200_000, 2024: 6_300_000}
    kept, bad = F.drop_scale_outliers(rows, ni)
    assert bad == [2024]
    assert [r["year"] for r in kept] == [2021, 2022, 2023]


def test_정상_종목은_아무것도_안_버린다():
    rows = [{"ticker": "AAPL", "year": y, "account": "eps", "amount": v}
            for y, v in [(2022, 6.15), (2023, 6.16), (2024, 6.11), (2025, 7.49)]]
    ni = {2022: 99_803e6, 2023: 96_995e6, 2024: 93_736e6, 2025: 112_010e6}
    kept, bad = F.drop_scale_outliers(rows, ni)
    assert bad == []
    assert len(kept) == 4


def test_연도가_적으면_가드를_안_건다():
    # 표본이 적으면 중앙값 자체가 못 믿을 값이다. 멀쩡한 값을 버리느니 통과시킨다.
    rows = [{"ticker": "X", "year": y, "account": "eps", "amount": v}
            for y, v in [(2023, 0.4), (2024, 40.0)]]
    kept, bad = F.drop_scale_outliers(rows, {2023: 4e6, 2024: 4e6})
    assert bad == []
    assert len(kept) == 2


def test_순이익이_없으면_가드를_안_건다():
    rows = [{"ticker": "X", "year": y, "account": "eps", "amount": 1.0}
            for y in range(2020, 2026)]
    kept, bad = F.drop_scale_outliers(rows, {})
    assert bad == []
    assert len(kept) == 6


def test_EPS가_0인_연도에서_0으로_안_나눈다():
    rows = [{"ticker": "X", "year": y, "account": "eps", "amount": v}
            for y, v in [(2021, 1.0), (2022, 0.0), (2023, 1.1), (2024, 1.2), (2025, 1.3)]]
    ni = {y: 1e8 for y in range(2021, 2026)}
    kept, bad = F.drop_scale_outliers(rows, ni)
    assert bad == []
    assert len(kept) == 5


# --- 통합 ---

def test_분기와_연간을_같이_낸다():
    facts = [fact("2025-02-01", "2026-01-30", 8.79, "2026-03-01"),
             fact("2025-05-03", "2025-08-01", 1.72, "2025-09-01", "10-Q")]
    q, a = F.build_rows("DELL", facts)
    assert [(r["year"], r["quarter"], r["amount"]) for r in q] == [(2025, "3Q", 1.72)]
    assert [(r["year"], r["amount"]) for r in a] == [(2026, 8.79)]


def test_사실이_없으면_빈_결과다():
    assert F.build_rows("X", []) == ([], [])


@pytest.mark.parametrize("bad", [None, ""])
def test_값이_없는_사실은_건너뛴다(bad):
    facts = [fact("2024-02-01", "2025-01-31", None, "2025-03-01")]
    _, a = F.build_rows("X", facts)
    assert a == []


def test_티커_NA는_결측이_아니라_종목이다(tmp_path):
    # Nano Labs Ltd의 티커가 실제로 "NA"다. pandas는 dtype=str를 줘도 이것을
    # NaN(float)으로 바꾸고, 루프에서 tk.upper()가 터져 2,700종목이 날아갔다
    # (2026-08-24 실행). 결측으로 버리면 멀쩡한 종목 하나를 조용히 잃는다.
    p = tmp_path / "stock_list.csv"
    p.write_text("ticker,name\nAAPL,Apple\nNA,Nano Labs Ltd\nMSFT,Microsoft\n",
                 encoding="utf-8-sig")
    assert F.load_tickers(p) == ["AAPL", "NA", "MSFT"]


def test_진짜_빈_칸은_빠진다(tmp_path):
    p = tmp_path / "stock_list.csv"
    p.write_text("ticker,name\nAAPL,Apple\n,빈칸\n  ,공백\nMSFT,Microsoft\n",
                 encoding="utf-8-sig")
    assert F.load_tickers(p) == ["AAPL", "MSFT"]


# --- 조회 상태: 삭제 여부를 가르는 관문 ---

class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


def _units(n, unit="USD/shares"):
    return {"units": {unit: [
        {"start": "2024-01-01", "end": "2024-12-31", "val": float(i),
         "filed": "2025-02-01", "form": "10-K"} for i in range(n)]}}


def _facts(*specs):
    """(분류, 태그, 건수[, 단위]) 여러 개로 companyfacts 응답을 만든다."""
    out = {}
    for spec in specs:
        taxonomy, tag, n = spec[0], spec[1], spec[2]
        unit = spec[3] if len(spec) > 3 else "USD/shares"
        out.setdefault(taxonomy, {})[tag] = _units(n, unit)
    return {"facts": out}


def _fake(payload, status=200):
    def get(url, **kw):
        return FakeResponse(status, payload)
    return type("S", (), {"get": staticmethod(get)})


def _fact(start, end, val, tag_rank=None, filed="2025-02-01", form="10-K"):
    f = {"start": start, "end": end, "val": val, "filed": filed, "form": form}
    if tag_rank is not None:
        f["_rank"] = tag_rank
    return f


def test_여러_태그를_합친다():
    # Ares 실측: 유닛당 태그가 2014~2019, 주당 태그가 2019~2025를 덮는다.
    # 한 태그만 고르면 나머지 기간이 통째로 사라진다.
    payload = _facts(("us-gaap", "EarningsPerShareBasic", 6),
                     ("us-gaap", "IncomeLossFromContinuingOperationsPerBasicShare", 56))
    facts, status = F.fetch_facts("0001831097", _fake(payload))
    assert status == "ok" and len(facts) == 62


def test_같은_기간이_겹치면_우선순위가_앞선_태그를_쓴다():
    # 계속사업 기준과 총액 기준이 한 계열에 섞이면 그 해만 성격이 달라진다.
    rank_basic = F.TAGS.index(("us-gaap", "EarningsPerShareBasic"))
    rank_diluted = F.TAGS.index(("us-gaap", "EarningsPerShareDiluted"))
    facts = [_fact("2024-01-01", "2024-12-31", 9.0, rank_diluted),
             _fact("2024-01-01", "2024-12-31", 7.0, rank_basic)]
    _q, ann = F.build_rows("AAA", facts)
    assert [r["amount"] for r in ann] == [7.0]


def test_한_태그만_덮는_기간은_그대로_들어온다():
    rank_basic = F.TAGS.index(("us-gaap", "EarningsPerShareBasic"))
    rank_lp = F.TAGS.index(
        ("us-gaap", "NetIncomeLossPerOutstandingLimitedPartnershipUnitBasicNetOfTax"))
    facts = [_fact("2018-01-01", "2018-12-31", 2.0, rank_lp),
             _fact("2024-01-01", "2024-12-31", 7.0, rank_basic)]
    _q, ann = F.build_rows("AAA", facts)
    assert sorted(r["year"] for r in ann) == [2018, 2024]


def test_비USD_단위도_받는다():
    # ASML은 EUR/shares, Ambev는 BRL/shares로 낸다. USD만 받으면 이들이
    # 미공시로 분류돼 기존 값까지 지워진다.
    payload = _facts(("us-gaap", "EarningsPerShareBasic", 51, "EUR/shares"))
    facts, status = F.fetch_facts("0000937966", _fake(payload))
    assert status == "ok" and len(facts) == 51


def test_USD가_있으면_다른_통화보다_먼저다():
    units = {"units": {"MXN/shares": _units(30)["units"]["USD/shares"],
                       "USD/shares": _units(4)["units"]["USD/shares"]}}
    assert len(F.pick_unit(units["units"])) == 4


def test_주당이_아닌_단위는_안_쓴다():
    assert F.pick_unit({"USD": [{"val": 1}], "shares": [{"val": 2}]}) == []


def test_합자회사_유닛당_태그도_찾는다():
    # AB·ARES·ARLP는 주당이 아니라 유닛당으로 낸다.
    tag = "NetIncomeLossPerOutstandingLimitedPartnershipUnitBasicNetOfTax"
    payload = _facts(("us-gaap", tag, 114))
    facts, status = F.fetch_facts("0001176948", _fake(payload))
    assert status == "ok" and len(facts) == 114


def test_정기보고서가_아닌_사실은_안_모은다():
    payload = {"facts": {"us-gaap": {
        "EarningsPerShareDiluted": {"units": {"USD/shares": [
            {"start": "2024-01-01", "end": "2024-12-31", "val": 1.0,
             "filed": "2025-02-01", "form": "8-K"} for _ in range(99)]}},
        "EarningsPerShareBasic": _units(3)}}}
    facts, status = F.fetch_facts("0000000004", _fake(payload))
    assert status == "ok" and len(facts) == 3


def test_태그는_있는데_쓸_사실이_없으면_미공시가_아니다():
    # 미공시로 뭉개면 기존 값이 지워진다. 우리가 못 건진 것과 회사가 안 낸
    # 것은 다르다. 여기서는 손대지 않고 넘어가야 한다.
    payload = {"facts": {"us-gaap": {"EarningsPerShareBasic": {"units": {
        "USD/shares": [{"start": "2024-01-01", "end": "2024-12-31", "val": 1.0,
                        "filed": "2025-02-01", "form": "8-K"}]}}}}}
    facts, status = F.fetch_facts("0000000006", _fake(payload))
    assert status == "ok" and facts == []


def test_SEC에_자료가_없으면_미공시로_보지_않는다():
    # Preferred Bank는 캘리포니아 주법 은행이라 10-K를 FDIC에 낸다.
    # Aya Gold & Silver는 캐나다 상장사다. 404는 없다는 증거가 아니다.
    facts, status = F.fetch_facts("0001492165", _fake(None, status=404))
    assert status == "no_filings" and facts == []


def test_손익도_주당이익도_없으면_미공시다():
    # 폐쇄형 펀드는 10-K 손익 자체가 없다. 이때만 기존 값을 지운다.
    payload = {"facts": {"us-gaap": {"Assets": {"units": {"USD": [{"val": 1}]}}}}}
    facts, status = F.fetch_facts("0000000005", _fake(payload))
    assert status == "none" and facts == []


def test_다중_클래스_회사를_미공시로_보지_않는다():
    # Constellation Brands 실측: 주당이익 사실에 ClassOfStockAxis가 붙어 있어
    # companyfacts에 안 보인다. us-gaap 태그는 657개고 NetIncomeLoss도 있다.
    # 미공시로 뭉개면 기존 EPS가 지워진다.
    payload = {"facts": {"us-gaap": {
        "PreferredStockParOrStatedValuePerShare": {"units": {"USD/shares": [
            {"val": 0.01, "end": "2025-02-28", "form": "10-K"}]}},
        "NetIncomeLoss": {"units": {"USD": [
            {"start": "2024-03-01", "end": "2025-02-28", "val": 8.0,
             "filed": "2025-04-20", "form": "10-K"}]}}}}}
    facts, status = F.fetch_facts("0000016918", _fake(payload))
    assert status == "ok" and facts == []      # 손대지 않고 넘어간다


def test_손익이_정기보고서가_아니면_사업회사로_안_본다():
    payload = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        {"start": "2024-01-01", "end": "2024-12-31", "val": 8.0,
         "filed": "2025-02-01", "form": "N-CSR"}]}}}}}
    facts, status = F.fetch_facts("0000000007", _fake(payload))
    assert status == "none" and facts == []


def test_조회가_실패하면_미공시로_보지_않는다():
    # 네트워크 문제를 미공시로 뭉개면 멀쩡한 종목의 EPS가 지워진다.
    def get(url, **kw):
        raise ConnectionError("boom")

    facts, status = F.fetch_facts("0000320193", type("S", (), {"get": staticmethod(get)}))
    assert status == "fail" and facts == []


def test_서버_오류는_실패로_본다():
    facts, status = F.fetch_facts("0000320193", _fake(None, status=503))
    assert status == "fail" and facts == []


def test_ifrs_태그도_찾는다():
    payload = _facts(("ifrs-full", "BasicEarningsLossPerShare", 9))
    facts, status = F.fetch_facts("0000000002", _fake(payload))
    assert status == "ok" and len(facts) == 9


# --- 쓰기: 처리한 종목만 갈아 끼운다 ---

KEYS = ["ticker", "year", "account"]


def _기존(path, eps_tickers):
    rows = [{"ticker": t, "year": 2025, "account": "eps", "amount": 1.0}
            for t in eps_tickers]
    rows.append({"ticker": "AAA", "year": 2025, "account": "net_income",
                 "amount": 5.0})
    pd.DataFrame(rows).to_parquet(path, index=False)


def _eps(path):
    d = pd.read_parquet(path)
    e = d[d.account == "eps"]
    return dict(zip(e.ticker, e.amount))


def test_처리하지_않은_종목은_손대지_않는다(tmp_path):
    # 2026-08-24: 전체를 지우고 덮는 방식이라 야후 차단으로 못 받은 4,325종목의
    # EPS가 사라졌다. 오래된 값이라도 없는 것보다 낫다.
    p = tmp_path / "annual.parquet"
    _기존(p, ["AAA", "BBB", "CCC"])
    새 = [{"ticker": "AAA", "year": 2025, "account": "eps", "amount": 9.0}]
    F.write_eps(p, 새, {"AAA"}, KEYS)

    got = _eps(p)
    assert got["AAA"] == 9.0          # 처리했으니 갈아 끼운다
    assert got["BBB"] == 1.0          # 못 받았으니 그대로 둔다
    assert got["CCC"] == 1.0


def test_EPS_태그가_없는_종목은_기존_값을_지운다(tmp_path):
    # 야후가 만든 계산값을 남기면 그것이 그대로 판정에 쓰인다. 폴백을 두지 않기로 했다.
    p = tmp_path / "annual.parquet"
    _기존(p, ["AAA", "BBB"])
    F.write_eps(p, [], {"BBB"}, KEYS)      # BBB는 응답을 받았으나 태그가 없었다

    got = _eps(p)
    assert "BBB" not in got
    assert got["AAA"] == 1.0


def test_다른_계정은_건드리지_않는다(tmp_path):
    p = tmp_path / "annual.parquet"
    _기존(p, ["AAA"])
    F.write_eps(p, [{"ticker": "AAA", "year": 2025, "account": "eps",
                     "amount": 9.0}], {"AAA"}, KEYS)
    d = pd.read_parquet(p)
    assert (d.account == "net_income").sum() == 1


def test_파일이_없으면_새로_만든다(tmp_path):
    p = tmp_path / "annual.parquet"
    F.write_eps(p, [{"ticker": "AAA", "year": 2025, "account": "eps",
                     "amount": 9.0}], {"AAA"}, KEYS)
    assert _eps(p) == {"AAA": 9.0}


def test_같은_키가_겹치면_새_값이_이긴다(tmp_path):
    p = tmp_path / "annual.parquet"
    _기존(p, ["AAA"])
    새 = [{"ticker": "AAA", "year": 2025, "account": "eps", "amount": 2.0},
          {"ticker": "AAA", "year": 2025, "account": "eps", "amount": 3.0}]
    F.write_eps(p, 새, {"AAA"}, KEYS)
    d = pd.read_parquet(p)
    assert len(d[(d.ticker == "AAA") & (d.account == "eps")]) == 1


# --- 뒤처짐 가드: SEC 태깅이 끊긴 종목을 옛 값으로 덮지 않는다 ---

def _행(*years):
    return [{"ticker": "AAA", "year": y, "account": "eps", "amount": 1.0}
            for y in years]


def _분기행(*pairs):
    return [{"ticker": "AAA", "year": y, "quarter": q, "account": "eps",
             "amount": 1.0} for y, q in pairs]


def test_연간_계열이_여러_해_뒤처지면_건너뛴다():
    # Ares 실측: SEC에는 2019년까지만 있고 기존에는 2025년까지 있다.
    # 덮으면 최신 연도가 사라져 A항목이 판정 불가가 된다.
    assert F.annual_too_stale(_행(2014, 2019), (2025, 0)) is True


def test_연간은_한_해_차이를_봐준다():
    # National Grid는 3월 결산이라 기존이 한 해 앞설 수 있다. 정상이다.
    assert F.annual_too_stale(_행(2016, 2025), (2026, 0)) is False


def test_연간_계열이_더_최신이면_건너뛰지_않는다():
    assert F.annual_too_stale(_행(2007, 2025), (2025, 0)) is False


def test_분기는_한_칸도_안_봐준다():
    # Northern Trust 실측: 기존 2026 2Q, SEC는 2026 1Q까지다. 덮으면
    # C항목이 한 분기 낡은 값으로 판정된다.
    assert F.quarterly_too_stale(_분기행((2026, "1Q")), (2026, 2)) is True


def test_분기가_같으면_갈아_끼운다():
    assert F.quarterly_too_stale(_분기행((2026, "2Q")), (2026, 2)) is False


def test_분기가_더_최신이면_갈아_끼운다():
    assert F.quarterly_too_stale(_분기행((2026, "3Q")), (2026, 2)) is False


def test_해가_바뀌어도_분기_순서를_지킨다():
    assert F.quarterly_too_stale(_분기행((2025, "4Q")), (2026, 1)) is True
    assert F.quarterly_too_stale(_분기행((2026, "1Q")), (2025, 4)) is False


def test_기존_값이_없으면_가드를_안_건다():
    assert F.annual_too_stale(_행(2019), None) is False
    assert F.quarterly_too_stale(_분기행((2020, "1Q")), None) is False


def test_새_행이_없으면_가드를_안_건다():
    assert F.annual_too_stale([], (2025, 0)) is False
    assert F.quarterly_too_stale([], (2026, 2)) is False


def test_최신_기간은_eps만_본다():
    # net_income은 eps보다 최신일 수 있다. 그걸 기준으로 삼으면 멀쩡한
    # SEC 계열이 뒤처졌다고 잘못 판정된다.
    df = pd.DataFrame([
        {"ticker": "AAA", "year": 2021, "account": "eps", "amount": 1.0},
        {"ticker": "AAA", "year": 2025, "account": "net_income", "amount": 5.0},
        {"ticker": "BBB", "year": 2025, "account": "eps", "amount": 2.0},
    ])
    assert F.last_eps_period(df, "AAA") == (2021, 0)
    assert F.last_eps_period(df, "CCC") is None
    assert F.last_eps_period(None, "AAA") is None


def test_분기_파일에서는_분기까지_본다():
    df = pd.DataFrame([
        {"ticker": "AAA", "year": 2026, "quarter": "1Q", "account": "eps",
         "amount": 1.0},
        {"ticker": "AAA", "year": 2026, "quarter": "2Q", "account": "eps",
         "amount": 2.0},
        {"ticker": "AAA", "year": 2026, "quarter": "3Q", "account": "net_income",
         "amount": 9.0},
    ])
    assert F.last_eps_period(df, "AAA") == (2026, 2)


def test_새_계열보다_최신인_기존_행은_남긴다(tmp_path):
    # 중남미 발행사 실측: SEC가 한 해 뒤처진다. 통째로 갈아 끼우면 최신 연도가
    # 사라져 A항목이 낡은 값으로 판정된다.
    p = tmp_path / "annual.parquet"
    pd.DataFrame([
        {"ticker": "AMX", "year": 2024, "account": "eps", "amount": 1.0},
        {"ticker": "AMX", "year": 2025, "account": "eps", "amount": 2.0},
    ]).to_parquet(p, index=False)
    새 = [{"ticker": "AMX", "year": 2023, "account": "eps", "amount": 8.0},
          {"ticker": "AMX", "year": 2024, "account": "eps", "amount": 9.0}]
    F.write_eps(p, 새, {"AMX"}, KEYS)

    got = dict(zip(pd.read_parquet(p)["year"], pd.read_parquet(p)["amount"]))
    assert got == {2023: 8.0, 2024: 9.0, 2025: 2.0}


def test_분기도_최신_기존_행을_남긴다(tmp_path):
    p = tmp_path / "quarterly.parquet"
    qkeys = ["ticker", "year", "quarter", "account"]
    pd.DataFrame([
        {"ticker": "AAA", "year": 2026, "quarter": "1Q", "account": "eps",
         "amount": 1.0},
        {"ticker": "AAA", "year": 2026, "quarter": "2Q", "account": "eps",
         "amount": 2.0},
    ]).to_parquet(p, index=False)
    새 = [{"ticker": "AAA", "year": 2026, "quarter": "1Q", "account": "eps",
           "amount": 9.0}]
    F.write_eps(p, 새, {"AAA"}, qkeys)

    d = pd.read_parquet(p)
    assert dict(zip(d["quarter"], d["amount"])) == {"1Q": 9.0, "2Q": 2.0}


def test_새_행이_없는_종목은_전부_지운다(tmp_path):
    # 주당이익을 아예 안 내는 회사다. 남길 경계가 없으므로 통째로 지운다.
    p = tmp_path / "annual.parquet"
    _기존(p, ["AAA", "BBB"])
    F.write_eps(p, [], {"BBB"}, KEYS)
    assert "BBB" not in _eps(p)


def test_연간만_받은_종목의_분기는_안_지운다(tmp_path):
    # 2026-08-24: 캐나다·아일랜드 발행사는 40-F/20-F로 연간만 내고 중간은
    # 6-K다. 한 집합으로 지우다가 AEM·AER·AGI 등 508종목이 분기를 잃었다.
    q = tmp_path / "quarterly.parquet"
    a = tmp_path / "annual.parquet"
    qkeys = ["ticker", "year", "quarter", "account"]
    pd.DataFrame([{"ticker": "AEM", "year": 2025, "quarter": "1Q",
                   "account": "eps", "amount": 0.5}]).to_parquet(q, index=False)
    _기존(a, ["AEM"])

    done_a, done_q = {"AEM"}, set()     # 연간만 받았다
    F.write_eps(q, [], done_q, qkeys)
    F.write_eps(a, [{"ticker": "AEM", "year": 2025, "account": "eps",
                     "amount": 4.0}], done_a, KEYS)

    assert _eps(a)["AEM"] == 4.0        # 연간은 갈아 끼운다
    assert _eps(q)["AEM"] == 0.5        # 분기는 그대로 둔다
