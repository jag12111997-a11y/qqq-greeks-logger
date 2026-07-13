# ============================================================
# QQQ 0DTE CALLS Greeks Logger — Alpaca version
# ============================================================
# WHAT IT DOES
#   - CALLS ONLY (puts skipped), today's expiration (0DTE)
#   - Strike window: spot +/- WINDOW dollars (set per session)
#   - Logs bid/ask/last, volume, open interest, and computed
#     Greeks (iv/delta/gamma/theta/vega) for each strike
#   - Appends one timestamped snapshot per capture to a CSV
#
# SESSION / LOOP MODE (set via environment variables):
#   WINDOW            dollars each side of spot     (default 15)
#   INTERVAL_SECONDS  seconds between snapshots      (default 0)
#   DURATION_SECONDS  total length of the session    (default 0)
#   If INTERVAL/DURATION are 0 it takes a single snapshot.
#   Otherwise it loops: snapshot, wait, repeat, until DURATION.
#   (GitHub can't schedule faster than every 5 min, so the burst
#    sessions run as ONE job that loops internally instead.)
#
# CREDENTIALS: env vars ALPACA_API_KEY / ALPACA_API_SECRET,
#   supplied by GitHub Secrets (never written in this file).
#
# GREEKS are computed locally with Black-Scholes (Alpaca's free
#   feed omits them). Theta uses the broker "1-day" convention
#   (time value you lose holding to expiry), matching Webull.
#     iv    -> implied vol, decimal (0.18 = 18%)
#     delta -> per $1 move in QQQ
#     gamma -> change in delta per $1 move
#     theta -> $ lost over 1 calendar day (0DTE: your time value)
#     vega  -> $ gained per 1 percentage-point rise in IV
#
# OPEN INTEREST comes from Alpaca's contracts endpoint. It is an
#   official OCC end-of-day figure (updated once a day, ~1-day
#   lag), so it does not change during the day.
# ============================================================

import csv
import os
import math
import time
import datetime
from zoneinfo import ZoneInfo

import requests

SYMBOL = "QQQ"
OUTPUT_FILE = "qqq_greeks_log.csv"
DATA_BASE = "https://data.alpaca.markets"
TRADING_BASE = os.environ.get("ALPACA_TRADING_BASE", "https://paper-api.alpaca.markets")

RISK_FREE_RATE = 0.043
DIVIDEND_YIELD = 0.0

WINDOW = float(os.environ.get("WINDOW", "15"))
INTERVAL_SECONDS = int(os.environ.get("INTERVAL_SECONDS", "0"))
DURATION_SECONDS = int(os.environ.get("DURATION_SECONDS", "0"))

API_KEY = os.environ.get("ALPACA_API_KEY")
API_SECRET = os.environ.get("ALPACA_API_SECRET")
HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
}

FIELDNAMES = ["run_time", "spot", "expiration", "strike", "bid", "ask", "last",
              "volume", "open_interest", "iv", "delta", "gamma", "theta", "vega"]


# ---------- Black-Scholes helpers ----------

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
    intrinsic = max(S * math.exp(-q * T) - K * math.exp(-r * T), 0.0)
    if price is None or T <= 0 or price <= intrinsic + 1e-6 or price >= S:
        return None
    lo, hi = 1e-4, 5.0
    if _bs_call_price(S, K, T, r, q, hi) < price:
        return None
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
    delta = math.exp(-q * T) * _norm_cdf(d1)
    gamma = math.exp(-q * T) * _norm_pdf(d1) / (S * sigma * sqrtT)
    vega = S * math.exp(-q * T) * _norm_pdf(d1) * sqrtT / 100.0
    # Broker "1-calendar-day" theta (see header note).
    T_next = max(T - 1.0 / 365.0, 0.0)
    theta = _bs_call_price(S, K, T_next, r, q, sigma) - _bs_call_price(S, K, T, r, q, sigma)
    return delta, gamma, theta, vega


# ---------- Alpaca data ----------

def get_spot_price():
    r = requests.get(f"{DATA_BASE}/v2/stocks/{SYMBOL}/trades/latest", headers=HEADERS)
    r.raise_for_status()
    return float(r.json()["trade"]["p"])


def get_option_chain(low, high, today):
    params = {
        "feed": "indicative",
        "type": "call",
        "expiration_date": today,
        "strike_price_gte": low,
        "strike_price_lte": high,
        "limit": 1000,
    }
    r = requests.get(f"{DATA_BASE}/v1beta1/options/snapshots/{SYMBOL}",
                     headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json().get("snapshots", {})


def get_open_interest(low, high, today):
    # Open interest lives on the trading API's contracts endpoint (OCC EOD).
    oi = {}
    try:
        params = {
            "underlying_symbols": SYMBOL,
            "type": "call",
            "expiration_date": today,
            "strike_price_gte": low,
            "strike_price_lte": high,
            "limit": 1000,
        }
        r = requests.get(f"{TRADING_BASE}/v2/options/contracts",
                         headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        for c in r.json().get("option_contracts", []):
            val = c.get("open_interest")
            oi[c.get("symbol")] = int(val) if val not in (None, "") else ""
    except Exception:
        pass  # OI is best-effort; never let it break a snapshot
    return oi


def parse_symbol(symbol):
    rest = symbol[len(SYMBOL):]
    date_str = rest[:6]
    opt_type = rest[6]
    strike = int(rest[7:15]) / 1000
    exp_date = f"20{date_str[0:2]}-{date_str[2:4]}-{date_str[4:6]}"
    return exp_date, opt_type, strike


def time_to_expiry_years(now_utc, today):
    et = ZoneInfo("America/New_York")
    y, m, d = (int(x) for x in today.split("-"))
    expiry_et = datetime.datetime(y, m, d, 16, 0, 0, tzinfo=et)
    seconds_left = max((expiry_et - now_utc).total_seconds(), 60.0)
    return seconds_left / (365.0 * 24.0 * 3600.0)


def _round_or_blank(value, digits):
    return round(value, digits) if value is not None else ""


# ---------- Snapshot + CSV ----------

def build_snapshot_rows():
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    today = datetime.date.today().isoformat()
    spot = get_spot_price()
    low, high = spot - WINDOW, spot + WINDOW
    snapshots = get_option_chain(low, high, today)
    if not snapshots:
        print(f"{now_utc.strftime('%H:%M:%S')} UTC: no contracts near spot "
              f"{spot:.2f} (market closed or no 0DTE). Skipping.")
        return None

    oi_map = get_open_interest(low, high, today)
    T = time_to_expiry_years(now_utc, today)
    r, q = RISK_FREE_RATE, DIVIDEND_YIELD

    rows = []
    for symbol, data in snapshots.items():
        exp_date, opt_type, strike = parse_symbol(symbol)
        quote = data.get("latestQuote") or {}
        trade = data.get("latestTrade") or {}
        daily_bar = data.get("dailyBar") or {}
        bid = quote.get("bp")
        ask = quote.get("ap")
        last = trade.get("p")
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
            pass

        rows.append({
            "run_time": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
            "spot": round(spot, 2),
            "expiration": exp_date,
            "strike": strike,
            "bid": bid,
            "ask": ask,
            "last": last,
            "volume": daily_bar.get("v"),
            "open_interest": oi_map.get(symbol, ""),
            "iv": _round_or_blank(iv, 4),
            "delta": _round_or_blank(delta, 4),
            "gamma": _round_or_blank(gamma, 5),
            "theta": _round_or_blank(theta, 4),
            "vega": _round_or_blank(vega, 4),
        })

    rows.sort(key=lambda x: x["strike"])
    filled = sum(1 for x in rows if x["delta"] != "")
    print(f"{now_utc.strftime('%H:%M:%S')} UTC: logged {len(rows)} rows "
          f"(spot {spot:.2f}, window +/-{WINDOW:g}, Greeks on {filled}/{len(rows)})")
    return rows


def write_rows(new_rows):
    # Append if the file's header already matches; otherwise create it, or
    # do a one-time migration to the new schema (preserving old rows).
    if not os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDNAMES)
            w.writeheader()
            w.writerows(new_rows)
        return

    with open(OUTPUT_FILE, newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames
        old_rows = list(reader) if header != FIELDNAMES else None

    if header == FIELDNAMES:
        with open(OUTPUT_FILE, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writerows(new_rows)
    else:
        with open(OUTPUT_FILE, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDNAMES)
            w.writeheader()
            for row in old_rows:
                w.writerow({k: row.get(k, "") for k in FIELDNAMES})
            w.writerows(new_rows)


def main():
    if not API_KEY or not API_SECRET:
        print("STOP: ALPACA_API_KEY / ALPACA_API_SECRET not set.")
        return

    # Single snapshot mode.
    if INTERVAL_SECONDS <= 0 or DURATION_SECONDS <= 0:
        rows = build_snapshot_rows()
        if rows:
            write_rows(rows)
        return

    # Session / loop mode: snapshot every INTERVAL for DURATION.
    print(f"Session start: every {INTERVAL_SECONDS}s for {DURATION_SECONDS}s "
          f"(window +/-{WINDOW:g}).")
    start = time.monotonic()
    count = 0
    while time.monotonic() - start < DURATION_SECONDS:
        try:
            rows = build_snapshot_rows()
            if rows:
                write_rows(rows)
                count += 1
        except Exception as e:
            print(f"Snapshot error (continuing): {e}")
        if time.monotonic() - start >= DURATION_SECONDS:
            break
        time.sleep(INTERVAL_SECONDS)
    print(f"Session done: {count} snapshots written.")


if __name__ == "__main__":
    main()
