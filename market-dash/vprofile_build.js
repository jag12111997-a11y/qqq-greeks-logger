#!/usr/bin/env node
/* 3v3 VOLUME PROFILE — saved every night (added Nov 22, 2026). PAPER / RESEARCH ONLY.
   Builds QQQ's volume-at-price for every logged day from the site's own 1-min RTH candles (history/<day>.json): each bar's volume is
   spread evenly over its high–low range in $0.25 bins (the standard way to build a profile from bars; true tick volume-at-price isn't in
   the feed). Per day: the bins, POC (most volume), value area (70%), the biggest nodes and the thinnest gaps. Also a 20-day composite
   (what a "1-month" profile on a chart shows) and the volume at each of the user's weekly levels (my_levels.json): how much traded within
   $0.25 of the line that day, and where that sits in the 20-day profile (percentile: 100 = the heaviest price, 0 = empty).
   Writes market-dash/vprofile.json. Usage (repo root): node market-dash/vprofile_build.js   Prints the path it wrote. */
"use strict";
const fs = require("fs"), path = require("path");
const ROOT = process.cwd(), MD = path.join(ROOT, "market-dash"), BIN = 0.25;
const rj = p => { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return null; } };
const r2 = v => Math.round(v * 100) / 100;
const key = px => Math.round(Math.floor(px / BIN + 1e-9) * BIN * 100) / 100;
const ix = rj(path.join(MD, "history", "index.json")) || { days: [] };
const days = ix.days.filter(d => fs.existsSync(path.join(MD, "history", d + ".json")));
function dayProfile(c) { const h = {}; c.forEach(b => { const v = b.volume || 0; if (!v) return; const lo = key(b.low), hi = key(b.high), n = Math.round((hi - lo) / BIN) + 1;
  for (let i = 0; i < n; i++) { const k = r2(lo + i * BIN); h[k] = (h[k] || 0) + v / n; } }); return h; }
function summarize(h) {
  const ks = Object.keys(h).map(Number).sort((a, b) => a - b); if (!ks.length) return null;
  const lo = ks[0], hi = ks[ks.length - 1], n = Math.round((hi - lo) / BIN) + 1, v = [];
  for (let i = 0; i < n; i++) v.push(h[r2(lo + i * BIN)] || 0);
  const sm = v.map((_, i) => { let s = 0, m = 0; for (let j = Math.max(0, i - 2); j <= Math.min(n - 1, i + 2); j++) { s += v[j]; m++; } return s / m; });
  const tot = v.reduce((a, b) => a + b, 0); let poc = 0; sm.forEach((x, i) => { if (x > sm[poc]) poc = i; });
  const order = v.map((x, i) => i).sort((a, b) => v[b] - v[a]); let acc = 0, vlo = n, vhi = 0;
  for (const i of order) { acc += v[i]; vlo = Math.min(vlo, i); vhi = Math.max(vhi, i); if (acc >= 0.7 * tot) break; }
  const W = 8, peak = [], gap = [];
  for (let i = 0; i < n; i++) { const a = Math.max(0, i - W), b = Math.min(n - 1, i + W); let mx = 0, mn = Infinity; for (let j = a; j <= b; j++) { mx = Math.max(mx, sm[j]); mn = Math.min(mn, sm[j]); }
    if (sm[i] === mx && sm[i] > 0) peak.push(i); else if (sm[i] === mn && i > 0 && i < n - 1) gap.push(i); }
  const sorted = sm.slice().sort((a, b) => a - b);
  return { lo, hi, step: BIN, vol: v.map(x => Math.round(x)), total: Math.round(tot), poc: r2(lo + poc * BIN), va: [r2(lo + vlo * BIN), r2(lo + vhi * BIN)],
    nodes: peak.sort((a, b) => sm[b] - sm[a]).slice(0, 10).map(i => r2(lo + i * BIN)).sort((a, b) => b - a),
    gaps: gap.sort((a, b) => sm[a] - sm[b]).reduce((acc, i) => { if (acc.length < 10 && acc.every(j => Math.abs(j - i) > W)) acc.push(i); return acc; }, []).map(i => r2(lo + i * BIN)).sort((a, b) => b - a),
    pctAt: px => { if (px < lo || px > hi) return null; const i = Math.min(n - 1, Math.max(0, Math.round((px - lo) / BIN))); let k = 0; while (k < n && sorted[k] < sm[i]) k++; return Math.round(100 * k / n); } };
}
const P = {}, out = { updated: new Date().toISOString(), note: "volume at price from 1-min RTH candles, each bar's volume spread over its high–low in $0.25 bins · composite = last 20 sessions", days: {}, composite: null, levels: [] };
days.forEach(d => { const j = rj(path.join(MD, "history", d + ".json")); if (!j || !j.candles || j.candles.length < 300) return; P[d] = dayProfile(j.candles.slice(0, 390));
  const s = summarize(P[d]); if (s) { const o = Object.assign({}, s); delete o.pctAt; out.days[d] = o; } });
const good = Object.keys(P).sort(), last20 = good.slice(-20), comp = {};
// DAILY AUCTION (Nov 22): each day's value area vs the day before — where value moved, how the day opened and closed against it.
//  value: HIGHER (VAL above yesterday's VAH) · LOWER (VAH below yesterday's VAL) · OVERLAP-UP / OVERLAP-DOWN (overlapping, POC moved up / down)
//  · INSIDE (whole VA inside yesterday's) · OUTSIDE (VA wider on both sides). open / close: above / inside / below yesterday's / today's VA.
good.forEach((d, i) => { if (!i || !out.days[d] || !out.days[good[i - 1]]) return;
  const T = out.days[d], Y = out.days[good[i - 1]], j = rj(path.join(MD, "history", d + ".json")), c = j.candles.slice(0, 390);
  const op = c[0].open, cl = c[c.length - 1].close, rel = (px, va) => px > va[1] ? "above" : px < va[0] ? "below" : "inside";
  let value;
  if (T.va[0] > Y.va[1]) value = "higher"; else if (T.va[1] < Y.va[0]) value = "lower";
  else if (T.va[0] >= Y.va[0] && T.va[1] <= Y.va[1]) value = "inside"; else if (T.va[0] < Y.va[0] && T.va[1] > Y.va[1]) value = "outside";
  else value = T.poc >= Y.poc ? "overlap-up" : "overlap-down";
  T.auction = { value, poc_shift: r2(T.poc - Y.poc), va_width: r2(T.va[1] - T.va[0]), va_width_chg: r2((T.va[1] - T.va[0]) - (Y.va[1] - Y.va[0])),
    open_vs_prior_va: rel(op, Y.va), close_vs_va: rel(cl, T.va), open: r2(op), close: r2(cl), prior_va: Y.va, prior_poc: Y.poc };
});
last20.forEach(d => Object.keys(P[d]).forEach(k => { comp[k] = (comp[k] || 0) + P[d][k]; }));
// auction stats, rebuilt nightly over every logged day (what yesterday's profile said vs what today did)
(() => { const st = { open: {}, r80: { tried: 0, crossed: 0, never: 0 }, after_value: {} };
  good.forEach((d, i) => { const A = out.days[d] && out.days[d].auction; if (!A) return; const c = rj(path.join(MD, "history", d + ".json")).candles.slice(0, 390), va = A.prior_va, up = A.close > A.open;
    const o = st.open[A.open_vs_prior_va] = st.open[A.open_vs_prior_va] || { days: 0, back_inside: 0, closed_up: 0 };
    o.days++; if (up) o.closed_up++; if (c.some(b => b.close >= va[0] && b.close <= va[1])) o.back_inside++;
    if (A.open_vs_prior_va !== "inside") { let run = 0, done = false;
      for (let m = 0; m < c.length && !done; m++) { run = (c[m].close >= va[0] && c[m].close <= va[1]) ? run + 1 : 0;
        if (run >= 30) { st.r80.tried++; done = true; const far = A.open_vs_prior_va === "above" ? va[0] : va[1];
          if (c.slice(m).some(b => A.open_vs_prior_va === "above" ? b.low <= far : b.high >= far)) st.r80.crossed++; } }
      if (!done) st.r80.never++; }
    const P2 = i > 0 && out.days[good[i - 1]] && out.days[good[i - 1]].auction;
    if (P2) { const v = st.after_value[P2.value] = st.after_value[P2.value] || { days: 0, closed_up: 0, poc_up: 0 }; v.days++; if (up) v.closed_up++; if (A.poc_shift > 0) v.poc_up++; } });
  out.auction_stats = st; })();
const C = summarize(comp); if (C) { const o = Object.assign({}, C); delete o.pctAt; o.from = last20[0]; o.to = last20[last20.length - 1]; out.composite = o; }
// the user's levels: volume within $0.25 of each line per day of its week, and its place in the 20-day composite at the time
const mondayOf = d => { const t = new Date(d + "T12:00:00Z"), w = t.getUTCDay(); t.setUTCDate(t.getUTCDate() - ((w + 6) % 7)); return t.toISOString().slice(0, 10); };
((rj(path.join(MD, "my_levels.json")) || {}).weeks || []).forEach(w => {
  const prior = good.filter(d => d < w.week).slice(-20), pc = {}; prior.forEach(d => Object.keys(P[d]).forEach(k => { pc[k] = (pc[k] || 0) + P[d][k]; }));
  const PS = summarize(pc), wd = good.filter(d => mondayOf(d) === w.week);
  out.levels.push({ week: w.week, profile_from: prior[0] || null, profile_to: prior[prior.length - 1] || null,
    levels: w.levels.map(l => { const near = PS ? PS.nodes.reduce((b, x) => Math.abs(x - l.px) < Math.abs(b - l.px) ? x : b, PS.nodes[0]) : null;
      return { px: l.px, color: l.color, pct_20d: PS ? PS.pctAt(l.px) : null, nearest_node: near, node_dist: near == null ? null : r2(Math.abs(near - l.px)),
        daily: wd.map(d => { let s = 0; [-BIN, 0, BIN].forEach(o => { s += P[d][r2(key(l.px) + o)] || 0; }); return { day: d, vol: Math.round(s) }; }) }; }) });
});
const f = path.join(MD, "vprofile.json"); fs.writeFileSync(f, JSON.stringify(out)); console.log(path.relative(ROOT, f));
// BOT LEVELS (Nov 22): every week, the bot saves its own key levels from the 20 sessions BEFORE that Monday — POC, value area high/low,
// the 10 biggest volume nodes, the 10 thinnest gaps — so they can be scored the same way as the user's lines (levels_paper_log.js).
const weeks = {}; good.forEach(d => { weeks[mondayOf(d)] = 1; });
const nextMon = (() => { const t = new Date(good[good.length - 1] + "T12:00:00Z"); t.setUTCDate(t.getUTCDate() + ((8 - t.getUTCDay()) % 7 || 7)); return t.toISOString().slice(0, 10); })();
weeks[nextMon] = 1;
const BL = { updated: out.updated, note: "the bot's weekly volume levels, each built only from the 20 sessions before that Monday (no look-ahead)", weeks: [] };
Object.keys(weeks).sort().forEach(m => {
  const prior = good.filter(d => d < m).slice(-20); if (prior.length < 15) return;
  const pc = {}; prior.forEach(d => Object.keys(P[d]).forEach(k => { pc[k] = (pc[k] || 0) + P[d][k]; }));
  const S = summarize(pc); if (!S) return;
  const lv = [{ px: S.poc, type: "POC" }, { px: S.va[1], type: "value area high" }, { px: S.va[0], type: "value area low" }];
  S.nodes.forEach(x => { if (Math.abs(x - S.poc) > 0.5) lv.push({ px: x, type: "volume node" }); });
  S.gaps.forEach(x => lv.push({ px: x, type: "thin gap" }));
  BL.weeks.push({ week: m, from: prior[0], to: prior[prior.length - 1], levels: lv });
});
const fb = path.join(MD, "bot_levels.json"); fs.writeFileSync(fb, JSON.stringify(BL, null, 1)); console.log(path.relative(ROOT, fb));
