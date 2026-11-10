/* 3v3 BIG-FISH LAYER (added Nov 10, 2026) — an add-on next to v9.5 + options + high-IV layer. It never changes a bot rule.
   Catches the big trend days (queue #8, tested Nov 9: options +$1,451 on 3 days, Oct 8 – Nov 9). NO IV rule: big-fish days are the
   high-IV days the bot's IV rule sits out.
     SETUP (known before the open): a selloff or fear market — 20-day return −8% or worse, OR 5-day −3% or worse, OR QQQ 20-day
            realized vol 26%+ — AND a gap of 1%+ either way (open vs yesterday's close).
     6:35   the first 5 minutes must go the SAME WAY AS THE GAP (6:35 close vs the open). If the open fights the gap → skip.
     ENTRY  6:36, the ~$1.00 0DTE contract (mid 0.80–1.25) on that side, 1 contract, at the ask.
     EXIT   from 6:40, the first 1-min close back across session VWAP (sell at the bid), or −35% disaster stop (bid), else 8:56.
     One position: skipped if the high-IV layer's bell put already holds the same side (same idea, Nov 5).
   Inputs: D = V92MAP.buildDay(...) day ({st, opt}), vw = session VWAP per minute, night = relay /live/night JSON (prev_close, rv20, r5, r20),
           openPx = the 6:30 open, atM = minutes after 6:30 now (146 = final), bell = the high-IV layer's bell trade (or null).
   Units: option prices 0.00 per contract; dollars at 1 contract, fees out (0.013 per contract per side). */
(function (root) {
  "use strict";
  var LO = 0.80, HI = 1.25, FEE = 0.013, END = 146;
  var RULE = { r20: -8, r5: -3, rv: 26, gap: 1, decide: 5, entry: 6, vwapFrom: 10, stop: 0.35 };
  function q(D, side, K, m) { var b = (D.opt[side] || {})[K]; return b ? b[m] || null : null; }
  function nearOne(D, side, m) {
    var best = null, O = D.opt[side] || {};
    Object.keys(O).forEach(function (K) { var x = O[K][m]; if (x && x[0] >= LO && x[0] <= HI && x[3] > 0 && (!best || Math.abs(x[0] - 1) < Math.abs(best.mid - 1))) best = { K: K, mid: x[0], ask: x[3], iv: x[1] }; });
    return best;
  }
  // before the open: which setup conditions are on (gap is filled in once the open is known)
  function setup(night, openPx) {
    if (!night || !(night.prev_close > 0)) return null;
    var why = [];
    if (night.r20 != null && night.r20 <= RULE.r20) why.push("20-day " + night.r20.toFixed(1) + "%");
    if (night.r5 != null && night.r5 <= RULE.r5) why.push("5-day " + night.r5.toFixed(1) + "%");
    if (night.rv20 != null && night.rv20 >= RULE.rv) why.push("fear market (20-day vol " + Math.round(night.rv20) + "%)");
    var gap = openPx > 0 ? (openPx / night.prev_close - 1) * 100 : null;
    return { ctx: why.length > 0, why: why, gap: gap, gapOk: gap != null && Math.abs(gap) >= RULE.gap, on: why.length > 0 && gap != null && Math.abs(gap) >= RULE.gap,
      r5: night.r5, r20: night.r20, rv: night.rv20, hasR: night.r20 != null };
  }
  function run(o) {
    var D = o.D, vw = o.vw || [], atM = o.atM == null ? END : o.atM, st = D.st || [], out = { trades: [], state: "nodata" };
    var S = setup(o.night, o.openPx); out.setup = S;
    if (!S) return out;
    if (!S.ctx) { out.state = "no"; out.txt = "no setup (no selloff or fear market)"; return out; }
    if (S.gap == null) { out.state = "preopen"; out.txt = "SETUP if the gap is 1%+ (" + S.why.join(" · ") + ")"; return out; }
    if (!S.gapOk) { out.state = "no"; out.txt = "no: gap " + (S.gap >= 0 ? "+" : "") + S.gap.toFixed(2) + "% (needs 1%+) · " + S.why.join(" · "); return out; }
    var gs = S.gap > 0 ? 1 : -1, side = gs > 0 ? "C" : "P", nm = gs > 0 ? "calls" : "puts";
    if (atM < RULE.entry || !st[RULE.decide]) { out.state = "armed"; out.txt = "SETUP · gap " + (gs > 0 ? "up " : "down ") + Math.abs(S.gap).toFixed(2) + "% · 6:35 decides: " + nm + " if QQQ is still " + (gs > 0 ? "above" : "below") + " the open " + o.openPx.toFixed(2); return out; }
    var s35 = st[RULE.decide][0] > o.openPx ? 1 : -1;
    if (s35 !== gs) { out.state = "skip"; out.txt = "SETUP, but skipped: 6:35 went against the gap"; return out; }
    if (o.bell && o.bell.side === side) { out.state = "skip"; out.txt = "SETUP, same put as the high-IV bell put (one position)"; return out; }
    var e = RULE.entry, p = nearOne(D, side, e);
    if (!p) { e = e + 1; p = nearOne(D, side, e); }
    if (!p) { out.state = atM <= RULE.entry + 1 ? "armed" : "noquote"; out.txt = "SETUP, " + nm + " — no contract priced 0.80–1.25 at 6:36"; return out; }
    var ask = p.ask, stop = ask * (1 - RULE.stop), t = { rule: "big fish", side: side, K: Number(p.K), mf: e, paid: ask, iv: p.iv, ct: 1, stop: stop, sellBy: END };
    var lastBid = null, lastM = e, now = Math.min(atM, END), done = null;
    for (var m = e + 1; m <= now && !done; m++) {
      var x = q(D, side, p.K, m); if (!x) continue;
      lastBid = x[2]; lastM = m;
      if (x[2] <= stop) done = { out: m, sold: x[2], why: "stop (−35%)" };
      else if (m >= RULE.vwapFrom && st[m] && vw[m] != null && (st[m][0] - vw[m]) * gs < 0) done = { out: m, sold: x[2], why: "QQQ closed back across VWAP" };
      else if (m >= END) done = { out: m, sold: x[2], why: "8:56 end" };
    }
    if (!done && atM >= END && lastBid != null) done = { out: lastM, sold: lastBid, why: "8:56 end" };
    if (done) Object.assign(t, { state: "closed", usd: (done.sold - ask - 2 * FEE) * 100 }, done);
    else Object.assign(t, { state: "open", bid: lastBid, at: lastM, usd: lastBid == null ? null : (lastBid - ask - 2 * FEE) * 100,
      vwapNow: vw[Math.min(now, vw.length - 1)] });
    out.trades.push(t); out.state = "fired"; out.txt = "FIRED · " + nm + " · hold until QQQ closes back " + (gs > 0 ? "below" : "above") + " VWAP";
    out.usd = t.usd;
    return out;
  }
  var api = { run: run, setup: setup, RULE: RULE };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.BIGFISH = api;
})(typeof self !== "undefined" ? self : this);
