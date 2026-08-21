# CANSLIM 여섯 항목(C·A·N·S·L·I)을 종목 단위로 판정하는 모듈
import pandas as pd

from canslim_bases import BREAKOUT_VOL_MULT, breakout_confirmed
from canslim_loaders import QUARTER_ORDER
from canslim_scoring import Criterion, grade_item

C_EPS_MIN_YOY = 20.0          # 스펙 5.2 핵심요소 1
ONE_OFF_MULT = 3.0            # 순이익 > 영업이익 × 3 이면 일회성 의심
C_REVENUE_MIN_YOY = 25.0      # 스펙 5.2 부가요소 1
PEER_STRONG_REVENUE = 25.0    # 스펙 5.2 부가요소 2

A_CAGR_MIN = 25.0             # 스펙 5.3 핵심요소 1. 3년 누적 증가율 하한
A_YEARS = 3                   # 3판에서 "4~5년"에서 3년으로 단축됨
A_ROE_MIN = 17.0              # 스펙 5.3 부가요소 1
A_CF_TO_NI_MIN = 1.2          # 스펙 5.3 부가요소 2
A_STABILITY_MAX = 0.50        # 스펙 5.3 부가요소 3. 표준편차 ÷ 평균

N_NEAR_HIGH_PCT = 5.0         # 스펙 5.4 핵심요소 1. 52주 신고점 -5% 이내

S_VOL_SURGE_MULT = 1.5        # 스펙 5.5 핵심요소 1. 50일 평균 대비 급증 배수
S_DEBT_RATIO_MAX = 0.50       # 부채총계 ÷ 자산총계 상한
S_INSIDER_MIN = 0.10          # 스펙 5.5 부가요소 3. 경영진 지분 하한

L_RS_MIN = 70                 # 스펙 5.6 핵심요소 1. 미만은 즉시 F
L_RS_STRONG = 80              # 스펙 5.6 부가요소 1
L_INDUSTRY_TOP_PCT = 0.20     # 스펙 5.6 핵심요소 2. 업종 평균 RS 상위 20%
L_RANK_MAX = 3                # 스펙 5.6 핵심요소 3. 업종 내 3위 이내
# 업종에 이 수보다 적은 종목만 있으면 "3위 이내"가 60% 이상을 통과시켜 변별력이 없다.
# 한국 업종은 분류당 중앙값이 8.5종목이라 미국(23종목)보다 이 문제가 크다. 없는
# 변별력을 있는 것처럼 보이게 하지 않으려고 미계산으로 보낸다 (스펙 3.2).
L_MIN_INDUSTRY_SIZE = 5
# 한국 레포에서 업종을 못 채운 종목에 넣는 센티널. 업종으로 취급하면 무관한 종목들이
# 서로 피어가 되어 업종 백분위가 거짓이 된다 (스펙 5.6).
INDUSTRY_SENTINELS = {"", "(분류 없음)", "-", "nan", "None"}

I_HELD_MAX = 0.90             # 스펙 5.7 부가요소 1. 기관 비중이 과도하지 않음
# 스펙 5.7 한국 핵심요소 1·2. 60일을 먼저, 20일을 다음으로 판정한다.
I_KR_WINDOWS = (60, 20)
# 두 투자자의 순매수를 더한 합으로 판정한다. 각각이 따로 양수여야 한다는 뜻이 아니다.
I_KR_INVESTORS = ("기관합계", "외국인")


def _rows(b, ticker, account, annual=False):
    src = b.annual if annual else b.quarterly
    return src.get((ticker, account))


def latest_two_yoy(df):
    """YoY가 채워진 최근 두 행을 최신순으로 돌려준다. 없으면 None."""
    if df is None or len(df) == 0 or "yoy" not in df.columns:
        return (None, None)
    d = df[df["yoy"].notna()]
    if len(d) == 0:
        return (None, None)
    d = d.sort_values([c for c in ("year", "quarter") if c in d.columns])
    recs = d.to_dict("records")
    cur = recs[-1]
    prev = recs[-2] if len(recs) >= 2 else None
    return (cur, prev)


def _seq(rec):
    # 연도 경계를 넘어도 1씩 증가하는 분기 일련번호. loaders.quarter_key는 정렬 전용이라
    # 2026 1Q(20261)와 2025 4Q(20254)의 차이가 7이 되어 인접 판정에 쓸 수 없다.
    return int(rec["year"]) * 4 + QUARTER_ORDER[rec["quarter"]]


def _adjacent(cur, prev):
    # SEC XBRL은 회계연도 4분기를 따로 태깅하지 않아 분기가 통째로 빠진 종목이 많다.
    # 인접 여부를 판정에 쓰지는 않고 사유 문자열에만 남긴다.
    if not cur or not prev or "quarter" not in cur or "quarter" not in prev:
        return None
    return _seq(cur) - _seq(prev) == 1


def _period(rec):
    return f"{rec['year']} {rec['quarter']}" if "quarter" in rec else f"{rec['year']}"


def _valid_denominator(v):
    """나눗셈 분모로 쓸 수 있는 값인지 판정한다. `v <= 0` 같은 단순 비교만 쓰면
    NaN은 모든 비교가 False라 조용히 통과해버리므로(스펙 5.2·5.3) None·NaN·0·음수를
    한 번에 걸러야 한다."""
    return v is not None and not pd.isna(v) and v > 0


def judge_c(ticker, b, peer_revenue_yoy):
    eps_cur, eps_prev = latest_two_yoy(_rows(b, ticker, "eps"))

    # 핵심요소 1. 분기 EPS가 전년 동기 대비 20% 이상 증가
    if eps_cur is None:
        c1 = Criterion("분기 EPS 20% 이상 증가", None, "EPS 전년 대비 증가율 없음")
    else:
        c1 = Criterion("분기 EPS 20% 이상 증가", bool(eps_cur["yoy"] >= C_EPS_MIN_YOY),
                       f"{_period(eps_cur)} {eps_cur['yoy']:+.1f}%")

    # 핵심요소 2. 일회성 상승 배제. 순이익 > 영업이익 × 3 이면 핵심요소 1을 미충족 처리
    ni, op = _rows(b, ticker, "net_income"), _rows(b, ticker, "operating_profit")
    ni_cur = latest_two_yoy(ni)[0] if ni is not None else None
    op_last = None
    if op is not None and len(op):
        op_last = op.sort_values(["year", "quarter"]).to_dict("records")[-1]
    amt = op_last["amount"] if op_last is not None else None
    # amt(분모)는 Task 4에서 가드했으나 ni_cur["amount"](분자)는 안 걸렸다. NaN은
    # truthy라 걸러지지 않으면 ratio가 NaN이 되고 ratio > ONE_OFF_MULT가 False라
    # "일회성 아님"으로 조용히 오판정된다 (스펙 5.2).
    if ni_cur is None or amt is None or not _valid_denominator(amt) or pd.isna(ni_cur["amount"]):
        c2 = Criterion("일회성 이익 아님", None, "순이익 또는 영업이익 없음")
    else:
        ratio = ni_cur["amount"] / amt
        suspect = ratio > ONE_OFF_MULT
        c2 = Criterion("일회성 이익 아님", not suspect,
                       f"순이익/영업이익 {ratio:.1f}배" + ("  일회성 의심" if suspect else ""))
        if suspect and c1.passed:
            c1 = Criterion(c1.name, False, c1.detail + "  일회성 의심으로 미충족 처리")

    # 핵심요소 3. 증가 속도가 빨라짐 (이번 분기 YoY > 직전 분기 YoY)
    if eps_cur is None or eps_prev is None:
        c3 = Criterion("이익 증가 가속", None, "비교할 직전 분기 증가율 없음")
    else:
        adj = _adjacent(eps_cur, eps_prev)
        note = "" if adj else "  인접 분기 아님(회계 4분기 미공시)"
        c3 = Criterion("이익 증가 가속", bool(eps_cur["yoy"] > eps_prev["yoy"]),
                       f"{eps_cur['yoy']:+.1f}% 대 {eps_prev['yoy']:+.1f}%{note}")

    # 부가요소 1. 지난 3분기 매출이 25% 이상 증가하거나 최소한 증가 속도가 빨라짐
    rev = _rows(b, ticker, "revenue")
    if rev is None or "yoy" not in rev.columns or rev["yoy"].notna().sum() == 0:
        b1 = Criterion("매출 증가 또는 가속", None, "매출 증가율 없음")
    else:
        last3 = rev[rev["yoy"].notna()].sort_values(["year", "quarter"])["yoy"].tolist()[-3:]
        strong = all(v >= C_REVENUE_MIN_YOY for v in last3)
        accel = len(last3) >= 2 and last3[-1] > last3[-2]
        b1 = Criterion("매출 증가 또는 가속", bool(strong or accel),
                       "최근 " + ", ".join(f"{v:+.1f}%" for v in last3))

    # 부가요소 2. 같은 업종에 강한 분기 매출 증가율을 보이는 다른 종목이 하나 이상
    industry = None
    if len(b.stock_list):
        hit = b.stock_list[b.stock_list["ticker"] == ticker]
        if len(hit):
            industry = str(hit.iloc[0].get("industry", "")).strip()
    peers = peer_revenue_yoy.get(industry) if industry else None
    if not industry or peers is None:
        b2 = Criterion("업종 내 강한 매출 종목 존재", None, "업종 미상")
    else:
        n = sum(1 for v in peers if v is not None and v >= PEER_STRONG_REVENUE)
        b2 = Criterion("업종 내 강한 매출 종목 존재", n >= 1, f"{industry} {n}종목")

    return grade_item("C", [c1, c2, c3], [b1, b2])


def _annual_last(b, ticker, account):
    d = _rows(b, ticker, account, annual=True)
    if d is None or len(d) == 0:
        return None
    return d.sort_values("year").to_dict("records")[-1]


def judge_a(ticker, b):
    eps = _rows(b, ticker, "eps", annual=True)
    recs = eps.sort_values("year").to_dict("records") if eps is not None else []
    # 3년 증가율 3개를 내려면 연도가 4개 필요하다.
    window = recs[-(A_YEARS + 1):]

    if len(window) < A_YEARS + 1:
        c1 = Criterion(f"{A_YEARS}년 누적 EPS 증가율 {A_CAGR_MIN:.0f}% 이상", None,
                       f"연간 EPS {len(recs)}개 (필요 {A_YEARS + 1}개)")
        c2 = Criterion(f"{A_YEARS}년 연속 EPS 증가", None, "연간 EPS 부족")
        growths = []
    else:
        first, last = window[0]["amount"], window[-1]["amount"]
        # NaN은 truthy라 first<=0 같은 단순 비교로는 걸러지지 않는다. first는
        # _valid_denominator로, last는 pd.isna로 따로 막는다 (스펙 5.3).
        if not _valid_denominator(first) or pd.isna(last):
            c1 = Criterion(f"{A_YEARS}년 누적 EPS 증가율 {A_CAGR_MIN:.0f}% 이상", None,
                           "기준 연도 EPS가 0 이하이거나 값이 없어 증가율을 낼 수 없다")
        else:
            cum = (last - first) / abs(first) * 100
            c1 = Criterion(f"{A_YEARS}년 누적 EPS 증가율 {A_CAGR_MIN:.0f}% 이상",
                           bool(cum >= A_CAGR_MIN),
                           f"{window[0]['year']}~{window[-1]['year']} {cum:+.1f}%")
        growths = [r["yoy"] for r in window[1:] if r.get("yoy") is not None and not pd.isna(r["yoy"])]
        if len(growths) < A_YEARS:
            c2 = Criterion(f"{A_YEARS}년 연속 EPS 증가", None, f"연간 증가율 {len(growths)}개")
        else:
            c2 = Criterion(f"{A_YEARS}년 연속 EPS 증가", all(g > 0 for g in growths),
                           ", ".join(f"{g:+.1f}%" for g in growths))

    # 부가요소 1. 자기자본이익률 17% 이상
    ni_a, eq_a = _annual_last(b, ticker, "net_income"), _annual_last(b, ticker, "total_equity")
    if (ni_a is None or eq_a is None or pd.isna(ni_a["amount"])
            or not _valid_denominator(eq_a["amount"])):
        b1 = Criterion(f"자기자본이익률 {A_ROE_MIN:.0f}% 이상", None, "순이익 또는 자본총계 없음")
    else:
        roe = ni_a["amount"] / eq_a["amount"] * 100
        b1 = Criterion(f"자기자본이익률 {A_ROE_MIN:.0f}% 이상", bool(roe >= A_ROE_MIN), f"{roe:.1f}%")

    # 부가요소 2. 주당현금흐름이 EPS보다 20% 이상 많음.
    # 주식수가 약분되어 영업현금흐름 ÷ 순이익으로 같은 판정이 된다 (스펙 5.3).
    ocf_a = _annual_last(b, ticker, "operating_cashflow")
    if (ni_a is None or ocf_a is None or pd.isna(ocf_a["amount"])
            or not _valid_denominator(ni_a["amount"])):
        b2 = Criterion(f"영업현금흐름이 순이익의 {A_CF_TO_NI_MIN}배 이상", None,
                       "순이익이 0 이하이거나 현금흐름 없음")
    else:
        ratio = ocf_a["amount"] / ni_a["amount"]
        b2 = Criterion(f"영업현금흐름이 순이익의 {A_CF_TO_NI_MIN}배 이상",
                       bool(ratio >= A_CF_TO_NI_MIN), f"{ratio:.2f}배")

    # 부가요소 3. 3년 증가율의 표준편차가 평균의 50% 이하
    if len(growths) < A_YEARS:
        b3 = Criterion("이익 증가가 꾸준함", None, "증가율 표본 부족")
    else:
        s = pd.Series(growths, dtype="float64")
        mean = s.mean()
        if mean <= 0:
            b3 = Criterion("이익 증가가 꾸준함", False, f"평균 증가율 {mean:+.1f}%")
        else:
            cv = s.std(ddof=0) / mean
            b3 = Criterion("이익 증가가 꾸준함", bool(cv <= A_STABILITY_MAX),
                           f"변동계수 {cv:.2f}")

    # 부가요소 4. 내년 추정 이익이 올해보다 많음
    row = b.analyst.loc[ticker] if ticker in getattr(b.analyst, "index", []) else None
    cur_est = row.get("eps_0y_current") if row is not None else None
    next_est = row.get("eps_1y_current") if row is not None else None
    if cur_est is None or next_est is None or pd.isna(cur_est) or pd.isna(next_est):
        b4 = Criterion("내년 추정 이익 증가", None, "애널리스트 추정 미커버")
    else:
        b4 = Criterion("내년 추정 이익 증가", bool(next_est > cur_est),
                       f"{cur_est:.2f} -> {next_est:.2f}")

    return grade_item("A", [c1, c2], [b1, b2, b3, b4])


def _res(b, ticker, col):
    if ticker not in getattr(b.results, "index", []):
        return None
    v = b.results.loc[ticker].get(col)
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else v


def _snapshot_metric(frame, ticker, col):
    """b.shares·b.analyst처럼 티커당 한 행뿐인 스냅샷 프레임에서 보조 지표를 꺼낸다.
    _res와 같은 모양(없으면 None, NaN도 None)이되 대상 프레임을 인자로 받아
    여러 스냅샷 프레임에 재사용하고, 있는 값은 float으로 맞춘다."""
    if ticker not in getattr(frame, "index", []):
        return None
    v = frame.loc[ticker].get(col)
    return None if v is None or pd.isna(v) else float(v)


def judge_n(ticker, b, base_state, vol_avg50):
    close, high52 = _res(b, ticker, "close"), _res(b, ticker, "high_52w")

    # 핵심요소 1. 52주 신고점이거나 신고점 -5% 이내
    if close is None or high52 is None or high52 <= 0:
        c1 = Criterion(f"52주 신고점 -{N_NEAR_HIGH_PCT:.0f}% 이내", None, "종가 또는 52주 고가 없음")
    else:
        gap = (1 - close / high52) * 100
        c1 = Criterion(f"52주 신고점 -{N_NEAR_HIGH_PCT:.0f}% 이내",
                       bool(gap <= N_NEAR_HIGH_PCT), f"신고점 대비 -{gap:.1f}%")

    # 핵심요소 2. 횡보(베이스) 이후의 돌파
    if base_state is None:
        c2 = Criterion("베이스 돌파", None, "베이스 계산 불가")
    elif base_state.in_base:
        c2 = Criterion("베이스 돌파", False, f"{base_state.label} (아직 돌파 전)")
    elif base_state.pivot is None:
        c2 = Criterion("베이스 돌파", False, base_state.label)
    else:
        c2 = Criterion("베이스 돌파", True, f"{base_state.label}, 피봇 {base_state.pivot:.2f}")

    # 부가요소 1. 돌파일 거래량 >= 50일 평균 × 1.4
    # vol_avg50은 신규 상장주 등에서 None뿐 아니라 NaN으로도 들어올 수 있어 pd.isna로
    # 따로 막는다. 0 이하도 막아야 한다. 거래정지 종목은 50일 내내 거래량이 0일 수
    # 있는데, volume이 넘파이 실수라 0으로 나눠도 예외가 아니라 inf가 나온다. 그러면
    # volume >= 0 × 1.4가 참이 되어 거래량 확인 없이 돌파 통과로 둔갑한다.
    # pd.isna를 먼저 두는 순서가 중요하다. NaN <= 0은 False라 순서가 바뀌면 NaN이 다시 샌다.
    # close는 위에서 이미 None 가능성이 남아있으므로 여기서도 걸러야
    # breakout_confirmed(close, ...)의 close > pivot 비교가 None으로 죽지 않는다.
    volume = _res(b, ticker, "volume")
    pivot = base_state.pivot if base_state else None
    if (volume is None or vol_avg50 is None or pd.isna(vol_avg50) or vol_avg50 <= 0
            or pivot is None or close is None):
        b1 = Criterion(f"돌파 거래량 50일 평균 {BREAKOUT_VOL_MULT}배 이상", None, "거래량 자료 부족")
    else:
        ok = breakout_confirmed(close, volume, pivot, vol_avg50)
        b1 = Criterion(f"돌파 거래량 50일 평균 {BREAKOUT_VOL_MULT}배 이상", ok,
                       f"{volume / vol_avg50:.2f}배")

    return grade_item("N", [c1, c2], [b1])


def judge_s(ticker, b, vol_surge_days):
    # 핵심요소 1. 최근 거래량이 50일 평균 대비 급증한 날이 존재
    if vol_surge_days is None:
        c1 = Criterion(f"거래량 50일 평균 {S_VOL_SURGE_MULT}배 급증일 존재", None, "가격 자료 부족")
    else:
        c1 = Criterion(f"거래량 50일 평균 {S_VOL_SURGE_MULT}배 급증일 존재",
                       vol_surge_days >= 1, f"최근 50일 중 {vol_surge_days}일")

    # 부가요소 1. 자산 대비 부채 비율이 낮고 지난 3년간 감소
    li = _rows(b, ticker, "total_liabilities", annual=True)
    ta = _rows(b, ticker, "total_assets", annual=True)
    ratios = []
    if li is not None and ta is not None:
        m = li[["year", "amount"]].merge(ta[["year", "amount"]], on="year",
                                         suffixes=("_li", "_ta")).sort_values("year")
        m = m[m["amount_ta"] > 0]
        ratios = (m["amount_li"] / m["amount_ta"]).tolist()[-3:]
    # NaN은 truthy라 ratios[-1]이 NaN이어도 "<=", "<" 비교는 그냥 False를 내
    # bool(False and False)로 조용히 "미통과"가 찍힌다(사유에 "nan%"까지 남는다).
    # any(pd.isna(...))로 먼저 걸러야 미계산으로 간다.
    if len(ratios) < 2 or any(pd.isna(v) for v in ratios):
        b1 = Criterion("부채비율이 낮고 감소 중", None, "부채·자산 자료 부족")
    else:
        low = ratios[-1] <= S_DEBT_RATIO_MAX
        falling = ratios[-1] < ratios[0]
        b1 = Criterion("부채비율이 낮고 감소 중", bool(low and falling),
                       " -> ".join(f"{v * 100:.0f}%" for v in ratios))

    # 부가요소 2. 자사주 매입. 주식수가 전년 대비 감소
    yoy = _snapshot_metric(b.shares, ticker, "shares_yoy")
    if yoy is None:
        b2 = Criterion("주식수 전년 대비 감소", None, "주식수 변화율 없음")
    else:
        b2 = Criterion("주식수 전년 대비 감소", yoy < 0, f"{yoy:+.2f}%")

    # 부가요소 3. 최고경영진 지분 비중이 큼
    ins = _snapshot_metric(b.analyst, ticker, "held_pct_insiders")
    if ins is None:
        b3 = Criterion(f"경영진 지분 {S_INSIDER_MIN * 100:.0f}% 이상", None, "지분 자료 없음")
    else:
        b3 = Criterion(f"경영진 지분 {S_INSIDER_MIN * 100:.0f}% 이상",
                       bool(ins >= S_INSIDER_MIN), f"{ins * 100:.1f}%")

    return grade_item("S", [c1], [b1, b2, b3])


def industry_stats(results, stock_list):
    """업종별 평균 RS와 RS 내림차순 티커 목록, 업종 평균 RS의 백분위를 만든다."""
    if not len(stock_list) or not len(results):
        return {"_percentile": {}}
    ind = dict(zip(stock_list["ticker"], stock_list.get("industry", "")))
    rows = []
    for tk, rs in results["rs_rating"].items():
        name = str(ind.get(tk, "")).strip()
        if name in INDUSTRY_SENTINELS or rs is None or pd.isna(rs):
            continue
        rows.append((name, tk, float(rs)))
    if not rows:
        return {"_percentile": {}}

    df = pd.DataFrame(rows, columns=["industry", "ticker", "rs"])
    stats = {}
    for name, g in df.groupby("industry", sort=False):
        g = g.sort_values("rs", ascending=False)
        stats[name] = {"mean_rs": float(g["rs"].mean()), "ranked": g["ticker"].tolist()}

    means = pd.Series({k: v["mean_rs"] for k, v in stats.items()})
    # 백분위 1.0이 가장 강한 업종이다.
    stats["_percentile"] = means.rank(pct=True).to_dict()
    return stats


def judge_l(ticker, b, ind_stats, corr_drawdown):
    rs = _res(b, ticker, "rs_rating")

    if rs is None:
        c1 = Criterion(f"RS 등급 {L_RS_MIN} 이상", None, "RS 등급 없음")
    else:
        c1 = Criterion(f"RS 등급 {L_RS_MIN} 이상", bool(rs >= L_RS_MIN), f"RS {rs:.0f}")

    industry = ""
    if len(b.stock_list):
        hit = b.stock_list[b.stock_list["ticker"] == ticker]
        if len(hit):
            industry = str(hit.iloc[0].get("industry", "")).strip()
    known = industry not in INDUSTRY_SENTINELS and industry in ind_stats

    if not known:
        c2 = Criterion(f"업종 평균 RS 상위 {L_INDUSTRY_TOP_PCT * 100:.0f}%", None, "업종 미상")
        c3 = Criterion(f"업종 내 RS {L_RANK_MAX}위 이내", None, "업종 미상")
    else:
        pct = ind_stats["_percentile"].get(industry, 0.0)
        c2 = Criterion(f"업종 평균 RS 상위 {L_INDUSTRY_TOP_PCT * 100:.0f}%",
                       bool(pct >= 1 - L_INDUSTRY_TOP_PCT),
                       f"{industry} 상위 {(1 - pct) * 100:.0f}%")
        ranked = ind_stats[industry]["ranked"]
        if len(ranked) < L_MIN_INDUSTRY_SIZE:
            # len(ranked)는 업종 상장 종목 수가 아니라 그중 RS를 보유한 종목 수다
            # (industry_stats가 RS 없는·NaN 종목을 건너뛴다). "RS 보유"를 명시하지
            # 않으면 업종 자체가 작다는 뜻으로 잘못 읽힌다.
            c3 = Criterion(f"업종 내 RS {L_RANK_MAX}위 이내", None,
                           f"{industry} RS 보유 {len(ranked)}종목 "
                           f"(순위 판정에 최소 {L_MIN_INDUSTRY_SIZE}종목 필요)")
        else:
            rank = ranked.index(ticker) + 1 if ticker in ranked else None
            if rank is None:
                c3 = Criterion(f"업종 내 RS {L_RANK_MAX}위 이내", None, "업종 순위 계산 불가")
            else:
                c3 = Criterion(f"업종 내 RS {L_RANK_MAX}위 이내", rank <= L_RANK_MAX,
                               f"{industry} {rank}/{len(ranked)}위")

    if rs is None:
        b1 = Criterion(f"RS 등급 {L_RS_STRONG} 이상", None, "RS 등급 없음")
    else:
        b1 = Criterion(f"RS 등급 {L_RS_STRONG} 이상", bool(rs >= L_RS_STRONG), f"RS {rs:.0f}")

    # corr_drawdown 자체가 None인 경우 외에, 가격 이력이 짧아 튜플 안의 값만 NaN으로
    # 들어올 수 있다. NaN은 truthy라 stock_dd > index_dd 비교가 조용히 False를 내고
    # 사유에 "nan%"가 찍힌다. pd.isna로 먼저 걸러야 미계산으로 간다 (스펙 5.6).
    if corr_drawdown is None or pd.isna(corr_drawdown[0]) or pd.isna(corr_drawdown[1]):
        b2 = Criterion("조정 구간 하락폭이 지수보다 작음", None, "조정 구간 자료 없음")
    else:
        stock_dd, index_dd = corr_drawdown
        b2 = Criterion("조정 구간 하락폭이 지수보다 작음", bool(stock_dd > index_dd),
                       f"종목 {stock_dd:+.1f}% 대 지수 {index_dd:+.1f}%")

    # RS 70 미만은 부진 종목이므로 다른 요소와 무관하게 즉시 F다 (스펙 5.6).
    hard_fail = c1.passed is False
    return grade_item("L", [c1, c2, c3], [b1, b2], hard_fail=hard_fail)


def judge_i_us(ticker, b):
    hist = b.inst_history
    series = []
    if len(hist):
        h = hist[hist["ticker"] == ticker].sort_values("asof")
        # 같은 asof가 중복된 행이 있으면 실제로는 스냅샷 하나인데 둘 이상으로 세게
        # 되어 비교 대상이 없는데도 핵심요소가 계산된 것처럼 오판정된다. 같은
        # 날짜는 하나로 합친다 (스펙 5.7).
        h = h.drop_duplicates("asof", keep="last")
        series = [(r["asof"], r["held_pct_institutions"]) for _, r in h.iterrows()
                  if r["held_pct_institutions"] is not None
                  and not pd.isna(r["held_pct_institutions"])]

    # 핵심요소 1. 기관 보유 비중이 존재하고 직전 스냅샷 대비 증가
    if len(series) < 2:
        c1 = Criterion("기관 보유 비중 증가", None,
                       f"비교할 이전 스냅샷 없음 (현재 {len(series)}개)")
    else:
        (_, prev), (asof, cur) = series[-2], series[-1]
        c1 = Criterion("기관 보유 비중 증가", bool(cur > prev),
                       f"{prev * 100:.1f}% -> {cur * 100:.1f}% ({asof})")

    # 부가요소 1. 기관 비중이 과도하지 않음
    held = series[-1][1] if series else None
    if held is None:
        held = _snapshot_metric(b.analyst, ticker, "held_pct_institutions")
    if held is None:
        b1 = Criterion(f"기관 비중 {I_HELD_MAX * 100:.0f}% 미만", None, "기관 비중 없음")
    else:
        b1 = Criterion(f"기관 비중 {I_HELD_MAX * 100:.0f}% 미만", bool(held < I_HELD_MAX),
                       f"{held * 100:.1f}%")

    return grade_item("I", [c1], [b1])


def _flow_sum(flows, ticker, window):
    """해당 종목·기간의 기관합계+외국인 순매수 합을 낸다.

    두 투자자 중 하나라도 행이 없거나 금액이 NaN이면 합 자체를 낼 수 없으므로
    None을 돌려준다. 한쪽 금액만으로 판정하면 "기관이 샀는데 외국인이 더 팔았다"를
    매수로 오판정한다. 금액 0은 결측이 아니라 실제 순매수 0이라 합에 그대로 더한다.
    """
    if flows is None or not len(flows):
        return None
    f = flows[(flows["ticker"] == ticker) & (flows["window"] == window)]
    total = 0.0
    for investor in I_KR_INVESTORS:
        hit = f[f["investor"] == investor]
        if not len(hit):
            return None
        # asof 오름차순 정렬 후 마지막 값을 집는다. 정렬 없이 iloc[-1]을 쓰면
        # "가장 최근 asof"가 아니라 "프레임 행 순서상 마지막"을 집어, 같은
        # (종목·기간·투자자)에 스냅샷이 여러 개 쌓이면 더 오래된 값이 최신으로
        # 오판정될 수 있다 (judge_i_us의 asof 정렬과 같은 이유, 스펙 5.7).
        v = hit.sort_values("asof")["amount"].iloc[-1]
        # pd.isna는 NaN만 걸러낸다. inf는 NaN이 아니라서 그대로 통과하면 total이
        # inf가 되고 total > 0이 조용히 True로 굳어 사유 문자열에도 "inf"가 샌다
        # (결함 부류 3). 유한한 값만 합에 더한다.
        if v is None or pd.isna(v) or v in (float("inf"), float("-inf")):
            return None
        total += float(v)
    return total


def judge_i_kr(ticker, b):
    # 핵심요소 1·2. 기관+외국인 60일·20일 누적 순매수가 각각 0보다 큼
    core = []
    for window in I_KR_WINDOWS:
        name = f"기관+외국인 {window}일 누적 순매수 양수"
        total = _flow_sum(b.flows, ticker, window)
        if total is None:
            core.append(Criterion(name, None, f"{window}일 수급 자료 없음"))
        else:
            core.append(Criterion(name, bool(total > 0), f"{total / 1e8:+,.0f}억원"))

    # 부가요소 1. 기관 비중이 과도하지 않음. 한국 애널리스트 스냅샷이 5종목뿐이라
    # 사실상 전 종목 데이터부족이 된다. 부가요소는 분모에서 빠지므로 등급은 핵심요소로
    # 정해진다 (스펙 3.1).
    held = _snapshot_metric(b.analyst, ticker, "held_pct_institutions")
    if held is None:
        b1 = Criterion(f"기관 비중 {I_HELD_MAX * 100:.0f}% 미만", None, "기관 비중 없음")
    else:
        b1 = Criterion(f"기관 비중 {I_HELD_MAX * 100:.0f}% 미만", bool(held < I_HELD_MAX),
                       f"{held * 100:.1f}%")

    return grade_item("I", core, [b1])
