"""Untouched hold-out data for Baby 3v3 (added Nov 23, 2026, Claude).

Every Baby experiment so far used Aug-Nov 2026, and Nov 9-20 was reused ~6 times. This pulls an older window nobody has tuned on:
  research/holdout/qqq_daily.csv                    QQQ daily bars 2025-01-01 -> end (for gap / drawdown / 20-day / vol-percentile features)
  research/holdout/<day>_qqq.csv.gz                 QQQ 1-min bars 9:30-13:15 ET (sip feed, raw)
  research/holdout/<day>_opt.csv.gz                 0DTE QQQ option 1-min bars (trade prints), strikes open-12..open+12 both sides, 9:30-13:15 ET
Bars are trades, not bid/ask. Nothing on the website reads these files.

Usage (repo root, ALPACA_API_KEY / ALPACA_API_SECRET in env):
    python research_holdout_bars.py 2026-01-02 2026-07-13
    python research_holdout_bars.py vix only                (just the Cboe VIX history)
Prints written paths. The workflow uploads research/holdout/ as a run artifact; nothing is committed."""
import csv
import datetime as dt
import gzip
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import gex_history as G  # noqa: E402

OUT = ROOT / "research" / "holdout"
NY = G.NY


def window(day):
    d = dt.date.fromisoformat(day)
    return (dt.datetime(d.year, d.month, d.day, 9, 30, tzinfo=NY), dt.datetime(d.year, d.month, d.day, 13, 15, tzinfo=NY))


def stock(api, start, end, timeframe):
    out, token = [], None
    while True:
        p = {"timeframe": timeframe, "start": G.iso(start), "end": G.iso(end), "feed": "sip", "adjustment": "raw", "limit": 10000, "sort": "asc"}
        if token:
            p["page_token"] = token
        j = api.get(f"{G.DATA}/v2/stocks/QQQ/bars", p)
        out.extend(j.get("bars") or [])
        token = j.get("next_page_token")
        if not token:
            return out


def options(api, syms, start, end):
    bars, token = {}, None
    while True:
        p = {"symbols": ",".join(syms), "timeframe": "1Min", "start": G.iso(start), "end": G.iso(end), "limit": 10000, "sort": "asc"}
        if token:
            p["page_token"] = token
        j = api.get(f"{G.DATA}/v1beta1/options/bars", p)
        for s, rows in (j.get("bars") or {}).items():
            bars.setdefault(s, []).extend(rows or [])
        token = j.get("next_page_token")
        if not token:
            return bars


def gz(path, fields, rows):
    with gzip.open(path, "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def vix():
    """Cboe's public daily VIX history (open/high/low/close since 1990) -> research/holdout/vix_history.csv."""
    import requests
    r = requests.get("https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv", timeout=60)
    r.raise_for_status()
    f = OUT / "vix_history.csv"
    f.write_text(r.text)
    print(f.relative_to(ROOT).as_posix())


def main():
    first, last = sys.argv[1], sys.argv[2]
    OUT.mkdir(parents=True, exist_ok=True)
    vix()
    if first == "vix":
        return
    api = G.Alpaca(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_API_SECRET"])
    OUT.mkdir(parents=True, exist_ok=True)
    s = dt.datetime(2025, 1, 1, tzinfo=NY)
    e = dt.datetime.combine(dt.date.fromisoformat(last), dt.time(23, 0), tzinfo=NY)
    daily = stock(api, s, e, "1Day")
    f = OUT / "qqq_daily.csv"
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        for b in daily:
            w.writerow([b["t"][:10], b["o"], b["h"], b["l"], b["c"], b["v"]])
    print(f.relative_to(ROOT).as_posix())
    days = [b["t"][:10] for b in daily if first <= b["t"][:10] <= last]
    for day in days:
        fq, fo = OUT / f"{day}_qqq.csv.gz", OUT / f"{day}_opt.csv.gz"
        if fq.exists() and fo.exists():
            continue
        a, b = window(day)
        bars = stock(api, a, b, "1Min")
        if len(bars) < 100:
            print(f"{day}: only {len(bars)} stock bars, skipped", file=sys.stderr)
            continue
        gz(fq, ["t", "o", "h", "l", "c", "v", "vw", "n"], [{k: x.get(k) for k in ("t", "o", "h", "l", "c", "v", "vw", "n")} for x in bars])
        mid = round(float(bars[0]["o"]))
        syms = {}
        for k in range(mid - 12, mid + 13):
            for side in ("call", "put"):
                syms[G.occ(day, side, k)] = (side, k)
        ob = options(api, sorted(syms), a, b)
        rows = []
        for sym, bl in ob.items():
            side, k = syms[sym]
            for x in bl:
                rows.append({"t": x["t"], "side": side, "strike": k, "o": x.get("o"), "h": x.get("h"), "l": x.get("l"), "c": x.get("c"),
                             "v": x.get("v"), "vw": x.get("vw"), "n": x.get("n")})
        rows.sort(key=lambda r: (r["t"], r["side"], r["strike"]))
        gz(fo, ["t", "side", "strike", "o", "h", "l", "c", "v", "vw", "n"], rows)
        print(fq.relative_to(ROOT).as_posix())
        print(fo.relative_to(ROOT).as_posix())
        print(f"{day}: {len(bars)} stock bars, {len(ob)}/{len(syms)} contracts traded, {len(rows)} option bars", file=sys.stderr)


if __name__ == "__main__":
    main()
