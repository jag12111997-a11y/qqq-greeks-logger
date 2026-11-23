"""Backfill REAL option prices for contracts whose logger quotes froze (added Nov 22, 2026).

On some mornings the logger's free "indicative" snapshot feed stops updating out-of-the-money strikes: the bid/ask stays the same all
morning while QQQ moves (Nov 19: C682 stuck at 0.31/0.38, P660 at 6.16/6.24). This pulls Alpaca's historical 1-minute option BARS
(real trade prints, OPRA, available for expired contracts) for those contracts and saves them next to the logger files:
    data/<day>/am/qqq_option_bars_<day>.csv   (t_utc, side, strike, open, high, low, close, volume, vwap, trades)
my_replay_build.js uses them in place of a frozen quote line. Bars are trades, not bid/ask: a minute with no trade has no bar.

Usage (repo root, ALPACA_API_KEY / ALPACA_API_SECRET in env):
    python option_bars_backfill.py 2026-11-19 [more days]   # every contract the user traded those days
    python option_bars_backfill.py --auto                   # days in the last 30 logged days where a traded contract froze
Prints the files it wrote, one per line (for publish_history)."""
import csv
import datetime as dt
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import gex_history as G  # noqa: E402  (reuses its Alpaca client, session bounds and OCC symbols)


def logger_rows(day, side):
    f = ROOT / "data" / day / "am" / f"qqq_greeks_{'calls' if side == 'call' else 'puts'}_{day}_am.csv"
    if not f.exists():
        return {}
    out = defaultdict(list)
    with open(f) as fh:
        for r in csv.DictReader(fh):
            if r.get("expiration") == day and r.get("bid"):
                try:
                    out[float(r["strike"])].append((r["run_time"], r["bid"], r["ask"], float(r.get("spot") or 0)))
                except ValueError:
                    pass
    return out


def frozen(rows):
    """Same bid/ask on every snapshot while QQQ moved $1+ = the feed stopped updating this strike."""
    if len(rows) < 5:
        return True
    spots = [x[3] for x in rows if x[3]]
    return len({(x[1], x[2]) for x in rows}) <= 1 and spots and max(spots) - min(spots) >= 1.0


def trades():
    try:
        return json.loads((ROOT / "market-dash" / "my_trades_list.json").read_text())
    except (OSError, ValueError):
        return []


def targets(days):
    want = defaultdict(set)
    for t in trades():
        if t.get("date") in days and t.get("sym") != "XSP" and t.get("strike") is not None:
            want[t["date"]].add((t["side"], float(t["strike"])))
    return want


def auto_days():
    logged = sorted(p.name for p in (ROOT / "data").glob("20*") if p.is_dir())[-30:]
    out = []
    by_day = defaultdict(list)
    for t in trades():
        if t.get("date") in logged and t.get("sym") != "XSP":
            by_day[t["date"]].append(t)
    for day, ts in by_day.items():
        if (ROOT / "data" / day / "am" / f"qqq_option_bars_{day}.csv").exists():
            continue
        cache = {s: logger_rows(day, s) for s in ("call", "put")}
        if any(frozen(cache[t["side"]].get(float(t["strike"]), [])) for t in ts):
            out.append(day)
    return sorted(out)


def main():
    args = sys.argv[1:]
    days = auto_days() if args == ["--auto"] else args
    if not days:
        return
    api = G.Alpaca(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_API_SECRET"])
    for day, contracts in sorted(targets(days).items()):
        syms = {G.occ(day, side, k): (side, k) for side, k in contracts}
        bars = api.option_bars(sorted(syms), day)
        f = ROOT / "data" / day / "am" / f"qqq_option_bars_{day}.csv"
        f.parent.mkdir(parents=True, exist_ok=True)
        rows = {}
        if f.exists():
            with open(f) as fh:
                for r in csv.DictReader(fh):
                    rows[(r["t_utc"], r["side"], r["strike"])] = r
        for sym, bl in bars.items():
            side, k = syms.get(sym, (None, None))
            if side is None:
                continue
            for b in bl:
                key = (b["t"], side, f"{k:g}")
                rows[key] = {"t_utc": b["t"], "side": side, "strike": f"{k:g}", "open": b.get("o"), "high": b.get("h"),
                             "low": b.get("l"), "close": b.get("c"), "volume": b.get("v"), "vwap": b.get("vw"), "trades": b.get("n")}
        if not rows:
            print(f"no bars for {day}", file=sys.stderr)
            continue
        with open(f, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["t_utc", "side", "strike", "open", "high", "low", "close", "volume", "vwap", "trades"])
            w.writeheader()
            for key in sorted(rows):
                w.writerow(rows[key])
        print(f.relative_to(ROOT).as_posix())
        print(f"{day}: {len(bars)} of {len(syms)} contracts had trades, {len(rows)} bars", file=sys.stderr)


if __name__ == "__main__":
    main()
