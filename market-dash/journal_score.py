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
    # flipping: opposite side within 10 min of a losing exit
    flips = [g for g in fills if any(g["side"] != f["side"] and 0 <= g["t0"] - f["t1"] <= 600 for f in losses)]
    chk("No flip within 10 min of a stop", not flips, "%d flips" % len(flips) if flips else "none")
    # adding to a loser: same-side entry while an earlier same-side trade is still open and ends red
    adds = [g for g in fills if any(f is not g and f["side"] == g["side"] and f["t0"] < g["t0"] < f["t1"] and f["pnl"] < 0 for f in fills)]
    chk("Never add to a loser", not adds, "%d adds into red" % len(adds) if adds else "none")
    # stop size: rule is $0.30
    dist = [f["buy"] - f["stop"] for f in fills if f.get("stop")]
    if dist:
        med = sorted(dist)[len(dist) // 2]
        chk("Stops about $0.30", med >= 0.25, "median stop $%.2f under entry" % med)
    elif losses:
        ml = sorted(f["buy"] - f["sell"] for f in losses)[len(losses) // 2]
        chk("Stops about $0.30", ml >= 0.25, "median loser −$%.2f a contract" % ml)
    else:
        chk("Stops about $0.30", None, "no losers")
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
    big = [f for f in fills if f["ct"] > 2]
    chk("Size 2 contracts or less", not big, "%d entries at 3+" % len(big) if big else "1–2 contracts")
    chk("Logged the day", True if note else None, "ramble logged" if note else "logging started Nov 17")

    scorable = [c for c in checks if c["ok"] is not None]
    kept = sum(1 for c in scorable if c["ok"])
    score = round(10.0 * kept / len(scorable), 1) if scorable else None
    best = max(fills, key=lambda f: f["pnl"]) if fills else None
    return {"date": date, "pnl": round(pnl, 2), "trades": n, "wins": len(wins), "losses": len(losses),
            "first": hm(fills[0]["t0"]) if fills else None, "last": hm(max(f["t1"] for f in fills)) if fills else None,
            "best": ("%s %s %s %+.0f" % (hm(best["t0"]), best["side"], best["strike"], best["pnl"])) if best else None,
            "score": score, "kept": kept, "of": len(scorable), "checks": checks,
            "summary": (note or {}).get("summary"), "right": (note or {}).get("right"), "fix": (note or {}).get("fix"),
            "ramble": bool(note)}


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
        json.dump({"rules_since": "2026-11-16", "entries": out[::-1]}, fh, indent=1)
    for e in out:
        print(e["date"], e["trades"], "%+.0f" % e["pnl"], "score", e["score"], "(%d/%d)" % (e["kept"], e["of"]))


if __name__ == "__main__":
    main()
