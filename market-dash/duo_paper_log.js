#!/usr/bin/env node
/* 3v3 COMBO ("duo") — nightly paper log (added Nov 22, 2026). PAPER ONLY — nothing here places orders.
   Combo = the old bot (v9.2 card trades) + the new bot (v11 trades) + the volume layer (shadow_vol, added Nov 22), every morning,
   opposite sides allowed.
   Add-ons, both fired when a trade first gets to +$0.30 (bid − paid) and both sold at the main trade's exit:
     ADD   = +1 more of the same contract at the ask
     LOTTO = the cheapest same-side 0DTE asking $0.30–0.80 (held max 45 min)
   Self-check per bot: a bot sits out (paper only, still logged) when, over its last 10 active mornings, its $/trade is below
   random entries' $/trade (the v11 baseline: 3 random entries, same exits) on those mornings. Needs 5 active mornings first.
   Writes "shadow_duo" into market-dash/v92_log/<day>.json (every other key left exactly as it is) + duo_* fields in index.json.
   Rebuilds every logged morning each run (self-check depends on history) but only rewrites files whose shadow_duo changed.
   Usage (repo root):  node market-dash/duo_paper_log.js      Prints the paths it wrote, one per line. */
"use strict";
const fs = require("fs"), path = require("path");
const ROOT = process.cwd(), MD = path.join(ROOT, "market-dash"), OUT = path.join(MD, "v92_log");
const V92MAP = require(path.join(MD, "v92_map.js"));
const ARM = 0.30, FEE = 1.3, LOT_LO = 0.30, LOT_HI = 0.80, LOT_HOLD = 45, END = 149, WIN = 10, MIN_ACTIVE = 5;
function readJSON(p) { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return null; } }
function csv(f) {
  if (!fs.existsSync(f)) return null;
  const L = fs.readFileSync(f, "utf8").trim().split("\n"), h = L[0].split(",");
  return L.slice(1).map(l => { const v = l.split(","), o = {}; h.forEach((k, i) => { o[k] = v[i]; }); return o; });
}
const num = x => (x === "" || x == null ? null : Number(x));
function quotes(day) {   // same as v92_paper_log.js / v10_paper_log.js
  const dir = path.join(ROOT, "data", day, "am"), out = [];
  const sep = [["C", "calls"], ["P", "puts"]].map(([s, n]) => [s, csv(path.join(dir, `qqq_greeks_${n}_${day}_am.csv`))]);
  if (sep.some(([, rows]) => rows)) {
    sep.forEach(([side, rows]) => (rows || []).forEach(r => {
      if (r.expiration !== day) return;
      out.push({ t: Date.parse(r.run_time.replace(" ", "T") + "Z") / 1000, side, strike: num(r.strike), bid: num(r.bid), ask: num(r.ask) });
    }));
    return out;
  }
  (csv(path.join(dir, `qqq_greeks_${day}_am.csv`)) || []).forEach(r => {
    if (r.expiration !== day) return; const dl = num(r.delta); if (dl === null) return;
    out.push({ t: Date.parse(r.run_time.replace(" ", "T") + "Z") / 1000, side: dl >= 0 ? "C" : "P", strike: num(r.strike), bid: num(r.bid), ask: num(r.ask) });
  });
  return out;
}
function optBook(day) {   // same minute bucketing as v92_map.buildDay: opt[side][strike][minute] = [mid, iv, bid, ask]
  const t0 = V92MAP.open930(day), opt = { C: {}, P: {} };
  quotes(day).forEach(r => {
    if (!(r.bid > 0) || !(r.ask > 0)) return;
    const mm = Math.floor((r.t - t0) / 60), b = opt[r.side][String(r.strike)] || (opt[r.side][String(r.strike)] = {});
    b[mm] = [(r.bid + r.ask) / 2, null, r.bid, r.ask];
  });
  return opt;
}
const toM = c => { if (!c) return null; const [h, m] = c.split(":").map(Number); return h * 60 + m - 390; };
const clock = m => { const t = 390 + m; return ((Math.floor(t / 60) + 11) % 12 + 1) + ":" + String(t % 60).padStart(2, "0"); };
const q = (O, s, K, m) => { const b = (O[s] || {})[K]; return b ? b[m] || null : null; };
function bidAt(O, s, K, m) { for (let j = m; j > m - 3; j--) { const x = q(O, s, K, j); if (x && x[2] != null) return x[2]; } return null; }
function askNear(O, s, K, m) { for (let j = m; j <= m + 2; j++) { const x = q(O, s, K, j); if (x && x[2] != null && x[3] > 0) return [j, x[3]]; } return null; }
function armedMin(O, t) { for (let j = t.a + 1; j < t.b; j++) { const b = bidAt(O, t.s, t.K, j); if (b != null && b - t.paid >= ARM) return j; } return null; }
function cheapest(O, s, m) {
  let best = null;
  Object.keys(O[s] || {}).forEach(K => { const x = askNear(O, s, K, m); if (x && x[1] >= LOT_LO && x[1] <= LOT_HI && (!best || x[1] < best[2])) best = [K, x[0], x[1]]; });
  return best;
}
function sellWithMain(O, s, K, j0, mainExit) {
  const end = Math.min(j0 + LOT_HOLD, END);
  for (let j = j0 + 1; j <= end; j++) { const b = bidAt(O, s, K, j); if (b == null) continue; if (j >= mainExit) return [b, j]; }
  for (let j = end; j > j0; j--) { const b = bidAt(O, s, K, j); if (b != null) return [b, j]; }
  return [0, end];
}
const r1 = v => Math.round(v * 10) / 10;
const files = fs.readdirSync(OUT).filter(f => /^\d{4}-\d\d-\d\d\.json$/.test(f)).sort();
const H = { old: [], new: [], vol: [] }, written = [], idx = {}, LANES = ["old", "new", "vol"];
for (const f of files) {
  const p = path.join(OUT, f), doc = readJSON(p); if (!doc) continue;
  const day = doc.date || f.slice(0, 10), v11 = doc.shadow_v11; if (!v11) continue;
  const lanes = { old: (doc.trades || []).filter(t => t.filled), new: (v11.trades || []).filter(t => t.filled), vol: ((doc.shadow_vol || {}).trades || []).filter(t => t.filled) };
  const rnd = v11.baseline_usd || 0;
  // self-check, from earlier mornings only
  const on = {};
  LANES.forEach(k => {
    const act = H[k].filter(h => h.n > 0).slice(-WIN);
    if (act.length < MIN_ACTIVE) { on[k] = { on: true, why: "warming up (" + act.length + " of " + MIN_ACTIVE + " active mornings)" }; return; }
    const lane = act.reduce((a, h) => a + h.usd, 0) / act.reduce((a, h) => a + h.n, 0), rr = act.reduce((a, h) => a + h.rnd, 0) / (3 * act.length);
    on[k] = { on: lane >= rr, why: "last " + act.length + " active mornings $" + lane.toFixed(0) + "/trade vs random $" + rr.toFixed(0) + "/trade" };
  });
  LANES.forEach(k => H[k].push({ usd: lanes[k].reduce((a, t) => a + (t.usd || 0), 0), n: lanes[k].length, rnd }));
  const O = optBook(day), legs = [], sat = [];
  LANES.forEach(k => lanes[k].forEach(t => {
    const s = t.side === "call" ? "C" : "P", T = { s, K: String(t.strike), a: toM(t.filled), b: t.exit ? toM(t.exit) : 146, paid: t.paid };
    const name = k === "old" ? "old bot (v9.2)" : k === "vol" ? "volume layer" : "new bot (v11)";
    const base = { lane: k, kind: "trade", rule: t.rule || t.setup || "", side: t.side, strike: t.strike, filled: t.filled, exit: t.exit, paid: t.paid, sold: t.sold, why: t.why, usd: t.usd, setup: name + " · " + (t.setup || t.rule || "") };
    if (!on[k].on) { sat.push(base); return; }
    legs.push(base);
    const m = armedMin(O, T); if (m == null) return;
    const xb = T.b < 150 ? bidAt(O, s, T.K, T.b) : null;
    const a = askNear(O, s, T.K, m + 1);
    if (a && xb != null) legs.push({ lane: k, kind: "add", rule: "add", side: t.side, strike: t.strike, filled: clock(a[0]), exit: t.exit, paid: a[1], sold: xb, usd: r1((xb - a[1]) * 100 - FEE), setup: name + " · ADD +1 at +$0.30" });
    const c = cheapest(O, s, m + 1);
    if (c) { const [b, j] = sellWithMain(O, s, c[0], c[1], T.b); legs.push({ lane: k, kind: "lotto", rule: "lotto", side: t.side, strike: Number(c[0]), filled: clock(c[1]), exit: clock(j), paid: c[2], sold: b, usd: r1((b - c[2]) * 100 - FEE), setup: name + " · LOTTO $0.30–0.80 at +$0.30" }); }
  }));
  const sum = F => r1(legs.filter(F).reduce((a, l) => a + l.usd, 0));
  const cash = F => { let mx = 0; for (let m = 0; m < 150; m++) { let c = 0; legs.filter(F).forEach(l => { const a = toM(l.filled), b = l.exit ? toM(l.exit) : 146; if (a <= m && m < b) c += l.paid * 100; }); mx = Math.max(mx, c); } return Math.round(mx); };
  const duo = {
    rule: "COMBO (paper): old bot (v9.2 card) + new bot (v11) + volume layer (gap breaks / confirmed node bounces, since Nov 22) every morning, opposite sides allowed · when a trade first gets to +$0.30: ADD +1 same contract and a LOTTO (cheapest same side asking $0.30–0.80), both sold with the main trade · self-check: a bot sits out when its last 10 active mornings make less per trade than random entries",
    self_check: on,
    legs, sat_out: sat,
    usd: { combo: sum(l => l.kind === "trade"), lotto: sum(l => l.kind !== "add"), add: sum(l => l.kind !== "lotto"), both: sum(() => true) },
    max_cash: { combo: cash(l => l.kind === "trade"), both: cash(() => true) }
  };
  idx[day] = duo;
  if (JSON.stringify(doc.shadow_duo) !== JSON.stringify(duo)) { doc.shadow_duo = duo; fs.writeFileSync(p, JSON.stringify(doc, null, 1)); written.push(path.relative(ROOT, p)); }
}
const ixf = path.join(OUT, "index.json"), ix = readJSON(ixf);
if (ix) {
  let ch = false;
  (ix.days || []).forEach(r => { const d = idx[r.date]; if (!d) return;
    const v = { duo_usd: d.usd.combo, duo_lotto_usd: d.usd.lotto, duo_add_usd: d.usd.add, duo_both_usd: d.usd.both, duo_old_on: d.self_check.old.on, duo_new_on: d.self_check.new.on };
    Object.keys(v).forEach(k => { if (r[k] !== v[k]) { r[k] = v[k]; ch = true; } }); });
  if (ch) { fs.writeFileSync(ixf, JSON.stringify(ix, null, 1)); written.push(path.relative(ROOT, ixf)); }
}
// Trade Replay: rebuild so the new bot's trades + add-ons show there too (v92_paper_log builds it earlier in the night, before this layer)
if (written.length) { try { written.push(require(path.join(MD, "bot_replay_build.js")).build(ROOT)); } catch (e) { console.error("bot replay build failed: " + e.message); } }
written.forEach(w => console.log(w));
