"""Habits tracker (Nov 27): tags every one of your trades (my_trades_list.json) with the good / bad habits found in the
Nov 27 study, so the Stats tab can show what each habit made or cost over 1W / 1M / 3M / 6M / YTD / ALL.
Inputs: my_trades_list.json · history/<day>.json (1-min QQQ, 6:30 PT first) · regime_tracker.json (morning type, big move)
        · history.js (evening realized − implied spread = the light). Output: habits.json. Run from anywhere; prints the path.
Times in my_trades_list: m / exit_m = minutes after 6:30 PT."""
import json, os, glob, collections, datetime as dt

MD = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(MD)
DEFS = {
    # good habits — trade level
    "winner_030":   {"kind": "good", "label": "Let a winner get to +0.30 or more", "vs": "winners sold under +0.30"},
    "winner_8min":  {"kind": "good", "label": "Held a winner 8+ min", "vs": "winners cut under 8 min"},
    "green_light":  {"kind": "unproven", "label": "Traded on a 🟢 / 🔥 light day", "vs": "🟡 / ⚫ days"},
    "tue_fri":      {"kind": "good", "label": "Tuesday – Friday", "vs": "Mondays"},
    "with_candle":  {"kind": "unproven", "label": "With the 20-min candle", "vs": "against it"},
    "small_loss":   {"kind": "good", "label": "Kept the loss to $0.20 or less", "vs": "bigger losses"},
    # good habits — day level
    "stopped_green": {"kind": "good", "scope": "day", "label": "Stopped while green (ended within $25 of the day high)", "vs": "gave back more"},
    "le8":           {"kind": "good", "scope": "day", "label": "8 trades or fewer", "vs": "more than 8"},
    # bad habits — trade level
    "light_bad":    {"kind": "unproven", "label": "Traded on a 🟡 / ⚫ light day"},
    "monday":       {"kind": "bad", "label": "Monday"},
    "against_candle": {"kind": "unproven", "label": "Against the 20-min candle"},
    "in_box":       {"kind": "unproven", "label": "Bought inside a box before the break"},
    "open_whiplash": {"kind": "bad", "label": "Open whiplash (both sides 6:30–6:45)"},
    "pricey_open":  {"kind": "bad", "label": "Contract over $1.20 at the open"},
    "flip_flop":    {"kind": "bad", "label": "Flip-flopped calls ↔ puts within 5 min"},
    # alarm
    "after_75":     {"kind": "alarm", "label": "Kept trading after −$75 on the day (the Monday alarm)"},
}

def spreads():
    out = {}
    for line in open(os.path.join(MD, "history.js")):
        line = line.strip()
        if not line.startswith("window.MARKET_DASH_HISTORY.push("): continue
        try: x = json.loads(line[len("window.MARKET_DASH_HISTORY.push("):].rstrip(";").rstrip(")"))
        except Exception: continue
        if x.get("partial"): continue
        v = ((x.get("quant") or {}).get("realized_vs_implied") or {}).get("spread_pct")
        if v is None: continue
        key = (x.get("entry_date"), str(x.get("fetched_pt") or ""))
        out[key] = float(v)
    return out

def main():
    T = json.load(open(os.path.join(MD, "my_trades_list.json")))
    rt = {}
    try: rt = {d["date"]: d for d in json.load(open(os.path.join(MD, "regime_tracker.json")))["days"]}
    except Exception: pass
    SP = spreads(); spk = sorted(SP)
    H = {}
    for p in glob.glob(os.path.join(MD, "history", "20??-??-??.json")):
        try: C = json.load(open(p)).get("candles") or []
        except Exception: continue
        if len(C) >= 30: H[os.path.basename(p)[:10]] = dict(o=float(C[0]["open"]), c=[float(x["close"]) for x in C])
    by = collections.defaultdict(list)
    for t in T: by[t["date"]].append(t)
    trades, days = [], []
    for d, L in sorted(by.items()):
        L = sorted(L, key=lambda t: t["m"]); dayp = round(sum(t["pnl"] for t in L), 2)
        dtags = []
        run = peak = 0
        for u in sorted(L, key=lambda u: u["exit_m"]): run += u["pnl"]; peak = max(peak, run)
        if peak > 0 and peak - dayp <= 25: dtags.append("stopped_green")
        if len(L) <= 8: dtags.append("le8")
        days.append(dict(d=d, pnl=dayp, n=len(L), tags=dtags))
        h = H.get(d)
        prev = [k for k in spk if k[0] < d]; light = SP[prev[-1]] if prev else None
        typ = rt.get(d, {}).get("type"); big = rt.get(d, {}).get("big") or 3.6
        mon = dt.date.fromisoformat(d).weekday() == 0
        for i, t in enumerate(L):
            tg = []; sg = 1 if t["side"] == "call" else -1
            if t["pnl"] > 0:
                if (t.get("per") if t.get("per") is not None else t["pnl"] / max(1, t.get("ct") or 1)) >= 30: tg.append("winner_030")
                if t["exit_m"] - t["m"] >= 8: tg.append("winner_8min")
            if t["pnl"] < 0 and (t["paid"] - t["sold"]) <= 0.20 + 1e-9: tg.append("small_loss")
            tg.append("monday" if mon else "tue_fri")
            if light is not None: tg.append("green_light" if light >= 3.5 else "light_bad")
            if t["paid"] > 1.20 and t["m"] < 30: tg.append("pricey_open")
            if t["m"] < 15:   # Nov 28 fix: only entries made up to this one (it used later trades before)
                early = [u for u in L[:i + 1] if u["m"] < 15]
                if len(early) >= 3 and len({u["side"] for u in early}) == 2: tg.append("open_whiplash")
            if i > 0 and L[i - 1]["side"] != t["side"] and t["m"] - L[i - 1]["exit_m"] <= 5: tg.append("flip_flop")
            r2 = sum(u["pnl"] for u in L[:i] if u["exit_m"] <= t["m"])
            if r2 <= -75: tg.append("after_75")
            if h:
                # Nov 28 fix (independent check): only prices known BEFORE the entry — the last completed minute (m-1),
                # never the close of the entry minute (that leaked up to 59 s of the future). Fixed the c[cs-21] -> c[-1] bug too.
                c = h["c"]; m = int(t["m"]); k = min(m - 1, len(c) - 1); cs = (max(m, 0) // 20) * 20
                now = c[k] if k >= 0 else h["o"]
                if m - cs < 3 and cs >= 20:   # barely into a new candle: use the candle before it (fully known)
                    ps = cs - 20; d = c[cs - 1] - (c[ps - 1] if ps > 0 else h["o"])
                else:
                    d = now - (c[cs - 1] if cs > 0 else h["o"])
                if d * sg > 0: tg.append("with_candle")
                elif d * sg < 0: tg.append("against_candle")
                if k >= 15:
                    box = c[k - 15:k]
                    if max(box) - min(box) <= 0.5 * big and min(box) <= now <= max(box): tg.append("in_box")
            trades.append(dict(d=d, pnl=round(t["pnl"], 2), win=t["pnl"] > 0, loss=t["pnl"] < 0, tags=tg))
    out = dict(updated=max(by) if by else None, defs=DEFS, trades=trades, days=days,
               note="Nov 28: candle / box / light tags moved to UNPROVEN after the independent check (they flip between Sep–Oct and Nov). Nov 27 study: claude/3v3_habits (project). Good = what the habit made; bad = what it cost. A trade can carry several tags.")
    f = os.path.join(MD, "habits.json"); json.dump(out, open(f, "w"), ensure_ascii=False, separators=(",", ":"))
    print(os.path.relpath(f, ROOT))

if __name__ == "__main__":
    main()
