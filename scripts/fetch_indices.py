# 시장 지수 일봉을 수집해 연도 무관 단일 Parquet으로 저장하는 스크립트
#
# M(시장 동향) 항목의 분산일 카운트와 지수 이평선 판정에 쓴다.
# 개별 종목 가격과 달리 지수는 수가 적어 연도 분할 없이 코드당 한 파일로 둔다.
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

OUT_DIR = Path("data/indices")
COLUMNS = ["date", "open", "high", "low", "close", "volume"]

# 저장 파일명 → (야후 심볼, 사람이 읽을 이름). 이름은 앱이 그대로 화면에 쓴다.
#
# FDR의 US500·IXIC·DJI 심볼을 쓰면 GitHub Actions에서 거래량 없는 소스로 폴백해
# Volume이 전부 NaN으로 온다(로컬에서는 야후로 붙어 정상이라 재현되지 않았다).
# 분산일 판정에 지수 거래량이 필수라 야후 심볼을 직접 지정한다.
INDICES = {
    "US500": ("^GSPC", "S&P 500"),
    "IXIC": ("^IXIC", "나스닥 종합"),
    "DJI": ("^DJI", "다우 산업"),
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
    # 거래량 컬럼이 아예 없거나, 있어도 값이 NaN으로 오는 소스가 있다
    # (astype("int64")가 여기서 죽는다). 분산일 판정이 불가능하다는 뜻이므로 0으로 두고,
    # 소비 측(canslim.py)이 0을 보고 데이터부족으로 처리하게 한다.
    if "Volume" in out.columns:
        out["volume"] = pd.to_numeric(out["Volume"], errors="coerce").fillna(0).astype("int64")
    else:
        out["volume"] = 0
    return out[COLUMNS].sort_values("date").reset_index(drop=True)


def main():
    years = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_YEARS
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for code, (symbol, name) in INDICES.items():
        try:
            raw = yf.Ticker(symbol).history(period=f"{years}y", auto_adjust=False)
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
