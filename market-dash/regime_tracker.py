"""Morning-type tracker (nightly). Reads every saved session in market-dash/history/<day>.json (1-min QQQ candles,
6:30 PT first), labels 6:30-10:00 PT into one morning type, and writes market-dash/regime_tracker.json:
rolling counts over the last 5/10/20 mornings vs the normal mix, the current streak, and every labeled morning.
Normal mix = 471 mornings Jan 2025 - Nov 24 2026 (Alpaca 1-min bars + saved sessions), the scenario library study.
Prints the written path (repo-relative) for publish_history."""
import json, glob, os, statistics as st
MD = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(MD)
N = 210                      # 6:30-10:00 PT
NORMAL_PER20 = {"reverse": 6.8, "one-way then box": 7.0, "continue": 3.3, "no move": 2.5}
NORMAL_COUNTS = {"reverse": 161, "one-way then box": 166, "continue": 77, "no move": 60, "late move": 7, "mornings": 471}
MOVES = ("TREND", "BURST", "GRIND")

def pivots(p, R):
    piv = [0]; mode = None; ext = 0
    for i in range(1, len(p)):
        if mode is None:
            hi = max(range(i + 1), key=lambda k: p[k]); lo = min(range(i + 1), key=lambda k: p[k])
            if p[hi] - p[i] >= R:
                if hi: piv.append(hi)
                mode, ext = "dn", i
            elif p[i] - p[lo] >= R:
                if lo: piv.append(lo)
                mode, ext = "up", i
        elif mode == "up":
            if p[i] >= p[ext]: ext = i
            elif p[ext] - p[i] >= R: piv.append(ext); mode, ext = "dn", i
        else:
            if p[i] <= p[ext]: ext = i
            elif p[i] - p[ext] >= R: piv.append(ext); mode, ext = "up", i
    if mode and ext != piv[-1]: piv.append(ext)
    if piv[-1] != len(p) - 1: piv.append(len(p) - 1)
    return piv

def segment(o, c, base, rf=0.25, bf=0.6, burst=15):
    p = [o] + c; R = max(1.0, rf * base); BIG = max(2.5, bf * base)
    P = pivots(p, R); legs = [(a, b, p[b] - p[a]) for a, b in zip(P, P[1:]) if b > a]
    out = []; run = []
    def flush():
        if not run: return
        pts = [run[0][0]] + [L[1] for L in run]
        def carve(lo, hi):
            best = None
            for i in range(lo, hi + 1):
                for j in range(i + 1, hi + 1):
                    mv = p[pts[j]] - p[pts[i]]
                    if abs(mv) >= BIG and (best is None or abs(mv) > abs(best[2])): best = (i, j, mv)
            if not best: return [("RANGE", pts[lo], pts[hi])]
            i, j, mv = best; r = []
            if i > lo: r += carve(lo, i)
            r.append(("GRIND", pts[i], pts[j]))
            if j < hi: r += carve(j, hi)
            return r
        out.extend(carve(0, len(pts) - 1)); run.clear()
    for a, b, mv in legs:
        if abs(mv) >= BIG: flush(); out.append(("BURST" if b - a <= burst else "TREND", a, b))
        else: run.append((a, b, mv))
    flush()
    res = []
    for k, a, b in out:
        if b <= a: continue
        res.append(dict(k=k, a=a - 1 if a > 0 else 0, b=b - 1, mv=round(p[b] - p[a], 2), du=b - a))
    return res, BIG

def label(segs):
    mv = [s for s in segs if s["k"] in MOVES and s["du"] > 0]
    if not mv: return "no move", "NO MOVE", None
    f = mv[0]
    start = "OPEN DRIVE" if f["a"] < 10 else "BOX → BREAK" if f["a"] < 90 else "LATE MOVE"
    if start == "LATE MOVE": return "late move", start, f
    nxt = next((s for s in segs if s["a"] >= f["b"] and s["k"] in MOVES and s["du"] > 0), None)
    if nxt is None: return "one-way then box", start + " → boxes the rest", f
    if (nxt["mv"] > 0) == (f["mv"] > 0): return "continue", start + " → continues", f
    return "reverse", start + " → reverses", f

def hm(m): return f"{6 + (30 + m) // 60}:{(30 + m) % 60:02d}"

def main():
    days = []
    for p in sorted(glob.glob(os.path.join(MD, "history", "20??-??-??.json"))):
        try: C = json.load(open(p))["candles"]
        except Exception: continue
        if len(C) < N: continue
        days.append(dict(d=os.path.basename(p)[:10], o=float(C[0]["open"]), c=[float(x["close"]) for x in C[:N]],
                         h=[float(x["high"]) for x in C[:150]], l=[float(x["low"]) for x in C[:150]],
                         hi=max(float(x["high"]) for x in C[:N]), lo=min(float(x["low"]) for x in C[:N])))
    hist = []; rows = []
    for x in days:
        r150 = max(x["h"]) - min(x["l"])
        base = st.median(hist[-20:]) if len(hist) >= 5 else r150; hist.append(r150)
        segs, BIG = segment(x["o"], x["c"], base)
        typ, scen, f = label(segs)
        rows.append(dict(date=x["d"], warmup=len(hist) <= 20, type=typ, scenario=scen, big=round(BIG, 2), range=round(x["hi"] - x["lo"], 2),
                         moves=[f"{hm(s['a'])} {s['k']} {s['mv']:+.2f}" for s in segs if s["k"] in MOVES and s["du"] > 0]))
    types = ["reverse", "one-way then box", "continue", "no move", "late move"]
    roll = {}
    for n in (5, 10, 20):
        last = rows[-n:]; roll[str(n)] = {t: sum(r["type"] == t for r in last) for t in types}
    streak = 0
    if rows:
        for r in reversed(rows):
            if r["type"] == rows[-1]["type"]: streak += 1
            else: break
    hist20 = []
    for i in range(20, len(rows) + 1):
        w = rows[i - 20:i]; hist20.append(dict(date=w[-1]["date"], reverse=sum(r["type"] == "reverse" for r in w),
                                               oneway=sum(r["type"] == "one-way then box" for r in w)))
    out = dict(updated=rows[-1]["date"] if rows else None, mornings=len(rows), first=rows[0]["date"] if rows else None,
               normal_per20=NORMAL_PER20, normal_counts=NORMAL_COUNTS, rolling=roll,
               streak=dict(type=rows[-1]["type"] if rows else None, length=streak),
               all_counts={t: sum(r["type"] == t for r in rows if not r["warmup"]) for t in types},
               warmup_note="The first 20 saved mornings (Jul 14 on) have no full 20-day range yet, so their labels are rough; all_counts skips them.",
               rolling20_history=hist20, days=rows,
               note="Normal: 20 mornings usually hold about 7 reversals and 7 one-way-then-box mornings. Longest reversal streak Jan 2025-Nov 2026 before Nov 13 2026 was 5.")
    f = os.path.join(MD, "regime_tracker.json")
    json.dump(out, open(f, "w"), indent=1)
    print(os.path.relpath(f, ROOT))

if __name__ == "__main__":
    main()
