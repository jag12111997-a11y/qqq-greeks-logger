"""Home run setup: detector, paper log and the live box's context file.

Rules (Rulebook v3.2, "Tulip Mania"):
  * Levels: walls, max +/- gamma, zero gamma, OI magnet, yesterday's high / low / close.
    Levels within 15c merge; a level counts once it has sat there 15 minutes ("sticky").
  * U = expected 30-min move: half the median range for this half hour on prior days,
    half today's last 30 minutes.
  * A test: price comes within 0.06U of the nearest floor (or ceiling) after being 0.4U+ away.
  * Home run: a test Tue-Fri, 10:35-11:34 ET (7:35-8:34 PT), with price near VWAP
    (-0.37U to +0.05U on the side of the trade). Buy a 0DTE call at a floor, a put at a ceiling,
    1 strike out of the money, at the ask. Stop -$0.30, take profit +$2.00, out within 60 min.
  * v3.2 checks (each passes or fails; they size the trade, they don't block it):
      1. wall clear: not a yesterday-only level with a GEX level 15-50c past it;
      2. volume drying up: last 5 min under 1.0x the 30 min before;
      3. 1-min turn: SMA 4 above SMA 7 for a call (below for a put).
    Chips: 3/3 = 5 (ladder: a full-setup loss steps the next full setup down one, min 2; a full win steps it back up),
    2/3 = 2, 1 or 0 = 1. The paper test trades 1 chip.
  * Day rules: at most 2 trades, the next only after the last closed; one loss ends the day.

usage:
  python home_run.py --match            check the detector against the backtest's setups
  python home_run.py --write            write market-dash/home_run_log.json and home_run_ctx.json
"""
import argparse
import csv
import datetime as dt
import glob
import json
import os
import statistics as st

HIST = os.path.join("market-dash", "history")
DATA = "data"
LOG_PATH = os.path.join("market-dash", "home_run_log.json")
CTX_PATH = os.path.join("market-dash", "home_run_ctx.json")
SKIP = {"2026-07-14", "2026-07-15"}            # option timestamps there don't line up with price

TYPES = ["gamma_flip", "call_wall", "put_wall", "max_pos_gamma", "max_neg_gamma", "oi_magnet"]
MERGE, PERSIST = 0.15, 15
K_TOUCH, K_AWAY, K_MINBOX = 0.06, 0.40, 0.30
VWAP_LO, VWAP_HI = -0.37, 0.05
WALL_NEAR, WALL_FAR = 0.15, 0.50               # check 1: a yesterday-only level with a GEX level 15-50c past it fails
VOL_FAST, VOL_SLOW, VOL_MAX = 5, 30, 1.0         # check 2: volume over the last 5 min must be under 1.0x the 30 min before
SMA_FAST, SMA_SLOW = 4, 7                        # check 3: 1-min SMA 4 above SMA 7 for a call (below for a put)
CHIPS_FULL_MAX, CHIPS_FULL_MIN, CHIPS_ONE, CHIPS_LOW = 5, 2, 2, 1   # chip ladder (v3.2)
WIN_START, WIN_END = 65, 124                    # minutes after 9:30 ET
TRADE_DAYS = (1, 2, 3, 4)                       # Tue..Fri (Monday = 0)
STOP, TARGET, MAX_HOLD = 0.30, 2.00, 60
MAX_TRADES = 2
FROZEN = "2026-10-08"                           # rules were built on data through Oct 8: later days are unseen (paper test)


# ---------- shared pieces ----------
def clusters(vals):
    out = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= MERGE:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out]


def rng(hi, lo, a, b):
    return max(hi[a:b]) - min(lo[a:b])


def slot_ranges(candles):
    """30-min ranges of one day, keyed by the half hour they end in."""
    hi = [c["high"] for c in candles]; lo = [c["low"] for c in candles]
    out = {}
    for e in range(30, len(candles) + 1):
        out.setdefault((e - 1) // 30, []).append(rng(hi, lo, e - 30, e))
    return out


def unit(hi, lo, m, slot_hist):
    h = slot_hist.get(m // 30, [])
    slot = st.median(h) if len(h) >= 5 else None
    real = rng(hi, lo, max(0, m - 30), m) * (30 / min(30, m)) if m >= 10 else None
    if slot and real:
        return 0.5 * slot + 0.5 * real
    return slot or real


def wall_beyond(L, side, p):
    """v3.1: if L is not itself a GEX level and a GEX level sits 15-50c past it (the side price would stop us on),
    return that GEX level: price tends to overshoot yesterday's level to tag the wall, so the setup is skipped."""
    gex = [p[t] for t in TYPES if p and p.get(t)]
    if any(abs(x - L) <= WALL_NEAR for x in gex):
        return None
    sg = 1 if side == "ceil" else -1
    past = sorted(sg * (x - L) for x in gex if WALL_NEAR < sg * (x - L) <= WALL_FAR)
    return round(L + sg * past[0], 2) if past else None


def checks(candles, m, side, wall):
    """v3.2: the three checks at the touch minute m (all from bars before m). Returns the values, which passed, and the tier."""
    vol = [c["volume"] for c in candles]; cl = [c["close"] for c in candles]
    fast = vol[max(0, m - VOL_FAST):m]; slow = vol[max(0, m - VOL_FAST - VOL_SLOW):max(0, m - VOL_FAST)]
    ratio = (sum(fast) / len(fast)) / (sum(slow) / len(slow)) if fast and slow and sum(slow) else None
    s4 = sum(cl[m - SMA_FAST:m]) / SMA_FAST if m >= SMA_FAST else None
    s7 = sum(cl[m - SMA_SLOW:m]) / SMA_SLOW if m >= SMA_SLOW else None
    sg = 1 if side == "floor" else -1
    ok = {"wall_clear": wall is None, "vol_ok": ratio is not None and ratio < VOL_MAX,
          "turn_ok": s4 is not None and s7 is not None and sg * (s4 - s7) > 0}
    n = sum(ok.values())
    return {"vol_ratio": None if ratio is None else round(ratio, 3), "sma4": None if s4 is None else round(s4, 3),
            "sma7": None if s7 is None else round(s7, 3), **ok, "checks_n": n, "tier": "full" if n == 3 else "one" if n == 2 else "low"}


def detect_day(candles, points, frames, prev, slot_hist, weekday):
    """Every floor/ceiling test of one day, with home-run flags. points/frames: minute -> record."""
    n = len(candles)
    hi = [c["high"] for c in candles]; lo = [c["low"] for c in candles]; cl = [c["close"] for c in candles]
    mins = [c["time"] // 60 for c in candles]
    yday = [prev["high"], prev["low"], prev["close"]] if prev else []
    raw_cache = {}

    def raw(m):
        if m not in raw_cache:
            p = points.get(mins[m])
            raw_cache[m] = clusters([p[t] for t in TYPES if p and p.get(t)] + yday)
        return raw_cache[m]

    def sticky(m):
        if m < PERSIST:
            return []
        return [L for L in raw(m) if all(any(abs(L - x) <= MERGE for x in raw(k)) for k in range(m - PERSIST + 1, m))]

    pv = vv = 0.0; vwap = []
    for c in candles:
        pv += (c["high"] + c["low"] + c["close"]) / 3 * c["volume"]; vv += c["volume"]
        vwap.append(pv / vv if vv else c["close"])

    tests, last = [], {"ceil": [], "floor": []}
    for m in range(1, n):
        U = unit(hi, lo, m, slot_hist)
        if not U:
            continue
        touch, away, minbox = K_TOUCH * U, K_AWAY * U, K_MINBOX * U
        lv = sticky(m - 1); pc = cl[m - 1]
        ups = [L for L in lv if L > pc + touch]; dns = [L for L in lv if L < pc - touch]
        ceil = min(ups) if ups else None; floor = max(dns) if dns else None
        if ceil is not None and floor is not None and ceil - floor < minbox:
            continue
        for side, L in (("ceil", ceil), ("floor", floor)):
            if L is None:
                continue
            if not (hi[m] >= L - touch if side == "ceil" else lo[m] <= L + touch):
                continue
            prior = [e for e in last[side] if abs(e[0] - L) <= MERGE]
            start = (prior[-1][1] + 1) if prior else max(0, m - 30)
            win = cl[start:m]
            if not win or not (min(win) <= L - away if side == "ceil" else max(win) >= L + away):
                continue
            last[side].append((L, m))
            vdist = ((pc - vwap[m - 1]) / U) * (-1 if side == "ceil" else 1)
            fr = frames.get(mins[m - 1])
            wall = wall_beyond(L, side, points.get(mins[m - 1]))
            hr = (weekday in TRADE_DAYS and WIN_START <= m < WIN_END and VWAP_LO <= vdist < VWAP_HI
                  and m >= 16 and bool(fr and fr.get("d")))
            ck = checks(candles, m, side, wall)
            tests.append({"m": m, "side": side, "kind": "call" if side == "floor" else "put", "L": round(L, 2),
                          "U": round(U, 3), "test_no": len(prior) + 1, "vwap": round(vwap[m - 1], 2),
                          "vwap_dist_U": round(vdist, 3), "wall_beyond": wall, **ck, "home_run": hr})
    return tests


def load_history():
    days = []
    for f in sorted(glob.glob(os.path.join(HIST, "20*.json"))):
        d = json.load(open(f))
        if d.get("candles"):
            days.append(d)
    return days


def utc_offset_h(day):
    """Eastern time: UTC-4 from the 2nd Sunday of March to the 1st Sunday of November, UTC-5 the rest of the year."""
    mar = dt.date(day.year, 3, 8); mar += dt.timedelta(days=(6 - mar.weekday()) % 7)
    nov = dt.date(day.year, 11, 1); nov += dt.timedelta(days=(6 - nov.weekday()) % 7)
    return 4 if mar <= day < nov else 5


def et_min(stamp):
    t = dt.datetime.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S")
    return t.hour * 60 + t.minute - utc_offset_h(t.date()) * 60 - 570


def option_book(date):
    """(kind, strike) -> {minute: (bid, ask, spot)} from the logged 0DTE quotes."""
    book = {}
    for f in sorted(glob.glob(os.path.join(DATA, date, "*", "*.csv"))):
        name = os.path.basename(f)
        with open(f, newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("expiration") != date:
                    continue
                kind = "call" if "call" in name else ("put" if "put" in name else None)
                try:
                    if kind is None:
                        kind = "call" if float(r["delta"]) > 0 else "put"
                    b, a, k = float(r["bid"]), float(r["ask"]), float(r["strike"])
                    m = et_min(r["run_time"])
                except (TypeError, ValueError, KeyError):
                    continue
                if 0 <= m < 390 and a > 0:
                    book.setdefault((kind, k), {})[m] = (b, a, float(r.get("spot") or 0))
    return book


def plan_trade(book, t):
    """1-OTM contract at the ask at the test minute; exits on the bid."""
    m = t["m"]
    spots = [v[m][2] for v in book.values() if m in v and v[m][2]]
    if not spots:
        return None
    atm = min(sorted({k for (_, k) in book}), key=lambda k: abs(k - spots[0]))
    k = atm + 1 if t["kind"] == "call" else atm - 1   # 1 OTM (~$0.70-1.20, how the user buys)
    ser = book.get((t["kind"], k), {})
    if m not in ser:
        return None
    b, a, _ = ser[m]
    if a < 0.10 or b <= 0 or (a - b) / a > 0.35:
        return None
    path = [(j, ser[j][0]) for j in sorted(ser) if m < j <= m + MAX_HOLD]
    if not path:
        return None
    px, j, why = path[-1][1], path[-1][0], "60-min exit"
    for jj, bb in path:
        if bb <= a - STOP:
            px, j, why = bb, jj, "stop"; break
        if bb >= a + TARGET:
            px, j, why = bb, jj, "take profit"; break
    return {"strike": k, "entry": a, "exit": round(px, 2), "exit_min": j, "held": j - m, "exit_reason": why,
            "pnl": round(100 * (px - a))}


def hm(m, offset=0):
    t = 570 + m - offset
    return f"{(t // 60 - 1) % 12 + 1}:{t % 60:02d}"


def run(one_loss_rule=True):
    days = load_history()
    slot_hist, prev, out, full_chips = {}, None, [], CHIPS_FULL_MAX
    for d in days:
        date = d["session_date"]
        candles = d["candles"]
        points = {p["time"] // 60: p for p in d["points"]}
        frames = {f["t"] // 60: f for f in d["frames"]}
        weekday = dt.date.fromisoformat(date).weekday()
        if date not in SKIP:
            tests = detect_day(candles, points, frames, prev, slot_hist, weekday)
            cands = [t for t in tests if t["home_run"]]
            book = option_book(date) if cands else {}
            free, taken, lost = 0, 0, False
            for t in cands:
                t = dict(t, chips=(full_chips if t["tier"] == "full" else CHIPS_ONE if t["tier"] == "one" else CHIPS_LOW))
                row = {"date": date, "weekday": dt.date.fromisoformat(date).strftime("%a"), "time_et": hm(t["m"]),
                       "time_pt": hm(t["m"], 180), **t}
                if t["m"] < free or taken >= MAX_TRADES or (one_loss_rule and lost):
                    row["status"] = "skipped (day rules)"
                else:
                    tr = plan_trade(book, t)
                    if tr is None:
                        row["status"] = "no quote"
                    else:
                        row.update(tr); row["status"] = "trade"; row["pnl_chips"] = tr["pnl"] * row["chips"]
                        taken += 1; free = tr["exit_min"] + 1; lost = tr["pnl"] <= 0
                        if row["tier"] == "full":   # ladder: only full-setup results move the full-setup size
                            full_chips = max(CHIPS_FULL_MIN, full_chips - 1) if lost else min(CHIPS_FULL_MAX, full_chips + 1)
                out.append(row)
        for k, v in slot_ranges(candles).items():
            slot_hist.setdefault(k, []).extend(v)
        prev = {"high": max(c["high"] for c in candles), "low": min(c["low"] for c in candles), "close": candles[-1]["close"]}
    run.full_chips = full_chips
    return out, slot_hist, prev, days[-1]["session_date"] if days else None


def match():
    sig, _, _, _ = run(one_loss_rule=False)
    mine = {(r["date"], r["time_pt"], r["kind"]): r for r in sig if r["status"] == "trade"}
    ref = json.load(open(os.environ.get("HR_REF", "full_trades.json")))
    ref = {(t["date"], t["time_pt"], t["side"]): t for t in ref if t["pick"] == "1 OTM"}
    both = sorted(set(mine) & set(ref)); only_m = sorted(set(mine) - set(ref)); only_r = sorted(set(ref) - set(mine))
    print(f"backtest setups {len(ref)} · detector {len(mine)} · same {len(both)}")
    for k in only_m: print("  only detector:", k)
    for k in only_r: print("  only backtest:", k)
    diff = [(k, mine[k]["pnl"], ref[k]["pnl_1"]) for k in both if mine[k]["pnl"] != ref[k]["pnl_1"]]
    for k, a, b in diff: print("  P&L differs:", k, a, b)
    print("detector total", sum(mine[k]["pnl"] for k in mine), "· backtest total", sum(t["pnl_1"] for t in ref.values()))
    return not only_m and not only_r and not diff


def write():
    sig, slot_hist, prev, last_day = run(one_loss_rule=True)
    for r in sig:
        r["period"] = "paper" if r["date"] > FROZEN else "backtest"

    def summary(rows):
        trades = [r for r in rows if r["status"] == "trade"]
        w = [r["pnl"] for r in trades if r["pnl"] > 0]; l = [r["pnl"] for r in trades if r["pnl"] <= 0]
        return {"signals": len(rows), "trades": len(trades), "wins": len(w), "pnl": sum(w) + sum(l),
                "pnl_chips": sum(r.get("pnl_chips", 0) for r in trades), "full_setups": sum(r["tier"] == "full" for r in trades),
                "profit_factor": round(sum(w) / -sum(l), 2) if l else None,
                "avg_loss": round(st.mean(l), 1) if l else None}
    paper = summary([r for r in sig if r["period"] == "paper"])
    log = {"generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
           "rules": "Rulebook v3.2 (Tulip Mania)", "contract": "0DTE, 1 OTM · chips: 3/3 = 5 (ladder), 2/3 = 2, 1-0/3 = 1 · paper test trades 1", "data_through": last_day,
           "paper_since": FROZEN,
           "pass_fail": {"min_trades": 8, "min_profit_factor": 1.3, "max_avg_loss": -45,
                         "status": ("not enough signals yet" if paper["trades"] < 8 else
                                    "pass" if (paper["profit_factor"] or 0) >= 1.3 and (paper["avg_loss"] or 0) >= -45 else "review")},
           "summary": {"paper": paper, "backtest": summary([r for r in sig if r["period"] == "backtest"])},
           "signals": sig}
    ctx = {"generated_utc": log["generated_utc"], "for_session_after": last_day,
           "slot_median_30m": {str(k): round(st.median(v), 3) for k, v in sorted(slot_hist.items()) if len(v) >= 5},
           "yesterday": prev, "full_chips_next": run.full_chips,
           "rules": {"merge": MERGE, "persist_min": PERSIST, "touch_U": K_TOUCH, "away_U": K_AWAY, "minbox_U": K_MINBOX,
                     "vwap_lo_U": VWAP_LO, "vwap_hi_U": VWAP_HI, "window_et_min": [WIN_START, WIN_END],
                     "trade_days": list(TRADE_DAYS), "stop": STOP, "target": TARGET, "max_hold_min": MAX_HOLD,
                     "max_trades": MAX_TRADES, "get_ready_U": 0.41, "cancel_through_U": 0.15, "level_types": TYPES,
                     "wall_near": WALL_NEAR, "wall_far": WALL_FAR, "vol_fast": VOL_FAST, "vol_slow": VOL_SLOW, "vol_max": VOL_MAX,
                     "sma_fast": SMA_FAST, "sma_slow": SMA_SLOW, "chips": {"full_max": CHIPS_FULL_MAX, "full_min": CHIPS_FULL_MIN, "one": CHIPS_ONE, "low": CHIPS_LOW}}}
    with open(LOG_PATH, "w") as f:
        json.dump(log, f, separators=(",", ":"))
    with open(CTX_PATH, "w") as f:
        json.dump(ctx, f, separators=(",", ":"))
    print(f"wrote {LOG_PATH} ({len(sig)} signals; paper {paper}) and {CTX_PATH}")
    return [LOG_PATH, CTX_PATH]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", action="store_true")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    if a.match:
        raise SystemExit(0 if match() else 1)
    if a.write:
        write()
