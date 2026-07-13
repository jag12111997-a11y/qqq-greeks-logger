# ============================================================
# QQQ 0DTE CALLS Greeks Logger — Alpaca version
# ============================================================
# Logic:
#   - CALLS ONLY (puts skipped)
#   - Strike window: spot +/- $15 ($30 wide total)
#   - Today's expiration (0DTE)
#   - Appends one timestamped snapshot per run to a CSV
#
# CREDENTIALS: read from environment variables ALPACA_API_KEY and
# ALPACA_API_SECRET — these come from GitHub Secrets, never typed
# into this file.
#
# GREEKS ARE COMPUTED LOCALLY:
#   Alpaca's free feed returns bid/ask/last/volume but NOT the
#   Greeks or implied volatility (those are a paid feature). So
#   this logger calculates them itself using the Black-Scholes
#   model from the data it already has (spot, strike, option
#   price, and time left until today's 4:00 PM ET expiration).
#
#   Column conventions written to the CSV:
#     iv     -> implied volatility as a decimal (0.18 = 18%)
#     delta  -> per $1 move in QQQ (0..1 for calls)
#     gamma  -> change in delta per $1 move in QQQ
#     theta  -> dollars lost over 1 calendar day (broker convention); for a
#               0DTE option this is the time value you lose holding to expiry
#     vega   -> dollars gained per 1 percentage-point rise in IV
#
#   IV is solved from the option's mid price (or last if no quote).
#   Deep in/out-of-the-money 0DTE contracts whose price is at/below
#   intrinsic value have no solvable IV, so their Greeks are left
#   blank rather than faked. Near-the-money strikes fill in.
#
#   Assumptions: risk-free rate r = 4.3%, dividend yield q = 0.
# ============================================================

import csv
import os
import math
import datetime
from zoneinfo import ZoneInfo

import requests

SYMBOL = "QQQ"
NEAR_MONEY_WINDOW = 15       # +/- $15 from spot
OUTPUT_FILE = "qqq_greeks_log.csv"
DATA_BASE = "https://data.alpaca.markets"

RISK_FREE_RATE = 0.043       # annualized, decimal
DIVIDEND_YIELD = 0.0         # QQQ yield is tiny; negligible for 0DTE

API_KEY = os.environ.get("ALPACA_API_KEY")
API_SECRET = os.environ.get("ALPACA_API_SECRET")
HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
}


# ---------- Black-Scholes helpers (standard normal via math.erf) ----------

def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_call_price(S, K, T, r, q, sigma):
    if sigma <= 0 or T <= 0:
        return max(S * math.exp(-q * T) - K * math.exp(-r * T), 0.0)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * math.exp(-q * T) * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def _implied_vol_call(price, S, K, T, r, q):
    # No solvable IV if the price is at/below intrinsic or above the underlying.
    intrinsic = max(S * math.exp(-q * T) - K * math.exp(-r * T), 0.0)
    if price is None or T <= 0 or price <= intrinsic + 1e-6 or price >= S:
        return None
    lo, hi = 1e-4, 5.0
    if _bs_call_price(S, K, T, r, q, hi) < price:
        return None  # price too rich for our vol ceiling
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if _bs_call_price(S, K, T, r, q, mid) < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-6:
            break
    return 0.5 * (lo + hi)


def _call_greeks(S, K, T, r, q, sigma):
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    delta = math.exp(-q * T) * _norm_cdf(d1)
    gamma = math.exp(-q * T) * _norm_pdf(d1) / (S * sigma * sqrtT)
    vega = S * math.exp(-q * T) * _norm_pdf(d1) * sqrtT / 100.0          # per 1% IV
    # Theta: broker "1-calendar-day" convention = the price change from today to
    # tomorrow. For a 0DTE option tomorrow is past expiry, so value drops to
    # intrinsic and theta becomes the remaining time value you lose by holding
    # to expiry -- matching what Webull / thinkorswim / etc. display. (The raw
    # Black-Scholes instantaneous theta blows up near expiry and overstates the
    # per-day loss, which is why brokers use this convention for 0DTE.)
    T_next = max(T - 1.0 / 365.0, 0.0)
    theta = _bs_call_price(S, K, T_next, r, q, sigma) - _bs_call_price(S, K, T, r, q, sigma)
    return delta, gamma, theta, vega


# ---------- Alpaca data ----------

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


def time_to_expiry_years(now_utc, today):
    # 0DTE options expire at 4:00 PM Eastern on the expiration date.
    et = ZoneInfo("America/New_York")
    y, m, d = (int(x) for x in today.split("-"))
    expiry_et = datetime.datetime(y, m, d, 16, 0, 0, tzinfo=et)
    seconds_left = (expiry_et - now_utc).total_seconds()
    seconds_left = max(seconds_left, 60.0)          # floor to avoid div-by-zero
    return seconds_left / (365.0 * 24.0 * 3600.0)


def _round_or_blank(value, digits):
    return round(value, digits) if value is not None else ""


def main():
    if not API_KEY or not API_SECRET:
        print("STOP: ALPACA_API_KEY / ALPACA_API_SECRET not set.")
        return

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    today = datetime.date.today().isoformat()
    spot = get_spot_price()
    snapshots = get_option_chain(spot, today)

    if not snapshots:
        print("No call contracts found for today's expiration near spot. "
              "Market may be closed, or QQQ has no 0DTE expiration today.")
        return

    T = time_to_expiry_years(now_utc, today)
    r = RISK_FREE_RATE
    q = DIVIDEND_YIELD

    rows = []
    for symbol, data in snapshots.items():
        exp_date, opt_type, strike = parse_symbol(symbol)
        quote = data.get("latestQuote") or {}
        trade = data.get("latestTrade") or {}
        daily_bar = data.get("dailyBar") or {}

        bid = quote.get("bp")
        ask = quote.get("ap")
        last = trade.get("p")

        # Prefer the mid price; fall back to last trade.
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            price = (bid + ask) / 2.0
        else:
            price = last

        iv = delta = gamma = theta = vega = None
        try:
            sigma = _implied_vol_call(price, spot, strike, T, r, q)
            if sigma is not None:
                iv = sigma
                delta, gamma, theta, vega = _call_greeks(spot, strike, T, r, q, sigma)
        except (ValueError, ZeroDivisionError):
            pass  # leave Greeks blank for this contract

        rows.append({
            "run_time": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
            "spot": round(spot, 2),
            "expiration": exp_date,
            "strike": strike,
            "bid": bid,
            "ask": ask,
            "last": last,
            "volume": daily_bar.get("v"),
            "iv": _round_or_blank(iv, 4),
            "delta": _round_or_blank(delta, 4),
            "gamma": _round_or_blank(gamma, 5),
            "theta": _round_or_blank(theta, 4),
            "vega": _round_or_blank(vega, 4),
        })

    rows.sort(key=lambda x: x["strike"])

    file_is_new = not os.path.exists(OUTPUT_FILE)
    with open(OUTPUT_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if file_is_new:
            writer.writeheader()
        writer.writerows(rows)

    filled = sum(1 for x in rows if x["delta"] != "")
    print(f"Logged {len(rows)} call rows at {now_utc.strftime('%H:%M:%S')} UTC "
          f"(spot {spot:.2f}, exp {today}, Greeks on {filled}/{len(rows)}) "
          f"-> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
