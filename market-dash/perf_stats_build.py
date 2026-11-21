"""Rebuild the Stats-tab blocks of trading_performance_data.js from my_trades_list.json (Nov 20, 2026).

One entry = one trade (a 2-contract entry counts once; its P&L is the full position).
Rebuilds: edges, heatmap, revenge, streaks, max_win_streak, max_loss_streak, r_dist, hold_time.
Leaves alone: equity / monthly / winrate (kept in sync separately) and alpha (needs data the trade list doesn't have).
Sessions (PT): AM 6:30–8:00 · Mid 8:00–10:30 · PM after 10:30. Revenge = within 15 min of a losing exit (same day) AND flipped side or sized up.
Win = P&L > +$0.50, loss = P&L < −$0.50, else break-even.   Run: python3 market-dash/perf_stats_build.py
"""
import json, os, collections, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
F = os.path.join(HERE, "trading_performance_data.js")
PRE = "window.PERF = "
T = json.load(open(os.path.join(HERE, "my_trades_list.json")))
src = open(F).read()
P = json.loads(src[len(PRE):].rstrip().rstrip(";"))

win = lambda t: t["pnl"] > 0.5
loss = lambda t: t["pnl"] < -0.5
hold = lambda t: t["exit_m"] - t["m"]
def hb(t):
    h = hold(t)
    return "<5min" if h < 5 else "5-14min" if h < 15 else "15-29min" if h < 30 else "30-59min" if h < 60 else "60+min"
HB = ["<5min", "5-14min", "15-29min", "30-59min", "60+min"]
def sess(t):
    m = t["m"]
    return "pre" if m < 0 else "morning" if m < 90 else "midday" if m < 240 else "afternoon"
dow = lambda t: dt.date.fromisoformat(t["date"]).strftime("%a")
R = lambda v: round(v, 2)

# edges: side x hold bucket, best first
edges = []
for side in ("call", "put"):
    for b in HB:
        L = [t for t in T if t["side"] == side and hb(t) == b]
        if L:
            gw = sum(t["pnl"] for t in L if t["pnl"] > 0); gl = sum(t["pnl"] for t in L if t["pnl"] < 0)
            edges.append({"option_type": side, "hold_bucket": b, "n": len(L), "wins": sum(map(win, L)),
                          "gross_win": R(gw), "gross_loss": R(gl), "pnl": R(gw + gl)})
edges.sort(key=lambda e: -e["pnl"])

heat = []
for d in ("Mon", "Tue", "Wed", "Thu", "Fri"):
    for s in ("pre", "morning", "midday", "afternoon"):
        L = [t for t in T if dow(t) == d and sess(t) == s]
        if L:
            heat.append({"dow": d, "session": s, "n": len(L), "wins": sum(map(win, L)), "pnl": R(sum(t["pnl"] for t in L))})

# revenge: within 15 min of a losing exit on the same day, and flipped side or sized up
byday = collections.defaultdict(list)
for t in T: byday[t["date"]].append(t)
cat = {}
for d, L in byday.items():
    for t in L:
        prev = [u for u in L if u is not t and loss(u) and 0 <= t["m"] - u["exit_m"] <= 15]
        cat[id(t)] = "revenge" if any(u["side"] != t["side"] or t["ct"] > u["ct"] for u in prev) else "normal"
rev = []
for c in ("normal", "revenge"):
    L = [t for t in T if cat[id(t)] == c]
    p = sum(t["pnl"] for t in L)
    rev.append({"category": c, "n": len(L), "wins": sum(map(win, L)), "pnl": R(p), "avg_pnl": R(p / len(L)) if L else 0})

# streaks in exit order (break-evens end a streak)
seq = sorted(T, key=lambda t: (t["date"], t["exit_m"], t["m"]))
iso = lambda t: t["date"] + "T" + t["tin"]
runs, cur = [], None
for t in seq:
    k = "win" if win(t) else "loss" if loss(t) else None
    if cur and k == cur["streak_type"]:
        cur["streak_len"] += 1; cur["ended"] = iso(t)
    else:
        if cur: runs.append(cur)
        cur = {"streak_type": k, "streak_len": 1, "started": iso(t), "ended": iso(t)} if k else None
if cur: runs.append(cur)
streaks = sorted([r for r in runs if r["streak_len"] >= 4], key=lambda r: -r["streak_len"])
mw = max([r["streak_len"] for r in runs if r["streak_type"] == "win"] or [0])
ml = max([r["streak_len"] for r in runs if r["streak_type"] == "loss"] or [0])
last = runs[-1] if runs else None

BK = [("-$200 to -$100", -1e9, -100), ("-$100 to -$50", -100, -50), ("-$50 to $0", -50, 0), ("$0 to $50", 0, 50),
      ("$50 to $100", 50, 100), ("$100 to $200", 100, 200), ("$200 to $500", 200, 500), ("$500+", 500, 1e9)]
rd = []
for name, lo, hi in BK:
    L = [t for t in T if (lo < t["pnl"] <= hi if lo >= 0 else lo <= t["pnl"] < hi) or (hi == 0 and t["pnl"] == 0)]
    rd.append({"bucket": name, "n": len(L), "pnl": R(sum(t["pnl"] for t in L))})

ht = []
for b in HB:
    L = [t for t in T if hb(t) == b]
    if L:
        gw = sum(t["pnl"] for t in L if t["pnl"] > 0); gl = -sum(t["pnl"] for t in L if t["pnl"] < 0)
        ht.append({"hold_bucket": b, "n": len(L), "wins": sum(map(win, L)), "pnl": R(gw - gl), "avg_pnl": R((gw - gl) / len(L)),
                   "pf": R(gw / gl) if gl else None})

assert sum(e["n"] for e in edges) == len(T) == sum(h["n"] for h in heat) == sum(r["n"] for r in rd)
P.update(edges=edges, heatmap=heat, revenge=rev, streaks=streaks, max_win_streak=mw, max_loss_streak=ml, r_dist=rd, hold_time=ht)
if last: P["current_streak"] = {"streak_type": last["streak_type"], "streak_len": last["streak_len"]}
P["meta"]["stats_rebuilt"] = "%s from my_trades_list.json (%d trades through %s; 1 per entry)" % (
    dt.datetime.now().strftime("%Y-%m-%d %H:%M"), len(T), max(t["date"] for t in T))
open(F, "w").write(PRE + json.dumps(P, separators=(",", ":")) + ";" + ("\n" if src.endswith("\n") else ""))
print(len(T), "trades · max win streak", mw, "· max loss streak", ml)
for h in ht: print("  hold", h)
for r in rev: print("  ", r)
