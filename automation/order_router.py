# automation/order_router.py
# Reads signals.csv and places bracket orders via Alpaca (PAPER by default).
# Start on PAPER. Flip to live only after you're confident.

import csv, os, math, time, uuid, sys, requests

ALPACA_KEY      = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET   = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

RISK_PCT        = float(os.getenv("RISK_PCT_PER_TRADE", "0.005"))  # 0.5% per trade
MAX_POS_PCT     = float(os.getenv("MAX_POS_PCT", "0.10"))          # cap any single position to 10% of equity
MAX_OPEN_NAMES  = int(os.getenv("MAX_OPEN_NAMES", "5"))
ACCOUNT_EQUITY  = os.getenv("ACCOUNT_EQUITY", "")                  # leave blank to fetch from Alpaca
CSV_PATH        = os.getenv("SIGNALS_CSV", "signals.csv")
ALLOW_SHORTS    = os.getenv("ALLOW_SHORTS", "false").lower() == "true"
USE_LIMIT       = os.getenv("USE_LIMIT", "false").lower() == "true"
TIME_IN_FORCE   = os.getenv("TIME_IN_FORCE", "day").lower()        # "day" or "gtc"

def H():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

def _get(path):
    return requests.get(ALPACA_BASE_URL + path, headers=H(), timeout=20)

def _post(path, json):
    return requests.post(ALPACA_BASE_URL + path, json=json, headers=H(), timeout=20)

def get_equity():
    if ACCOUNT_EQUITY:
        return float(ACCOUNT_EQUITY)
    r = _get("/v2/account"); r.raise_for_status()
    return float(r.json()["equity"])

def get_open_symbols():
    r = _get("/v2/positions")
    if not r.ok: return set()
    return {p.get("symbol","").upper() for p in r.json()}

def submit_bracket(symbol, qty, side, entry, stop, target, client_id):
    order = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side.lower(),             # "buy" or "sell"
        "type": "limit" if USE_LIMIT else "market",
        "time_in_force": TIME_IN_FORCE,
        "client_order_id": client_id,
        "order_class": "bracket",
        "stop_loss":  {"stop_price": round(float(stop), 4)},
        "take_profit":{"limit_price": round(float(target), 4)}
    }
    if USE_LIMIT:
        order["limit_price"] = round(float(entry), 4)
    r = _post("/v2/orders", order)
    if not r.ok:
        raise RuntimeError(f"{r.status_code} {r.text}")
    return r.json()

def main():
    assert ALPACA_KEY and ALPACA_SECRET, "Missing Alpaca API credentials"
    if not os.path.exists(CSV_PATH):
        print(f"⚠ No {CSV_PATH}; nothing to trade."); return

    with open(CSV_PATH, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("⚠ signals.csv empty; nothing to trade."); return

    equity = get_equity()
    open_syms = get_open_symbols()
    print(f"Equity: ${equity:,.2f} | Open names: {len(open_syms)}")

    placed = []
    for r in rows:
        sym  = (r.get("symbol") or "").upper().strip()
        side = (r.get("side") or "BUY").upper().strip()
        try:
            entry  = float(r.get("entry", 0))
            stop   = float(r.get("stop", 0))
            target = float(r.get("target", 0))
        except Exception:
            print(f"⚠ Bad numeric values for {sym}, skip."); continue

        if not sym or entry <= 0 or stop <= 0 or target <= 0:
            print(f"⚠ Incomplete signal for {sym}, skip."); continue
        if side not in ("BUY","SELL"):
            print(f"⚠ Invalid side {side} for {sym}, skip."); continue
        if side == "SELL" and not ALLOW_SHORTS:
            print(f"ℹ Skip short {sym} (ALLOW_SHORTS=false)."); continue
        if sym in open_syms:
            print(f"ℹ Already open {sym}, skip."); continue
        if len(open_syms) >= MAX_OPEN_NAMES:
            print("ℹ Max open names reached; stop queueing."); break

        per_share_risk = entry - stop if side=="BUY" else stop - entry
        if per_share_risk <= 0:
            print(f"⚠ Non-positive risk for {sym}, skip."); continue

        risk_dollars = RISK_PCT * equity
        qty = math.floor(max(0, risk_dollars / per_share_risk))
        max_qty_by_value = math.floor((MAX_POS_PCT * equity) / entry)
        qty = max(0, min(qty, max_qty_by_value))
        if qty == 0:
            print(f"⚠ {sym} sized to 0 by caps; skip."); continue

        client_id = f"sig-{sym}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        try:
            resp = submit_bracket(sym, qty, "buy" if side=="BUY" else "sell", entry, stop, target, client_id)
            placed.append((sym, qty, side, resp.get("id")))
            open_syms.add(sym)
            print(f"✅ Placed {side} {qty} {sym} @~{entry} | SL {stop} / TP {target}")
        except Exception as e:
            print(f"❌ Order failed {sym}: {e}")

    if not placed:
        print("ℹ No orders placed.")
    else:
        print("=== Placed orders ===")
        for sym, qty, side, oid in placed:
            print(f"{side} {qty} {sym}  (order_id={oid})")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("❌ Fatal:", e); sys.exit(1)
