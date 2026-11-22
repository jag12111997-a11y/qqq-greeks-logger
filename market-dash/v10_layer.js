/* 3v3 v10 "auction" layer (added Nov 21, 2026) — PAPER ONLY. Never changes the card, v9.2 or any other layer.
   Built from the Nov 21 auction study (claude/3v3_bot_auction_study_2026-11-21.txt): trade only where one side pushes into
   new ground, judge every morning against random entries with the same exits.
     Slot A · ORB   : first 1-min close outside the 6:30–6:45 range, once per side, before 8:30.
     Slot B · EXT   : QQQ closes through a level (opening range, yesterday's high/low/close, 10-day high/low, put/call wall,
                      OI magnet, max ± gamma) AND that close is a new high/low of the day, once 40–100% of the morning's
                      implied move (open × ATM IV ÷ √252) is used.
              POP   : only when the site's GARCH vol (prior evening) is over 22%: QQQ moved 15% of its 10-day morning range
                      in the last 3 min, in the direction of the last finished 20-min candle. Slot B max 2 a morning.
     Never against the last 3-min move. One open trade per slot.
   Contract: 0DTE with delta nearest 0.35, ask 0.40–3.00 (no IV cap). Buy at the ask, sell at the bid, $1.30 fee per trade.
   Exit: 25% stop; at +$0.30 the 3-min trail turns on (sell when a 3-min QQQ close breaks the prior 3-min low for calls /
   high for puts); otherwise sell after 60 min or at 8:56.
   Baseline: 3 random entries a morning (6:45–8:29), random side, same contract + exits, averaged over 20 fixed seeds.
   Inputs: D = V92MAP.buildDay(...) ({st:[close,high,low,ema8] per minute, opt:{C|P:{K:{m:[mid,iv,bid,ask,delta,sec]}}}, pts, lv}),
           X = {garch: prior-evening GARCH vol %, range10: 10-day average 6:30–8:56 range}. */
(function (root) {
  "use strict";
  var CFG = { orEnd: 15, orbLast: 120, scanEnd: 140, ruLo: 0.4, ruHi: 1.0, popT: 1.0, popG: 22, capA: 4, capB: 2,
    delta: 0.35, lo: 0.40, hi: 3.0, pct: 0.25, arm: 0.30, hold: 60, END: 146, FEE: 1.3, Tk: 0.15 };
  var LEVELS = ["put_wall", "call_wall", "oi_magnet", "max_pos_gamma", "max_neg_gamma"];

  function bars(D) { return D.st.map(function (s) { return s ? { c: s[0], h: s[1], l: s[2] } : null; }); }
  function q(D, s, K, m) { var b = (D.opt[s] || {})[K]; return b ? b[m] || null : null; }
  function nq(D, s, K, m, ahead) {
    for (var j = m; j <= m + ahead; j++) { var x = q(D, s, K, j); if (x && x[2] != null && x[3] != null && x[3] > 0) return [j, x]; }
    return [null, null];
  }
  function median(a) { var s = a.slice().sort(function (x, y) { return x - y; }), n = s.length; return n ? (n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2) : null; }
  function atmIV(D) {
    var c = D.st[15] ? D.st[15][0] : D.st[0][0], v = [];
    ["C", "P"].forEach(function (s) { Object.keys(D.opt[s] || {}).forEach(function (K) {
      if (Math.abs(Number(K) - c) <= 2) { var x = q(D, s, K, 15) || q(D, s, K, 16); if (x && x[1]) v.push(x[1]); } }); });
    return median(v);
  }
  function pick(D, s, m) {
    var best = null;
    Object.keys(D.opt[s] || {}).forEach(function (K) {
      var r = nq(D, s, K, m, 2), x = r[1]; if (!x || x[4] == null) return;
      if (!(x[3] >= CFG.lo && x[3] <= CFG.hi)) return;
      var d = Math.abs(Math.abs(x[4]) - CFG.delta);
      if (best === null || d < best.d) best = { d: d, K: K, j: r[0], x: x };
    });
    return best;
  }
  function three(B, m) {
    var seg = []; for (var j = m - 2; j <= m; j++) if (j >= 0 && j < B.length && B[j]) seg.push(B[j]);
    if (!seg.length) return null;
    return { c: seg[seg.length - 1].c, h: Math.max.apply(null, seg.map(function (b) { return b.h; })), l: Math.min.apply(null, seg.map(function (b) { return b.l; })) };
  }
  function manage(D, B, s, K, m0, paid) {
    var sg = s === "C" ? 1 : -1, armed = false, hw = 0, stop = paid * (1 - CFG.pct), last = Math.min(m0 + CFG.hold, CFG.END);
    for (var j = m0 + 1; j <= last; j++) {
      var x = q(D, s, K, j); if (!x || x[2] == null) continue;
      var bid = x[2]; hw = Math.max(hw, bid - paid);
      if (bid <= stop) return { out: j, px: bid, why: "stop", hw: hw };
      if (!armed && bid - paid >= CFG.arm) armed = true;
      if (armed && j % 3 === 2 && j >= 5) {
        var cur = three(B, j), prv = three(B, j - 3);
        if (cur && prv && ((sg > 0 && cur.c < prv.l) || (sg < 0 && cur.c > prv.h))) return { out: j, px: bid, why: "trail", hw: hw };
      }
    }
    for (var k = last; k > m0; k--) { var y = q(D, s, K, k); if (y && y[2] != null) return { out: k, px: y[2], why: "time", hw: hw }; }
    return { out: m0, px: paid, why: "none", hw: 0 };
  }
  function levels(D, m, orh, orl) {
    var p = (D.pts && m < D.pts.length && D.pts[m]) || {}, lv = [["PDH", D.lv.pdh], ["PDL", D.lv.pdl], ["PDC", D.lv.pdc], ["H10", D.lv.h10], ["L10", D.lv.l10]];
    if (m >= CFG.orEnd) lv.push(["ORH", orh], ["ORL", orl]);
    LEVELS.forEach(function (k) { if (p[k]) lv.push([k, p[k]]); });
    return lv;
  }
  // slot "A" = ORB only, slot "B" = EXT + POP
  function signals(D, B, X, iv, slot) {
    var E = B[0].c * iv / Math.sqrt(252), T = CFG.Tk * X.range10, orh = -1e9, orl = 1e9, out = [], done = {}, last = {}, hi = -1e9, lo = 1e9, fades = [];
    for (var i = 0; i < CFG.orEnd; i++) if (B[i]) { orh = Math.max(orh, B[i].h); orl = Math.min(orl, B[i].l); }
    for (i = 0; i < CFG.orEnd; i++) if (B[i]) { hi = Math.max(hi, B[i].h); lo = Math.min(lo, B[i].l); }
    for (var m = CFG.orEnd; m < CFG.scanEnd; m++) {
      if (B[m]) { hi = Math.max(hi, B[m].h); lo = Math.min(lo, B[m].l); }
      if (!B[m] || !B[m - 1]) continue;
      var c = B[m].c, c0 = B[m - 1].c, ru = (hi - lo) / E, mv3 = B[m - 3] ? c - B[m - 3].c : 0, cand = [];
      var k = Math.floor(m / 20), io = 20 * (k - 1) - 1, ic = 20 * k - 1;
      var o20 = (k >= 1 && io >= 0 && B[io]) ? B[io].c : B[0].c, c20 = (k >= 1 && B[ic]) ? B[ic].c : null;
      var t20 = (c20 === null || m < 20) ? 0 : (c20 > o20 ? 1 : -1);
      if (slot === "A" && m < CFG.orbLast && X.mode !== "fade") {
        if (!done[1] && c > orh) { cand.push([1, "orb"]); done[1] = true; }
        if (!done[-1] && c < orl) { cand.push([-1, "orb"]); done[-1] = true; }
      }
      // v11 (Nov 22): on a "fade" morning the first close outside the opening range is NOT bought; if price closes back inside
      // within 10 min, buy the other way (the failed break). Not filtered by the 3-min rule — it is a reversal by design.
      if (slot === "A" && m < CFG.orbLast && X.mode === "fade") {
        [[1, orh], [-1, orl]].forEach(function (pr) {
          var dr = pr[0], L = pr[1];
          if (done[dr] || !(dr * (c - L) > 0)) return;
          done[dr] = true;
          for (var j = m + 1; j < Math.min(m + 11, CFG.scanEnd); j++) if (B[j] && dr * (B[j].c - L) < -0.05) { fades.push({ m: j, s: dr > 0 ? "P" : "C", kind: "orbfade", lvl: dr > 0 ? "ORH" : "ORL" }); break; }
        });
      }
      if (slot === "B") {
        if (ru >= CFG.ruLo && ru < CFG.ruHi) levels(D, m, orh, orl).forEach(function (pr) {
          [1, -1].forEach(function (dr) {
            var L = pr[1], key = pr[0] + dr;
            if (dr * (c - L) > 0.05 && dr * (c0 - L) <= 0 && m - (last[key] == null ? -99 : last[key]) >= 6) {
              last[key] = m;
              if ((dr > 0 && c >= hi - 0.05) || (dr < 0 && c <= lo + 0.05)) cand.push([dr, "ext", pr[0]]);
            }
          });
        });
        if ((X.garch || 0) > CFG.popG && t20 && t20 * mv3 >= CFG.popT * T) cand.push([t20, "pop"]);
      }
      for (var n = 0; n < cand.length; n++) {
        var dr = cand[n][0];
        if (dr * mv3 < 0) continue;                       // never against the last 3-min move
        out.push({ m: m, s: dr > 0 ? "C" : "P", kind: cand[n][1], lvl: cand[n][2] || null }); break;
      }
    }
    if (fades.length) out = out.concat(fades).sort(function (a, b) { return a.m - b.m; });
    return out;
  }
  function trade(D, B, sig) {
    var p = pick(D, sig.s, sig.m); if (!p) return null;
    var r = manage(D, B, sig.s, p.K, p.j, p.x[3]);
    return { kind: sig.kind, lvl: sig.lvl, side: sig.s, m: sig.m, mf: p.j, K: Number(p.K), paid: p.x[3], delta: p.x[4], iv: p.x[1],
      out: r.out, sold: r.px, why: r.why, hw: r.hw, usd: Math.round(((r.px - p.x[3]) * 100 - CFG.FEE) * 10) / 10 };
  }
  // seeded random baseline: same contract + exits, 3 random times 6:45–8:29, random side; mean over 20 seeds
  function rng(seed) { return function () { seed |= 0; seed = seed + 0x6D2B79F5 | 0; var t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  function baseline(D, B, day) {
    var tot = 0, n = 0, h = 0;
    for (var i = 0; i < day.length; i++) h = (h * 31 + day.charCodeAt(i)) | 0;
    for (var sd = 0; sd < 20; sd++) {
      var R = rng(h + sd * 7919), ms = [];
      while (ms.length < 3) { var mm = 15 + Math.floor(R() * 105); if (ms.indexOf(mm) < 0) ms.push(mm); }
      ms.sort(function (a, b) { return a - b; });
      var busy = -1;
      ms.forEach(function (m) {
        var s = R() < 0.5 ? "C" : "P";
        if (m <= busy || !B[m]) return;
        var t = trade(D, B, { m: m, s: s, kind: "random" }); if (!t) return;
        tot += t.usd; n++; busy = t.out;
      });
    }
    return { usd: Math.round(tot / 20 * 10) / 10, trades: Math.round(n / 20 * 10) / 10 };
  }
  function runDay(D, X, day) {
    var B = bars(D), iv = atmIV(D);
    if (!B[0] || !X.range10) return { skip: "not enough data" };
    if (iv == null || iv > 1.2) return { skip: "option quotes look stale (ATM IV " + (iv == null ? "missing" : Math.round(iv * 100) + "%") + ")" };
    var trades = [];
    ["A", "B"].forEach(function (slot) {
      var busy = -1, n = 0, cap = slot === "A" ? CFG.capA : CFG.capB;
      signals(D, B, X, iv, slot).forEach(function (sig) {
        if (sig.m <= busy || n >= cap) return;
        var t = trade(D, B, sig); if (!t) return;
        trades.push(t); busy = t.out; n++;
      });
    });
    trades.sort(function (a, b) { return a.mf - b.mf; });
    var usd = trades.reduce(function (a, t) { return a + t.usd; }, 0);
    return { iv: iv, implied_move: Math.round(B[0].c * iv / Math.sqrt(252) * 100) / 100, range10: Math.round(X.range10 * 100) / 100,
      garch: X.garch, hv: (X.garch || 0) > CFG.popG, mode: X.mode || "follow", trades: trades, usd: Math.round(usd * 10) / 10, baseline: baseline(D, B, day || "") };
  }
  // v11 master switch (Nov 22 regime study): last evening's VIX change + GARCH vol decide follow vs fade for the opening range.
  //   VIX fell > 3.5% (vol release) or VIX up ≥ 2.5% with GARCH ≥ 22 (fast vol bid) → "follow"; otherwise (vol stall, calm vol bid) → "fade".
  function regime(vixChg, garch) {
    if (vixChg == null) return { mode: "follow", name: "no VIX read" };
    if (vixChg < -3.5) return { mode: "follow", name: "vol release" };
    if (vixChg >= 2.5 && (garch || 0) >= 22) return { mode: "follow", name: "fast vol bid" };
    if (vixChg >= 2.5) return { mode: "fade", name: "calm vol bid" };
    return { mode: "fade", name: "vol stall" };
  }
  var api = { runDay: runDay, regime: regime, CFG: CFG };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.V10 = api;
})(this);
