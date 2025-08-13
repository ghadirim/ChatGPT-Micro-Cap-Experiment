#!/usr/bin/env python3
"""
Trading_Script.py — self-contained daily scanner that outputs BUY signals.

- Universe: from env UNIVERSE (comma-sep) or universe.txt (one per line) or defaults below.
- Risk model (for ~$2k account by default):
    ACCOUNT_EQUITY=2000, RISK_PCT=0.01, MAX_POS_PCT=0.25
- Filters: price >= $1, 20-day avg $ volume >= $300k, gap filter ±20%
- Entry: 20-day breakout AND price > 50-SMA AND RSI(14) in [50,70]
- Stop: entry - 2*ATR(14)
- Target: ~2R (entry + 4*ATR)
- Output: buy_signals.csv and signals.csv with identical BUY rows (or header-only if none)

Env overrides (optional):
  ACCOUNT_EQUITY, RISK_PCT, MAX_POS_PCT, MIN_PRICE, MIN_ADV_USD, UNIVERSE
"""

import os, sys, math, warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone

# ---------- Configuration ----------
TODAY = datetime.now(timezone.utc).date().isoformat()

EQUITY      = float(os.getenv("ACCOUNT_EQUITY", "2000"))
RISK_PCT    = float(os.getenv("RISK_PCT", "0.01"))       # 1% risk = $20 on $2k
MAX_POS_PCT = float(os.getenv("MAX_POS_PCT", "0.25"))    # 25% per name

MIN_PRICE   = float(os.getenv("MIN_PRICE", "1.0"))       # skip sub-$1
MIN_ADV_USD = float(os.getenv("MIN_ADV_USD", "300000"))  # 20d avg $ volume

CSV_COLS = ["date","symbol","side","entry","stop","target","confidence","notes","shares"]
OUT_MAIN = "signals.csv"
OUT_BUYS = "buy_signals.csv"

DEFAULT_UNIVERSE = ["ABEO","CADL","CSAI","AZTR","IINN","ACTU","ESPR"]  # edit as you like


# ---------- Helpers ----------
def load_universe():
    env = os.getenv("UNIVERSE", "").strip()
    if env:
        return [s.strip().upper() for s in env.split(",") if s.strip()]
    if os.path.exists("universe.txt"):
        with open("universe.txt", "r") as f:
            return [ln.strip().upper() for ln in f if ln.strip() and not ln.startswith("#")]
    return DEFAULT_UNIVERSE

def rsi14(close: pd.Series) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    roll_up = gain.rolling(14, min_periods=14).mean()
    roll_dn = loss.rolling(14, min_periods=14).mean().replace(0, np.nan)
    rs = roll_up / roll_dn
    return 100 - (100 / (1 + rs))

def atr14(df: pd.DataFrame) -> pd.Series:
    h,l,c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([(h-l).abs(), (h-c.shift(1)).abs(), (l-c.shift(1)).abs()], axis=1).max(axis=1)
    return tr.rolling(14, min_periods=14).mean()

def passes_liquidity(df: pd.DataFrame) -> bool:
    px = float(df["Close"].iloc[-1])
    if px < MIN_PRICE:
        return False
    adv_usd = (df["Close"].tail(20) * df["Volume"].tail(20)).mean()
    if pd.isna(adv_usd) or adv_usd < MIN_ADV_USD:
        return False
    return True

def gap_spike_ok(df: pd.DataFrame) -> bool:
    """True if NOT a +/-20% open gap spike."""
    if len(df) < 2:
        return True
    prev_close = float(df["Close"].iloc[-2])
    today_open = float(df["Open"].iloc[-1])
    if prev_close <= 0:
        return True
    gap = abs(today_open/prev_close - 1.0)
    return gap < 0.20

def breakout_signal(df: pd.DataFrame) -> bool:
    close = df["Close"]
    if len(close) < 60:
        return False
    hi20 = close.rolling(20).max()
    sma50 = close.rolling(50).mean()
    rsi = rsi14(close)
    last = close.index[-1]
    cond_break = close.loc[last] >= (hi20.loc[last] - 1e-8)
    cond_trend = close.loc[last] > sma50.loc[last]
    val_rsi = rsi.loc[last]
    cond_rsi = (not pd.isna(val_rsi)) and (50 <= val_rsi <= 70)
    return bool(cond_break and cond_trend and cond_rsi)

def size_shares(entry: float, stop: float) -> int:
    if entry <= 0 or stop >= entry:
        return 0
    risk_dollars = EQUITY * RISK_PCT
    stop_dist = entry - stop
    if stop_dist <= 0:
        return 0
    by_risk = int(math.floor(risk_dollars / stop_dist))
    by_cap  = int(math.floor((EQUITY * MAX_POS_PCT) / entry))
    return max(0, min(by_risk, by_cap))

def fetch_daily(sym: str) -> pd.DataFrame | None:
    try:
        df = yf.Ticker(sym).history(period="6mo", interval="1d", auto_adjust=False)
        if df is None or df.empty:
            return None
        for col in ["Open","High","Low","Close","Volume"]:
            if col not in df.columns:
                return None
        return df.dropna()
    except Exception:
        return None


# ---------- Main ----------
def main():
    universe = load_universe()
    print(f"[INFO] Universe ({len(universe)}): {', '.join(universe)}")
    rows = []

    for sym in universe:
        df = fetch_daily(sym)
        if df is None or len(df) < 60:
            print(f"[SKIP] {sym}: insufficient data")
            continue
        if not passes_liquidity(df):
            print(f"[SKIP] {sym}: liquidity/price filter failed")
            continue
        if not gap_spike_ok(df):
            print(f"[SKIP] {sym}: open gap spike >= 20%")
            continue
        if not breakout_signal(df):
            print(f"[SKIP] {sym}: no breakout setup")
            continue

        entry = float(df["Close"].iloc[-1])            # using close; you can switch to next open for live routing
        atrv  = float(atr14(df).iloc[-1])
        if math.isnan(atrv) or atrv <= 0:
            print(f"[SKIP] {sym}: ATR invalid")
            continue

        stop   = round(entry - 2.0 * atrv, 2)
        target = round(entry + 4.0 * atrv, 2)
        if stop >= entry:
            print(f"[SKIP] {sym}: stop >= entry")
            continue

        shares = size_shares(entry, stop)
        if shares < 1:
            print(f"[SKIP] {sym}: <1 share under risk/cap constraints")
            continue

        notes = "20d breakout & >50SMA & RSI(50-70); ATR stop x2; ~2R target"
        row = {
            "date": TODAY,
            "symbol": sym,
            "side": "BUY",
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "target": round(target, 2),
            "confidence": 0.60,
            "notes": notes,
            "shares": shares,
        }
        rows.append(row)
        print(f"[BUY ] {sym}: entry {row['entry']} stop {row['stop']} target {row['target']} shares {shares}")

    # Write outputs (always create files with header)
    out_df = pd.DataFrame(rows, columns=CSV_COLS)
    if out_df.empty:
        pd.DataFrame(columns=CSV_COLS).to_csv(OUT_BUYS, index=False)
        pd.DataFrame(columns=CSV_COLS).to_csv(OUT_MAIN, index=False)
        print("[INFO] No buys today. Wrote header-only buy_signals.csv and signals.csv")
    else:
        out_df.to_csv(OUT_BUYS, index=False)
        out_df.to_csv(OUT_MAIN, index=False)
        print(f"[INFO] Wrote {len(out_df)} rows to {OUT_BUYS} and {OUT_MAIN}")

if __name__ == "__main__":
    # accept and ignore --headless for compatibility
    _ = [a for a in sys.argv[1:] if a == "--headless"]
    main()
