"""Daily trading log + discipline score for the Journal tab (Nov 17, 2026).

Inputs (all in market-dash/):
  my_trades_list.json   - your filled trades from the Webull CSV export (times in ET)
  journal_fills.json    - days not in the CSV yet, pulled from Webull order history (times in PT,
                          includes cancelled entries so chasing can be checked)
  journal_notes.json    - your rambles, summarised: {date: {summary, right, fix}}
Output:
  journal_log.json      - one card per trading day: P&L, trades, 10 rule checks, score, notes

Score = rules kept / rules that could be checked that day, out of 10. P&L is shown, never scored.
Rules were written Nov 16; earlier days are scored against them for reference only.
"""
import json, os, collections

HERE = os.path.dirname(os.path.abspath(__file__))
P = lambda f: os.path.join(HERE, f)
START = "2026-11-01"


def load(f, default):
    try:
        with open(P(f)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


_MIN = None
def qqq_bars(date):
    """1-min QQQ bars for the day keyed by minute after 6:30 PT -> (open, close). history/<day>.json first, then qqq_candle_history.json."""
    global _MIN
    import datetime as dt
    t0 = int(dt.datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()) + int(14.5 * 3600)   # 6:30 PT (PST)
    c = (load(os.path.join("history", date + ".json"), {}) or {}).get("candles")
    if not c:
        if _MIN is None:
            _MIN = load("qqq_candle_history.json", {}).get("minute", [])
        c = [b for b in _MIN if t0 <= b["time"] < t0 + 390 * 60]
    out = {}
    for b in c:
        k = (b["time"] - t0) // 60
        if 0 <= k < 390:
            out[k] = (b["open"], b["close"])
    return out


def candle_dir(bars, t0):
    """+1 green / -1 red / 0 flat: the 20-min candle the entry sits in, open -> price at entry; if it's barely moved (<$0.05), the candle before."""
    m = int((t0 - 6.5 * 3600) // 60)
    k = m // 20
    for kk in (k, k - 1):
        if kk < 0:
            break
        first = bars.get(kk * 20)
        # Nov 28 fix: last COMPLETED minute (m-1) — the entry minute's close is after the fill
        last = next((bars[j] for j in range(min(m - 1, kk * 20 + 19), kk * 20 - 1, -1) if j in bars), None)
        if first and last:
            d = last[1] - first[0]
            if abs(d) >= 0.05 or kk < k:
                return 1 if d > 0 else -1 if d < 0 else 0
    return 0


def hm(sec):   # seconds after midnight PT -> "7:31"
    m = int(sec // 60)
    return "%d:%02d" % ((m // 60 - 1) % 12 + 1, m % 60)


def days_from_csv():
    out = collections.defaultdict(list)
    for t in load("my_trades_list.json", []):
        if t["date"] < START:
            continue
        h, m, s = [int(x) for x in t["tin"].split(":")]
        h2, m2, s2 = [int(x) for x in t["tout"].split(":")]
        out[t["date"]].append({"side": t["side"], "strike": t["strike"], "ct": int(t.get("ct") or 1),
                               "t0": (h - 3) * 3600 + m * 60 + s, "t1": (h2 - 3) * 3600 + m2 * 60 + s2,
                               "buy": float(t["paid"]), "sell": float(t["sold"]) if t.get("sold") is not None else 0.0,
                               "pnl": float(t["pnl"]), "stop": None, "expired": t.get("why") == "expired"})
    return out


def score_day(date, fills, cancels, note):
    fills = sorted(fills, key=lambda f: f["t0"])
    n = len(fills)
    pnl = sum(f["pnl"] for f in fills)
    wins = [f for f in fills if f["pnl"] > 0.5]
    losses = [f for f in fills if f["pnl"] < -0.5]
    checks = []

    def chk(rule, ok, why):
        checks.append({"rule": rule, "ok": ok, "why": why})

    chk("8 trades or fewer", n <= 8, "%d trades" % n)
    # with the trend: calls only while the 20-min QQQ candle is green, puts only while it's red (rule changed Nov 17)
    bars = qqq_bars(date)
    if bars:
        vs = [f for f in fills if candle_dir(bars, f["t0"]) * (1 if f["side"] in ("C", "call") else -1) < 0]
        chk("No entry against the 20-min candle", not vs,
            ("%d against: %s" % (len(vs), ", ".join("%s %s" % (hm(f["t0"]), "call" if f["side"] in ("C", "call") else "put") for f in vs[:4]))) if vs else "all with the candle")
    else:
        chk("No entry against the 20-min candle", None, "no QQQ bars yet")
    per = collections.Counter(int((f["t0"] - 6.5 * 3600) // 1200) for f in fills)
    over = sorted(k for k, v in per.items() if v > 2)
    chk("Max 2 entries per 20-min candle", not over,
        ("candle %s had %s" % (", ".join("#%d" % (k + 1) for k in over), ", ".join(str(per[k]) for k in over))) if over else "max %d" % max(per.values()))
    # adding to a loser: same-side entry while an earlier same-side trade is still open and ends red
    adds = [g for g in fills if any(f is not g and f["side"] == g["side"] and f["t0"] < g["t0"] < f["t1"] and f["pnl"] < 0 for f in fills)]
    chk("Never add to a loser", not adds, "%d adds into red" % len(adds) if adds else "none")
    # stop size: $0.15–0.20 (Nov 17: wider stops lost more on your entries — see the stop study)
    dist = [f["buy"] - f["stop"] for f in fills if f.get("stop")]
    R = "Stops $0.15–0.20"
    if dist:
        med = sorted(dist)[len(dist) // 2]
        chk(R, 0.12 <= med <= 0.22, "median stop $%.2f under entry" % med)
    elif losses:
        stopped = [f for f in losses if not f.get("expired")] or losses   # an expired lottery ticket isn't a stop
        ml = sorted(f["buy"] - f["sell"] for f in stopped)[len(stopped) // 2]
        chk(R, 0.12 <= ml <= 0.22, "median loser −$%.2f a contract" % ml)
    else:
        chk(R, None, "no losers")
    # daily stop: no new entry after 3 losses in a row or −$75
    run, cum, broke, why = 0, 0.0, False, "kept"
    for f in sorted(fills, key=lambda x: x["t1"]):
        later = [g for g in fills if g["t0"] > f["t1"]]
        run = run + 1 if f["pnl"] < -0.5 else 0
        cum += f["pnl"]
        if (run >= 3 or cum <= -75) and later and not broke:
            broke, why = True, "kept trading at %s after %s" % (hm(f["t1"]), "3 losses in a row" if run >= 3 else "−$75")
    chk("Done after 3 losses in a row or −$75", not broke, why)
    # chasing: needs cancelled entries (Webull pull days only)
    if cancels is None:
        chk("No chasing ($0.10+ past entry)", None, "not in the CSV export")
    else:
        ch = [c for c in cancels for f in fills if f["side"] == c["side"] and 0 <= f["t0"] - c["t"] <= 120 and f["buy"] - c["lim"] >= 0.10]
        chk("No chasing ($0.10+ past entry)", not ch, "%d chased fills" % len(ch) if ch else "none")
    # open whiplash: 3+ entries in 6:30–6:45 with both sides
    early = [f for f in fills if 6.5 * 3600 <= f["t0"] < 6.75 * 3600]
    whip = len(early) >= 3 and len(set(f["side"] for f in early)) > 1
    chk("Waited for a setup (no open whiplash)", not whip, "%d entries 6:30–6:45, both sides" % len(early) if whip else "calm open")
    # held a winner
    held = [f for f in wins if f["t1"] - f["t0"] >= 480]
    chk("Held a winner (8+ min)", bool(held) if wins else False,
        ("held %s for %d min, %+.0f" % (hm(held[0]["t0"]), (held[0]["t1"] - held[0]["t0"]) // 60, held[0]["pnl"])) if held else ("winners cut early" if wins else "no winners"))
    # Nov 23: sat out on time — once you're up $60+ and the 20-min candles have flipped colour (the last two FINISHED candles are opposite
    # colours = the tape is rotating, not trending), stop. Agreed Nov 19 from Nov 12 / Nov 18: the money came early, half went back in the chop.
    # The stop moment = the first trade exit (or candle close) where both are true; any entry after it fails the check.
    def col(kk):
        a, b = bars.get(kk * 20), next((bars[j] for j in range(kk * 20 + 19, kk * 20 - 1, -1) if j in bars), None)
        return None if (kk < 0 or not a or not b) else (1 if b[1] > a[0] else -1)
    def flipped(sec):                       # last two finished 20-min candles at this moment are opposite colours
        k = int((sec - 6.5 * 3600) // 1200)
        a, b = col(k - 2), col(k - 1)
        return a is not None and b is not None and a != b
    R2 = "Sat out on time (+$60 on a flipping tape)"
    if not bars:
        chk(R2, None, "no QQQ bars yet")
    else:
        ev = sorted([(f["t1"], f["pnl"]) for f in fills] + [(6.5 * 3600 + 1200 * k, 0.0) for k in range(2, 20)])   # exits + every candle close
        cum, hit = 0.0, None
        for t, p in ev:
            cum += p
            if cum >= 60 and flipped(t) and t < 9 * 3600:
                hit = (t, cum)
                break
        if not hit:
            chk(R2, None, "never up $60 on a flipping tape")
        else:
            after = [g for g in fills if g["t0"] > hit[0]]
            chk(R2, not after, ("up %+.0f at %s with the candles flipping, then %d more trade%s (%+.0f)" % (hit[1], hm(hit[0]), len(after), "" if len(after) == 1 else "s", sum(g["pnl"] for g in after)))
                if after else "up %+.0f at %s with the candles flipping — stopped" % (hit[1], hm(hit[0])))
    big = [f for f in fills if f["ct"] > 2]
    chk("Size 2 contracts or less", not big, "%d entries at 3+" % len(big) if big else "1–2 contracts")
    chk("Logged the day", True if note else None, "ramble logged" if note else "logging started Nov 17")

    scorable = [c for c in checks if c["ok"] is not None]
    kept = sum(1 for c in scorable if c["ok"])
    score = round(10.0 * kept / len(scorable), 1) if scorable else None
    best = max(fills, key=lambda f: f["pnl"]) if fills else None
    big2 = [f for f in fills if f["ct"] >= 2]   # Nov 20: entries with size (one entry = one trade, tagged ×N)
    sized = {"n": len(big2), "pnl": round(sum(f["pnl"] for f in big2), 2), "wins": sum(1 for f in big2 if f["pnl"] > 0.5),
             "list": ["%s %s%s ×%d %+.0f" % (hm(f["t0"]), "C" if f["side"] in ("C", "call") else "P", int(float(f["strike"])), f["ct"], f["pnl"]) for f in big2]}
    return {"date": date, "pnl": round(pnl, 2), "trades": n, "wins": len(wins), "losses": len(losses),
            "first": hm(fills[0]["t0"]) if fills else None, "last": hm(max(f["t1"] for f in fills)) if fills else None,
            "best": ("%s %s %s %+.0f" % (hm(best["t0"]), best["side"], best["strike"], best["pnl"])) if best else None,
            "score": score, "kept": kept, "of": len(scorable), "checks": checks,
            "summary": (note or {}).get("summary"), "right": (note or {}).get("right"), "fix": (note or {}).get("fix"),
            "ramble": bool(note), "sized": sized}


def weeks_from(days, out):
    """Nov 20: one row per Mon–Fri week for the Week-by-week table (same data as the day cards)."""
    import datetime as dt
    by = collections.OrderedDict()
    for e in out:
        d = dt.date.fromisoformat(e["date"])
        by.setdefault((d - dt.timedelta(days=d.weekday())).isoformat(), []).append(e)
    rows = []
    for wk, es in by.items():
        F = [f for e in es for f in days[e["date"]]]
        pn = sorted((f["pnl"] for f in F), reverse=True)
        first20 = sum(f["pnl"] for f in F if f["t0"] < 6.5 * 3600 + 1200)
        after8 = 0.0
        lows = []
        for e in es:
            fs = sorted(days[e["date"]], key=lambda f: f["t0"])
            after8 += sum(f["pnl"] for f in fs[8:])
            run = low = 0.0
            for f in sorted(fs, key=lambda f: f["t1"]):
                run += f["pnl"]
                low = min(low, run)
            lows.append(low)
        rules = collections.OrderedDict()
        for e in es:
            for c in e["checks"]:
                if c["ok"] is not None:
                    r = rules.setdefault(c["rule"], [0, 0])
                    r[0] += 1 if c["ok"] else 0
                    r[1] += 1
        sc = [e["score"] for e in es if e["score"] is not None]
        rows.append({"week": wk, "days": len(es), "green": sum(1 for e in es if e["pnl"] > 0),
                     "pnl": round(sum(e["pnl"] for e in es), 2), "trades": len(F),
                     "wins": sum(1 for f in F if f["pnl"] > 0.5),
                     "score": round(sum(sc) / len(sc), 1) if sc else None,
                     "kept": sum(e["kept"] for e in es), "of": sum(e["of"] for e in es),
                     "best2": round(sum(pn[:2]), 2), "rest": round(sum(pn[2:]), 2),
                     "first20": round(first20, 2), "after8": round(after8, 2),
                     "days8": sum(1 for e in es if e["trades"] <= 8), "worst_low": round(min(lows), 2),
                     "daily": [{"date": e["date"], "pnl": e["pnl"], "score": e["score"], "trades": e["trades"]} for e in es],
                     "rules": rules,
                     "sized": {"n": sum(1 for f in F if f["ct"] >= 2), "pnl": round(sum(f["pnl"] for f in F if f["ct"] >= 2), 2),
                               "wins": sum(1 for f in F if f["ct"] >= 2 and f["pnl"] > 0.5)},
                     "single": {"n": sum(1 for f in F if f["ct"] < 2), "pnl": round(sum(f["pnl"] for f in F if f["ct"] < 2), 2),
                                "wins": sum(1 for f in F if f["ct"] < 2 and f["pnl"] > 0.5)}})
    return rows[::-1]


def main():
    days = days_from_csv()
    extra = load("journal_fills.json", {})
    notes = load("journal_notes.json", {})
    cancels_by = {}
    for d, v in extra.items():   # Webull pulls replace the CSV for that day (they also carry stops + cancels)
        days[d] = v["fills"]
        cancels_by[d] = v.get("cancels", [])
    out = [score_day(d, days[d], cancels_by.get(d), notes.get(d)) for d in sorted(days) if days[d]]
    with open(P("journal_log.json"), "w") as fh:
        json.dump({"rules_since": "2026-11-16", "rules_updated": "2026-11-17",
                   # Nov 25: the user's exit rule (agreed in chat) — shown on the Daily Log and on every card from this date
                   "exit_rule": {"since": "2026-11-25", "text": "TESTING, NOT PROVEN (Nov 28 independent check: with realistic stop fills the ladder and the +2.00 target did not beat a plain stop; being re-tested on full-day option prices). What held: a $0.15 stop set the moment you enter. Under test: LADDER + 2.00 — stop $0.15 under entry and a take-profit order at +2.00. "
                                 "At +0.30 stop to breakeven, at +0.60 lock +0.30, keep stepping 0.30 behind. At +2.00 take it all; "
                                 "if the trend holds (pullback under $1) re-enter a fresh $0.80–1.20 contract the same way. "
                                 "Trend day = trade with it only, no fades. Flat first 15 min = 2 trades max. Daily stop −$75."},
                   "entries": out[::-1],
                   "weeks": weeks_from(days, out)}, fh, indent=1)
    for e in out:
        print(e["date"], e["trades"], "%+.0f" % e["pnl"], "score", e["score"], "(%d/%d)" % (e["kept"], e["of"]))


if __name__ == "__main__":
    main()
