#!/usr/bin/env node
/* Bot Trade Replay data — every trade the bot (the v9.5 card + options) made, for the Trade Replay tab.
   Reads the nightly paper log (v92_log/<day>.json), the day's bars + GEX points (history/<day>.json) and the logger's option quotes
   (data/<day>/am/*.csv) and writes market-dash/bot_replay.js (window.BOTREPLAY). Called by v92_paper_log.js at the end of every run;
   can also run alone:  node market-dash/bot_replay_build.js
   Units: option prices in 0.00 per contract; P&L in dollars (contracts as the bot sized them). */
"use strict";
const fs = require("fs"), path = require("path");
function build(ROOT) {
  ROOT = ROOT || process.cwd();
  const MD = path.join(ROOT, "market-dash"), LOG = path.join(MD, "v92_log");
  if (!global.V92MAP) global.V92MAP = require(path.join(MD, "v92_map.js"));
  const rj = p => { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return null; } };
  const r2 = v => v == null || !isFinite(v) ? null : Math.round(v * 100) / 100;
  const toM = c => { if (!c) return null; const [h, m] = c.split(":").map(Number); return (h < 6 ? h + 12 : h) * 60 + m - 390; };
  const num = x => (x === "" || x == null ? null : Number(x));
  function csv(f) { if (!fs.existsSync(f)) return null; const L = fs.readFileSync(f, "utf8").trim().split("\n"), h = L[0].split(","); return L.slice(1).map(l => { const v = l.split(","), o = {}; h.forEach((k, i) => o[k] = v[i]); return o; }); }
  function quotes(day) { const dir = path.join(ROOT, "data", day, "am"), out = [];
    const sep = [["C", "calls"], ["P", "puts"]].map(([s, n]) => [s, csv(path.join(dir, `qqq_greeks_${n}_${day}_am.csv`))]);
    if (sep.some(([, r]) => r)) { sep.forEach(([side, rows]) => (rows || []).forEach(r => { if (r.expiration !== day) return; out.push({ t: Date.parse(r.run_time.replace(" ", "T") + "Z") / 1000, side, strike: num(r.strike), bid: num(r.bid), ask: num(r.ask), iv: num(r.iv) }); })); return out; }
    (csv(path.join(dir, `qqq_greeks_${day}_am.csv`)) || []).forEach(r => { if (r.expiration !== day) return; const dl = num(r.delta); if (dl === null) return; out.push({ t: Date.parse(r.run_time.replace(" ", "T") + "Z") / 1000, side: dl >= 0 ? "C" : "P", strike: num(r.strike), bid: num(r.bid), ask: num(r.ask), iv: num(r.iv) }); });
    return out; }
  const index = rj(path.join(MD, "v92_log", "index.json")) || { days: [] }, ROW = {}; index.days.forEach(r => ROW[r.date] = r);
  const hix = rj(path.join(MD, "history", "index.json")), hdays = ((hix && hix.days) || []).filter(d => fs.existsSync(path.join(MD, "history", d + ".json")));
  const CLOSES = {}; const closesOf = d => { if (CLOSES[d] !== undefined) return CLOSES[d]; const j = rj(path.join(MD, "history", d + ".json")); CLOSES[d] = j && j.candles && j.candles.length >= 300 ? j.candles.slice(0, 390).map(c => c.close) : null; return CLOSES[d]; };
  const logged = fs.readdirSync(LOG).filter(f => /^\d{4}-\d\d-\d\d\.json$/.test(f)).map(f => f.slice(0, 10)).sort();
  const N = 181;   // 6:30 .. 9:30 PT
  const out = { built: new Date().toISOString(), units: "option prices 0.00 per contract · P&L in dollars at the bot's size", days: {}, cards: [], daily: [] };
  for (const day of logged) {
    const doc = rj(path.join(LOG, day + ".json")), H = rj(path.join(MD, "history", day + ".json")); if (!doc || !H || !H.candles) continue;
    const t0 = V92MAP.open930(day), bars = []; H.candles.forEach(c => { const m = Math.floor((c.time - t0) / 60); if (m >= 0 && m < N) bars[m] = c; });
    let pv = 0, vv = 0, lastC = null, lastVW = null; const candles = [];
    for (let m = 0; m < N; m++) { const b = bars[m]; if (b) { lastC = b.close; if (b.volume > 0) { pv += (b.high + b.low + b.close) / 3 * b.volume; vv += b.volume; } lastVW = vv > 0 ? pv / vv : b.close; candles.push([r2(b.open), r2(b.high), r2(b.low), r2(b.close), b.volume || 0, r2(lastVW)]); }
      else if (lastC != null) candles.push([r2(lastC), r2(lastC), r2(lastC), r2(lastC), 0, r2(lastVW)]); else candles.push(null); }
    const f0 = candles.findIndex(Boolean); if (f0 < 0) continue; for (let m = 0; m < f0; m++) candles[m] = candles[f0].slice();
    // the user's lines: green EMA 8 / red EMA 304, seeded on the 3 prior sessions
    const prev = hdays.filter(d => d < day).slice(-3).map(closesOf).filter(Boolean), yc = [].concat(...prev), lines = {};
    const seed = {}; [8, 14, 35, 52, 80, 120, 250, 304, 400].forEach(n => { const a = 2 / (n + 1); let e = null; yc.forEach(x => { e = e === null ? x : a * x + (1 - a) * e; }); seed[n] = e == null ? null : Math.round(e * 10000) / 10000;
      if (n === 8 || n === 304) lines[n] = candles.map(k => { e = e === null ? k[3] : a * k[3] + (1 - a) * e; return r2(e); }); });
    const lv = { call_wall: [], put_wall: [], gamma_flip: [] }, P = []; (H.points || []).forEach(p => { const m = Math.floor((p.time - t0) / 60); if (m >= -30 && m < N) P[m + 30] = p; });
    let lp = null; for (let m = -30; m < N; m++) { if (P[m + 30]) lp = P[m + 30]; if (m >= 0) Object.keys(lv).forEach(k => lv[k].push(lp && lp[k] != null ? lp[k] : null)); }
    const mp = doc.map || {}, row = ROW[day] || {};
    out.days[day] = { weekday: new Date(day + "T12:00:00Z").toUTCString().slice(0, 3), candles, g8: lines[8], r304: lines[304], levels: lv,
      yday: { high: mp.yday_high, low: mp.yday_low, close: mp.yday_close }, map: mp, vix: row.vix == null ? null : row.vix, htf: row.htf || null, seed, va: mp.value_area || null };
    // option quotes per minute (last quote of the minute)
    const Q = quotes(day), OQ = {}; Q.forEach(q => { if (!(q.bid > 0) || !(q.ask > 0)) return; const m = Math.floor((q.t - t0) / 60); if (m < -5 || m > 335) return; (OQ[q.side + "|" + q.strike] = OQ[q.side + "|" + q.strike] || {})[m] = q; });
    // contract mid + bid per minute, with the option's own EMA 11 / 18 (forward-filled mid from its first quote, same as the bot)
    function series(side, K, a, z) { const o = OQ[side + "|" + Number(K)] || {}, s = [], e = { 11: null, 18: null }; let last = null;
      for (let m = -5; m <= z; m++) { if (o[m]) last = (o[m].bid + o[m].ask) / 2; if (last == null) continue; [11, 18].forEach(n => { const k = 2 / (n + 1); e[n] = e[n] === null ? last : k * last + (1 - k) * e[n]; });
        if (m >= a && o[m]) s.push([m, r2(last), r2(o[m].bid), r2(e[11]), r2(e[18])]); } return s; }
    function path_(side, K, fill, exit, paid) { const o = OQ[side + "|" + Number(K)] || {}; const q0 = o[fill] || o[fill + 1] || o[fill - 1]; const aMid = q0 ? (q0.bid + q0.ask) / 2 : paid; let best = -9, worst = 9, hit30 = null, hit50 = null;
      for (let m = fill + 1; m <= (exit == null ? fill + 90 : exit); m++) { const q = o[m]; if (!q) continue; const mid = (q.bid + q.ask) / 2; best = Math.max(best, q.bid - paid); worst = Math.min(worst, q.bid - paid); if (hit30 == null && mid >= aMid + 0.30) hit30 = m; if (hit50 == null && mid >= aMid + 0.50) hit50 = m; }
      let after = -9; for (let m = (exit == null ? fill : exit) + 1; m <= (exit == null ? fill : exit) + 30; m++) { const q = o[m]; if (q) after = Math.max(after, q.bid - paid); }
      return { aMid: r2(aMid), iv: q0 ? q0.iv : null, best: best > -9 ? r2(best) : null, worst: worst < 9 ? r2(worst) : null, hit30, hit50, after: after > -9 ? r2(after) : null }; }
    // the contract's own 30-min candles (mid), 6:30 .. 12:00 PT: [open, high, low, close] or null
    function c30(side, K) { const o = OQ[side + "|" + Number(K)] || {}, out = [];
      for (let w = 0; w < 11; w++) { const v = []; for (let m = w * 30; m < w * 30 + 30; m++) if (o[m]) v.push((o[m].bid + o[m].ask) / 2);
        out.push(v.length >= 5 ? [r2(v[0]), r2(Math.max(...v)), r2(Math.min(...v)), r2(v[v.length - 1])] : null); }
      while (out.length && out[out.length - 1] === null) out.pop();
      return out; }
    const sideOf = s => s === "call" ? "C" : "P";
    const cardsToday = [];
    (doc.shadow_v95 && doc.shadow_v95.trades || []).forEach((t, i) => { const fill = toM(t.filled), exit = toM(t.exit), side = sideOf(t.side), pth = path_(side, t.strike, fill, exit, t.paid);
      cardsToday.push({ src: "card", fake: t.fakeout_line == null ? null : t.fakeout_line, setup: t.setup, side: t.side, strike: t.strike, sig: toM(t.signal), m: fill, exit_m: exit, paid: t.paid, sold: t.sold, why: t.why, per: t.usd, ct: t.contracts || 1, pnl: r2((t.usd || 0) * (t.contracts || 1)),
        opt: series(side, t.strike, fill - 10, (exit == null ? fill + 60 : exit) + 20), c30: c30(side, t.strike), path: pth }); });
    (doc.shadow_options && doc.shadow_options.trades || []).forEach(t => { const sig = toM(t.signal), fill = sig + 1, exit = toM(t.exit), side = sideOf(t.side), pth = path_(side, t.strike, fill, exit, t.paid);
      cardsToday.push({ src: "option", box: t.box || null, level: t.level || null, levelPx: t.level_px == null ? null : t.level_px, setup: t.from, how: t.how, flipped_from: t.flipped_from || null, side: t.side, strike: t.strike, sig, m: fill, exit_m: exit, paid: t.paid, sold: t.paid == null ? null : r2(t.paid + (t.usd + 2.6) / 100), why: t.why, per: t.usd, ct: t.contracts || 1, pnl: r2((t.usd || 0) * (t.contracts || 1)),
        opt: series(side, t.strike, fill - 10, (exit == null ? fill + 60 : exit) + 20), c30: c30(side, t.strike), path: pth }); });
    cardsToday.sort((a, b) => a.m - b.m).forEach((c, i) => { c.date = day; c.n = i + 1; out.cards.push(c); });
    const cs = cardsToday.filter(c => c.src === "card"), os = cardsToday.filter(c => c.src === "option");
    out.daily.push({ date: day, weekday: out.days[day].weekday, card: r2(cs.reduce((a, c) => a + c.pnl, 0)), cardN: cs.length, card1: r2(cs.reduce((a, c) => a + (c.per || 0), 0)), opt: r2(os.reduce((a, c) => a + c.pnl, 0)), optN: os.length, forward: !!doc.forward });
  }
  const f = path.join(MD, "bot_replay.js");
  fs.writeFileSync(f, "window.BOTREPLAY = " + JSON.stringify(out) + ";\n");
  return path.relative(ROOT, f);
}
if (require.main === module) console.log(build(process.cwd()));
module.exports = { build };
