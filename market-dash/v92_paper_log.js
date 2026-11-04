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
const V95OPT = require(path.join(MD, "v95_options.js"));   // "v9.5 + options" shadow (paper only)
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
// v9.5 candidate (paper only): the user's MAIN QQQ 1m chart lines = "the ribbon":
// green EMA 8/14/35 · yellow 52/80 · orange 120 · red 250/304/400, seeded on the 3 prior sessions, plus session VWAP.
// Returns null when anything is missing -> the v9.5 shadow switches off for that morning (never guesses).
const UL = [8, 14, 35, 52, 80, 120, 250, 304, 400], USTK = [[8, 52, 304], [14, 80, 250], [35, 120, 400]];
function userLines(hist, st, candles, t0) {
  try {
    const ds = Object.keys(hist).sort().slice(-3); if (ds.length < 3) return null;
    const yc = [].concat(...ds.map(d => hist[d].slice(0, 390).map(c => c.close))), ts = st.map(x => x ? x[0] : null), f = ts.find(x => x !== null);
    if (f == null) return null;
    const c = ts.map(x => x === null ? f : x), cl = yc.concat(c), V = { c };
    UL.forEach(n => { const a = 2 / (n + 1); let e = null; const out = []; cl.forEach((x, i) => { e = e === null ? x : a * x + (1 - a) * e; if (i >= yc.length) out.push(e); }); V[n] = out; });
    const bars = {}; (candles || []).forEach(k => { const m = Math.floor((k.time - t0) / 60); if (m >= 0 && m < 390) bars[m] = k; });
    let pv = 0, vv = 0, last = c[0]; V.vw = c.map((x, m) => { const b = bars[m]; if (b && b.volume > 0) { pv += (b.high + b.low + b.close) / 3 * b.volume; vv += b.volume; } if (vv > 0) last = pv / vv; return last; });
    if (!(vv > 0) || UL.some(n => V[n].some(v => !isFinite(v)))) return null;
    return V;
  } catch (e) { return null; }
}
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
// ---- higher time frames (user's MA17 on daily + weekly): trend from closes BEFORE the morning (v9.5 bounce-call sizing) ----
// daily closes = qqq_candle_history.json daily bars, filled with the 4:00 PM ET close of any saved history day it is missing.
const CLOSES = (() => { const m = {};
  const ch = readJSON(path.join(MD, "qqq_candle_history.json"));
  ((ch && ch.daily) || []).forEach(c => { m[new Date((c.time + 12 * 3600) * 1000).toISOString().slice(0, 10)] = c.close; });
  days.forEach(d => { if (m[d] != null) return; const j = readJSON(path.join(MD, "history", d + ".json")); if (j && j.candles && j.candles.length >= 390) m[d] = j.candles[389].close; });
  return Object.keys(m).sort().map(d => ({ d, c: m[d] })); })();
function htfBefore(day) {   // {daily, weekly}: +1 up / -1 down / 0 mixed — close vs its MA17 and the MA17's slope; null if not enough history
  const avg = (a, n, i) => i + 1 < n ? null : a.slice(i + 1 - n, i + 1).reduce((x, y) => x + y, 0) / n;
  const D = CLOSES.filter(x => x.d < day); if (D.length < 30) return null;
  const C = D.map(x => x.c), i = C.length - 1, m17 = avg(C, 17, i), m17p = avg(C, 17, i - 5);
  const W = []; D.forEach(x => { const dt = new Date(x.d + "T12:00:00Z"), wk = new Date(dt - ((dt.getUTCDay() + 6) % 7) * 864e5).toISOString().slice(0, 10);
    if (!W.length || W[W.length - 1].w !== wk) W.push({ w: wk, c: x.c }); else W[W.length - 1].c = x.c; });
  const WC = W.map(x => x.c), wi = WC.length - 1, w17 = avg(WC, 17, wi), w17p = avg(WC, 17, wi - 4);
  if (m17 == null || m17p == null || w17 == null || w17p == null) return null;
  return { through: D[D.length - 1].d, daily: C[i] > m17 && m17 > m17p ? 1 : C[i] < m17 && m17 < m17p ? -1 : 0, weekly: WC[wi] > w17 && w17 > w17p ? 1 : WC[wi] < w17 && w17 < w17p ? -1 : 0 };
}
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
  // shadow 3: v9.5 CANDIDATE = combo + failed poke "B" (gives way to v9.2 setups, keeps its one flip, never uses A's limits,
  // max 1/day 7:00-8:30) + 3 contracts on A trades when the user's lines agree. Paper only — the card's signals stay v9.2.
  const UV = userLines(hist, D.st, H.candles, D.info && D.info.t0);
  if (!UV) doc.shadow_v95 = { status: "off this morning: the user's lines / VWAP could not be built (missing data)", trades: [], usd_1ct: null, usd_sized: null };
  else {
    const o5 = Object.assign({}, o3, { fp: "ma17", fpVwap: true, fpDay: true, fpYield: true, fpYieldTo: ["trap", "gap", "fw"], fpFlip: true, fpStart: 30, fpEnd: 120 });
    const R5 = V92.runDay(Object.assign({}, D, { ind: { m17: UV[14], vw: UV.vw } }), o5), r5 = R5.trades.filter(t => t.why !== "none");
    const fan = m => { const o = USTK.map(k => k.every((n, i) => i === 0 || UV[k[i - 1]][m] > UV[n][m]) ? 1 : k.every((n, i) => i === 0 || UV[k[i - 1]][m] < UV[n][m]) ? -1 : 0); return o.every(x => x === 1) ? 1 : o.every(x => x === -1) ? -1 : 0; };
    const spread = m => { const v = UL.map(n => UV[n][m]); return Math.max(...v) - Math.min(...v); };
    const HT = htfBefore(day);
    const x3 = t => { if (t.kind === "fp") return false; const sg = t.side === "C" ? 1 : -1, m = t.m;
      if (t.kind.indexOf("vb") >= 0 && HT && HT.daily === -sg && HT.weekly === -sg) return false;   // bounce call against daily + weekly -> 1x
      return t.kind === "trap" ? (fan(m) === -sg && spread(m) >= 5) : sg * (UV[8][m] - UV[304][m]) > 0; };
    const tr5 = r5.map(t => ({ side: t.side === "C" ? "call" : "put", setup: t.kind === "fp" ? "failed poke (B)" : t.kind, signal: clock(t.m), filled: clock(t.mf), strike: t.K,
      paid: +t.ask.toFixed(2), exit: clock(t.out), sold: t.px == null ? null : +t.px.toFixed(2),
      why: { stop: "stop", q8: "trail (3 ticks)", time: "time exit", yield: "gave way to a v9.2 setup" }[t.why] || t.why,
      usd: t.res == null ? null : Math.round(t.res * 10000) / 100, contracts: x3(t) ? 3 : 1 }));
    doc.shadow_v95 = { rule: "v9.5 candidate: combo + failed poke B (rejected at green 14, room to VWAP, trap-color days, gives way to trap/gap/fade) + 3 contracts on A trades when your lines agree (green 8 vs red 304; traps: stretched from the reds)",
      trades: tr5, usd_1ct: Math.round(tr5.reduce((s, t) => s + (t.usd || 0), 0) * 100) / 100,
      usd_sized: Math.round(tr5.reduce((s, t) => s + (t.usd || 0) * t.contracts, 0) * 100) / 100, htf: HT };
    // shadow 4: "v9.5 + options" — 6 older strategies add trades in the card's direction (agree = as fired · against = flipped to the
    // card's side with the card's ladder · before the card's first buy only the box). 1 contract. Paper only.
    try {
      const OP = V95OPT.run(D, H, hist, UV, D.info && D.info.t0, r5.map(t => ({ side: t.side, m: t.m, mf: t.mf })), day);
      const tro = OP.trades.map(t => ({ how: t.how, from: t.src, side: t.side === "C" ? "call" : "put", signal: clock(Math.floor(t.m)), strike: t.K, paid: t.paid == null ? null : +t.paid.toFixed(2),
        exit: t.out == null ? null : clock(t.out), why: t.why, usd: Math.round(t.usd * 100) / 100, flipped_from: t.flipped_from ? (t.flipped_from === "C" ? "call" : "put") : undefined }));
      const addUsd = Math.round(tro.reduce((s, t) => s + t.usd, 0) * 100) / 100;
      doc.shadow_options = { rule: "v9.5 (1 ct) + options: box · Playbook home run · Tulip v3.2 · turn entry · sniper · calls checkpoint. Card's latest side = direction; agreeing signals as fired; signals against it flipped to the card's side (card's ladder); before the card's first buy only the box.",
        trades: tro, addons_usd: addUsd, total_usd: Math.round((doc.shadow_v95.usd_1ct + addUsd) * 100) / 100 };
    } catch (e) { doc.shadow_options = { status: "off this morning: " + e.message, trades: [], addons_usd: null, total_usd: null }; }
  }
  const f = path.join(OUT, day + ".json");
  fs.writeFileSync(f, JSON.stringify(doc, null, 1));
  written.push(path.relative(ROOT, f));
}
// index: every logged morning + totals
const rows = fs.readdirSync(OUT).filter(f => /^\d{4}-\d\d-\d\d\.json$/.test(f)).sort().map(f => readJSON(path.join(OUT, f))).filter(Boolean)
  .map(d => { const sh = d.shadow_v93_vix_bounce || {}; return { date: d.date, forward: d.forward, n: d.trades.length, usd: d.usd, stops: d.stops,
    vix: sh.vix_yesterday == null ? null : sh.vix_yesterday, bounce_day: !!sh.bounce_day, shadow_usd: sh.usd == null ? d.usd : sh.usd,
    combo_usd: d.shadow_combo ? d.shadow_combo.usd : null, ribbon_x3_usd: d.shadow_combo && d.shadow_combo.ribbon_x3_usd != null ? d.shadow_combo.ribbon_x3_usd : null,
    ribbon_trades: d.shadow_combo ? d.shadow_combo.trades.filter(t => t.ribbon5).length : 0,
    htf: d.shadow_v95 && d.shadow_v95.htf ? { daily: d.shadow_v95.htf.daily, weekly: d.shadow_v95.htf.weekly } : null,
    v95_usd: d.shadow_v95 && d.shadow_v95.usd_1ct != null ? d.shadow_v95.usd_1ct : null, v95_sized_usd: d.shadow_v95 && d.shadow_v95.usd_sized != null ? d.shadow_v95.usd_sized : null,
    opt_addons_usd: d.shadow_options && d.shadow_options.addons_usd != null ? d.shadow_options.addons_usd : null, opt_addons_n: d.shadow_options ? d.shadow_options.trades.length : 0 }; });
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
        forward: { usd: sum(fw.map(r => ({ usd: r.ribbon_x3_usd == null ? 0 : r.ribbon_x3_usd }))), ribbon_trades: fw.reduce((s, r) => s + r.ribbon_trades, 0) } } },
    shadow_v95: { note: "v9.5 candidate (paper only): combo + failed poke B + 3x on A trades by your lines. Promote over the combo only if, after 10 forward mornings, it made more, drawdown no bigger, zero glitches.",
      all: { usd_1ct: sum(rows.map(r => ({ usd: r.v95_usd == null ? 0 : r.v95_usd }))), usd_sized: sum(rows.map(r => ({ usd: r.v95_sized_usd == null ? 0 : r.v95_sized_usd }))), off_mornings: rows.filter(r => r.v95_usd == null).length },
      forward: { usd_1ct: sum(fw.map(r => ({ usd: r.v95_usd == null ? 0 : r.v95_usd }))), usd_sized: sum(fw.map(r => ({ usd: r.v95_sized_usd == null ? 0 : r.v95_sized_usd }))), mornings: fw.length },
      last10: { usd_1ct: sum(last10.map(r => ({ usd: r.v95_usd == null ? 0 : r.v95_usd }))), usd_sized: sum(last10.map(r => ({ usd: r.v95_sized_usd == null ? 0 : r.v95_sized_usd }))) } },
    shadow_options: { note: "v9.5 + options (paper only, 1 ct): 6 older strategies add trades in the card's direction; signals against it are flipped to the card's side. Keep only if it stays positive.",
      all: { addons_usd: sum(rows.map(r => ({ usd: r.opt_addons_usd || 0 }))), addons: rows.reduce((s, r) => s + r.opt_addons_n, 0), total_usd: sum(rows.map(r => ({ usd: (r.v95_usd || 0) + (r.opt_addons_usd || 0) }))) },
      forward: { addons_usd: sum(fw.map(r => ({ usd: r.opt_addons_usd || 0 }))), addons: fw.reduce((s, r) => s + r.opt_addons_n, 0), total_usd: sum(fw.map(r => ({ usd: (r.v95_usd || 0) + (r.opt_addons_usd || 0) }))), mornings: fw.length },
      last10: { addons_usd: sum(last10.map(r => ({ usd: r.opt_addons_usd || 0 }))), total_usd: sum(last10.map(r => ({ usd: (r.v95_usd || 0) + (r.opt_addons_usd || 0) }))) } } },
  htf_next: htfBefore("9999-12-31"),   // daily/weekly trend for the NEXT morning (the card reads this)
  days: rows }, null, 1));
written.push(path.relative(ROOT, path.join(OUT, "index.json")));
written.forEach(p => console.log(p));
