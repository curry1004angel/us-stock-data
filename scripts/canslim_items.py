# CANSLIM 여섯 항목(C·A·N·S·L·I)을 종목 단위로 판정하는 모듈
from canslim_loaders import QUARTER_ORDER
from canslim_scoring import Criterion, grade_item

C_EPS_MIN_YOY = 20.0          # 스펙 5.2 핵심요소 1
ONE_OFF_MULT = 3.0            # 순이익 > 영업이익 × 3 이면 일회성 의심
C_REVENUE_MIN_YOY = 25.0      # 스펙 5.2 부가요소 1
PEER_STRONG_REVENUE = 25.0    # 스펙 5.2 부가요소 2


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
    if ni_cur is None or op_last is None or not op_last["amount"] or op_last["amount"] <= 0:
        c2 = Criterion("일회성 이익 아님", None, "순이익 또는 영업이익 없음")
    else:
        ratio = ni_cur["amount"] / op_last["amount"]
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
