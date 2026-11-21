#!/usr/bin/env node
/* 3v3 v10 auction layer — nightly paper log (added Nov 21, 2026). PAPER ONLY.
   Adds "shadow_v10" to market-dash/v92_log/<day>.json (every other key in the file is left exactly as it is) and a v10 total
   to v92_log/index.json. Same day builder as v92_paper_log.js (history/<day>.json bars + GEX points, data/<day>/am quotes).
   Usage (repo root):  node market-dash/v10_paper_log.js [YYYY-MM-DD ...]   (no dates = every logged morning missing v10)
   Prints the paths it wrote, one per line. */
"use strict";
const fs = require("fs"), path = require("path");
const ROOT = process.cwd(), MD = path.join(ROOT, "market-dash"), OUT = path.join(MD, "v92_log");
global.V92MAP = require(path.join(MD, "v92_map.js"));
const V10 = require(path.join(MD, "v10_layer.js"));
function readJSON(p) { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return null; } }
function csv(f) {
  if (!fs.existsSync(f)) return null;
  const L = fs.readFileSync(f, "utf8").trim().split("\n"), h = L[0].split(",");
  return L.slice(1).map(l => { const v = l.split(","), o = {}; h.forEach((k, i) => { o[k] = v[i]; }); return o; });
}
const num = x => (x === "" || x == null ? null : Number(x));
function quotes(day) {   // same as v92_paper_log.js
  const dir = path.join(ROOT, "data", day, "am"), out = [];
  const sep = [["C", "calls"], ["P", "puts"]].map(([s, n]) => [s, csv(path.join(dir, `qqq_greeks_${n}_${day}_am.csv`))]);
  if (sep.some(([, rows]) => rows)) {
    sep.forEach(([side, rows]) => (rows || []).forEach(r => {
      if (r.expiration !== day) return;
      out.push({ t: Date.parse(r.run_time.replace(" ", "T") + "Z") / 1000, side, strike: num(r.strike), bid: num(r.bid), ask: num(r.ask), iv: num(r.iv), delta: num(r.delta) });
    }));
    return out;
  }
  (csv(path.join(dir, `qqq_greeks_${day}_am.csv`)) || []).forEach(r => {
    if (r.expiration !== day) return; const dl = num(r.delta); if (dl === null) return;
    out.push({ t: Date.parse(r.run_time.replace(" ", "T") + "Z") / 1000, side: dl >= 0 ? "C" : "P", strike: num(r.strike), bid: num(r.bid), ask: num(r.ask), iv: num(r.iv), delta: dl });
  });
  return out;
}
const clock = m => { const t = 390 + m; return ((Math.floor(t / 60) + 11) % 12 + 1) + ":" + String(t % 60).padStart(2, "0"); };
// prior-evening GARCH vol from the site's evening logs (history.js)
let HIST = null;
function garchBefore(day) {
  if (!HIST) { global.window = global.window || {}; try { require(path.join(MD, "history.js")); } catch (e) {} HIST = (global.window.MARKET_DASH_HISTORY || []); }
  let g = null;
  HIST.forEach(e => { if (e && e.entry_date && e.entry_date < day && e.quant && e.quant.garch && e.quant.garch.conditional_vol_pct != null) g = e.quant.garch.conditional_vol_pct; });
  return g;
}
const index = readJSON(path.join(MD, "history", "index.json"));
const days = ((index && index.days) || []).filter(d => fs.existsSync(path.join(MD, "history", d + ".json")));
const RANGE = {};
function morningRange(d) {
  if (d in RANGE) return RANGE[d];
  const j = readJSON(path.join(MD, "history", d + ".json")); let r = null;
  if (j && j.candles && j.candles.length >= 147) { const c = j.candles.slice(0, 147); r = Math.max(...c.map(x => x.high)) - Math.min(...c.map(x => x.low)); }
  return (RANGE[d] = r);
}
let want = process.argv.slice(2);
if (!want.length) want = fs.readdirSync(OUT).filter(f => /^\d{4}-\d\d-\d\d\.json$/.test(f)).map(f => f.slice(0, 10))
  .filter(d => { const j = readJSON(path.join(OUT, d + ".json")); return j && !j.shadow_v10; }).sort();
const written = [];
for (const day of want) {
  const f = path.join(OUT, day + ".json"), doc = readJSON(f); if (!doc) continue;
  const prev = days.filter(d => d < day).slice(-20); if (prev.length < 12) continue;
  const hist = {}; prev.forEach(d => { const j = readJSON(path.join(MD, "history", d + ".json")); if (j && j.candles && j.candles.length >= 300) hist[d] = j.candles; });
  const H = readJSON(path.join(MD, "history", day + ".json")); if (!H || !H.candles || H.candles.length < 150) continue;
  const Q = quotes(day); if (!Q.length) continue;
  const D = V92MAP.buildDay(hist, { date: day, candles: H.candles, points: H.points || [], quotes: Q, complete: true });
  const rs = prev.slice(-10).map(morningRange).filter(x => x != null);
  const X = { garch: garchBefore(day), range10: rs.length ? rs.reduce((a, b) => a + b, 0) / rs.length : null };
  const R = V10.runDay(D, X, day);
  const KIND = { orb: "opening-range break", ext: "new-extreme break", pop: "pop with the 20-min candle" };
  doc.shadow_v10 = {
    rule: "v10 auction layer (paper): ORB = first close outside 6:30–6:45 · EXT = level break that makes a new day high/low once 40–100% of the implied move is used · POP (GARCH > 22%) = 15% of the 10-day range in 3 min with the 20-min candle · never against the last 3-min move · delta 0.35, ask 0.40–3.00 · 25% stop, +$0.30 → 3-min trail, 60 min max · baseline = random entries, same exits",
    regime: R.skip ? null : { garch: R.garch, high_vol: R.hv, atm_iv: R.iv, implied_move: R.implied_move, range10: R.range10 },
    skip: R.skip || null,
    trades: (R.trades || []).map(t => ({ rule: t.kind, setup: KIND[t.kind] + (t.lvl ? " · " + t.lvl : ""), side: t.side === "C" ? "call" : "put", signal: clock(t.m), filled: clock(t.mf),
      strike: t.K, paid: +t.paid.toFixed(2), exit: clock(t.out), sold: +t.sold.toFixed(2), why: t.why, usd: t.usd, best: +t.hw.toFixed(2) })),
    usd: R.usd || 0, baseline_usd: R.baseline ? R.baseline.usd : null, baseline_trades: R.baseline ? R.baseline.trades : null
  };
  fs.writeFileSync(f, JSON.stringify(doc, null, 1));
  written.push(path.relative(ROOT, f));
}
// index: v10 totals per morning (other fields untouched)
const ixf = path.join(OUT, "index.json"), ix = readJSON(ixf);
if (ix && written.length) {
  (ix.days || []).forEach(r => { const j = readJSON(path.join(OUT, r.date + ".json")); if (j && j.shadow_v10) { r.v10_usd = j.shadow_v10.usd; r.v10_baseline = j.shadow_v10.baseline_usd; } });
  fs.writeFileSync(ixf, JSON.stringify(ix, null, 1)); written.push(path.relative(ROOT, ixf));
}
written.forEach(w => console.log(w));
