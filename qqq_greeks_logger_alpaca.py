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
import hashlib
import json
import os
import math
import time
import datetime
import subprocess
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
GEX_INTRADAY_PATH = os.path.join("market-dash", "gex_intraday.json")
# Per-minute strike profile (OI-weighted AND volume-weighted gamma) for the
# GEXBot-style page: lets it show how positioning moves, not just where it is.
GEX_FRAMES_PATH = os.path.join("market-dash", "gex_frames.json")
GEX_FRAMES_CAP = 480
AUCTION_LIVE_PATH = os.path.join("market-dash", "auction_live.json")

# Intraday live-push: on GitHub Actions, push the live JSONs every few minutes
# DURING the session so the dashboard updates in eve's 7:17-8:30 window instead
# of only when the 2-hour run ends. ~5 min because GitHub Pages rebuilds ~10x/hr.
CI_PUSH = os.environ.get("GITHUB_ACTIONS") == "true"

# Live relay (Cloudflare Worker, cloudflare/relay): every snapshot's live files
# go there within seconds, so the chart is ~1 minute behind instead of waiting
# for the 5-minute git push + Pages rebuild. Git stays the backup and archive.
RELAY_URL = os.environ.get("RELAY_URL", "https://gex-relay.jag12111997.workers.dev").rstrip("/")
RELAY_KEY = os.environ.get("RELAY_KEY", "")
RELAY_FILES = ("gex_live.json", "gex_intraday.json", "gex_frames.json",
               "auction_live.json", "options_latest.json")
_RELAY_SENT = {}
LIVE_PUSH_SECONDS = int(os.environ.get("LIVE_PUSH_SECONDS", "300"))
CANDLE_REFRESH_SECONDS = int(os.environ.get("CANDLE_REFRESH_SECONDS", "300"))

WINDOW = float(os.environ.get("WINDOW", "15"))
INTERVAL_SECONDS = int(os.environ.get("INTERVAL_SECONDS", "0"))
DURATION_SECONDS = int(os.environ.get("DURATION_SECONDS", "0"))

# A stalled request must never freeze the minute loop (the job would sit until
# its timeout with no snapshots, publishes or relay uploads).
HTTP_TIMEOUT = 15

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


def _vanna_charm(S, K, T, r, q, sigma):
    """Second-order hedge dials (3v3 notes F1), closed-form from d1/d2 — no extra
    chain data needed. Verified against finite differences; identical for calls
    and puts (put delta = call delta - e^{-qT}).
        VANNA = dDelta/dsigma = -e^{-qT} phi(d1) d2 / sigma
        CHARM = dDelta/dt      (per year)
    """
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return None, None
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    pdf = _norm_pdf(d1)
    vanna = -math.exp(-q * T) * pdf * d2 / sigma
    charm = -math.exp(-q * T) * pdf * (2 * (r - q) * T - d2 * sigma * sqrtT) / (2 * T * sigma * sqrtT)
    return vanna, charm


# ---------- Alpaca data ----------

def get_spot_price():
    r = requests.get(f"{DATA_BASE}/v2/stocks/{SYMBOL}/trades/latest", headers=HEADERS,
                     timeout=HTTP_TIMEOUT)
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
                     headers=HEADERS, params=params, timeout=HTTP_TIMEOUT)
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

def _et_date(stamp):
    """New York calendar date of an Alpaca RFC 3339 timestamp, or None."""
    if not stamp:
        return None
    try:
        t = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=datetime.timezone.utc)
        return t.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except ValueError:
        return None


def build_snapshot_rows(opt_type="call", spot=None):
    """Build rows for ONE option type ("call" or "put").

    Calls and puts are fetched and written separately (own CSV each), never
    mixed. Pass a shared `spot` so both types are priced off the same underlying.
    """
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    # The session's date in New York, not the runner's (UTC) date: after
    # 5 PM PT the UTC date is already tomorrow.
    today = now_utc.astimezone(ZoneInfo("America/New_York")).date().isoformat()
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
        # Alpaca keeps a contract's previous-session bar and trade until it
        # trades today (Oct 6: the 751 call showed 7,013 until 9:47, then 10).
        # Only today's volume and today's last trade count; otherwise 0 / none.
        last = trade.get("p") if _et_date(trade.get("t")) == today else None
        bar_day = _et_date(daily_bar.get("t"))
        volume = daily_bar.get("v") if (bar_day == today or (bar_day is None and not daily_bar.get("t"))) else 0
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
            "quote_time": quote.get("t"),
            "run_time": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
            "spot": round(spot, 2),
            "expiration": exp_date,
            "strike": strike,
            "bid": bid,
            "ask": ask,
            "last": last,
            "volume": volume,
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


def fill_missing_greeks(call_rows, put_rows, spot):
    """One IV per strike, so no contract drops out of GEX.

    In-the-money quotes on the free feed often have a mid below intrinsic
    (wide spreads), so no IV solves and the row used to carry no gamma: on
    Oct 7 that was 392 of 2,880 call rows and 301 of 2,880 put rows, all in
    the money. Standard practice: take each strike's IV from its
    out-of-the-money side and use it for both the call and the put; where
    neither side solves, read the smile in a straight line between the
    nearest solved strikes (flat past the edges). Returns rows filled.
    """
    def f(v):
        try:
            return None if v in (None, "") else float(v)
        except (TypeError, ValueError):
            return None

    sides = ((call_rows or [], True), (put_rows or [], False))
    smile = {}
    for rows, is_call in sides:
        for r in rows:
            iv, k = f(r.get("iv")), f(r.get("strike"))
            if iv is None or k is None or iv <= 0:
                continue
            otm = k >= spot if is_call else k < spot
            if otm or k not in smile:            # the out-of-the-money side wins
                smile[k] = iv
    if not smile:
        return 0
    ks = sorted(smile)

    def iv_at(k):
        if k in smile:
            return smile[k]
        lo = [x for x in ks if x < k]
        hi = [x for x in ks if x > k]
        if lo and hi:
            a, b = lo[-1], hi[0]
            return smile[a] + (smile[b] - smile[a]) * (k - a) / (b - a)
        return smile[lo[-1]] if lo else smile[hi[0]]

    filled = 0
    for rows, is_call in sides:
        for r in rows:
            if r.get("iv") not in (None, ""):
                continue
            k = f(r.get("strike"))
            if k is None or k <= 0:
                continue
            try:
                now = datetime.datetime.strptime(r["run_time"], "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=datetime.timezone.utc)
            except (KeyError, TypeError, ValueError):
                now = datetime.datetime.now(datetime.timezone.utc)
            expiry = r.get("expiration") or now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
            T = time_to_expiry_years(now, expiry)
            sigma = iv_at(k)
            if not sigma or sigma <= 0:
                continue
            greeks = _call_greeks if is_call else _put_greeks
            delta, gamma, theta, vega = greeks(spot, k, T, RISK_FREE_RATE, DIVIDEND_YIELD, sigma)
            r.update({"iv": _round_or_blank(sigma, 4), "delta": _round_or_blank(delta, 4),
                      "gamma": _round_or_blank(gamma, 5), "theta": _round_or_blank(theta, 4),
                      "vega": _round_or_blank(vega, 4)})
            filled += 1
    return filled


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
        w.writerows({key: row.get(key, "") for key in FIELDNAMES} for row in new_rows)


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
    _now = datetime.datetime.now(datetime.timezone.utc)
    # Reuse the capture time when rebuilding a saved session. Live rows carry
    # the same timestamp, so this is identical in production and keeps vanna /
    # charm history honest during a backfill after expiration.
    for sample_rows in (call_rows, put_rows):
        if not sample_rows:
            continue
        try:
            _now = datetime.datetime.strptime(
                sample_rows[0]["run_time"], "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=datetime.timezone.utc)
            break
        except (KeyError, TypeError, ValueError):
            pass
    by = {}

    def add(rows, is_call):
        for r in rows:
            k = num(r.get("strike"))
            g = num(r.get("gamma"))
            oi = num(r.get("open_interest")) or 0
            vol = num(r.get("volume")) or 0
            if k is None or g is None or (not oi and not vol):
                continue
            oi = int(oi)
            vol = int(vol)
            d = by.setdefault(k, {"strike": k, "call_gex": 0.0, "put_gex": 0.0,
                                  "call_oi": 0, "put_oi": 0, "dex": 0.0, "vex": 0.0,
                                  "tex": 0.0, "vannaex": 0.0, "charmex": 0.0,
                                  "call_g": 0.0, "put_g": 0.0,
                                  "call_vol": 0, "put_vol": 0,
                                  "call_gex_vol": 0.0, "put_gex_vol": 0.0})
            dollars = g * oi * unit
            # Same dollar-gamma formula weighted by TODAY'S volume instead of
            # yesterday's settled OI: this is the part that moves with the tape.
            vol_dollars = g * vol * unit
            if is_call:
                d["call_gex"] += dollars; d["call_oi"] += oi; d["call_g"] += g * oi
                d["call_gex_vol"] += vol_dollars; d["call_vol"] += vol
            else:
                d["put_gex"] -= dollars; d["put_oi"] += oi; d["put_g"] += g * oi
                d["put_gex_vol"] -= vol_dollars; d["put_vol"] += vol
            de, ve, th = num(r.get("delta")), num(r.get("vega")), num(r.get("theta"))
            if de is not None:
                d["dex"] += de * oi * CONTRACT_MULTIPLIER * spot
            if ve is not None:
                d["vex"] += ve * oi * CONTRACT_MULTIPLIER
            if th is not None:
                d["tex"] += th * oi * CONTRACT_MULTIPLIER
            iv = num(r.get("iv"))
            exp = r.get("expiration")
            if iv and exp:
                try:
                    Tv = time_to_expiry_years(_now, exp)
                    vanna, charm = _vanna_charm(spot, k, Tv, 0.0, 0.0, iv)
                    if vanna is not None:
                        d["vannaex"] += vanna * oi * CONTRACT_MULTIPLIER
                        d["charmex"] += charm * oi * CONTRACT_MULTIPLIER
                except Exception:
                    pass

    add(call_rows, True)
    add(put_rows, False)
    if not by:
        return {"error": "no usable rows for live GEX"}

    all_strikes = sorted(by.values(), key=lambda x: x["strike"])
    for s in all_strikes:
        s["net_gex"] = (s["call_gex"] + s["put_gex"]) * GEX_DEALER_SIGN
        s["net_gex_vol"] = (s["call_gex_vol"] + s["put_gex_vol"]) * GEX_DEALER_SIGN
        for key in ("call_gex", "put_gex", "net_gex", "dex", "vex", "tex",
                    "vannaex", "charmex", "call_gex_vol", "put_gex_vol", "net_gex_vol"):
            s[key] = round(s[key], 2)
    # OI-based levels use only strikes that carry OI, exactly as before.
    strikes = [s for s in all_strikes if s["call_oi"] + s["put_oi"] > 0] or all_strikes

    net = round(sum(s["net_gex"] for s in strikes), 2)
    net_dex = round(sum(s["dex"] for s in strikes), 2)
    net_vex = round(sum(s["vex"] for s in strikes), 2)
    net_tex = round(sum(s["tex"] for s in strikes), 2)
    net_vannaex = round(sum(s["vannaex"] for s in strikes), 2)
    net_charmex = round(sum(s["charmex"] for s in strikes), 2)
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

    # Volume-weighted structure (today's flow): its own flip and major levels.
    flip_vol, run_v, prev_v = None, 0.0, 0.0
    for i, s in enumerate(all_strikes):
        prev_v = run_v
        run_v += s["net_gex_vol"]
        if i and ((prev_v < 0 <= run_v) or (prev_v > 0 >= run_v)):
            a, b = all_strikes[i - 1]["strike"], s["strike"]
            frac = abs(prev_v) / (abs(prev_v) + abs(run_v)) if (prev_v or run_v) else 0.5
            flip_vol = round(a + (b - a) * frac, 2)
    has_vol = any(s["call_vol"] + s["put_vol"] for s in all_strikes)
    net_vol = round(sum(s["net_gex_vol"] for s in all_strikes), 2)
    vol_levels = {}
    if has_vol:
        vol_levels = {
            "major_call_gamma_vol": max(all_strikes, key=lambda s: s["call_gex_vol"])["strike"],
            "major_put_gamma_vol": min(all_strikes, key=lambda s: s["put_gex_vol"])["strike"],
            "major_pos_vol": max(all_strikes, key=lambda s: s["net_gex_vol"])["strike"],
            "major_neg_vol": min(all_strikes, key=lambda s: s["net_gex_vol"])["strike"],
        }

    call_wall = max(strikes, key=lambda s: s["call_gex"])["strike"]
    # Put wall = strike with the most negative NET dealer gamma (max short gamma
    # = the real support level). Using raw put_gex snapped it to the ATM strike,
    # whose put gamma is huge just from being at-the-money, so the wall kept
    # printing on top of spot. Net gamma puts it at the true downside level.
    put_wall = min(strikes, key=lambda s: s["net_gex"])["strike"]
    top_oi = max(strikes, key=lambda s: s["call_oi"] + s["put_oi"])["strike"]

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    return {
        "symbol": SYMBOL, "spot": round(spot, 2),
        "net_gex": net, "net_dex": net_dex, "net_vex": net_vex, "net_tex": net_tex,
        "net_vannaex": net_vannaex, "net_charmex": net_charmex,
        "gamma_ratio": gamma_ratio,
        "gamma_flip": flip, "gamma_flip_method": method,
        "regime": ("POSITIVE GAMMA"
                   if (spot > flip if method == "zero crossing" else net > 0)
                   else "NEGATIVE GAMMA"),
        "distance_to_flip_pct": round((spot - flip) / spot * 100, 2) if flip else None,
        "levels": {"call_wall": call_wall, "put_wall": put_wall, "highest_oi_strike": top_oi,
                   **vol_levels},
        "net_gex_vol": net_vol if has_vol else None,
        "gamma_flip_vol": flip_vol if has_vol else None,
        "strikes": [{"strike": s["strike"], "call_gex": s["call_gex"], "put_gex": s["put_gex"],
                     "net_gex": s["net_gex"], "call_oi": s["call_oi"], "put_oi": s["put_oi"],
                     "dex": s["dex"], "vex": s["vex"], "tex": s["tex"],
                     "vannaex": s["vannaex"], "charmex": s["charmex"],
                     "call_vol": s["call_vol"], "put_vol": s["put_vol"],
                     "call_gex_vol": s["call_gex_vol"], "put_gex_vol": s["put_gex_vol"],
                     "net_gex_vol": s["net_gex_vol"]} for s in all_strikes],
        "contracts_used": len(call_rows) + len(put_rows),
        "dealer_convention": "dealers long call gamma, short put gamma",
        "source": "1-min greeks logger (calls + puts, 0DTE)",
        "generated_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
    }


# Wall steadiness: a rival strike must beat the current wall by 10% for 3
# straight snapshots before the wall moves. On Oct 7 the call wall swapped
# between 755 and 762 21 times in 96 minutes; with this rule it moved once.
WALL_SWITCH_EDGE = 1.10
WALL_SWITCH_SNAPSHOTS = 3
_WALLS = {"date": None, "call": None, "put": None, "call_try": (None, 0), "put_try": (None, 0)}


def _seed_walls(session_date):
    """Start from the last saved wall state so a new run doesn't jump or reset
    its 3-snapshot count (each refresh run is a fresh process)."""
    try:
        with open(GEX_LIVE_PATH) as f:
            state = (json.load(f) or {}).get("wall_state") or {}
        if state.get("date") == session_date:
            return state
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    try:
        with open(GEX_INTRADAY_PATH) as f:
            hist = json.load(f)
        if hist.get("session_date") == session_date and hist.get("points"):
            last = hist["points"][-1]
            return {"call": last.get("call_wall"), "put": last.get("put_wall")}
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return {}


def stabilize_walls(gx):
    """Hold call/put walls through near-ties. Raw picks stay in levels.*_raw."""
    if not gx or gx.get("error"):
        return gx
    levels = gx.setdefault("levels", {})
    strikes = gx.get("strikes") or []
    try:
        stamp = datetime.datetime.strptime(gx["generated_utc"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=datetime.timezone.utc)
    except (KeyError, TypeError, ValueError):
        stamp = datetime.datetime.now(datetime.timezone.utc)
    day = stamp.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    if _WALLS["date"] != day:
        seed = _seed_walls(day)
        _WALLS.update({"date": day, "call": seed.get("call"), "put": seed.get("put"),
                       "call_try": tuple(seed.get("call_try") or (None, 0)),
                       "put_try": tuple(seed.get("put_try") or (None, 0))})
    sides = (
        ("call", "call_wall", {s["strike"]: s["call_gex"] for s in strikes},
         lambda new, cur: new > 0 and new >= WALL_SWITCH_EDGE * cur),
        ("put", "put_wall", {s["strike"]: s["net_gex"] for s in strikes},
         lambda new, cur: new < 0 and new <= WALL_SWITCH_EDGE * cur),
    )
    for side, key, vals, beats in sides:
        raw, cur = levels.get(key), _WALLS[side]
        levels[key + "_raw"] = raw
        cand, n = _WALLS[side + "_try"]
        if raw is None or cur is None or cur not in vals or raw == cur:
            held, cand, n = raw, None, 0
        elif beats(vals[raw], vals[cur]):
            n = n + 1 if cand == raw else 1
            cand = raw
            if n >= WALL_SWITCH_SNAPSHOTS:
                held, cand, n = raw, None, 0
            else:
                held = cur
        else:
            held, cand, n = cur, None, 0
        _WALLS[side], _WALLS[side + "_try"] = held, (cand, n)
        levels[key] = held
    gx["wall_state"] = {"date": day, "call": _WALLS["call"], "put": _WALLS["put"],
                        "call_try": list(_WALLS["call_try"]), "put_try": list(_WALLS["put_try"])}
    return gx


def write_gex_live(gx):
    os.makedirs(os.path.dirname(GEX_LIVE_PATH), exist_ok=True)
    with open(GEX_LIVE_PATH, "w") as f:
        json.dump(gx, f, indent=2)


def gex_intraday_point(gx):
    """Reduce one full option-chain snapshot to the changing chart levels.

    The full strike map remains in gex_live.json; this compact record is what
    lets the dashboard draw genuine historical step-lines instead of extending
    today's latest levels backward across the whole price chart.
    """
    if not gx or gx.get("error"):
        return None
    try:
        stamp = datetime.datetime.strptime(gx["generated_utc"], "%Y-%m-%d %H:%M:%S")
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    except (KeyError, TypeError, ValueError):
        stamp = datetime.datetime.now(datetime.timezone.utc)
    levels = gx.get("levels") or {}
    strikes = gx.get("strikes") or []

    def extreme(test, pick):
        found = None
        for row in strikes:
            try:
                value = float(row.get("net_gex"))
                strike = float(row.get("strike"))
            except (TypeError, ValueError):
                continue
            if not test(value) or (found is not None and not pick(value, found[1])):
                continue
            found = (strike, value)
        return found[0] if found else None

    def concentration(field):
        found = None
        for row in strikes:
            try:
                value = abs(float(row.get(field)))
                strike = float(row.get("strike"))
            except (TypeError, ValueError):
                continue
            if found is None or value > found[1]:
                found = (strike, value)
        return found[0] if found and found[1] > 0 else None

    return {
        "time": int(stamp.timestamp()),
        "spot": gx.get("spot"),
        "gamma_flip": gx.get("gamma_flip") if gx.get("gamma_flip_method") == "zero crossing" else None,
        "call_wall": levels.get("call_wall"),
        "put_wall": levels.get("put_wall"),
        "oi_magnet": levels.get("highest_oi_strike"),
        "max_pos_gamma": extreme(lambda n: n > 0, lambda n, old: n > old),
        "max_neg_gamma": extreme(lambda n: n < 0, lambda n, old: n < old),
        "vanna_strike": concentration("vannaex"),
        "charm_strike": concentration("charmex"),
        "theta_strike": concentration("tex"),
    }


def append_gex_intraday(gx):
    point = gex_intraday_point(gx)
    if not point:
        return
    session_date = datetime.datetime.fromtimestamp(
        point["time"], ZoneInfo("America/New_York")).date().isoformat()
    history = {"symbol": SYMBOL, "session_date": session_date, "points": []}
    try:
        with open(GEX_INTRADAY_PATH) as f:
            existing = json.load(f)
        if existing.get("session_date") == session_date and isinstance(existing.get("points"), list):
            history = existing
    except (OSError, ValueError, TypeError):
        pass

    # One point per captured minute. A retry replaces that minute rather than
    # drawing a one-second zig-zag, and the cap keeps GitHub Pages lightweight.
    by_minute = {int(p.get("time", 0)) // 60: p for p in history["points"] if p.get("time")}
    by_minute[point["time"] // 60] = point
    history["points"] = sorted(by_minute.values(), key=lambda p: p["time"])[-600:]
    history["updated_utc"] = gx.get("generated_utc")
    os.makedirs(os.path.dirname(GEX_INTRADAY_PATH), exist_ok=True)
    tmp = GEX_INTRADAY_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(history, f, separators=(",", ":"))
    os.replace(tmp, GEX_INTRADAY_PATH)


def gex_frame(gx):
    """One per-minute strike profile: net gamma per strike by OI and by
    volume, in $ thousands. Shared by the live logger and gex_history.py.
    Returns (session_date, frame) or (None, None)."""
    if not gx or gx.get("error") or not gx.get("strikes"):
        return None, None
    try:
        stamp = datetime.datetime.strptime(gx["generated_utc"], "%Y-%m-%d %H:%M:%S")
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    except (KeyError, TypeError, ValueError):
        stamp = datetime.datetime.now(datetime.timezone.utc)
    t = int(stamp.timestamp())
    session_date = datetime.datetime.fromtimestamp(
        t, ZoneInfo("America/New_York")).date().isoformat()

    def k(v):
        try:
            return int(round(float(v) / 1000.0))
        except (TypeError, ValueError):
            return 0

    frame = {
        "t": t,
        "s": gx.get("spot"),
        "f": gx.get("gamma_flip") if gx.get("gamma_flip_method") == "zero crossing" else None,
        "fv": gx.get("gamma_flip_vol"),
        # [strike, net gamma by OI ($K), net gamma by volume ($K), call vol, put vol]
        "d": [[r.get("strike"), k(r.get("net_gex")), k(r.get("net_gex_vol")),
               int(r.get("call_vol") or 0), int(r.get("put_vol") or 0)]
              for r in gx["strikes"]],
    }
    return session_date, frame


def append_gex_frame(gx):
    """Append one per-minute strike profile to gex_frames.json, so the page
    can replay the day and measure how fast each strike's gamma changed."""
    session_date, frame = gex_frame(gx)
    if not frame:
        return
    t = frame["t"]
    doc = {"symbol": SYMBOL, "session_date": session_date, "units": "USD thousands", "frames": []}
    try:
        with open(GEX_FRAMES_PATH) as f:
            existing = json.load(f)
        if existing.get("session_date") == session_date and isinstance(existing.get("frames"), list):
            doc = existing
    except (OSError, ValueError, TypeError):
        pass
    by_minute = {int(fr.get("t", 0)) // 60: fr for fr in doc["frames"] if fr.get("t")}
    by_minute[t // 60] = frame
    doc["frames"] = sorted(by_minute.values(), key=lambda fr: fr["t"])[-GEX_FRAMES_CAP:]
    doc["updated_utc"] = gx.get("generated_utc")
    os.makedirs(os.path.dirname(GEX_FRAMES_PATH), exist_ok=True)
    tmp = GEX_FRAMES_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    os.replace(tmp, GEX_FRAMES_PATH)


# ---------- Auction metrics (VWAP, A/D line, opening range, volume) ----------
# All computed from QQQ 1-minute bars on Alpaca's FREE IEX feed. IEX is a SUBSET
# of the consolidated tape, so VWAP/volume here are IEX-only reads, not the exact
# tape numbers a broker screen shows — the dashboard labels them "IEX" honestly.
# Fully isolated: any failure is swallowed so it can NEVER break greeks logging.
def get_qqq_minute_bars():
    """Today's RTH 1-minute bars for QQQ (IEX). Returns a list of bar dicts."""
    ny = ZoneInfo("America/New_York")
    now_ny = datetime.datetime.now(ny)
    session_open = now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
    start_iso = session_open.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Stop at the 3:59 PM bar: after-hours IEX prints were leaking into the
    # candles, VWAP and A/D after 4:00 PM.
    session_close = min(now_ny, now_ny.replace(hour=15, minute=59, second=59, microsecond=0))
    end_iso = session_close.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bars, page_token = [], None
    for _ in range(12):                       # hard cap on pagination (safety)
        params = {"timeframe": "1Min", "start": start_iso, "end": end_iso,
                  "feed": "iex", "limit": 10000, "adjustment": "raw", "sort": "asc"}
        if page_token:
            params["page_token"] = page_token
        r = requests.get(f"{DATA_BASE}/v2/stocks/{SYMBOL}/bars",
                         headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        j = r.json()
        bars.extend(j.get("bars") or [])
        page_token = j.get("next_page_token")
        if not page_token:
            break
    return bars, session_open.strftime("%Y-%m-%d")


def compute_auction(bars, session_date, spot):
    """VWAP, Accumulation/Distribution line + z, opening range, volume pace.
    Returns None if there aren't enough bars to say anything real."""
    rows = []
    for b in bars:
        try:
            o, h, l, c, v = float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"]), float(b["v"])
        except (KeyError, TypeError, ValueError):
            continue
        if v <= 0 or h < l:
            continue
        vw = b.get("vw")
        try:
            vw = float(vw)
        except (TypeError, ValueError):
            vw = (h + l + c) / 3.0            # typical-price fallback
        # Lightweight Charts expects an intraday Unix timestamp. Keep the raw
        # Alpaca bar time alongside the auction inputs so the dashboard can
        # draw the same QQQ tape this function is already analysing.
        ts = b.get("t")
        try:
            if isinstance(ts, (int, float)):
                ts = int(ts)
            else:
                ts = int(datetime.datetime.fromisoformat(
                    str(ts).replace("Z", "+00:00")
                ).timestamp())
        except (TypeError, ValueError, OverflowError):
            ts = None
        rows.append({"time": ts, "o": o, "h": h, "l": l, "c": c, "v": v, "vw": vw})
    n = len(rows)
    if n < 5:
        return None

    # VWAP (cumulative, IEX) — Σ(bar_vwap · vol) / Σvol
    pv = sum(r["vw"] * r["v"] for r in rows)
    tv = sum(r["v"] for r in rows)
    vwap = pv / tv if tv else None

    # Accumulation/Distribution line (Chaikin): cumulative Σ CLV·vol
    ad, ad_series = 0.0, []
    for r in rows:
        rng = r["h"] - r["l"]
        clv = (((r["c"] - r["l"]) - (r["h"] - r["c"])) / rng) if rng > 0 else 0.0
        ad += clv * r["v"]
        ad_series.append(ad)
    mean_ad = sum(ad_series) / n
    var_ad = sum((x - mean_ad) ** 2 for x in ad_series) / n
    std_ad = math.sqrt(var_ad)
    ad_z = ((ad_series[-1] - mean_ad) / std_ad) if std_ad > 0 else 0.0

    # Opening range — first 15 and 30 one-minute bars of the session
    or15 = rows[:15]
    or30 = rows[:30]
    or15_hi, or15_lo = max(r["h"] for r in or15), min(r["l"] for r in or15)
    or30_hi, or30_lo = max(r["h"] for r in or30), min(r["l"] for r in or30)
    if spot is None:
        spot_vs_or = None
    elif spot > or30_hi:
        spot_vs_or = "above"
    elif spot < or30_lo:
        spot_vs_or = "below"
    else:
        spot_vs_or = "inside"

    # Volume pace — recent 5-min avg per-minute vs whole-session avg per-minute
    recent = rows[-5:]
    per_min = tv / n
    recent_per_min = sum(r["v"] for r in recent) / len(recent)
    vol_pace = (recent_per_min / per_min) if per_min > 0 else None

    dist = ((spot - vwap) / vwap * 100.0) if (spot and vwap) else None
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    candles = [
        {
            "time": r["time"],
            "open": round(r["o"], 4),
            "high": round(r["h"], 4),
            "low": round(r["l"], 4),
            "close": round(r["c"], 4),
            "volume": round(r["v"], 0),
        }
        for r in rows[-450:] if r["time"] is not None
    ]

    return {
        "generated_utc": stamp,
        "session_date": session_date,
        "feed": "iex",
        "bars": n,
        "spot": round(spot, 2) if spot else None,
        "vwap": round(vwap, 2) if vwap else None,
        "vwap_dist_pct": round(dist, 2) if dist is not None else None,
        "ad_z": round(ad_z, 2),
        "ad_last": round(ad_series[-1], 0),
        "or15_high": round(or15_hi, 2), "or15_low": round(or15_lo, 2),
        "or30_high": round(or30_hi, 2), "or30_low": round(or30_lo, 2),
        "spot_vs_or": spot_vs_or,
        "vol_total": round(tv, 0),
        "vol_pace": round(vol_pace, 2) if vol_pace is not None else None,
        # QQQ only. The same IEX one-minute bars already fetched for VWAP and
        # opening-range metrics now power the Nasdaq tab's TradingView chart.
        "candles": candles,
    }


def write_auction_live(a):
    os.makedirs(os.path.dirname(AUCTION_LIVE_PATH), exist_ok=True)
    with open(AUCTION_LIVE_PATH, "w") as f:
        json.dump(a, f, indent=2)


_git_ready = [False]


def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, timeout=120)


def push_live_snapshots():
    """Publish without changing the checkout where the logger writes CSVs."""
    if not CI_PUSH:
        return False
    try:
        from publish_history import publish, LIVE_PATHS
        paths = LIVE_PATHS + [output_path(kind) for kind in ("call", "put")]
        publish(os.path.dirname(os.path.abspath(__file__)), paths)
        return True
    except Exception as exc:
        print(f"Live publish failed; retrying next minute: {exc}", flush=True)
        return False


def push_relay():
    """Send the live files that changed to the relay. Never raises."""
    if not RELAY_KEY or not RELAY_URL.startswith("https://"):
        return
    for name in RELAY_FILES:
        path = os.path.join("market-dash", name)
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            continue
        digest = hashlib.sha1(body).hexdigest()
        if _RELAY_SENT.get(name) == digest:
            continue
        try:
            r = requests.put(f"{RELAY_URL}/v1/{name}", data=body, timeout=10,
                             headers={"Authorization": f"Bearer {RELAY_KEY}",
                                      "Content-Type": "application/json"})
            if r.status_code == 200:
                _RELAY_SENT[name] = digest
            else:
                print(f"Relay {name}: HTTP {r.status_code} (git push still covers it)", flush=True)
        except requests.RequestException as exc:
            print(f"Relay {name} skipped: {exc.__class__.__name__}", flush=True)


def snapshot_and_write(spot):
    """Capture calls + puts (separate CSVs) and write the live GEX json."""
    got = {}
    for t in ("call", "put"):
        rows = build_snapshot_rows(t, spot=spot)
        if rows:
            got[t] = rows
    try:
        filled = fill_missing_greeks(got.get("call"), got.get("put"), spot)
        if filled:
            print(f"Filled IV/greeks on {filled} in-the-money rows from the smile")
    except Exception as e:
        print(f"IV fill skipped (continuing): {e}")
    for t, rows in got.items():
        write_rows(rows, t)
    try:
        if got.get("call") or got.get("put"):
            live_gex = compute_gex_live(got.get("call", []), got.get("put", []), spot)
            try:
                stabilize_walls(live_gex)
            except Exception as e:
                print(f"Wall steadiness skipped (continuing): {e}")
            write_gex_live(live_gex)
            append_gex_intraday(live_gex)
            try:
                append_gex_frame(live_gex)
            except Exception as e:
                print(f"GEX frames skipped (continuing): {e}")
    except Exception as e:
        print(f"live GEX skipped (continuing): {e}")

    try:
        from qqq_option_snapshot import write_option_snapshot
        write_option_snapshot(got.get("call"), got.get("put"))
    except Exception as exc:
        print(f"Options snapshot skipped (keeping previous data): {exc}")

    # Auction metrics — fully isolated; a failure here must not affect anything above.
    try:
        bars, sess_date = get_qqq_minute_bars()
        a = compute_auction(bars, sess_date, spot)
        if a:
            write_auction_live(a)
    except Exception as e:
        print(f"auction metrics skipped (continuing): {e}")

    push_relay()


def refresh_candles():
    """Rewrite market-dash/qqq_candle_history.json. The dashboard picks "today"
    from this file, so the logger keeps it current during the session."""
    try:
        from qqq_candle_history import update_history
        history = update_history(HEADERS)
        print(f"Candle history: {len(history['daily'])} daily, {len(history['minute'])} minute bars")
    except Exception as exc:
        print(f"Candle history skipped (keeping previous data): {exc}")


def main():
    if not API_KEY or not API_SECRET:
        print("STOP: ALPACA_API_KEY / ALPACA_API_SECRET not set.")
        return

    # Fetch candle history at the start of every run (and every few minutes in
    # the loop below). A failed data request leaves the prior file intact and
    # must not stop options logging.
    refresh_candles()

    # Single snapshot mode. Calls AND puts (separate files) + live GEX.
    if INTERVAL_SECONDS <= 0 or DURATION_SECONDS <= 0:
        snapshot_and_write(get_spot_price())
        # Same isolated, merging publisher as the minute loop: no rebase
        # conflicts with a logger run publishing the same files.
        if CI_PUSH and not push_live_snapshots():
            raise SystemExit("Publish failed (the relay upload above still went out).")
        return

    # Session / loop mode: snapshot every INTERVAL for DURATION.
    print(f"Session start: every {INTERVAL_SECONDS}s for {DURATION_SECONDS}s "
          f"(window +/-{WINDOW:g}, calls + puts to separate files).")
    start = time.monotonic()
    last_push = start
    last_candles = start
    retry_push = False
    count = 0
    while time.monotonic() - start < DURATION_SECONDS:
        try:
            snapshot_and_write(get_spot_price())    # calls + puts (separate files) + live GEX
            count += 1
        except Exception as e:
            print(f"Snapshot error (continuing): {e}")
        # Refresh today's candles every few minutes so the next push carries them.
        if time.monotonic() - last_candles >= CANDLE_REFRESH_SECONDS:
            refresh_candles()
            last_candles = time.monotonic()
        # Push the live JSONs mid-run so the dashboard is fresh DURING the window.
        # A failed push is retried on the very next snapshot, not 5 minutes later.
        if CI_PUSH and (retry_push or time.monotonic() - last_push >= LIVE_PUSH_SECONDS):
            if push_live_snapshots():
                last_push, retry_push = time.monotonic(), False
            else:
                retry_push = True
        if time.monotonic() - start >= DURATION_SECONDS:
            break
        # Sleep to 2 s past the next interval boundary (:02 each minute), so a
        # slow snapshot never pushes the next one into the following minute.
        time.sleep(max(1.0, INTERVAL_SECONDS - ((time.time() - 2) % INTERVAL_SECONDS)))
    print(f"Session done: {count} snapshots written (calls + puts, separate files).")


if __name__ == "__main__":
    main()
