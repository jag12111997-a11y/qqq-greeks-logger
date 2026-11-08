#!/usr/bin/env node
/* My Trades replay data — the user's own QQQ option trades (from market-dash/my_trades_list.json, built from the complete
   Webull order files) with each day's 1-min QQQ bars (history/<day>.json, full session) and the logger's quotes for the
   contract (data/<day>/am + pm CSVs). Writes market-dash/my_replay.js (window.MYREPLAY) in the same shape as bot_replay.js.
   Run:  node market-dash/my_replay_build.js      Units: option prices 0.00 per contract; P&L in dollars (before fees). */
"use strict";
const fs = require("fs"), path = require("path");
const ROOT = process.cwd(), MD = path.join(ROOT, "market-dash");
global.V92MAP = require(path.join(MD, "v92_map.js"));
const rj = p => { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return null; } };
const r2 = v => v == null || !isFinite(v) ? null : Math.round(v * 100) / 100;
const num = x => (x === "" || x == null ? null : Number(x));
function csv(f) { if (!fs.existsSync(f)) return null; const L = fs.readFileSync(f, "utf8").trim().split("\n"), h = L[0].split(","); return L.slice(1).map(l => { const v = l.split(","), o = {}; h.forEach((k, i) => o[k] = v[i]); return o; }); }
function quotes(day) { const out = [];
  for (const ses of ["am", "pm"]) { const dir = path.join(ROOT, "data", day, ses);
    const sep = [["C", "calls"], ["P", "puts"]].map(([s, n]) => [s, csv(path.join(dir, `qqq_greeks_${n}_${day}_${ses}.csv`))]);
    if (sep.some(([, r]) => r)) { sep.forEach(([side, rows]) => (rows || []).forEach(r => { if (r.expiration !== day) return; out.push({ t: Date.parse(r.run_time.replace(" ", "T") + "Z") / 1000, side, strike: num(r.strike), bid: num(r.bid), ask: num(r.ask), iv: num(r.iv) }); })); continue; }
    (csv(path.join(dir, `qqq_greeks_${day}_${ses}.csv`)) || []).forEach(r => { if (r.expiration !== day) return; const dl = num(r.delta); if (dl === null) return; out.push({ t: Date.parse(r.run_time.replace(" ", "T") + "Z") / 1000, side: dl >= 0 ? "C" : "P", strike: num(r.strike), bid: num(r.bid), ask: num(r.ask), iv: num(r.iv) }); }); }
  return out; }
const T = rj(path.join(MD, "my_trades_list.json")) || [];
const hix = rj(path.join(MD, "history", "index.json")), hdays = ((hix && hix.days) || []).filter(d => fs.existsSync(path.join(MD, "history", d + ".json")));
const CL = {}; const closesOf = d => { if (CL[d] !== undefined) return CL[d]; const j = rj(path.join(MD, "history", d + ".json")); CL[d] = j && j.candles && j.candles.length >= 300 ? j.candles.slice(0, 390).map(c => c.close) : null; return CL[d]; };
const N = 391, out = { built: new Date().toISOString(), units: "option prices 0.00 per contract · P&L in dollars before fees (same as Trading Performance)", days: {}, cards: [], daily: [], skipped: [] };
const byDay = {}; T.forEach(t => (byDay[t.date] = byDay[t.date] || []).push(t));
for (const day of Object.keys(byDay).sort()) {
  const H = rj(path.join(MD, "history", day + ".json")); if (!H || !H.candles) { byDay[day].forEach(t => out.skipped.push(day + " (no bars)")); continue; }
  const t0 = V92MAP.open930(day), bars = []; H.candles.forEach(c => { const m = Math.floor((c.time - t0) / 60); if (m >= 0 && m < N) bars[m] = c; });
  let pv = 0, vv = 0, lastC = null, lastVW = null; const candles = [];
  for (let m = 0; m < N; m++) { const b = bars[m]; if (b) { lastC = b.close; if (b.volume > 0) { pv += (b.high + b.low + b.close) / 3 * b.volume; vv += b.volume; } lastVW = vv > 0 ? pv / vv : b.close; candles.push([r2(b.open), r2(b.high), r2(b.low), r2(b.close), b.volume || 0, r2(lastVW)]); }
    else if (lastC != null) candles.push([r2(lastC), r2(lastC), r2(lastC), r2(lastC), 0, r2(lastVW)]); else candles.push(null); }
  const f0 = candles.findIndex(Boolean); if (f0 < 0) continue; for (let m = 0; m < f0; m++) candles[m] = candles[f0].slice();
  while (candles.length && candles[candles.length - 1] && candles[candles.length - 1][4] === 0 && !bars[candles.length - 1]) candles.pop();
  const prev = hdays.filter(d => d < day).slice(-3).map(closesOf).filter(Boolean), yc = [].concat(...prev), lines = {}, seed = {};
  [8, 14, 35, 52, 80, 120, 250, 304, 400].forEach(n => { const a = 2 / (n + 1); let e = null; yc.forEach(x => { e = e === null ? x : a * x + (1 - a) * e; }); seed[n] = e == null ? null : Math.round(e * 10000) / 10000;
    if (n === 8 || n === 304) lines[n] = candles.map(k => { e = e === null ? k[3] : a * k[3] + (1 - a) * e; return r2(e); }); });
  const lv = { call_wall: [], put_wall: [], gamma_flip: [] }, P = []; (H.points || []).forEach(p => { const m = Math.floor((p.time - t0) / 60); if (m >= -30 && m < N) P[m + 30] = p; });
  let lp = null; for (let m = -30; m < candles.length; m++) { if (P[m + 30]) lp = P[m + 30]; if (m >= 0) Object.keys(lv).forEach(k => lv[k].push(lp && lp[k] != null ? lp[k] : null)); }
  // yesterday's levels from the previous session's bars
  const pd = hdays.filter(d => d < day).slice(-1)[0], PH = pd ? rj(path.join(MD, "history", pd + ".json")) : null, pc = PH && PH.candles ? PH.candles.slice(0, 390) : [];
  const yday = pc.length ? { high: Math.max(...pc.map(c => c.high)), low: Math.min(...pc.map(c => c.low)), close: pc[pc.length - 1].close } : {};
  out.days[day] = { weekday: new Date(day + "T12:00:00Z").toUTCString().slice(0, 3), candles, g8: lines[8], r304: lines[304], levels: lv, yday, map: { yday_high: yday.high, yday_low: yday.low, yday_close: yday.close }, vix: null, htf: null, seed, va: null };
  const Q = quotes(day), OQ = {}; Q.forEach(q => { if (!(q.bid > 0) || !(q.ask > 0)) return; const m = Math.floor((q.t - t0) / 60); if (m < -5 || m > N) return; (OQ[q.side + "|" + q.strike] = OQ[q.side + "|" + q.strike] || {})[m] = q; });
  function series(side, K, a, z) { const o = OQ[side + "|" + Number(K)] || {}, s = [], e = { 11: null, 18: null }; let last = null;
    for (let m = -5; m <= z; m++) { if (o[m]) last = (o[m].bid + o[m].ask) / 2; if (last == null) continue; [11, 18].forEach(n => { const k = 2 / (n + 1); e[n] = e[n] === null ? last : k * last + (1 - k) * e[n]; });
      if (m >= a && o[m]) s.push([m, r2(last), r2(o[m].bid), r2(e[11]), r2(e[18])]); } return s; }
  function c30(side, K) { const o = OQ[side + "|" + Number(K)] || {}, res = [];
    for (let w = 0; w < 13; w++) { const v = []; for (let m = w * 30; m < w * 30 + 30; m++) if (o[m]) v.push((o[m].bid + o[m].ask) / 2); res.push(v.length >= 5 ? [r2(v[0]), r2(Math.max(...v)), r2(Math.min(...v)), r2(v[v.length - 1])] : null); }
    while (res.length && res[res.length - 1] === null) res.pop(); return res; }
  function pathOf(side, K, fill, exit, paid) { const o = OQ[side + "|" + Number(K)] || {}, q0 = o[fill] || o[fill + 1] || o[fill - 1], aMid = q0 ? (q0.bid + q0.ask) / 2 : paid; let best = -9, worst = 9, after = -9;
    for (let m = fill + 1; m <= exit; m++) { const q = o[m]; if (!q) continue; best = Math.max(best, q.bid - paid); worst = Math.min(worst, q.bid - paid); }
    for (let m = exit + 1; m <= exit + 30; m++) { const q = o[m]; if (q) after = Math.max(after, q.bid - paid); }
    return { aMid: r2(aMid), iv: q0 ? q0.iv : null, best: best > -9 ? r2(best) : null, worst: worst < 9 ? r2(worst) : null, hit30: null, hit50: null, after: after > -9 ? r2(after) : null }; }
  const today = byDay[day].map(t => { const side = t.side === "call" ? "C" : "P", fill = Math.floor(t.m), exit = Math.min(Math.floor(t.exit_m), N - 1);
    return { src: "you", setup: "you", how: null, side: t.side, strike: t.strike, sig: fill, m: fill, exit_m: exit, paid: t.paid, sold: t.sold, why: t.why, per: t.per, ct: t.ct, pnl: t.pnl,
      tin: t.tin, tout: t.tout, opt: series(side, t.strike, fill - 10, exit + 20), c30: c30(side, t.strike), path: pathOf(side, t.strike, fill, exit, t.paid), fake: null, box: null, level: null, levelPx: null }; });
  today.sort((a, b) => a.m - b.m || (a.tin < b.tin ? -1 : 1)).forEach((c, i) => { c.date = day; c.n = i + 1; out.cards.push(c); });
  out.daily.push({ date: day, weekday: out.days[day].weekday, card: r2(today.reduce((a, c) => a + c.pnl, 0)), cardN: today.length, card1: 0, opt: 0, optN: 0, forward: false });
}
fs.writeFileSync(path.join(MD, "my_replay.js"), "window.MYREPLAY = " + JSON.stringify(out) + ";\n");
console.log("market-dash/my_replay.js", out.cards.length, "trades,", Object.keys(out.days).length, "days, skipped", out.skipped.length,
  "| with quotes:", out.cards.filter(c => c.opt.length).length, "| P&L", r2(out.cards.reduce((a, c) => a + c.pnl, 0)));
