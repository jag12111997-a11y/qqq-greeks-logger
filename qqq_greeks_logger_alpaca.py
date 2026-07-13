# ============================================================
# QQQ 0DTE CALLS Greeks Logger — Alpaca version
# ============================================================
# Logic (unchanged from the Tradier version):
#   - CALLS ONLY (puts skipped)
#   - Strike window: spot +/- $15 ($30 wide total)
#   - Today's expiration (0DTE)
#   - Appends one timestamped snapshot per run to a CSV
#
# CREDENTIALS: read from environment variables ALPACA_API_KEY and
# ALPACA_API_SECRET — these come from GitHub Secrets, never typed
# into this file.
#
# ONE DIFFERENCE FROM TRADIER: Alpaca's free "indicative" options
# feed does not return open interest in this same call (open
# interest would need one extra API call per contract). So this
# version logs bid/ask/last/IV/delta/gamma/theta/vega and daily
# volume, but not open interest. Flagging that so it's not a
# surprise later.
# ============================================================

import csv
import os
import datetime
import requests

SYMBOL = "QQQ"
NEAR_MONEY_WINDOW = 15       # +/- $15 from spot
OUTPUT_FILE = "qqq_greeks_log.csv"
DATA_BASE = "https://data.alpaca.markets"

API_KEY = os.environ.get("ALPACA_API_KEY")
API_SECRET = os.environ.get("ALPACA_API_SECRET")
HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
}


def get_spot_price():
    r = requests.get(f"{DATA_BASE}/v2/stocks/{SYMBOL}/trades/latest", headers=HEADERS)
    r.raise_for_status()
    return float(r.json()["trade"]["p"])


def get_option_chain(spot, today):
    params = {
        "feed": "indicative",
        "type": "call",
        "expiration_date": today,
        "strike_price_gte": spot - NEAR_MONEY_WINDOW,
        "strike_price_lte": spot + NEAR_MONEY_WINDOW,
        "limit": 1000,
    }
    r = requests.get(f"{DATA_BASE}/v1beta1/options/snapshots/{SYMBOL}",
                      headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json().get("snapshots", {})


def parse_symbol(symbol):
    # OCC-style symbol: QQQ + YYMMDD + C/P + 8-digit strike (thousandths)
    rest = symbol[len(SYMBOL):]
    date_str = rest[:6]
    opt_type = rest[6]
    strike = int(rest[7:15]) / 1000
    exp_date = f"20{date_str[0:2]}-{date_str[2:4]}-{date_str[4:6]}"
    return exp_date, opt_type, strike


def main():
    if not API_KEY or not API_SECRET:
        print("STOP: ALPACA_API_KEY / ALPACA_API_SECRET not set.")
        return

    now = datetime.datetime.now()
    today = datetime.date.today().isoformat()
    spot = get_spot_price()
    snapshots = get_option_chain(spot, today)

    if not snapshots:
        print("No call contracts found for today's expiration near spot. "
              "Market may be closed, or QQQ has no 0DTE expiration today.")
        return

    rows = []
    for symbol, data in snapshots.items():
        exp_date, opt_type, strike = parse_symbol(symbol)
        greeks = data.get("greeks") or {}
        quote = data.get("latestQuote") or {}
        trade = data.get("latestTrade") or {}
        daily_bar = data.get("dailyBar") or {}
        rows.append({
            "run_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "spot": round(spot, 2),
            "expiration": exp_date,
            "strike": strike,
            "bid": quote.get("bp"),
            "ask": quote.get("ap"),
            "last": trade.get("p"),
            "volume": daily_bar.get("v"),
            "iv": data.get("impliedVolatility"),
            "delta": greeks.get("delta"),
            "gamma": greeks.get("gamma"),
            "theta": greeks.get("theta"),
            "vega": greeks.get("vega"),
        })

    rows.sort(key=lambda x: x["strike"])

    file_is_new = not os.path.exists(OUTPUT_FILE)
    with open(OUTPUT_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if file_is_new:
            writer.writeheader()
        writer.writerows(rows)

    print(f"Logged {len(rows)} call rows at {now.strftime('%H:%M:%S')} "
          f"(spot {spot:.2f}, exp {today}) -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
