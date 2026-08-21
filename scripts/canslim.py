# 수집된 데이터로 전 종목 CANSLIM 등급·총점과 시장 신호를 계산해 CSV로 저장하는 스크립트
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from canslim_bases import base_status, buy_range, detect_base
from canslim_items import (industry_stats, judge_a, judge_c, judge_i_us, judge_l,
                           judge_n, judge_s, INDUSTRY_SENTINELS)
from canslim_loaders import BASE_INSUFFICIENT_LABEL, MIN_BASE_ROWS, load_all
from canslim_market import market_signal
from canslim_scoring import total_score

OUT_RESULTS = Path("data/screener/canslim_results.csv")
OUT_MARKET = Path("data/screener/market_signal.csv")

VOL_SURGE_MULT = 1.5          # S 항목 급증일 판정 배수
VOL_SURGE_WINDOW = 50         # 급증일을 세는 최근 거래일 수
DRAWDOWN_WINDOW = 60          # L 부가요소 2. 조정 구간을 찾는 최근 거래일 수
ITEM_KEYS = ["C", "A", "N", "S", "L", "I"]
# 실패 종목 비율 임계값. 전량 실패는 빈 DataFrame에서 KeyError로 크게 죽어 오히려
# 안전하지만, 일부만 실패하면 잘린 CSV가 그대로 쓰이고 커밋되어 어제의 완전한
# 결과를 조용히 덮어쓴다. 잘린 결과로 좋은 결과를 덮어쓰는 것이 아무것도 안 쓰는
# 것보다 나쁘다고 보고 이 문턱을 넘으면 CSV를 쓰지 않고 크게 실패시킨다.
MAX_FAILURE_RATE = 0.05


def _price_features(g):
    """종목 일봉에서 50일 평균 거래량과 최근 급증일 수를 낸다."""
    if len(g) < VOL_SURGE_WINDOW:
        return None, None
    vol = g["volume"]
    avg50 = vol.rolling(VOL_SURGE_WINDOW).mean()
    last_avg = avg50.iloc[-1]
    if pd.isna(last_avg) or last_avg <= 0:
        return None, None
    surge = (vol > avg50 * VOL_SURGE_MULT).iloc[-VOL_SURGE_WINDOW:]
    return float(last_avg), int(surge.fillna(False).sum())


def _index_drawdown(index_df):
    """최근 구간에서 지수 고점 이후 저점까지의 하락률과 그 날짜 구간을 낸다."""
    if index_df is None or len(index_df) < DRAWDOWN_WINDOW:
        return None
    d = index_df.sort_values("date").iloc[-DRAWDOWN_WINDOW:]
    # 종가가 결측이거나 0 이하인 행이 섞이면 idxmax/idxmin이 전부 NaN인 구간에서
    # ValueError로 죽거나(이 함수는 종목 루프보다 앞서 돌아 한 종목도 처리하기 전에
    # 전체 실행을 죽인다) 0을 고점/저점으로 잡아 하락률을 왜곡한다. index_signal과
    # 동일하게 유효 종가만 남기고 계산한다.
    d = d[d["close"].notna() & (d["close"] > 0)]
    if len(d) < 2:
        return None
    peak_i = d["close"].idxmax()
    after = d.loc[peak_i:]
    if len(after) < 2:
        return None
    trough_i = after["close"].idxmin()
    peak, trough = d.loc[peak_i, "close"], d.loc[trough_i, "close"]
    return (str(d.loc[peak_i, "date"]), str(d.loc[trough_i, "date"]),
            (trough - peak) / peak * 100)


def _stock_drawdown(g, start, end):
    d = g[(g["date"] >= pd.to_datetime(start, format="%Y%m%d")) &
          (g["date"] <= pd.to_datetime(end, format="%Y%m%d"))]
    if len(d) < 2:
        return None
    first, low = d["close"].iloc[0], d["close"].min()
    if first <= 0:
        return None
    return (low - first) / first * 100


def _check_failure_rate(n_errors, n_attempted):
    """실패 종목 비율이 MAX_FAILURE_RATE를 넘으면 예외를 던져 CSV 쓰기 전에 멈춘다.
    문턱 아래인 나머지는 GitHub Actions 실행 요약에 뜨도록 ::warning:: 한 줄을 남긴다."""
    if n_errors == 0:
        return
    fail_rate = n_errors / n_attempted if n_attempted else 1.0
    if fail_rate > MAX_FAILURE_RATE:
        raise RuntimeError(
            f"CANSLIM 판정 실패율이 임계값을 초과했다: {n_errors}/{n_attempted}종목 "
            f"({fail_rate:.1%}) 실패, 임계값 {MAX_FAILURE_RATE:.0%}."
        )
    print(f"::warning::CANSLIM 판정 실패 {n_errors}종목: 전체 {n_attempted}종목 중 {fail_rate:.1%}.")


def main():
    asof = date.today().strftime("%Y%m%d")
    b = load_all()
    print(f"로드 완료: results {len(b.results)}종목, prices {b.prices['ticker'].nunique()}종목")

    ind_stats = industry_stats(b.results, b.stock_list)
    print(f"업종 통계: {len(ind_stats) - 1}개 업종")

    # 업종별 최근 분기 매출 증가율 (C 부가요소 2)
    peer_rev = {}
    ind_of = dict(zip(b.stock_list["ticker"], b.stock_list.get("industry", "")))
    for (tk, account), df in b.quarterly.items():
        if account != "revenue":
            continue
        name = str(ind_of.get(tk, "")).strip()
        if name in INDUSTRY_SENTINELS:
            continue
        d = df[df["yoy"].notna()]
        if len(d):
            peer_rev.setdefault(name, []).append(float(d["yoy"].iloc[-1]))

    market = market_signal(b.indices)
    dd = _index_drawdown(b.indices.get("US500"))
    if dd:
        print(f"지수 조정 구간: {dd[0]}~{dd[1]} {dd[2]:+.1f}%")

    price_groups = dict(tuple(b.prices.groupby("ticker", sort=False))) if len(b.prices) else {}
    name_of = dict(zip(b.stock_list["ticker"], b.stock_list["name"]))

    rows = []
    errors = []
    # 요소별 미계산(passed is None) 집계. 종목 루프 안에서 같이 센다.
    crit_total, crit_unknown = {}, {}
    for ticker in b.results.index:
        try:
            g = price_groups.get(ticker)
            vol_avg50, surge_days = _price_features(g) if g is not None else (None, None)
            base = detect_base(g) if g is not None and len(g) >= MIN_BASE_ROWS else None
            # 라벨을 세 경우로 나눈다. 판정했으면 그 라벨, 일봉이 짧아 판정을 안 했으면
            # "자료 부족", 가격 데이터가 아예 없으면 "-". 뒤 두 경우를 같은 기호로
            # 뭉개면 "자료가 없다"와 "자료가 짧다"를 되짚을 수 없다. compute_bases.py도
            # 같은 상수를 써서 results.csv에 같은 라벨을 넣는다.
            if base is not None:
                label = base.label
            elif g is None:
                label = "-"
            else:
                label = BASE_INSUFFICIENT_LABEL

            corr = None
            if dd and g is not None:
                sd = _stock_drawdown(g, dd[0], dd[1])
                if sd is not None:
                    corr = (sd, dd[2])

            items = [
                judge_c(ticker, b, peer_rev),
                judge_a(ticker, b),
                judge_n(ticker, b, base, vol_avg50),
                judge_s(ticker, b, surge_days),
                judge_l(ticker, b, ind_stats, corr),
                judge_i_us(ticker, b),
            ]
            agg = total_score(items)

            pivot = base.pivot if base else None
            br = buy_range(pivot)
            # close가 결측이면 na_values=[""] 파싱 규칙상 NaN(float)으로 들어온다. None이
            # 아니라서 "is not None" 가드를 그냥 통과해버리면 base_status가 NaN을 그대로
            # 받아 "매수범위 초과(추격 금지)"로 잘못 굳어버린다 (NaN 비교는 항상 False).
            close = b.results.loc[ticker].get("close")
            if isinstance(close, float) and pd.isna(close):
                close = None
            row = {
                "ticker": ticker,
                "name": name_of.get(ticker, ""),
                "market": b.results.loc[ticker].get("market", ""),
                "industry": str(ind_of.get(ticker, "")).strip(),
                "score": agg["score"],
                "verdict": agg["verdict"],
                "insufficient": ",".join(agg["insufficient"]),
                "rs_rating": b.results.loc[ticker].get("rs_rating"),
                "close": close,
                "base_label": label,
                "pivot": round(pivot, 4) if pivot else None,
                "buy_low": round(br[0], 4) if br else None,
                "buy_high": round(br[1], 4) if br else None,
                # close가 0.0(거래정지)이면 예전 "if base and close"에서 falsy로 빠져
                # 베이스 상태를 잃었다. close is not None으로 0.0과 결측을 구분한다.
                "base_status": (base_status(close, pivot, base.in_base)
                                 if base and close is not None else "-"),
                "asof": asof,
            }
            for it in items:
                row[f"grade_{it.key}"] = it.grade
                row[f"reason_{it.key}"] = it.reason
                for kind, crits in (("핵심", it.core), ("부가", it.bonus)):
                    for i, c in enumerate(crits, 1):
                        label = f"{it.key}{kind}{i}"
                        crit_total[label] = crit_total.get(label, 0) + 1
                        if c.passed is None:
                            crit_unknown[label] = crit_unknown.get(label, 0) + 1
            for col in ("market_cap", "float_shares", "shares_yoy"):
                row[col] = b.shares.loc[ticker].get(col) if ticker in getattr(b.shares, "index", []) else None
            rows.append(row)
        except Exception as e:
            # 종목 하나의 예외로 5,840종목 전체 실행이 죽으면 일일 워크플로가 결과 자체를
            # 못 만든다. 그렇다고 통째로 삼키면 어떤 종목이 왜 빠졌는지 알 수 없으므로
            # 티커와 예외를 반드시 출력한다.
            errors.append((ticker, f"{type(e).__name__}: {e}"))

    _check_failure_rate(len(errors), len(b.results.index))
    if errors:
        print(f"경고: {len(errors)}종목에서 예외가 발생해 판정에서 제외됨.")
        for tk, msg in errors:
            print(f"  {tk}: {msg}")

    out = pd.DataFrame(rows).sort_values("score", ascending=False, na_position="last")
    OUT_RESULTS.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_RESULTS, index=False, encoding="utf-8-sig")

    n_ok = int((out["verdict"] == "정상").sum())
    print(f"판정 완료: {len(out)}종목 (정상 {n_ok}, 판정불가 {len(out) - n_ok}) -> {OUT_RESULTS}")
    for k in ITEM_KEYS:
        print(f"  {k} 등급 분포: {out[f'grade_{k}'].value_counts().to_dict()}")

    # 요소별 미계산 비율. 사유 문자열은 통과한 부가요소만 싣기 때문에, 어떤 요소가
    # 전 종목 미계산이어도 출력 어디에도 드러나지 않는다(그래서 결함 하나가 몇 주를
    # 살아남았다). 100%에 가까운 요소는 데이터가 희소한 게 아니라 입력 계약이 깨진 것이다.
    for k in ITEM_KEYS:
        parts = [f"{lab[len(k):]} {crit_unknown.get(lab, 0) / crit_total[lab]:.1%}"
                 for lab in crit_total if lab.startswith(k)]
        print(f"  {k} 요소별 미계산율: {' / '.join(parts)}")

    # 선도 종목 동향을 시장 강도 단서로 삼는다 (스펙 5.8).
    top20 = out[out["verdict"] == "정상"].head(20)["ticker"].tolist()
    changes = []
    for tk in top20:
        g = price_groups.get(tk)
        if g is not None and len(g) >= 2:
            prev, cur = g["close"].iloc[-2], g["close"].iloc[-1]
            if prev > 0:
                changes.append((cur - prev) / prev * 100)
    top20_avg = round(sum(changes) / len(changes), 2) if changes else None

    mrows = []
    for code, s in market["per_index"].items():
        sd = s["stall_days"]
        if sd is None:
            # 거래량을 셀 수 없음(결측·0으로 채워진 날이 창에 섞임). "0건"과 반드시
            # 구분해야 하므로 빈 문자열로 뭉개지 않고 그대로 None(빈 칸)으로 둔다.
            stall_repr = None
        elif sd:
            stall_repr = ",".join(sd)
        else:
            # 셀 수는 있었지만 정체일이 하루도 없었던 경우. 위 None과 달리 명시적으로
            # "0건"이라고 적어 CSV에서 두 경우가 똑같은 빈 칸으로 보이지 않게 한다.
            stall_repr = "0건"
        mrows.append({
            "asof": asof, "index_code": code, "close": s["close"],
            "change_pct": s["change_pct"], "above_ma50": s["above_ma50"],
            "above_ma200": s["above_ma200"], "ma200_rising": s["ma200_rising"],
            "distribution_days": s["distribution_days"],
            "stall_days": stall_repr,
            "signal": market["signal"], "advice": market["advice"],
            "top20_avg_change": top20_avg,
        })
    if not mrows:
        # 지수를 하나도 못 읽으면 pd.DataFrame([]).to_csv는 헤더도 없는 빈 파일을 쓰고,
        # 워크플로가 그것을 커밋해 어제의 정상 시장 신호를 덮어쓴다. 결과 CSV 쪽
        # MAX_FAILURE_RATE와 같은 이유로, 나쁜 것으로 좋은 것을 덮어쓰느니 안 쓴다.
        print(f"::warning::시장 신호 행이 0건이라 {OUT_MARKET}를 쓰지 않는다. "
              f"data/indices/를 확인하라.")
    else:
        pd.DataFrame(mrows).to_csv(OUT_MARKET, index=False, encoding="utf-8-sig")
        print(f"시장 신호: {market['signal']} ({market['advice']}), "
              f"분산일 {market['distribution_days']}, 상위20 평균 {top20_avg} -> {OUT_MARKET}")


if __name__ == "__main__":
    main()
