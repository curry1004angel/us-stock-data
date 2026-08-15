# 시장 지수 일봉을 수집해 연도 무관 단일 Parquet으로 저장하는 스크립트
#
# M(시장 동향) 항목의 분산일 카운트와 지수 이평선 판정에 쓴다.
# 개별 종목 가격과 달리 지수는 수가 적어 연도 분할 없이 코드당 한 파일로 둔다.
import sys
from datetime import date, timedelta
from pathlib import Path

import FinanceDataReader as fdr
import pandas as pd

OUT_DIR = Path("data/indices")
COLUMNS = ["date", "open", "high", "low", "close", "volume"]

# 코드 → 사람이 읽을 이름. 앱이 이 이름을 그대로 화면에 쓴다.
INDICES = {
    "US500": "S&P 500",
    "IXIC": "나스닥 종합",
    "DJI": "다우 산업",
}

DEFAULT_YEARS = 5


def normalize(df) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=COLUMNS)
    out = df.reset_index()
    date_col = out.columns[0]
    out["date"] = pd.to_datetime(out[date_col]).dt.strftime("%Y%m%d")
    for src, dst in (("Open", "open"), ("High", "high"), ("Low", "low"), ("Close", "close")):
        out[dst] = out[src].astype(float)
    # 일부 지수는 거래량을 주지 않는다. 분산일 판정이 불가능하다는 뜻이므로 0으로 두고
    # 소비 측(canslim.py)이 0을 보고 데이터부족으로 처리하게 한다.
    out["volume"] = out["Volume"].astype("int64") if "Volume" in out.columns else 0
    return out[COLUMNS].sort_values("date").reset_index(drop=True)


def main():
    years = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_YEARS
    start = (date.today() - timedelta(days=365 * years + 10)).strftime("%Y-%m-%d")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for code, name in INDICES.items():
        try:
            raw = fdr.DataReader(code, start)
        except Exception as e:  # noqa: BLE001
            print(f"  {code}({name}) 수집 실패: {e}")
            continue
        new = normalize(raw)
        if new.empty:
            print(f"  {code}({name}) 데이터 없음")
            continue
        path = OUT_DIR / f"{code}.parquet"
        if path.exists():
            old = pd.read_parquet(path)
            new = pd.concat([old, new], ignore_index=True).drop_duplicates("date", keep="last")
            new = new.sort_values("date").reset_index(drop=True)
        new.to_parquet(path, index=False, compression="snappy")
        print(f"  {code}({name}): {len(new)}행 ({new['date'].iloc[0]}~{new['date'].iloc[-1]}) → {path}")


if __name__ == "__main__":
    main()
