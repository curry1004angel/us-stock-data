# yfinance로 애널리스트 컨센서스·EPS 추정 리비전·실적일정·밸류에이션 배수·보유구조를 수집해 Parquet으로 저장하는 스크립트
#
# 카드 생성기(analysis-cards)는 네트워크를 타지 않는다. 같은 종목 카드가 실행 시각마다
# 달라지면 재현성이 깨지므로, 네트워크 수집은 이 단계에서만 하고 날짜(asof)를 붙여 캐시한다.
#
# 사용법:
#     python scripts/fetch_analyst.py AAPL MSFT
#     python scripts/fetch_analyst.py --all
#     python scripts/fetch_analyst.py --all --limit 500 --sleep 0.5
import argparse
import time
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

DATA = Path("data")
OUT_DIR = DATA / "analyst"

# info에서 그대로 가져오는 스칼라 필드 (카드 컬럼명 ← 야후 필드명)
INFO_FIELDS = {
    "n_analysts": "numberOfAnalystOpinions",
    "rec_mean": "recommendationMean",
    "rec_key": "recommendationKey",
    "trailing_pe": "trailingPE",
    "forward_pe": "forwardPE",
    "price_to_book": "priceToBook",
    "peg_ratio": "pegRatio",
    "ev_to_ebitda": "enterpriseToEbitda",
    "dividend_yield": "dividendYield",
    "payout_ratio": "payoutRatio",
    "beta": "beta",
    "market_cap": "marketCap",
    "held_pct_institutions": "heldPercentInstitutions",
    "held_pct_insiders": "heldPercentInsiders",
    "float_shares": "floatShares",
    "shares_outstanding": "sharesOutstanding",
    "short_pct_float": "shortPercentOfFloat",
}

# eps_trend / earnings_estimate 에서 뽑을 기간 (0y=당해 회계연도, +1y=차기)
PERIODS = {"0y": "0y", "1y": "+1y"}
HORIZONS = {"current": "current", "7d": "7daysAgo", "30d": "30daysAgo",
            "60d": "60daysAgo", "90d": "90daysAgo"}


def pending_tickers(all_tickers, existing_df, max_age_days: int, today: str) -> list:
    # 6132종목 × info 호출은 장시간 작업이라 중단 위험이 실재한다.
    # 이미 최근에 받은 종목은 건너뛰어 재실행이 이어서 진행되게 한다.
    # asof는 이 스크립트가 "%Y-%m-%d"로 저장한다. 파싱 실패(NaT)는 대상으로 남긴다.
    if existing_df is None or len(existing_df) == 0:
        return list(all_tickers)
    cutoff = pd.Timestamp(today) - pd.Timedelta(days=max_age_days)
    asof = pd.to_datetime(existing_df["asof"], format="%Y-%m-%d", errors="coerce")
    fresh = set(existing_df.loc[asof >= cutoff, "ticker"])
    return [t for t in all_tickers if t not in fresh]


def yahoo_symbol(ticker: str, market: str = "") -> str:
    # 미국은 티커 그대로 쓴다. (한국 저장소 쪽 스크립트가 .KS/.KQ를 붙인다.)
    return str(ticker).strip()


def _num(x):
    # 값이 없거나 NaN이면 None으로 통일해 저장한다 (0으로 채우지 않는다).
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    return x


def _first_date(x):
    # calendar의 'Earnings Date'는 리스트로 오기도 한다. 첫 값을 YYYY-MM-DD로.
    if isinstance(x, (list, tuple)):
        x = x[0] if x else None
    if x is None:
        return None
    try:
        return pd.Timestamp(x).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def fetch_one(symbol: str) -> tuple:
    # 종목 하나의 스냅샷 dict와 실적 서프라이즈 이력 DataFrame을 돌려준다.
    t = yf.Ticker(symbol)
    row = {}

    try:
        info = t.info or {}
    except Exception:
        info = {}
    for key, field in INFO_FIELDS.items():
        row[key] = _num(info.get(field))

    # 목표주가
    try:
        tp = t.analyst_price_targets or {}
    except Exception:
        tp = {}
    for key in ("current", "high", "low", "mean", "median"):
        row[f"target_{key}"] = _num(tp.get(key))

    # 등급 분포 (가장 최근 기간 0m)
    try:
        rec = t.recommendations
    except Exception:
        rec = None
    for key in ("strongBuy", "buy", "hold", "sell", "strongSell"):
        row[f"rec_{key}"] = None
    if rec is not None and not rec.empty:
        latest = rec.iloc[0]
        for key in ("strongBuy", "buy", "hold", "sell", "strongSell"):
            if key in rec.columns:
                row[f"rec_{key}"] = _num(latest.get(key))

    # EPS 추정 리비전
    try:
        trend = t.eps_trend
    except Exception:
        trend = None
    for pkey, pidx in PERIODS.items():
        for hkey, hcol in HORIZONS.items():
            val = None
            if trend is not None and not trend.empty and pidx in trend.index and hcol in trend.columns:
                val = _num(trend.loc[pidx, hcol])
            row[f"eps_{pkey}_{hkey}"] = val

    # 당해연도 EPS·매출 추정 요약
    for name, attr in (("earnings", "earnings_estimate"), ("revenue", "revenue_estimate")):
        try:
            est = getattr(t, attr)
        except Exception:
            est = None
        for col, key in (("avg", "avg"), ("low", "low"), ("high", "high"),
                         ("numberOfAnalysts", "n"), ("growth", "growth"),
                         ("yearAgoEps", "year_ago")):
            val = None
            if est is not None and not est.empty and "0y" in est.index and col in est.columns:
                val = _num(est.loc["0y", col])
            row[f"{name}_est_{key}"] = val

    # 실적 일정
    try:
        cal = t.calendar or {}
    except Exception:
        cal = {}
    row["next_earnings_date"] = _first_date(cal.get("Earnings Date"))
    row["ex_dividend_date"] = _first_date(cal.get("Ex-Dividend Date"))

    # 서프라이즈 이력
    hist = pd.DataFrame()
    try:
        ed = t.get_earnings_dates(limit=24)
    except Exception:
        ed = None
    if ed is not None and not ed.empty:
        ed = ed.reset_index()
        ren = {}
        for c in ed.columns:
            cl = str(c).lower()
            if cl.startswith("earnings date") or cl == "index":
                ren[c] = "date"
            elif "eps estimate" in cl:
                ren[c] = "eps_estimate"
            elif "reported eps" in cl:
                ren[c] = "eps_actual"
            elif "surprise" in cl:
                ren[c] = "surprise_pct"
        ed = ed.rename(columns=ren)
        keep = [c for c in ("date", "eps_estimate", "eps_actual", "surprise_pct") if c in ed.columns]
        if "date" in keep:
            hist = ed[keep].copy()
            hist["date"] = pd.to_datetime(hist["date"], utc=True, errors="coerce").dt.strftime("%Y-%m-%d")
            # 아직 발표 전 행(실제값 없음)은 버린다 — 이력 표에 빈 줄이 생기지 않게.
            if "eps_actual" in hist.columns:
                hist = hist[hist["eps_actual"].notna()]
            hist = hist.dropna(subset=["date"])
    return row, hist


def upsert(path: Path, new: pd.DataFrame, keys: list):
    # 기존 파일이 있으면 같은 키의 행을 새 값으로 갈아끼운다.
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = pd.read_parquet(path)
        merged = pd.concat([old, new], ignore_index=True)
        merged = merged.drop_duplicates(subset=keys, keep="last")
    else:
        merged = new
    merged.to_parquet(path, index=False)
    return len(merged)


def main():
    ap = argparse.ArgumentParser(description="애널리스트 컨센서스·추정 리비전 수집기")
    ap.add_argument("tickers", nargs="*", help="종목 티커 (없으면 --all 필요)")
    ap.add_argument("--all", action="store_true", help="stock_list.csv 전체")
    ap.add_argument("--limit", type=int, default=0, help="--all일 때 상위 N개만")
    ap.add_argument("--sleep", type=float, default=0.3, help="종목간 대기 초")
    ap.add_argument("--max-age", type=int, default=30,
                    help="--all일 때 이 일수 안에 받은 종목은 건너뛴다")
    ap.add_argument("--save-every", type=int, default=25,
                    help="N종목마다 중간 저장한다")
    args = ap.parse_args()

    lst = pd.read_csv(DATA / "stock_list.csv", dtype={"ticker": str})
    lst["ticker"] = lst["ticker"].astype(str).str.strip()
    if args.all:
        tickers = lst["ticker"].dropna().tolist()
        existing = None
        snap_path = OUT_DIR / "snapshot.parquet"
        if snap_path.exists():
            existing = pd.read_parquet(snap_path, columns=["ticker", "asof"])
        tickers = pending_tickers(tickers, existing, args.max_age,
                                  date.today().strftime("%Y-%m-%d"))
        if args.limit:
            tickers = tickers[:args.limit]
        print(f"수집 대상 {len(tickers)}종목 (최근 {args.max_age}일 내 수집분은 제외)")
    else:
        tickers = [t.strip() for t in args.tickers]
    if not tickers:
        ap.error("티커를 지정하거나 --all을 쓰세요.")

    market_of = dict(zip(lst["ticker"], lst.get("market", pd.Series(dtype=str))))
    asof = date.today().strftime("%Y-%m-%d")
    rows, hists = [], []
    for i, tk in enumerate(tickers, 1):
        sym = yahoo_symbol(tk, market_of.get(tk, ""))
        try:
            row, hist = fetch_one(sym)
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(tickers)}] {tk} 실패: {e}")
            continue
        row["ticker"] = tk
        row["asof"] = asof
        rows.append(row)
        if not hist.empty:
            hist["ticker"] = tk
            hists.append(hist)
        got = "O" if row.get("target_mean") is not None else "-"
        print(f"[{i}/{len(tickers)}] {tk} 컨센서스={got} 애널리스트={row.get('n_analysts')}")
        if args.save_every and i % args.save_every == 0 and rows:
            # 6000종목 순회 중 중단되면 전부 날아간다. 주기적으로 확정한다.
            upsert(OUT_DIR / "snapshot.parquet", pd.DataFrame(rows), ["ticker"])
            rows = []
        if args.sleep:
            time.sleep(args.sleep)

    if rows:
        snap = pd.DataFrame(rows)
        n = upsert(OUT_DIR / "snapshot.parquet", snap, ["ticker"])
        print(f"[저장] {OUT_DIR/'snapshot.parquet'} (총 {n}종목)")
    if hists:
        hh = pd.concat(hists, ignore_index=True)
        n2 = upsert(OUT_DIR / "earnings_history.parquet", hh, ["ticker", "date"])
        print(f"[저장] {OUT_DIR/'earnings_history.parquet'} (총 {n2}행)")


if __name__ == "__main__":
    main()
