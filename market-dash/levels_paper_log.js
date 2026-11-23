#!/usr/bin/env node
/* 3v3 YOUR LEVELS — nightly scorecard (added Nov 22, 2026). PAPER ONLY — the card / live trading never read this.
   Scores the user's weekly volume-profile levels (market-dash/my_levels.json) against what QQQ actually did, next to the levels the
   site's own data gives (yesterday's high / low / close, yesterday's value area edges, call wall, put wall, gamma flip at the open)
   and RANDOM lines (the honest baseline: a level only has an edge if it holds more often than a line drawn anywhere).
   A TOUCH = a 1-min bar within $0.25 of the level after price was at least R away (R = 10% of the 10-day average RTH range).
   After a touch, within 60 min: HELD = price closes R back on the side it came from first · BROKE = closes R through first · else CHOP.
   MOVE = the best move away on the side it came from within 30 min ($). A level re-arms once price is R away again.
   Writes market-dash/levels_log.json. Usage (repo root): node market-dash/levels_paper_log.js   Prints the path it wrote. */
"use strict";
const fs = require("fs"), path = require("path");
const ROOT = process.cwd(), MD = path.join(ROOT, "market-dash");
const TOL = 0.25, RES_WIN = 60, MOVE_WIN = 30, N_RANDOM = 15;
const rj = p => { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return null; } };
const r2 = v => Math.round(v * 100) / 100;
const ix = rj(path.join(MD, "history", "index.json")) || { days: [] };
const days = ix.days.filter(d => fs.existsSync(path.join(MD, "history", d + ".json")));
const C = {}, P = {};
days.forEach(d => { const j = rj(path.join(MD, "history", d + ".json")); if (j && j.candles && j.candles.length >= 300) { C[d] = j.candles.slice(0, 390); P[d] = j.points || []; } });
const good = days.filter(d => C[d]);
const rng = d => Math.max(...C[d].map(c => c.high)) - Math.min(...C[d].map(c => c.low));
const clock = m => { const t = 390 + m; return ((Math.floor(t / 60) + 11) % 12 + 1) + ":" + String(t % 60).padStart(2, "0"); };
function mulberry(a) { return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
const mondayOf = d => { const t = new Date(d + "T12:00:00Z"), w = t.getUTCDay(); t.setUTCDate(t.getUTCDate() - ((w + 6) % 7)); return t.toISOString().slice(0, 10); };
function score(c, L, R) {   // every touch episode of level L on one day's 1-min candles
  const out = []; let armed = Math.abs(c[0].open - L) > TOL, closest = Infinity;
  for (let m = 0; m < c.length; m++) {
    const b = c[m]; closest = Math.min(closest, b.low > L ? b.low - L : b.high < L ? L - b.high : 0);
    if (!armed) { if (Math.abs(b.close - L) >= R) armed = true; continue; }
    if (b.low <= L + TOL && b.high >= L - TOL) {
      const ref = m > 0 ? c[m - 1].close : c[0].open, side = ref >= L ? 1 : -1;
      let res = "chop", resM = null, best = 0;
      for (let j = m; j < Math.min(c.length, m + RES_WIN); j++) {
        const x = side * (c[j].close - L);
        if (j < m + MOVE_WIN) best = Math.max(best, side > 0 ? c[j].high - L : L - c[j].low);
        if (res === "chop" && x >= R) { res = "held"; resM = j; }
        else if (res === "chop" && x <= -R) { res = "broke"; resM = j; }
        if (res !== "chop" && j >= m + MOVE_WIN) break;
      }
      out.push({ t: clock(m), from: side > 0 ? "above" : "below", res, move: r2(best), at: resM == null ? null : clock(resM) });
      armed = false;
    }
  }
  return { touches: out, closest: closest === Infinity ? null : r2(closest) };
}
function tally(list) {   // list of touch episodes
  const o = { touches: list.length, held: 0, broke: 0, chop: 0, move: 0 };
  list.forEach(t => { o[t.res]++; o.move += t.move; });
  o.held_pct = o.touches ? Math.round(100 * o.held / o.touches) : null; o.avg_move = o.touches ? r2(o.move / o.touches) : null; delete o.move;
  return o;
}
const mine = (rj(path.join(MD, "my_levels.json")) || { weeks: [] }).weeks;
const out = { updated: new Date().toISOString(), rules: "touch = within $" + TOL + " after being R away (R = 10% of the 10-day avg range) · held / broke = closes R back / through within " + RES_WIN + " min · move = best move away on the side it came from within " + MOVE_WIN + " min · random = " + N_RANDOM + " lines a day drawn between the last 5 days' low and high",
  site_all: {}, random_all: null, weeks: [] };
const siteEp = {}, randEp = [], byDay = {};
good.forEach((d, i) => {
  const prev = good.slice(Math.max(0, i - 10), i); if (prev.length < 5) return;
  const R = Math.max(0.5, 0.10 * prev.reduce((a, x) => a + rng(x), 0) / prev.length), c = C[d], y = C[good[i - 1]];
  // the site's own levels for the day
  const yh = Math.max(...y.map(x => x.high)), yl = Math.min(...y.map(x => x.low)), yc = y[y.length - 1].close;
  const map = (rj(path.join(MD, "v92_log", d + ".json")) || {}).map || {}, p0 = (P[d] || []).find(q => q.call_wall != null || q.put_wall != null) || {};
  const site = [["yesterday high", yh], ["yesterday low", yl], ["yesterday close", yc]];
  if (map.value_area) { site.push(["value area low", map.value_area[0]]); site.push(["value area high", map.value_area[1]]); }
  if (p0.call_wall != null) site.push(["call wall", p0.call_wall]); if (p0.put_wall != null) site.push(["put wall", p0.put_wall]); if (p0.gamma_flip != null) site.push(["gamma flip", p0.gamma_flip]);
  site.forEach(([k, L]) => { const s = score(c, L, R); (siteEp[k] = siteEp[k] || []).push(...s.touches.map(t => Object.assign({ day: d }, t))); });
  // random lines
  const p5 = good.slice(Math.max(0, i - 5), i), lo = Math.min(...p5.map(x => Math.min(...C[x].map(q => q.low)))), hi = Math.max(...p5.map(x => Math.max(...C[x].map(q => q.high))));
  const rnd = mulberry(Number(d.replace(/-/g, "")));
  for (let k = 0; k < N_RANDOM; k++) { const L = r2(lo + rnd() * (hi - lo)); randEp.push(...score(c, L, R).touches); }
  byDay[d] = { R: r2(R), site: site.map(([k, L]) => Object.assign({ type: k, px: r2(L) }, score(c, L, R))) };
});
Object.keys(siteEp).forEach(k => { out.site_all[k] = tally(siteEp[k]); });
out.site_all["ALL site levels"] = tally([].concat(...Object.values(siteEp)));
out.random_all = tally(randEp);
mine.forEach(w => {
  const wd = good.filter(d => mondayOf(d) === w.week && byDay[d]);
  const levels = w.levels.map(l => { const touches = [], cl = [];
    wd.forEach(d => { const s = score(C[d], l.px, byDay[d].R); s.touches.forEach(t => touches.push(Object.assign({ day: d }, t))); if (s.closest != null) cl.push(s.closest); });
    return Object.assign({}, l, { touches, closest: cl.length ? Math.min(...cl) : null }); });
  const byColor = {}; levels.forEach(l => { (byColor[l.color] = byColor[l.color] || []).push(...l.touches); });
  Object.keys(byColor).forEach(k => { byColor[k] = tally(byColor[k]); });
  const siteW = [].concat(...wd.map(d => [].concat(...byDay[d].site.map(s => s.touches))));
  const randW = []; wd.forEach(d => { const i = good.indexOf(d), p5 = good.slice(Math.max(0, i - 5), i), lo = Math.min(...p5.map(x => Math.min(...C[x].map(q => q.low)))), hi = Math.max(...p5.map(x => Math.max(...C[x].map(q => q.high)))), rnd = mulberry(Number(d.replace(/-/g, "")));
    for (let k = 0; k < N_RANDOM; k++) randW.push(...score(C[d], r2(lo + rnd() * (hi - lo)), byDay[d].R).touches); });
  out.weeks.push({ week: w.week, days: wd, levels, mine: tally([].concat(...levels.map(l => l.touches))), byColor, site: tally(siteW), random: tally(randW),
    site_days: wd.map(d => ({ day: d, R: byDay[d].R, site: byDay[d].site })) });
});
const f = path.join(MD, "levels_log.json");
fs.writeFileSync(f, JSON.stringify(out, null, 1));
console.log(path.relative(ROOT, f));
