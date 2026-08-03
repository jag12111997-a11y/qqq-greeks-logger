# ============================================================
# QQQ 0DTE CALLS Greeks Logger — Alpaca version
# ============================================================
# WHAT IT DOES
#   - CALLS AND PUTS (each written to its OWN file, never mixed),
#     today's expiration (0DTE)
#   - Strike window: spot +/- WINDOW dollars (set per session)
#   - Logs bid/ask/last, volume, open interest, and computed
#     Greeks (iv/delta/gamma/theta/vega) for each strike
#   - Appends one timestamped snapshot per capture to each CSV
#
# OUTPUT LAYOUT (organized by day and session; calls and puts SEPARATE):
#   data/YYYY-MM-DD/am/qqq_greeks_calls_YYYY-MM-DD_am.csv   (morning calls)
#   data/YYYY-MM-DD/am/qqq_greeks_puts_YYYY-MM-DD_am.csv    (morning puts)
#   data/YYYY-MM-DD/pm/qqq_greeks_calls_YYYY-MM-DD_pm.csv   (midday calls)
#   data/YYYY-MM-DD/pm/qqq_greeks_puts_YYYY-MM-DD_pm.csv    (midday puts)
#   Each session writes its own dated files into its am/ or pm/
#   folder, so history stays sorted by date instead of one big file.
#
# SESSION / LOOP MODE (set via environment variables):
#   SESSION           "am" or "pm" (which folder to write into)
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
import json
import os
import math
import time
import datetime
from zoneinfo import ZoneInfo

import requests

SYMBOL = "QQQ"
BASE_DIR = "data"                       # top-level folder for organized history
SESSION = os.environ.get("SESSION", "").lower()   # "am" / "pm" (auto if blank)
DATA_BASE = "https://data.alpaca.markets"
TRADING_BASE = os.environ.get("ALPACA_TRADING_BASE", "https://paper-api.alpaca.markets")

RISK_FREE_RATE = 0.043
DIVIDEND_YIELD = 0.0

# LIVE GEX — dealer convention: long call gamma, short put gamma (SqueezeMetrics).
# The logger now computes a full calls+puts GEX each cycle and writes it here,
# so the dashboard has a 5-min live source independent of the twice-daily API pull.
CONTRACT_MULTIPLIER = 100
GEX_DEALER_SIGN = 1
GEX_LIVE_PATH = os.path.join("market-dash", "gex_live.json")

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


def _bs_put_price(S, K, T, r, q, sigma):
    if sigma <= 0 or T <= 0:
        return max(K * math.exp(-r * T) - S * math.exp(-q * T), 0.0)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * math.exp(-q * T) * _norm_cdf(-d1)


def _implied_vol_put(price, S, K, T, r, q):
    upper = K * math.exp(-r * T)                       # a European put can't exceed K discounted
    intrinsic = max(upper - S * math.exp(-q * T), 0.0)
    if price is None or T <= 0 or price <= intrinsic + 1e-6 or price >= upper:
        return None
    lo, hi = 1e-4, 5.0
    if _bs_put_price(S, K, T, r, q, hi) < price:
        return None
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if _bs_put_price(S, K, T, r, q, mid) < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-6:
            break
    return 0.5 * (lo + hi)


def _put_greeks(S, K, T, r, q, sigma):
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    # Put delta = call delta - e^{-qT}; gamma and vega are identical to the call.
    delta = math.exp(-q * T) * (_norm_cdf(d1) - 1.0)
    gamma = math.exp(-q * T) * _norm_pdf(d1) / (S * sigma * sqrtT)
    vega = S * math.exp(-q * T) * _norm_pdf(d1) * sqrtT / 100.0
    # Broker "1-calendar-day" theta, same convention as the call.
    T_next = max(T - 1.0 / 365.0, 0.0)
    theta = _bs_put_price(S, K, T_next, r, q, sigma) - _bs_put_price(S, K, T, r, q, sigma)
    return delta, gamma, theta, vega


# ---------- Alpaca data ----------

def get_spot_price():
    r = requests.get(f"{DATA_BASE}/v2/stocks/{SYMBOL}/trades/latest", headers=HEADERS)
    r.raise_for_status()
    return float(r.json()["trade"]["p"])


def get_option_chain(low, high, today, opt_type="call"):
    params = {
        "feed": "indicative",
        "type": opt_type,
        "expiration_date": today,
        "strike_price_gte": low,
        "strike_price_lte": high,
        "limit": 1000,
    }
    r = requests.get(f"{DATA_BASE}/v1beta1/options/snapshots/{SYMBOL}",
                     headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json().get("snapshots", {})


def get_open_interest(low, high, today, opt_type="call"):
    # Open interest lives on the trading API's contracts endpoint (OCC EOD).
    oi = {}
    try:
        params = {
            "underlying_symbols": SYMBOL,
            "type": opt_type,
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

def build_snapshot_rows(opt_type="call", spot=None):
    """Build rows for ONE option type ("call" or "put").

    Calls and puts are fetched and written separately (own CSV each), never
    mixed. Pass a shared `spot` so both types are priced off the same underlying.
    """
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    today = datetime.date.today().isoformat()
    if spot is None:
        spot = get_spot_price()
    low, high = spot - WINDOW, spot + WINDOW
    snapshots = get_option_chain(low, high, today, opt_type)
    if not snapshots:
        print(f"{now_utc.strftime('%H:%M:%S')} UTC: no {opt_type} contracts near spot "
              f"{spot:.2f} (market closed or no 0DTE). Skipping.")
        return None

    oi_map = get_open_interest(low, high, today, opt_type)
    T = time_to_expiry_years(now_utc, today)
    r, q = RISK_FREE_RATE, DIVIDEND_YIELD

    rows = []
    for symbol, data in snapshots.items():
        exp_date, _sym_cp, strike = parse_symbol(symbol)
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
            if opt_type == "call":
                sigma = _implied_vol_call(price, spot, strike, T, r, q)
            else:
                sigma = _implied_vol_put(price, spot, strike, T, r, q)
            if sigma is not None:
                iv = sigma
                if opt_type == "call":
                    delta, gamma, theta, vega = _call_greeks(spot, strike, T, r, q, sigma)
                else:
                    delta, gamma, theta, vega = _put_greeks(spot, strike, T, r, q, sigma)
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
    print(f"{now_utc.strftime('%H:%M:%S')} UTC: logged {len(rows)} {opt_type} rows "
          f"(spot {spot:.2f}, window +/-{WINDOW:g}, Greeks on {filled}/{len(rows)})")
    return rows


def output_path(opt_type="call"):
    # data/<trading date>/<am|pm>/qqq_greeks_<calls|puts>_<date>_<session>.csv
    # Calls and puts ALWAYS go to separate files — never the same CSV.
    et = ZoneInfo("America/New_York")
    now_et = datetime.datetime.now(et)
    date = now_et.date().isoformat()
    session = SESSION if SESSION in ("am", "pm") else ("am" if now_et.hour < 12 else "pm")
    kind = "calls" if opt_type == "call" else "puts"
    return os.path.join(BASE_DIR, date, session, f"qqq_greeks_{kind}_{date}_{session}.csv")


def write_rows(new_rows, opt_type="call"):
    # Each day+session+type has its own file; create with a header, else append.
    path = output_path(opt_type)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    is_new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if is_new:
            w.writeheader()
        w.writerows(new_rows)


def compute_gex_live(call_rows, put_rows, spot):
    """Full calls+puts GEX from the just-captured rows. Same shape/convention as
    market_dash_fetch.compute_gex, so the dashboard renders it identically."""
    def num(v):
        try:
            if v in (None, ""):
                return None
            return float(v)
        except (TypeError, ValueError):
            return None

    unit = CONTRACT_MULTIPLIER * (spot ** 2) * 0.01
    by = {}

    def add(rows, is_call):
        for r in rows:
            k = num(r.get("strike"))
            g = num(r.get("gamma"))
            oi = num(r.get("open_interest"))
            if k is None or g is None or not oi:
                continue
            oi = int(oi)
            d = by.setdefault(k, {"strike": k, "call_gex": 0.0, "put_gex": 0.0,
                                  "call_oi": 0, "put_oi": 0, "dex": 0.0, "vex": 0.0,
                                  "tex": 0.0, "call_g": 0.0, "put_g": 0.0})
            dollars = g * oi * unit
            if is_call:
                d["call_gex"] += dollars; d["call_oi"] += oi; d["call_g"] += g * oi
            else:
                d["put_gex"] -= dollars; d["put_oi"] += oi; d["put_g"] += g * oi
            de, ve, th = num(r.get("delta")), num(r.get("vega")), num(r.get("theta"))
            if de is not None:
                d["dex"] += de * oi * CONTRACT_MULTIPLIER * spot
            if ve is not None:
                d["vex"] += ve * oi * CONTRACT_MULTIPLIER
            if th is not None:
                d["tex"] += th * oi * CONTRACT_MULTIPLIER

    add(call_rows, True)
    add(put_rows, False)
    if not by:
        return {"error": "no usable rows for live GEX"}

    strikes = sorted(by.values(), key=lambda x: x["strike"])
    for s in strikes:
        s["net_gex"] = (s["call_gex"] + s["put_gex"]) * GEX_DEALER_SIGN
        for key in ("call_gex", "put_gex", "net_gex", "dex", "vex", "tex"):
            s[key] = round(s[key], 2)

    net = round(sum(s["net_gex"] for s in strikes), 2)
    net_dex = round(sum(s["dex"] for s in strikes), 2)
    net_vex = round(sum(s["vex"] for s in strikes), 2)
    net_tex = round(sum(s["tex"] for s in strikes), 2)
    cg = sum(s["call_g"] for s in strikes)
    pg = sum(s["put_g"] for s in strikes)
    gamma_ratio = round(cg / (cg + pg), 4) if (cg + pg) else None

    flip, run, method, cum = None, 0.0, None, []
    for i, s in enumerate(strikes):
        prev = run
        run += s["net_gex"]
        cum.append((s["strike"], run))
        if i and ((prev < 0 <= run) or (prev > 0 >= run)):
            a, b = strikes[i - 1]["strike"], s["strike"]
            frac = abs(prev) / (abs(prev) + abs(run)) if (prev or run) else 0.5
            flip = round(a + (b - a) * frac, 2)
            method = "zero crossing"
    if flip is None and cum:
        k, _ = min(cum, key=lambda t: abs(t[1]))
        flip = round(k, 2)
        method = "nearest-to-zero (no crossing in window)"

    call_wall = max(strikes, key=lambda s: s["call_gex"])["strike"]
    put_wall = min(strikes, key=lambda s: s["put_gex"])["strike"]
    top_oi = max(strikes, key=lambda s: s["call_oi"] + s["put_oi"])["strike"]

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    return {
        "symbol": SYMBOL, "spot": round(spot, 2),
        "net_gex": net, "net_dex": net_dex, "net_vex": net_vex, "net_tex": net_tex,
        "gamma_ratio": gamma_ratio,
        "gamma_flip": flip, "gamma_flip_method": method,
        "regime": ("POSITIVE GAMMA"
                   if (spot > flip if method == "zero crossing" else net > 0)
                   else "NEGATIVE GAMMA"),
        "distance_to_flip_pct": round((spot - flip) / spot * 100, 2) if flip else None,
        "levels": {"call_wall": call_wall, "put_wall": put_wall, "highest_oi_strike": top_oi},
        "strikes": [{"strike": s["strike"], "call_gex": s["call_gex"], "put_gex": s["put_gex"],
                     "net_gex": s["net_gex"], "call_oi": s["call_oi"], "put_oi": s["put_oi"],
                     "dex": s["dex"], "vex": s["vex"], "tex": s["tex"]} for s in strikes],
        "contracts_used": len(call_rows) + len(put_rows),
        "dealer_convention": "dealers long call gamma, short put gamma",
        "source": "5-min greeks logger (calls + puts, 0DTE)",
        "generated_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
    }


def write_gex_live(gx):
    os.makedirs(os.path.dirname(GEX_LIVE_PATH), exist_ok=True)
    with open(GEX_LIVE_PATH, "w") as f:
        json.dump(gx, f, indent=2)


def snapshot_and_write(spot):
    """Capture calls + puts (separate CSVs) and write the live GEX json."""
    got = {}
    for t in ("call", "put"):
        rows = build_snapshot_rows(t, spot=spot)
        if rows:
            write_rows(rows, t)
            got[t] = rows
    try:
        if got.get("call") or got.get("put"):
            write_gex_live(compute_gex_live(got.get("call", []), got.get("put", []), spot))
    except Exception as e:
        print(f"live GEX skipped (continuing): {e}")


def main():
    if not API_KEY or not API_SECRET:
        print("STOP: ALPACA_API_KEY / ALPACA_API_SECRET not set.")
        return

    # Single snapshot mode. Calls AND puts (separate files) + live GEX.
    if INTERVAL_SECONDS <= 0 or DURATION_SECONDS <= 0:
        snapshot_and_write(get_spot_price())
        return

    # Session / loop mode: snapshot every INTERVAL for DURATION.
    print(f"Session start: every {INTERVAL_SECONDS}s for {DURATION_SECONDS}s "
          f"(window +/-{WINDOW:g}, calls + puts to separate files).")
    start = time.monotonic()
    count = 0
    while time.monotonic() - start < DURATION_SECONDS:
        try:
            snapshot_and_write(get_spot_price())    # calls + puts (separate files) + live GEX
            count += 1
        except Exception as e:
            print(f"Snapshot error (continuing): {e}")
        if time.monotonic() - start >= DURATION_SECONDS:
            break
        time.sleep(INTERVAL_SECONDS)
    print(f"Session done: {count} snapshots written (calls + puts, separate files).")


if __name__ == "__main__":
    main()
