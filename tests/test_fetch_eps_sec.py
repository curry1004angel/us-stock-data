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


def _units(n, tag="EarningsPerShareBasic"):
    return {"units": {"USD/shares": [
        {"start": "2024-01-01", "end": "2024-12-31", "val": float(i),
         "filed": "2025-02-01", "form": "10-K"} for i in range(n)]}}


def _facts(n, taxonomy="us-gaap", tag="EarningsPerShareBasic"):
    return {"facts": {taxonomy: {tag: _units(n)}}}


def test_주_태그에_값이_있으면_바로_쓴다():
    calls = []

    def get(url, **kw):
        calls.append(url)
        return FakeResponse(200, _units(3))

    facts, status = F.fetch_facts("0000320193", type("S", (), {"get": staticmethod(get)}))
    assert status == "ok" and len(facts) == 3
    assert len(calls) == 1


def test_주_태그가_200에_0건이면_보조_태그를_믿지_않는다():
    # ACHC 실측: 주 태그가 200에 0건이고 보조 태그에 12건뿐인데, 정작
    # companyfacts에는 주 태그가 254건 있다. 보조 태그가 이기면 계열이 잘린다.
    seen = []

    def get(url, **kw):
        seen.append(url)
        if "companyfacts" in url:
            return FakeResponse(200, _facts(254))
        return FakeResponse(200, _units(0))

    facts, status = F.fetch_facts("0001520697", type("S", (), {"get": staticmethod(get)}))
    assert status == "ok" and len(facts) == 254
    # 주 태그 한 번 보고 곧장 companyfacts로 갔다. 보조 태그는 묻지 않는다.
    assert len(seen) == 2 and "companyfacts" in seen[1]


def test_주_태그가_404면_보조_태그를_쓴다():
    # ABNB 실측: EarningsPerShareBasic이 404고 계속사업 주당이익만 있다.
    def get(url, **kw):
        if "EarningsPerShareBasic.json" in url:
            return FakeResponse(404)
        if "IncomeLossFromContinuingOperationsPerBasicShare" in url:
            return FakeResponse(200, _units(68))
        return FakeResponse(404)

    facts, status = F.fetch_facts("0001559720", type("S", (), {"get": staticmethod(get)}))
    assert status == "ok" and len(facts) == 68


def test_전부_404면_주당이익_미공시다():
    # 이때만 기존 값을 지운다. 폐쇄형 펀드·SPAC이 여기 해당한다.
    def get(url, **kw):
        return FakeResponse(404)

    facts, status = F.fetch_facts("0000000001", type("S", (), {"get": staticmethod(get)}))
    assert status == "none" and facts == []


def test_조회가_실패하면_미공시로_보지_않는다():
    # 네트워크 문제를 미공시로 뭉개면 멀쩡한 종목의 EPS가 지워진다.
    def get(url, **kw):
        raise ConnectionError("boom")

    facts, status = F.fetch_facts("0000320193", type("S", (), {"get": staticmethod(get)}))
    assert status == "fail" and facts == []


def test_일부_실패하고_나머지가_404면_실패로_본다():
    # 한 태그라도 조회에 실패했으면 "안 쓰는 회사"라고 단정할 수 없다.
    def get(url, **kw):
        if "companyfacts" in url:
            return FakeResponse(404)
        if "EarningsPerShareBasic.json" in url:
            return FakeResponse(503)
        return FakeResponse(404)

    facts, status = F.fetch_facts("0000320193", type("S", (), {"get": staticmethod(get)}))
    assert status == "fail"


def test_ifrs_태그도_찾는다():
    def get(url, **kw):
        if "companyfacts" in url:
            return FakeResponse(200, _facts(9, "ifrs-full", "BasicEarningsLossPerShare"))
        return FakeResponse(404)

    facts, status = F.fetch_facts("0000000002", type("S", (), {"get": staticmethod(get)}))
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
