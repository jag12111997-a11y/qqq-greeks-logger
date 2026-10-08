"""Rebuild each past session minute by minute for the GEX Chart.

For every trading day it writes market-dash/history/<YYYY-MM-DD>.json:
  frames  one strike profile per minute (same shape as gex_frames.json)
  points  one level record per minute (same shape as gex_intraday.json)

Minutes the live logger captured are used as logged (calls + puts, with the
logger's one-IV-per-strike fill). Minutes it missed (before 6:45 AM PT and the
old 11:45-3:15 ET midday gap) are rebuilt from Alpaca's historical 1-minute
bars: QQQ price from stock bars, option prices and volume from option bars,
open interest from that day's logged files (it is fixed for the day).
Rebuilt frames and points carry "b": 1 so the chart can show them differently.

Option bars are trades, not quotes, so a rebuilt minute prices each contract
off its last trade. To keep a stale print from distorting IV, each trade's IV
is solved against QQQ's price in the minute that trade happened, and IVs are
only taken from trades in the last STALE_MINUTES. The smile is then read per
strike (out-of-the-money side first) and greeks are computed at the current
minute's QQQ price, the same way the live logger fills its gaps.

Usage (needs ALPACA_API_KEY / ALPACA_API_SECRET):
  python gex_history.py                       # every day in data/
  python gex_history.py --days 2026-10-07     # one or more days
  python gex_history.py --validate 2026-10-06 # rebuild logged minutes too and
                                              # compare against the live log
  python gex_history.py --days today          # today's session (end of day)
"""
import argparse
import bisect
import csv
import datetime as dt
import glob
import json
import math
import os
import re
import statistics
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import qqq_greeks_logger_alpaca as L

NY = ZoneInfo("America/New_York")
UTC = dt.timezone.utc
DATA = "https://data.alpaca.markets"
OUT_DIR = Path("market-dash/history")
OPEN_MIN, CLOSE_MIN = 9 * 60 + 30, 16 * 60     # 9:30 to 16:00 ET, minute starts
WINDOW = 15.0                                  # strikes within +/- $15 of spot, as live
STALE_MINUTES = 15                             # ignore trades older than this for IV
FORMAT_VERSION = 1
DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


# ---------- Alpaca ----------

class Alpaca:
    def __init__(self, key, secret):
        self.headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        self.calls = 0

    def get(self, url, params):
        for attempt in range(6):
            self.calls += 1
            r = requests.get(url, headers=self.headers, params=params, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(60, 5 * (attempt + 1)))
                continue
            r.raise_for_status()
            time.sleep(0.35)                       # stay well under 200 requests/min
            return r.json()
        r.raise_for_status()

    def stock_bars(self, day):
        start, end = session_bounds(day)
        out, token = [], None
        for feed in ("sip", "iex"):
            out, token = [], None
            try:
                while True:
                    params = {"timeframe": "1Min", "start": iso(start), "end": iso(end),
                              "feed": feed, "adjustment": "raw", "limit": 10000, "sort": "asc"}
                    if token:
                        params["page_token"] = token
                    j = self.get(f"{DATA}/v2/stocks/QQQ/bars", params)
                    out.extend(j.get("bars") or [])
                    token = j.get("next_page_token")
                    if not token:
                        break
                if out:
                    return out, feed
            except requests.HTTPError:
                continue
        return out, None

    def option_bars(self, symbols, day):
        start, end = session_bounds(day)
        bars = {}
        for i in range(0, len(symbols), 100):
            chunk, token = symbols[i:i + 100], None
            while True:
                params = {"symbols": ",".join(chunk), "timeframe": "1Min", "start": iso(start),
                          "end": iso(end), "limit": 10000, "sort": "asc"}
                if token:
                    params["page_token"] = token
                j = self.get(f"{DATA}/v1beta1/options/bars", params)
                for sym, rows in (j.get("bars") or {}).items():
                    bars.setdefault(sym, []).extend(rows or [])
                token = j.get("next_page_token")
                if not token:
                    break
        return bars

    def open_interest(self, day, kind, lo, hi):
        """OI for expired contracts (fallback when the day's log lacks a side)."""
        found, token = {}, None
        while True:
            params = {"underlying_symbols": "QQQ", "type": kind, "expiration_date": day,
                      "strike_price_gte": lo, "strike_price_lte": hi, "status": "inactive",
                      "limit": 1000}
            if token:
                params["page_token"] = token
            j = self.get(f"{L.TRADING_BASE}/v2/options/contracts", params)
            for c in j.get("option_contracts") or []:
                if c.get("open_interest") not in (None, ""):
                    found[float(c["strike_price"])] = int(c["open_interest"])
            token = j.get("next_page_token")
            if not token:
                return found


def iso(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def session_bounds(day):
    d = dt.date.fromisoformat(day)
    return (dt.datetime(d.year, d.month, d.day, 9, 30, tzinfo=NY),
            dt.datetime(d.year, d.month, d.day, 16, 0, tzinfo=NY))


def minute_utc(day, m):
    d = dt.date.fromisoformat(day)
    return dt.datetime(d.year, d.month, d.day, m // 60, m % 60, tzinfo=NY).astimezone(UTC)


def occ(day, kind, strike):
    d = dt.date.fromisoformat(day)
    return f"QQQ{d:%y%m%d}{'C' if kind == 'call' else 'P'}{int(round(strike * 1000)):08d}"


def et_minute(stamp):
    local = stamp.astimezone(NY)
    return local.hour * 60 + local.minute


def num(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


# ---------- The live log ----------

def load_logged(day):
    """Logged rows grouped by ET minute: {minute: {"call": [...], "put": [...]}}."""
    by = {}
    files = glob.glob(f"data/{day}/*/qqq_greeks_*{day}_*.csv")
    for path in sorted(files):
        name = os.path.basename(path)
        kind = "put" if "_puts_" in name else "call"   # old July files are calls only
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                try:
                    stamp = dt.datetime.strptime(r["run_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                except (KeyError, ValueError):
                    continue
                m = et_minute(stamp)
                if not OPEN_MIN <= m < CLOSE_MIN:
                    continue
                slot = by.setdefault(m, {"call": {}, "put": {}, "stamp": stamp})
                k = num(r.get("strike"))
                if k is None:
                    continue
                prev = slot[kind].get(k)
                if prev is None or r["run_time"] >= prev["run_time"]:  # last capture in the minute
                    slot[kind][k] = dict(r)
                slot["stamp"] = max(slot["stamp"], stamp)
    return by


def logged_oi(by):
    oi = {"call": {}, "put": {}}
    for slot in by.values():
        for kind in ("call", "put"):
            for k, r in slot[kind].items():
                v = num(r.get("open_interest"))
                if v:
                    oi[kind][k] = max(oi[kind].get(k, 0), int(v))
    return oi


def logged_gex(day, slot):
    """GEX for a logged minute, from the rows exactly as captured."""
    calls = [dict(r) for r in slot["call"].values()]
    puts = [dict(r) for r in slot["put"].values()]
    if not calls or not puts:
        return None
    spot = statistics.median(num(r["spot"]) for r in calls + puts if num(r.get("spot")))
    for r in calls + puts:
        if r.get("iv") not in (None, "") and r.get("gamma") in (None, ""):
            r["iv"] = ""                               # recompute greeks consistently
    L.fill_missing_greeks(calls, puts, spot)
    gx = L.compute_gex_live(calls, puts, spot)
    if gx.get("error"):
        return None
    gx["generated_utc"] = slot["stamp"].strftime("%Y-%m-%d %H:%M:%S")
    return gx


# ---------- Rebuilding a minute from bars ----------

class DayBars:
    """Per-minute QQQ price and per-contract trade history for one session."""

    def __init__(self, day, stock, options):
        self.day = day
        self.spot = {}
        for b in stock:
            m = et_minute(dt.datetime.fromisoformat(b["t"].replace("Z", "+00:00")))
            if OPEN_MIN <= m < CLOSE_MIN:
                self.spot[m] = float(b["c"])
        self.trades = {}                                # (kind, strike) -> [(minute, close, volume)]
        self._cum, self._mins = {}, {}
        for sym, rows in options.items():
            kind = "call" if sym[9] == "C" else "put"
            strike = int(sym[10:]) / 1000.0
            seq = []
            for b in rows:
                m = et_minute(dt.datetime.fromisoformat(b["t"].replace("Z", "+00:00")))
                if OPEN_MIN <= m < CLOSE_MIN and b.get("c") is not None:
                    seq.append((m, float(b["c"]), int(b.get("v") or 0)))
            seq.sort()
            self.trades[(kind, strike)] = seq
            run, total = [], 0
            for _, _, v in seq:
                total += v
                run.append(total)
            self._cum[(kind, strike)] = run
            self._mins[(kind, strike)] = [x[0] for x in seq]
        self._spot_keys = sorted(self.spot)

    def spot_at(self, m):
        i = bisect.bisect_right(self._spot_keys, m)
        return self.spot[self._spot_keys[i - 1]] if i else None

    def state(self, kind, strike, m):
        """(last trade minute, last price, cumulative volume) through minute m."""
        mins = self._mins.get((kind, strike))
        if not mins:
            return None, None, 0
        i = bisect.bisect_right(mins, m)
        if not i:
            return None, None, 0
        tm, px, _ = self.trades[(kind, strike)][i - 1]
        return tm, px, self._cum[(kind, strike)][i - 1]


def rebuild_minute(day, m, bars, oi, wall_strikes=None):
    """Rows like the logger's for minute m, priced off trades. Returns gx or None."""
    spot = bars.spot_at(m)
    if not spot:
        return None
    now = minute_utc(day, m) + dt.timedelta(seconds=59)   # end of the minute
    T = L.time_to_expiry_years(now, day)
    r, q = L.RISK_FREE_RATE, L.DIVIDEND_YIELD
    lo, hi = spot - WINDOW, spot + WINDOW
    strikes = sorted({k for (_, k) in bars.trades if lo <= k <= hi} |
                     {k for kind in oi for k in oi[kind] if lo <= k <= hi})
    smile = {}
    rows = {"call": [], "put": []}
    for kind in ("call", "put"):
        for k in strikes:
            tm, px, cum = bars.state(kind, k, m)
            if tm is not None and m - tm <= STALE_MINUTES:
                s_then = bars.spot_at(tm) or spot
                t_then = L.time_to_expiry_years(minute_utc(day, tm) + dt.timedelta(seconds=30), day)
                iv = (L._implied_vol_call if kind == "call" else L._implied_vol_put)(px, s_then, k, t_then, r, q)
                if iv and 0.01 < iv < 3.0:
                    otm = k >= spot if kind == "call" else k < spot
                    if otm or k not in smile:
                        smile[k] = iv
            rows[kind].append({"run_time": now.strftime("%Y-%m-%d %H:%M:%S"), "spot": round(spot, 2),
                               "expiration": day, "strike": k, "bid": "", "ask": "",
                               "last": px if px is not None else "", "volume": cum,
                               "open_interest": oi[kind].get(k, ""), "iv": "", "delta": "",
                               "gamma": "", "theta": "", "vega": ""})
    if len(smile) < 2:
        return None                                    # not enough fresh trades to read a smile
    # Seed the per-strike smile, then let the logger's fill compute every row.
    for kind in ("call", "put"):
        for row in rows[kind]:
            if row["strike"] in smile:
                row["iv"] = smile[row["strike"]]
    for kind in ("call", "put"):
        for row in rows[kind]:
            if row["iv"] != "":
                greeks = L._call_greeks if kind == "call" else L._put_greeks
                d, g, th, v = greeks(spot, row["strike"], T, r, q, row["iv"])
                row.update({"iv": round(row["iv"], 4), "delta": round(d, 4), "gamma": round(g, 5),
                            "theta": round(th, 4), "vega": round(v, 4)})
    L.fill_missing_greeks(rows["call"], rows["put"], spot)
    gx = L.compute_gex_live(rows["call"], rows["put"], spot)
    if gx.get("error"):
        return None
    gx["generated_utc"] = now.strftime("%Y-%m-%d %H:%M:%S")
    return gx


# ---------- A whole day ----------

def build_day(day, api, validate=False):
    logged = load_logged(day)
    complete = {m: s for m, s in logged.items() if s["call"] and s["put"]}
    oi = logged_oi(logged)
    need = [m for m in range(OPEN_MIN, CLOSE_MIN) if m not in complete]
    report = {"day": day, "logged_minutes": len(complete), "missing_minutes": len(need)}

    bars, candles = None, []
    if (need or validate) and api:
        stock, feed = api.stock_bars(day)
        report["stock_feed"] = feed
        for b in stock:
            t = dt.datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
            if OPEN_MIN <= et_minute(t) < CLOSE_MIN:
                candles.append({"time": int(t.timestamp()), "open": b["o"], "high": b["h"],
                                "low": b["l"], "close": b["c"], "volume": b.get("v", 0)})
        if stock:
            lo = min(float(b["l"]) for b in stock) - WINDOW - 1
            hi = max(float(b["h"]) for b in stock) + WINDOW + 1
            ks = [float(k) for k in range(math.floor(lo), math.ceil(hi) + 1)]
            symbols = [occ(day, kind, k) for kind in ("call", "put") for k in ks]
            bars = DayBars(day, stock, api.option_bars(symbols, day))
            report["option_contracts_traded"] = sum(1 for v in bars.trades.values() if v)
            for kind in ("call", "put"):
                if not oi[kind]:
                    oi[kind] = api.open_interest(day, kind, math.floor(lo), math.ceil(hi))
                    report[f"{kind}_oi_source"] = "alpaca contracts"
    report["oi_strikes"] = {k: len(v) for k, v in oi.items()}

    # Walls are steadied across the whole day in time order, as live.
    L._WALLS.update({"date": day, "call": None, "put": None,
                     "call_try": (None, 0), "put_try": (None, 0)})
    frames, points, rebuilt, skipped = [], [], 0, 0
    for m in range(OPEN_MIN, CLOSE_MIN):
        if m in complete:
            gx, b = logged_gex(day, complete[m]), 0
        elif bars:
            gx, b = rebuild_minute(day, m, bars, oi), 1
        else:
            gx, b = None, 0
        if not gx:
            skipped += 1
            continue
        L.stabilize_walls(gx)
        _, frame = L.gex_frame(gx)
        point = L.gex_intraday_point(gx)
        if b:
            frame["b"] = 1
            point["b"] = 1
            rebuilt += 1
        frames.append(frame)
        points.append(point)
    report.update({"rebuilt_minutes": rebuilt, "empty_minutes": skipped, "api_calls": api.calls if api else 0})

    doc = {"symbol": "QQQ", "session_date": day, "version": FORMAT_VERSION,
           "units": "USD thousands",
           "built_utc": dt.datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
           "sources": {"logged": len(frames) - rebuilt, "rebuilt": rebuilt,
                       "rebuilt_from": "Alpaca 1-minute option + stock bars (trades)"},
           "frames": frames, "points": points}
    if candles:                     # QQQ 1-minute candles, for days the chart has none of
        doc["candles"] = candles
        doc["sources"]["candles"] = report.get("stock_feed")
    if validate and bars:
        report["validation"] = validate_day(day, complete, bars, oi)
    return doc, report


def validate_day(day, complete, bars, oi):
    """Rebuild minutes we DID log, from bars only, and compare with the log."""
    rows = []
    for m in sorted(complete):
        live = logged_gex(day, complete[m])
        reb = rebuild_minute(day, m, bars, oi)
        if not live or not reb:
            continue
        lv, rb = live["levels"], reb["levels"]
        lf = live["gamma_flip"] if live["gamma_flip_method"] == "zero crossing" else None
        rf = reb["gamma_flip"] if reb["gamma_flip_method"] == "zero crossing" else None
        rows.append({
            "call_wall": lv["call_wall"] == rb["call_wall"],
            "call_wall_near": abs(lv["call_wall"] - rb["call_wall"]) <= 1,
            "put_wall": lv["put_wall"] == rb["put_wall"],
            "put_wall_near": abs(lv["put_wall"] - rb["put_wall"]) <= 1,
            "flip_err": abs(lf - rf) if lf is not None and rf is not None else None,
            "net_err": abs(reb["net_gex"] - live["net_gex"]) / abs(live["net_gex"]) if live["net_gex"] else None,
            "vol_err": (abs(reb["net_gex_vol"] - live["net_gex_vol"]) / abs(live["net_gex_vol"])
                        if live.get("net_gex_vol") and reb.get("net_gex_vol") is not None else None),
            "net_sign": (reb["net_gex"] > 0) == (live["net_gex"] > 0),
        })
    if not rows:
        return {"minutes": 0}

    def pct(key):
        return round(100 * sum(1 for r in rows if r[key]) / len(rows), 1)

    def med(key):
        vals = [r[key] for r in rows if r[key] is not None]
        return round(statistics.median(vals), 3) if vals else None

    return {"minutes": len(rows), "call_wall_same_pct": pct("call_wall"),
            "call_wall_within_1_pct": pct("call_wall_near"), "put_wall_same_pct": pct("put_wall"),
            "put_wall_within_1_pct": pct("put_wall_near"), "zero_gamma_median_err": med("flip_err"),
            "net_gex_median_rel_err": med("net_err"), "volume_gex_median_rel_err": med("vol_err"),
            "net_gex_same_sign_pct": pct("net_sign")}


def write_day(doc):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{doc['session_date']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")))
    os.replace(tmp, path)
    return path


def write_index():
    days = sorted(p.stem for p in OUT_DIR.glob("????-??-??.json"))
    (OUT_DIR / "index.json").write_text(json.dumps({"days": days}, separators=(",", ":")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="*", help="YYYY-MM-DD ... or 'today' (default: every day in data/)")
    ap.add_argument("--validate", nargs="*", help="days to accuracy-test (no files written)")
    ap.add_argument("--no-alpaca", action="store_true", help="logged minutes only")
    ap.add_argument("--publish", action="store_true", help="commit the written files (GitHub Actions)")
    args = ap.parse_args()
    for d in (args.days or []) + (args.validate or []):
        if d != "today" and not DAY_RE.fullmatch(d):
            raise SystemExit(f"Not a date: {d!r} (use YYYY-MM-DD)")
    written = []

    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_API_SECRET")
    api = None if args.no_alpaca or not key or not secret else Alpaca(key, secret)
    if not api and not args.no_alpaca:
        print("No Alpaca keys: building logged minutes only.")

    if args.validate is not None:
        if not api:
            raise SystemExit("Validation needs Alpaca keys.")
        reports = []
        for day in args.validate:
            _, rep = build_day(day, api, validate=True)
            print(json.dumps(rep), flush=True)
            reports.append(rep)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "_validation.json").write_text(json.dumps(reports, indent=1))
        if args.publish:
            publish([OUT_DIR / "_validation.json"])
        return

    today = dt.datetime.now(NY).date().isoformat()
    days = args.days or sorted(os.path.basename(p) for p in glob.glob("data/????-??-??"))
    days = [today if d == "today" else d for d in days]
    for day in days:
        try:
            doc, rep = build_day(day, api)
        except Exception as exc:                        # one bad day must not stop the rest
            print(json.dumps({"day": day, "error": str(exc)}), flush=True)
            continue
        if doc["frames"]:
            written.append(write_day(doc))
        print(json.dumps(rep), flush=True)
    write_index()
    if args.publish and written:
        publish(written + [OUT_DIR / "index.json"])


def publish(paths):
    from publish_history import publish as push
    push(Path(__file__).resolve().parent, [Path(p).as_posix() for p in paths])
    print(f"Published {len(paths)} files", flush=True)


if __name__ == "__main__":
    main()
