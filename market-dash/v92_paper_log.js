#!/usr/bin/env node
/* 3v3 v9.2 nightly paper log — runs the SAME engine and morning map as the live card on the day's final data
   (history/<day>.json bars + GEX points, data/<day>/am logger quotes) and writes:
     market-dash/v92_log/<day>.json   every signal, fill, exit and dollar result for that morning
     market-dash/v92_log/index.json   one row per morning + totals (the card's 10-morning meter reads this)
   Usage (from the repo root):  node market-dash/v92_paper_log.js [YYYY-MM-DD ...]
   With no dates it logs every morning that has a history file but no log yet (from 2026-07-20 on).
   Prints the paths it wrote, one per line, so the workflow can publish them. */
"use strict";
const fs = require("fs"), path = require("path");
const ROOT = process.cwd(), MD = path.join(ROOT, "market-dash"), OUT = path.join(MD, "v92_log");
global.V92 = require(path.join(MD, "v92_engine.js"));
global.V92MAP = require(path.join(MD, "v92_map.js"));
const FORWARD_FROM = "2026-11-02";     // mornings on or after this date were never seen by the research
const VB = { th: 16.5, vbStart: 60, vbMax: 1, from: "2026-11-02" };   // v9.3 candidate "VIX bounce calls" (shadow only)

// Yesterday's VIX close from the dashboard's own snapshots (data/<day>/<am|pm>/market_dash_*.json):
// a snapshot taken after that session closed gives "last"; a snapshot taken during the next session gives "prev_close".
function ptMin(f) { const h = Number(f.slice(11, 13)), m = Number(f.slice(14, 16)); return ((h % 12) + (f.slice(17, 19) === "PM" ? 12 : 0)) * 60 + m; }
let SNAPS = null;
function snaps() {
  if (SNAPS) return SNAPS; SNAPS = [];
  const base = path.join(MD, "data");
  if (!fs.existsSync(base)) return SNAPS;
  for (const d of fs.readdirSync(base)) for (const s of ["am", "pm"]) {
    const f = path.join(base, d, s, `market_dash_${d}_${s}.json`); const j = fs.existsSync(f) ? readJSON(f) : null;
    const v = j && j.markets && j.markets.VIX; if (v && j.fetched_pt) SNAPS.push({ day: j.fetched_pt.slice(0, 10), t: ptMin(j.fetched_pt), last: v.last, prev: v.prev_close });
  }
  return SNAPS;
}
function vixClose(session, next) {
  const a = snaps().filter(x => x.day === session && x.t >= 13 * 60 + 30).map(x => x.last);
  const b = next ? snaps().filter(x => x.day === next && x.t >= 7 * 60).map(x => x.prev) : [];
  const pick = b.length ? b : a;
  if (!pick.length) return null;
  const c = {}; pick.forEach(v => { c[v] = (c[v] || 0) + 1; });
  return Number(Object.keys(c).sort((x, y) => c[y] - c[x])[0]);
}

// EMA 5/8/13/21 on 5-min bars aligned to 6:30, live value at each minute (forming bar = current close). Seeded with yesterday's session.
function ribbon5(yc, st) {
  const ys = (yc || []).slice(0, 390).map(c => c.close), ts = st.map(x => x ? x[0] : null), f = ts.find(x => x !== null);
  const cl = ys.concat(ts.map(x => x === null ? f : x)), N = [5, 8, 13, 21], e = N.map(() => null), out = [];
  for (let i = 0; i < cl.length; i++) {
    const rel = i - ys.length, mi = rel >= 0 ? rel : i, done = (mi + 1) % 5 === 0;
    const v = N.map((n, j) => { const a = 2 / (n + 1); return e[j] === null ? cl[i] : a * cl[i] + (1 - a) * e[j]; });
    if (done) v.forEach((x, j) => { e[j] = x; });
    if (rel >= 0) out.push(v);
  }
  return out;
}
function readJSON(p) { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return null; } }
function csv(f) {
  if (!fs.existsSync(f)) return null;
  const L = fs.readFileSync(f, "utf8").trim().split("\n"), h = L[0].split(","), ix = {};
  h.forEach((k, i) => { ix[k] = i; });
  return L.slice(1).map(l => { const v = l.split(","), o = {}; h.forEach((k, i) => { o[k] = v[i]; }); return o; });
}
const num = x => (x === "" || x == null ? null : Number(x));
function quotes(day) {
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

const index = readJSON(path.join(MD, "history", "index.json"));
const days = ((index && index.days) || []).filter(d => fs.existsSync(path.join(MD, "history", d + ".json")));
let want = process.argv.slice(2);
if (!want.length) want = days.filter(d => d >= "2026-07-20" && !fs.existsSync(path.join(OUT, d + ".json")));
fs.mkdirSync(OUT, { recursive: true });
const written = [];
for (const day of want) {
  const prev = days.filter(d => d < day).slice(-20);
  if (prev.length < 12) continue;
  const hist = {}; prev.forEach(d => { const j = readJSON(path.join(MD, "history", d + ".json")); if (j && j.candles && j.candles.length >= 300) hist[d] = j.candles; });
  const H = readJSON(path.join(MD, "history", day + ".json"));
  if (!H || !H.candles || H.candles.length < 150) continue;
  const Q = quotes(day);
  if (!Q.length) continue;
  const D = V92MAP.buildDay(hist, { date: day, candles: H.candles, points: H.points || [], quotes: Q, complete: true });
  const R = V92.runDay(D, { causal: true }), c = D.ctx;
  const trades = R.trades.filter(t => t.why !== "none").map(t => ({
    side: t.side === "C" ? "call" : "put", setup: t.kind, signal: clock(t.m), filled: clock(t.mf), strike: t.K,
    paid: +t.ask.toFixed(2), exit: clock(t.out), sold: t.px == null ? null : +t.px.toFixed(2), why: { stop: "stop", q8: "trail", time: "time exit" }[t.why] || t.why,
    usd: t.res == null ? null : Math.round(t.res * 10000) / 100, fakeout_line: +t.fakeout.toFixed(2), iv: t.iv }));
  const usd = Math.round(trades.reduce((s, t) => s + (t.usd || 0), 0) * 100) / 100;
  const doc = {
    date: day, forward: day >= FORWARD_FROM, engine: "v9.2 (market-dash/v92_engine.js)", units: "P&L in dollars at 1 contract, fees out; prices in 0.00",
    map: { yesterday: c.day > 0 ? "green" : "red", last_week: c.week > 0 ? "up" : "down", yday_high: c.PDH, yday_low: c.PDL, yday_close: c.PDC,
      trap: c.day > 0 ? `calls below ${(c.PDL - 1).toFixed(2)}` : `puts above ${(c.PDH + 1).toFixed(2)}`,
      gap: +(R.gap).toFixed(2), gap_and_go_armed: R.gapArmed.C ? "calls" : R.gapArmed.P ? "puts" : "off",
      value_area: [D.va.yval, D.va.yvah].map(v => +v.toFixed(2)), fade_week_value_ok: R.fwOk,
      trap_zone_reached: R.zoneFirst.C !== null ? clock(R.zoneFirst.C) : R.zoneFirst.P !== null ? clock(R.zoneFirst.P) : null },
    trades, usd, stops: trades.filter(t => t.why === "stop").length, generated_utc: new Date().toISOString() };
  // shadow: v9.2 + "VIX bounce calls" (v9.3 candidate). Not on the card. Compared with v9.2 in index.json.
  const vix = vixClose(prev[prev.length - 1], day), vbDay = c.day < 0 && vix !== null && vix >= VB.th;
  const R2 = vbDay ? V92.runDay(D, { causal: true, vb: true, vbStart: VB.vbStart, vbMax: VB.vbMax }) : R;
  const tr2 = R2.trades.filter(t => t.why !== "none").map(t => ({ side: t.side === "C" ? "call" : "put", setup: t.kind, signal: clock(t.m), filled: clock(t.mf), strike: t.K,
    paid: +t.ask.toFixed(2), exit: clock(t.out), sold: t.px == null ? null : +t.px.toFixed(2), why: { stop: "stop", q8: "trail", time: "time exit" }[t.why] || t.why,
    usd: t.res == null ? null : Math.round(t.res * 10000) / 100 }));
  doc.shadow_v93_vix_bounce = { rule: `yesterday red + VIX close >= ${VB.th} -> also buy call EMA8 turns after 7:30, 1 per day`,
    vix_yesterday: vix, bounce_day: vbDay, trades: tr2, usd: Math.round(tr2.reduce((s, t) => s + (t.usd || 0), 0) * 100) / 100,
    bounce_usd: Math.round(tr2.filter(t => t.setup.indexOf("vb") >= 0).reduce((s, t) => s + (t.usd || 0), 0) * 100) / 100 };
  // shadow 2: the "combo" milestone candidate = v9.2 + 3-tick trail + VIX bounce calls. Not on the card's signals.
  const o3 = { causal: true, trailMode: "three" }; if (vbDay) { o3.vb = true; o3.vbStart = VB.vbStart; o3.vbMax = VB.vbMax; }
  const R3 = V92.runDay(D, o3);
  const tr3 = R3.trades.filter(t => t.why !== "none").map(t => ({ side: t.side === "C" ? "call" : "put", setup: t.kind, signal: clock(t.m), filled: clock(t.mf), strike: t.K,
    paid: +t.ask.toFixed(2), exit: clock(t.out), sold: t.px == null ? null : +t.px.toFixed(2), why: { stop: "stop", q8: "trail (3 ticks)", time: "time exit" }[t.why] || t.why,
    usd: t.res == null ? null : Math.round(t.res * 10000) / 100 }));
  // ribbon watch (paper only): EMA 5/8/13/21 on the 5-min chart lined up WITH the trade at the signal = "high confidence"
  const rib = ribbon5(hist[prev[prev.length - 1]], D.st), r3 = R3.trades.filter(t => t.why !== "none");
  tr3.forEach((t, i) => { const m = r3[i].m, v = rib[m], sg = r3[i].side === "C" ? 1 : -1;
    t.ribbon5 = !!(v && sg * (v[0] - v[1]) > 0 && sg * (v[1] - v[2]) > 0 && sg * (v[2] - v[3]) > 0); });
  doc.shadow_combo = { rule: "v9.2 + sell after 3 EMA8 ticks against (not 1) + VIX bounce calls", trades: tr3,
    usd: Math.round(tr3.reduce((s, t) => s + (t.usd || 0), 0) * 100) / 100,
    ribbon_rule: "watch only: 3 contracts when the 5-min EMA 5/8/13/21 ribbon agrees with the trade, else 1",
    ribbon_x3_usd: Math.round(tr3.reduce((s, t) => s + (t.usd || 0) * (t.ribbon5 ? 3 : 1), 0) * 100) / 100 };
  const f = path.join(OUT, day + ".json");
  fs.writeFileSync(f, JSON.stringify(doc, null, 1));
  written.push(path.relative(ROOT, f));
}
// index: every logged morning + totals
const rows = fs.readdirSync(OUT).filter(f => /^\d{4}-\d\d-\d\d\.json$/.test(f)).sort().map(f => readJSON(path.join(OUT, f))).filter(Boolean)
  .map(d => { const sh = d.shadow_v93_vix_bounce || {}; return { date: d.date, forward: d.forward, n: d.trades.length, usd: d.usd, stops: d.stops,
    vix: sh.vix_yesterday == null ? null : sh.vix_yesterday, bounce_day: !!sh.bounce_day, shadow_usd: sh.usd == null ? d.usd : sh.usd,
    combo_usd: d.shadow_combo ? d.shadow_combo.usd : null, ribbon_x3_usd: d.shadow_combo && d.shadow_combo.ribbon_x3_usd != null ? d.shadow_combo.ribbon_x3_usd : null,
    ribbon_trades: d.shadow_combo ? d.shadow_combo.trades.filter(t => t.ribbon5).length : 0 }; });
const sum = a => Math.round(a.reduce((s, r) => s + r.usd, 0) * 100) / 100;
const fw = rows.filter(r => r.forward), last10 = rows.slice(-10);
let eq = 0, pk = 0, dd = 0; last10.forEach(r => { eq += r.usd; pk = Math.max(pk, eq); dd = Math.max(dd, pk - eq); });
fs.writeFileSync(path.join(OUT, "index.json"), JSON.stringify({
  updated_utc: new Date().toISOString(), forward_from: FORWARD_FROM, units: "dollars at 1 contract",
  totals: { all: { mornings: rows.length, usd: sum(rows) }, forward: { mornings: fw.length, usd: sum(fw), green: fw.filter(r => r.usd > 0).length, red: fw.filter(r => r.usd < 0).length },
    last10: { usd: sum(last10), worst_dip: Math.round(dd * 100) / 100, from: last10.length ? last10[0].date : null },
    shadow_v93_vix_bounce: { note: "v9.2 + VIX bounce calls (paper only, not on the card)",
      all: { usd: sum(rows.map(r => ({ usd: r.shadow_usd }))), vs_v92: Math.round((sum(rows.map(r => ({ usd: r.shadow_usd }))) - sum(rows)) * 100) / 100, bounce_days: rows.filter(r => r.bounce_day).length },
      forward: { usd: sum(fw.map(r => ({ usd: r.shadow_usd }))), vs_v92: Math.round((sum(fw.map(r => ({ usd: r.shadow_usd }))) - sum(fw)) * 100) / 100, bounce_days: fw.filter(r => r.bounce_day).length } },
    shadow_combo: { note: "v9.2 + 3-tick trail + VIX bounce calls (paper only; the card's signals stay v9.2)",
      all: { usd: sum(rows.map(r => ({ usd: r.combo_usd == null ? r.usd : r.combo_usd }))) },
      forward: { usd: sum(fw.map(r => ({ usd: r.combo_usd == null ? r.usd : r.combo_usd }))), mornings: fw.length },
      last10: { usd: sum(last10.map(r => ({ usd: r.combo_usd == null ? r.usd : r.combo_usd }))) },
      ribbon_watch: { note: "combo with 3 contracts when the 5-min EMA 5/8/13/21 ribbon agrees, else 1 (paper only)",
        all: { usd: sum(rows.map(r => ({ usd: r.ribbon_x3_usd == null ? 0 : r.ribbon_x3_usd }))), ribbon_trades: rows.reduce((s, r) => s + r.ribbon_trades, 0) },
        forward: { usd: sum(fw.map(r => ({ usd: r.ribbon_x3_usd == null ? 0 : r.ribbon_x3_usd }))), ribbon_trades: fw.reduce((s, r) => s + r.ribbon_trades, 0) } } } },
  days: rows }, null, 1));
written.push(path.relative(ROOT, path.join(OUT, "index.json")));
written.forEach(p => console.log(p));
