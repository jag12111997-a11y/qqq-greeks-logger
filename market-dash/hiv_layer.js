/* 3v3 HIGH-IV LAYER (added Nov 9, 2026) — an add-on next to v9.5 + options. It never changes a bot rule.
   It only acts when the ~$1.00 0DTE contracts are expensive (IV over 35% AT THAT MINUTE), i.e. the moments the bot's
   own IV rule sits out. Two put setups + two info lines. Same logic as the Nov 9 tests (62 mornings, +$1,690 on top of the bot).
     POP PUT  : after 6:50, QQQ $1.50+ above session VWAP AND up $1.50+ in the last 15 min -> buy the ~$1.00 put (0.80-1.25),
                1 per morning, 25% stop, sell within 40 min. Skipped while the bot holds a call.
     BELL PUT : at 6:31, QQQ fell 0.15%+ from 6:00 to the open (last 30 min of premarket) AND the night was red
                (4:00 AM ET price below yesterday's close) -> buy the ~$1.00 put, 1 contract, 25% stop; after +50% the stop
                moves to breakeven; otherwise hold to 8:56.
     NIGHT LEAN (info only), CRASH ODDS (info only).
   Inputs: D = V92MAP.buildDay(...) day ({st, opt}), vw = session VWAP per minute, night = relay /live/night JSON,
           openPx = the 6:30 open, botTrades = [{side, mf, out}] (out null while open), atM = minutes after 6:30 now (146 = final).
   Units: option prices 0.00 per contract; dollars at 1 contract, fees out (0.013 per contract per side). */
(function (root) {
  "use strict";
  var IVMAX = 0.35, LO = 0.80, HI = 1.25, FEE = 0.013, END = 146;
  var POP = { start: 20, last: 119, size: 1.5, rise: 1.5, look: 15, stop: 0.25, hold: 40 };
  var BELL = { drop: -0.15, stop: 0.25, be: 0.50 };
  // Event days for the crash-odds line (hand-kept; add new ones as they are announced). Times PT.
  var EVENTS = {
    "2026-11-10": "CPI (5:30 AM)", "2026-11-18": "day after NVDA earnings", "2026-12-04": "jobs report (5:30 AM)",
    "2026-12-09": "Fed decision (11:00 AM)", "2026-12-10": "day after the Fed"
  };
  var FEAR_RV = 26;   // QQQ 20-day realized vol (annualized %) top quarter of 2022-2026

  function median(a) { if (!a.length) return null; var s = a.slice().sort(function (x, y) { return x - y; }), n = s.length; return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2; }
  function q(D, side, K, m) { var b = (D.opt[side] || {})[K]; return b ? b[m] || null : null; }
  // the ~$1.00 contracts' IV right now: median IV of all 0DTE contracts priced 0.80-1.25 in the latest minute at or before m (both sides)
  function ivAt(D, m, look) {
    for (var k = m; k >= Math.max(0, m - (look || 5)); k--) {
      var v = [];
      ["C", "P"].forEach(function (s) { var O = D.opt[s] || {}; Object.keys(O).forEach(function (K) { var x = O[K][k]; if (x && x[1] != null && x[0] >= LO && x[0] <= HI) v.push(x[1]); }); });
      if (v.length) return median(v);
    }
    return null;
  }
  function nearOne(D, side, m) {
    var best = null, O = D.opt[side] || {};
    Object.keys(O).forEach(function (K) { var x = O[K][m]; if (x && x[0] >= LO && x[0] <= HI && x[3] > 0 && (!best || Math.abs(x[0] - 1) < Math.abs(best.mid - 1))) best = { K: K, mid: x[0], ask: x[3], iv: x[1] }; });
    return best;
  }
  function holdingCall(bot, m) { return (bot || []).some(function (t) { return t.side === "C" && t.mf <= m && (t.out == null || t.out >= m); }); }
  // walk one contract forward from the fill; rule "pop" or "bell"
  function walk(D, side, K, e, ask, rule, atM) {
    var stop = ask * (1 - (rule === "pop" ? POP.stop : BELL.stop)), be = false, lastBid = null, lastM = e;
    var limit = rule === "pop" ? Math.min(END, e + POP.hold) : END, now = Math.min(atM, END);
    for (var m = e + 1; m <= now; m++) {
      var x = q(D, side, K, m); if (!x) continue;
      var bid = x[2]; lastBid = bid; lastM = m;
      if (rule === "bell" && !be && bid >= ask * (1 + BELL.be)) { be = true; stop = ask; }
      if (bid <= stop) return { state: "closed", out: m, sold: bid, why: be ? "breakeven stop" : "stop (−25%)", usd: (bid - ask - 2 * FEE) * 100 };
      if (m >= limit) return { state: "closed", out: m, sold: bid, why: rule === "pop" ? "40-min limit" : "8:56 end", usd: (bid - ask - 2 * FEE) * 100 };
    }
    if (atM >= limit && lastBid != null) return { state: "closed", out: lastM, sold: lastBid, why: rule === "pop" ? "40-min limit" : "8:56 end", usd: (lastBid - ask - 2 * FEE) * 100 };
    return { state: "open", bid: lastBid, at: lastM, stop: stop, be: be, sellBy: limit, usd: lastBid == null ? null : (lastBid - ask - 2 * FEE) * 100 };
  }
  function nightNumbers(night, openPx) {
    if (!night || !(night.prev_close > 0)) return null;
    var n = { prev: night.prev_close, p4: night.pre_first, p9: night.pre_0900, open: openPx };
    n.nightPct = n.p4 > 0 ? (n.p4 / n.prev - 1) * 100 : null;                    // after-hours + overnight
    n.lastPct = openPx > 0 && n.p9 > 0 ? (openPx / n.p9 - 1) * 100 : null;      // 6:00 -> 6:30 PT
    n.prePct = openPx > 0 && n.p4 > 0 ? (openPx / n.p4 - 1) * 100 : null;       // whole premarket
    n.gapPct = openPx > 0 ? (openPx / n.prev - 1) * 100 : null;
    return n;
  }
  function lean(n) {
    if (!n || n.nightPct == null || n.prePct == null) return null;
    var nt = n.nightPct, pre = n.prePct;
    if (n.lastPct != null && n.lastPct <= BELL.drop && nt < 0) return { side: "P", txt: "puts — falling into the bell after a red night" };
    if (nt < -0.2 && pre > 0.1) return { side: "P", txt: "puts — red night, premarket bounce (bounces like this usually fade)" };
    if (nt > 0.2 && pre < -0.1) return { side: "P", txt: "puts — green night, premarket sold off" };
    if (nt < -0.2 && pre < -0.1) return { side: "C", txt: "calls — red all night (sellers usually tired by the open)" };
    if (nt > 0.2 && pre > 0.1) return { side: "C", txt: "calls — green night kept rising" };
    return { side: null, txt: "no clear lean" };
  }
  function crashOdds(night, day, gapPct) {
    if (!night || night.rv20 == null) return null;
    var fear = night.rv20 >= FEAR_RV, below = !!night.below50, ev = EVENTS[day] || null, p, why, vol = " (20-day volatility " + Math.round(night.rv20) + "%)";
    if (fear && gapPct != null && gapPct <= -1.5) { p = 41; why = "fear market" + vol + " + gap down 1.5%+"; }
    else if (fear && below && ev) { p = 23; why = "fear market" + vol + " + below the 50-day + " + ev; }
    else if (fear && below) { p = 11.5; why = "fear market" + vol + " + below the 50-day"; }
    else if (fear) { p = 7.5; why = "fear market" + vol; }
    else { p = 2.0; why = "normal market" + vol + (ev ? " · " + ev + " today" : ""); }
    return { pct: p, why: why, rv: night.rv20, below50: below, event: ev, base: 3.4 };
  }
  function run(o) {
    var D = o.D, vw = o.vw || [], atM = o.atM == null ? END : o.atM, st = D.st || [], out = { trades: [], ivNow: null, on: false };
    out.ivNow = ivAt(D, Math.max(0, Math.min(atM, END)), 15);   // newest quotes can be a minute ahead of the bars; Pages history lags ~9 min
    out.on = out.ivNow != null && out.ivNow > IVMAX;
    var n = nightNumbers(o.night, o.openPx);
    out.night = n; out.lean = lean(n); out.odds = crashOdds(o.night, o.day, n && n.gapPct);
    // BELL PUT at 6:31
    out.bell = { state: "off" };
    if (!n || n.lastPct == null || n.nightPct == null) out.bell = { state: "nodata" };
    else if (!(n.lastPct <= BELL.drop && n.nightPct < 0)) out.bell = { state: "no", txt: "no (6:00→open " + n.lastPct.toFixed(2) + "%, night " + n.nightPct.toFixed(2) + "%)" };
    else if (atM >= 1) {
      var iv1 = ivAt(D, 1), p1 = nearOne(D, "P", 1) || nearOne(D, "P", 2);
      if (!(iv1 > IVMAX)) out.bell = { state: "no", txt: "signal, but IV " + (iv1 == null ? "?" : Math.round(iv1 * 100) + "%") + " at 6:31 (needs over 35%)" };
      else if (!p1) out.bell = { state: "no", txt: "signal, but no put priced 0.80–1.25" };
      else {
        var e1 = (D.opt.P[p1.K] || {})[1] ? 1 : 2, w1 = walk(D, "P", p1.K, e1, p1.ask, "bell", atM);
        out.trades.push(Object.assign({ rule: "bell", side: "P", K: Number(p1.K), mf: e1, paid: p1.ask, iv: iv1, ct: 1 }, w1));
        out.bell = { state: "fired" };
      }
    } else out.bell = { state: "armed", txt: "ARMED — buy the ~$1.00 put at 6:31 if IV is over 35%" };
    // POP PUT (first one only)
    out.pop = { state: "watching" };
    var lastM = Math.min(atM, POP.last, st.length - 1);
    for (var m = POP.start; m <= lastM; m++) {
      var s = st[m], s0 = st[m - POP.look]; if (!s || !s0 || vw[m] == null) continue;
      if (s[0] - vw[m] >= POP.size && s[0] - s0[0] >= POP.rise) {
        var iv = ivAt(D, m);
        if (!(iv > IVMAX)) continue;                                   // only when options are expensive at that minute
        if (holdingCall(o.botTrades, m)) { out.pop = { state: "skipped", txt: "pop at " + m + " skipped: the bot holds a call" }; break; }
        var e = m + 1; if (e > atM) { out.pop = { state: "signal", m: m }; break; }
        var p = nearOne(D, "P", e) || nearOne(D, "P", e + 1);
        if (!p) { out.pop = { state: "noquote", m: m }; break; }
        if (!(D.opt.P[p.K] || {})[e]) e = e + 1;
        var w = walk(D, "P", p.K, e, p.ask, "pop", atM);
        out.trades.push(Object.assign({ rule: "pop", side: "P", K: Number(p.K), mf: e, paid: p.ask, iv: iv, ct: 1, sig: m, qqq: s[0], vwap: vw[m] }, w));
        out.pop = { state: "fired" }; break;
      }
    }
    if (out.pop.state === "watching" && st.length) {
      var lm = Math.min(atM, st.length - 1); while (lm > 0 && !st[lm]) lm--;
      if (st[lm] && vw[lm] != null && st[Math.max(0, lm - POP.look)]) out.pop.level = Math.max(vw[lm] + POP.size, st[Math.max(0, lm - POP.look)][0] + POP.rise);
      if (atM > POP.last) out.pop.state = "done";
    }
    out.usd = out.trades.reduce(function (a, t) { return a + (t.usd || 0); }, 0);
    return out;
  }
  var api = { run: run, ivAt: ivAt, nightNumbers: nightNumbers, lean: lean, crashOdds: crashOdds, EVENTS: EVENTS, IVMAX: IVMAX, POP: POP, BELL: BELL };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.HIVLAYER = api;
})(typeof self !== "undefined" ? self : this);
