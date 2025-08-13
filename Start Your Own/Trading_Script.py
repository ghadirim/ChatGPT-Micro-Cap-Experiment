"""Wrapper for the shared trading script using local data directory."""

from pathlib import Path
import sys

# Allow importing the shared module from the repository root
sys.path.append(str(Path(__file__).resolve().parents[1]))

from trading_script import main


if __name__ == "__main__":

    data_dir = Path(__file__).resolve().parent
    main("Start Your Own/chatgpt_portfolio_update.csv", Path("Start Your Own"))

# === Signals CSV writer (append-only; safe to add at the very end) ===
def _emit_signals_csv(_picks, csv_path="signals.csv"):
    import os, datetime as dt
    try:
        import pandas as pd
    except Exception as e:
        print("⚠ pandas not available; cannot write CSV:", e)
        return

    rows = []
    for p in (_picks or []):
        if not isinstance(p, dict):
            continue
        # Accept a few common field names
        sym = str(p.get("symbol") or p.get("ticker") or "").strip().upper()
        if not sym:
            continue
        side = str(p.get("side") or p.get("action") or "BUY").strip().upper()
        def _f(x, default=0.0):
            try: return float(x)
            except Exception: return float(default)

        entry  = _f(p.get("entry") or p.get("entry_price") or p.get("price"))
        stop   = _f(p.get("stop") or p.get("stop_loss"))
        target = _f(p.get("target") or p.get("take_profit"))
        conf   = _f(p.get("conf") or p.get("confidence") or 0.5)
        notes  = str(p.get("reason") or p.get("notes") or "")

        rows.append({
            "date": dt.date.today().isoformat(),
            "symbol": sym,
            "side": side,
            "entry": round(entry, 4),
            "stop": round(stop, 4),
            "target": round(target, 4),
            "confidence": round(conf, 2),
            "notes": notes
        })

    import pandas as pd  # ensure alias present for concat below
    df = pd.DataFrame(rows)
    if df.empty:
        print("⚠ No signals produced today.")
        return

    if os.path.exists(csv_path):
        try:
            old = pd.read_csv(csv_path)
            df = (pd.concat([old, df], ignore_index=True)
                    .drop_duplicates(subset=["date", "symbol"], keep="last"))
        except Exception as e:
            print("ℹ️ Could not merge with existing CSV, writing fresh:", e)

    df.to_csv(csv_path, index=False)
    print(f"✅ Wrote {len(df)} rows to {csv_path}")

# Try to emit using a variable named `picks`. Change the name here if yours differs.
try:
    _emit_signals_csv(picks)
except NameError:
    print("⚠ 'picks' variable not found; CSV not written.")
except Exception as e:
    print("⚠ Error while writing signals.csv:", e)
# === End CSV writer ===
