#!/usr/bin/env node
/* 3v3 VOLUME LAYER — nightly paper log (added Nov 22, 2026). PAPER ONLY — never touches the card or live trading.
   From the Nov 22 alpha test (claude/3v3_my_levels.txt → "Does it add alpha"): levels = the bot's own weekly volume levels
   (bot_levels.json: thin gaps + volume nodes / POC, built only from the 20 sessions before that Monday). Morning 6:31–8:50, R = 10% of the
   10-day average 6:30–8:56 range. A level arms once price is R away from it.
     GAP BREAK   : a 1-min close $0.25 THROUGH a thin gap → buy the break (puts if it came from above, calls if from below).
     NODE BOUNCE : price touches a volume node / POC, then closes 30% of R back the way it came within 10 min → buy the bounce.
   Max 2 of each a morning, 10 min apart; skipped when v11 already bought the same side within 10 min (no double count).
   Contract + exits = the v10/v11 layer: delta ~0.35, ask 0.40–3.00, buy ask / sell bid, $1.30, 25% stop, +$0.30 → 3-min trail, 60 min, 8:56.
   Writes "shadow_vol" into v92_log/<day>.json (other keys untouched) + vol_usd in index.json.
   Usage (repo root): node market-dash/volume_paper_log.js   (rebuilds every logged morning; rewrites only changed files) */
"use strict";
const fs = require("fs"), path = require("path");
const ROOT = process.cwd(), MD = path.join(ROOT, "market-dash"), OUT = path.join(MD, "v92_log");
global.V92MAP = require(path.join(MD, "v92_map.js"));
const V10 = require(path.join(MD, "v10_layer.js"));
const rj = p => { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return null; } };
function csv(f) { if (!fs.existsSync(f)) return null; const L = fs.readFileSync(f, "utf8").trim().split("\n"), h = L[0].split(",");
  return L.slice(1).map(l => { const v = l.split(","), o = {}; h.forEach((k, i) => { o[k] = v[i]; }); return o; }); }
const num = x => (x === "" || x == null ? null : Number(x));
function quotes(day) {   // same as v10_paper_log.js
  const dir = path.join(ROOT, "data", day, "am"), out = [];
  const sep = [["C", "calls"], ["P", "puts"]].map(([s, n]) => [s, csv(path.join(dir, `qqq_greeks_${n}_${day}_am.csv`))]);
  if (sep.some(([, rows]) => rows)) { sep.forEach(([side, rows]) => (rows || []).forEach(r => { if (r.expiration !== day) return;
    out.push({ t: Date.parse(r.run_time.replace(" ", "T") + "Z") / 1000, side, strike: num(r.strike), bid: num(r.bid), ask: num(r.ask), iv: num(r.iv), delta: num(r.delta) }); })); return out; }
  (csv(path.join(dir, `qqq_greeks_${day}_am.csv`)) || []).forEach(r => { if (r.expiration !== day) return; const dl = num(r.delta); if (dl === null) return;
    out.push({ t: Date.parse(r.run_time.replace(" ", "T") + "Z") / 1000, side: dl >= 0 ? "C" : "P", strike: num(r.strike), bid: num(r.bid), ask: num(r.ask), iv: num(r.iv), delta: dl }); });
  return out;
}
const clock = m => { const t = 390 + m; return ((Math.floor(t / 60) + 11) % 12 + 1) + ":" + String(t % 60).padStart(2, "0"); };
const toM = c => { if (!c) return null; const [h, m] = c.split(":").map(Number); return h * 60 + m - 390; };
const mondayOf = d => { const t = new Date(d + "T12:00:00Z"), w = t.getUTCDay(); t.setUTCDate(t.getUTCDate() - ((w + 6) % 7)); return t.toISOString().slice(0, 10); };
const index = rj(path.join(MD, "history", "index.json")), hdays = ((index && index.days) || []).filter(d => fs.existsSync(path.join(MD, "history", d + ".json")));
const RANGE = {};
function morningRange(d) { if (d in RANGE) return RANGE[d]; const j = rj(path.join(MD, "history", d + ".json")); let r = null;
  if (j && j.candles && j.candles.length >= 147) { const c = j.candles.slice(0, 147); r = Math.max(...c.map(x => x.high)) - Math.min(...c.map(x => x.low)); } return (RANGE[d] = r); }
const BL = {}; ((rj(path.join(MD, "bot_levels.json")) || {}).weeks || []).forEach(w => { BL[w.week] = w.levels; });
function sigs(B, levels, R, kind) {   // kind "break" (gap) or "bounce" (node, confirmed)
  const out = [];
  levels.forEach(([L, typ]) => {
    let armed = null;
    for (let m = 1; m < 140; m++) {
      const b = B[m], pb = B[m - 1]; if (!b || !pb) continue;
      if (armed === null) armed = Math.abs(pb.c - L) > R;
      if (!armed) { if (Math.abs(b.c - L) >= R) armed = true; continue; }
      const side = pb.c > L ? 1 : -1;
      if (kind === "break" && side * (b.c - L) <= -0.25) { out.push({ m, s: side > 0 ? "P" : "C", lvl: L, typ }); armed = false; }
      if (kind === "bounce" && b.l <= L + 0.25 && b.h >= L - 0.25) {
        for (let j = m + 1; j < Math.min(m + 11, 140); j++) if (B[j] && side * (B[j].c - L) >= 0.3 * R) { out.push({ m: j, s: side > 0 ? "C" : "P", lvl: L, typ }); break; }
        armed = false;
      }
    }
  });
  return out.sort((a, b) => a.m - b.m);
}
const files = fs.readdirSync(OUT).filter(f => /^\d{4}-\d\d-\d\d\.json$/.test(f)).sort(), written = [], IX = {};
for (const f of files) {
  const p = path.join(OUT, f), doc = rj(p); if (!doc) continue;
  const day = doc.date || f.slice(0, 10), lv = BL[mondayOf(day)];
  const prev = hdays.filter(d => d < day).slice(-20); if (prev.length < 12 || !lv) continue;
  const hist = {}; prev.forEach(d => { const j = rj(path.join(MD, "history", d + ".json")); if (j && j.candles && j.candles.length >= 300) hist[d] = j.candles; });
  const H = rj(path.join(MD, "history", day + ".json")); if (!H || !H.candles || H.candles.length < 150) continue;
  const Q = quotes(day); if (!Q.length) continue;
  const D = V92MAP.buildDay(hist, { date: day, candles: H.candles, points: H.points || [], quotes: Q, complete: true }), B = V10.bars(D);
  const rs = prev.slice(-10).map(morningRange).filter(x => x != null); if (!rs.length) continue;
  const R = 0.10 * rs.reduce((a, b) => a + b, 0) / rs.length;
  const v11 = ((doc.shadow_v11 || {}).trades || []).map(t => ({ m: toM(t.filled), s: t.side === "call" ? "C" : "P" }));
  const trades = [];
  [["gapbreak", "break", lv.filter(l => l.type === "thin gap")], ["nodebounce", "bounce", lv.filter(l => l.type === "volume node" || l.type === "POC")]].forEach(([rule, kind, L]) => {
    let n = 0, busy = -1;
    sigs(B, L.map(l => [l.px, l.type]), R, kind).forEach(sg => {
      if (n >= 2 || sg.m <= busy) return;
      if (v11.some(v => v.s === sg.s && Math.abs(v.m - sg.m) <= 10)) return;
      const t = V10.tradeAt(D, sg.s, sg.m); if (!t) return;
      n++; busy = sg.m + 10;
      trades.push({ rule, setup: (rule === "gapbreak" ? "broke through thin gap " : "confirmed bounce off volume " + (sg.typ === "POC" ? "POC " : "node ")) + sg.lvl.toFixed(2),
        side: t.side === "C" ? "call" : "put", signal: clock(sg.m), filled: clock(t.mf), strike: t.K, paid: +t.paid.toFixed(2), exit: clock(t.out), sold: +t.sold.toFixed(2), why: t.why, usd: t.usd, best: +t.hw.toFixed(2) });
    });
  });
  trades.sort((a, b) => toM(a.filled) - toM(b.filled));
  const vol = { rule: "volume layer (paper): GAP BREAK = 1-min close $0.25 through one of the bot's weekly thin gaps → buy the break · NODE BOUNCE = touch a volume node / POC, then 30% of R back within 10 min → buy the bounce · max 2 each a morning, not when v11 already bought that side within 10 min · v11 contract + exits",
    R: Math.round(R * 100) / 100, trades, usd: Math.round(trades.reduce((a, t) => a + t.usd, 0) * 10) / 10 };
  IX[day] = vol.usd;
  if (JSON.stringify(doc.shadow_vol) !== JSON.stringify(vol)) { doc.shadow_vol = vol; fs.writeFileSync(p, JSON.stringify(doc, null, 1)); written.push(path.relative(ROOT, p)); }
}
const ixf = path.join(OUT, "index.json"), ix = rj(ixf);
if (ix) { let ch = false; (ix.days || []).forEach(r => { if (r.date in IX && r.vol_usd !== IX[r.date]) { r.vol_usd = IX[r.date]; ch = true; } });
  if (ch) { fs.writeFileSync(ixf, JSON.stringify(ix, null, 1)); written.push(path.relative(ROOT, ixf)); } }
written.forEach(w => console.log(w));
