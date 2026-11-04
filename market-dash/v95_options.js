/* 3v3 "v9.5 + options" — PAPER ONLY (shadow line in the nightly paper log; the card never reads this).
   Six older strategies are allowed to add trades, but the card (v9.5) sets the direction:
     1) the card's latest side today = the direction (nothing counts before the card's first buy, except the box)
     2) an old signal on the card's side      -> taken as it fired, with its own exits ("agree")
     3) an old signal AGAINST the card's side -> flipped: buy the card's side at that minute, card's ladder exit ("flip")
     4) before the card's first buy           -> only the box scalp may trade ("box")
   The six: box scalp · Playbook home run · Tulip v3.2 · turn entry + 0.55 stop · sniper (A+ 3.0 / B 1.5 under red 304) · calls checkpoint.
   Honest timing everywhere: signal on a bar close -> first logged quote after it, buy the ask, sell the bid, $0.013/ct fee per side.
   Backtest (68 mornings, Jul 20 – Nov 3): card +$4,505 · card + options +$7,415 (142 trades, SQN 5.23). */
"use strict";
(function (root) {
  var FEE = 2.6, LN = [8, 14, 35, 52, 80, 120, 250, 304, 400];
  function firstQ(b, m) { for (var j = m + 1; j <= m + 3; j++) if (b && b[j]) return j; return null; }
  function e8up(st, m) { return st[m] && st[m - 1] && st[m - 2] && st[m][3] > st[m - 1][3] && st[m - 1][3] <= st[m - 2][3]; }
  function e8dn(st, m) { return st[m] && st[m - 1] && st[m - 2] && st[m][3] < st[m - 1][3] && st[m - 1][3] >= st[m - 2][3]; }
  function pick(D, side, m, lo, hi, ivmax) { var best = null, spot = D.st[m][0], o = D.opt[side] || {};
    Object.keys(o).forEach(function (k) { var b = o[k], j = firstQ(b, m); if (j == null) return; var q = b[j]; if (q[3] < lo || q[3] > hi || (ivmax && q[1] > ivmax)) return;
      var d = Math.abs(Number(k) - spot); if (!best || d < best.d) best = { K: k, j: j, q: q, d: d }; }); return best; }
  function pickOtm1(D, side, m) { var spot = D.st[m][0], ks = Object.keys(D.opt[side] || {}).map(Number).sort(function (a, b) { return a - b; });
    var K = side === "C" ? ks.find(function (k) { return k > spot; }) : ks.filter(function (k) { return k < spot; }).pop(); if (K == null) return null;
    var b = D.opt[side][String(K)], j = firstQ(b, m); return j == null ? null : { K: String(K), j: j, q: b[j] }; }
  function oEMA(D, side, K, n) { var b = D.opt[side][K] || {}, out = {}, e = null, last = null, a = 2 / (n + 1);
    for (var m = -5; m <= 160; m++) { if (b[m]) last = b[m][0]; if (last == null) { out[m] = null; continue; } e = e === null ? last : a * last + (1 - a) * e; out[m] = e; }
    return { get: function (m) { return out[m] == null ? null : out[m]; }, mid: function (m) { for (var j = m; j >= m - 5; j--) if (b[j]) return b[j][0]; return null; } }; }
  function walk(D, side, p, endM, f) { var b = D.opt[side][p.K], lastJ = null;
    for (var j = p.j + 1; j <= Math.min(endM, 150); j++) { var q = b[j]; if (!q) continue; lastJ = j; var why = f(j, q);
      if (why) { var px = typeof why === "object" ? why.px : q[2]; return { usd: (px - p.q[3]) * 100 - FEE, why: why.why || why, out: j }; } }
    return lastJ == null ? null : { usd: (b[lastJ][2] - p.q[3]) * 100 - FEE, why: "time", out: lastJ }; }
  // the card's ladder (used for flipped signals): 0.50 stop, +0.30 -> stop −0.15, +0.50 arms a 3-tick EMA8 trail, 90 min / 8:56
  function ladder(D, side, m) { var st = D.st, sg = side === "C" ? 1 : -1, p = st[m] ? pick(D, side, m, 0.80, 1.25, 0.35) : null; if (!p) return null;
    var b = D.opt[side][p.K], a = p.q[0], paid = p.q[3], stop = a - 0.50, armed = false, cnt = 0, last = null, lastJ = null;
    for (var j = p.j + 1; j <= Math.min(p.j + 90, 146); j++) { var x = b[j]; if (!x) continue; last = x; lastJ = j;
      if (x[0] <= stop) return { usd: (x[2] - paid) * 100 - FEE, why: "stop", out: j, K: p.K, paid: paid, sold: x[2], fill: p.j };
      if (x[0] >= a + 0.30) stop = Math.max(stop, a - 0.15); if (x[0] >= a + 0.50) armed = true;
      if (armed && st[j] && st[j - 1]) { cnt = sg * (st[j][3] - st[j - 1][3]) < 0 ? cnt + 1 : 0; if (cnt >= 3) return { usd: (x[2] - paid) * 100 - FEE, why: "trail (3 ticks)", out: j, K: p.K, paid: paid, sold: x[2], fill: p.j }; } }
    return last ? { usd: (last[2] - paid) * 100 - FEE, why: "time", out: lastJ, K: p.K, paid: paid, sold: last[2], fill: p.j } : null; }
  // ---------- the six old strategies (signals + their own exits) ----------
  function signals(D, H, hist, V, t0, day) {
    var st = D.st, X = D.ctx, out = [], PT = {}, FR = {}, vol = {};
    (H.points || []).forEach(function (p) { var m = Math.floor((p.time - t0) / 60); if (m >= -60 && m < 390) PT[m] = p; });
    (H.frames || []).forEach(function (f) { var m = Math.floor((f.t - t0) / 60); if (m >= -60 && m < 390) FR[m] = f; });
    (H.candles || []).forEach(function (c) { var m = Math.floor((c.time - t0) / 60); if (m >= 0 && m < 390) vol[m] = c.volume; });
    function pt(m) { for (var j = m - 1; j >= m - 4; j--) if (PT[j]) return PT[j]; return null; }
    function fr(m) { for (var j = m - 1; j >= m - 4; j--) if (FR[j]) return FR[j]; return null; }
    function add(src, side, m, res, extra) { if (!res) return; var o = { src: src, side: side, m: m, usd: res.usd, why: res.why, out: res.out }; for (var k in (extra || {})) o[k] = extra[k]; out.push(o); }
    if (!st.length || !st[5]) return out;
    var dow = new Date(day + "T12:00:00Z").getUTCDay();
    var crossback = function (sgn) { var went = false; return function (j) { var x = st[j]; if (!x) return null; if (sgn * (x[0] - V[304][j]) > 0) went = true; else if (went && sgn * (x[0] - V[304][j]) < 0) return "crossback"; return null; }; };
    // CALLS CHECKPOINT
    (function () { var armed = false; for (var m = 5; m <= 130; m++) { var s = st[m]; if (!s) continue; if (s[0] < V[304][m] && e8up(st, m)) armed = true; if (!armed || s[0] >= V[304][m]) continue;
      var p = pick(D, "C", m, 0.70, 1.50, 0.35); if (!p) continue; var E = oEMA(D, "C", p.K, 11), mid = E.mid(m), e = E.get(m); if (mid == null || e == null || mid < e * 1.08) continue;
      add("calls checkpoint", "C", m, walk(D, "C", p, 146, crossback(1)), { K: p.K, paid: p.q[3] }); break; } })();
    // TURN ENTRY + 0.55 stop
    (function () { var armed = false; for (var m = 5; m <= 90; m++) { var s = st[m]; if (!s || !st[m - 1]) continue; var rising = s[3] > st[m - 1][3];
      if (s[0] < V[304][m] && rising && armed) { var p = pick(D, "C", m, 0.70, 1.50, 0.35); if (p) { var E = oEMA(D, "C", p.K, 18), mid = E.mid(m), e = E.get(m);
        if (mid != null && e != null && mid <= e * 0.85) { var a = p.q[0], cb = crossback(1);
          add("turn entry", "C", m, walk(D, "C", p, 146, function (j, q) { if (q[0] <= a - 0.55) return "stop"; return cb(j); }), { K: p.K, paid: p.q[3] }); break; } } }
      if (s[0] < V[304][m] && rising) armed = true; } })();
    // TULIP touches (shared by Tulip v3.2 and Playbook home run)
    var TUL = [];
    (function () { var prev = Object.keys(hist).sort().slice(-5), mv = [];
      prev.forEach(function (d) { var c = hist[d].slice(0, 390); for (var i = 30; i < c.length; i++) mv.push(Math.abs(c[i].close - c[i - 30].close)); });
      mv.sort(function (a, b) { return a - b; }); var U = mv[Math.floor(mv.length / 2)] || 1, KEYS = ["call_wall", "put_wall", "max_pos_gamma", "max_neg_gamma", "gamma_flip", "oi_magnet"];
      function lvAt(m) { var o = { "y-high": X.PDH, "y-low": X.PDL, "y-close": X.PDC }, p = pt(m), p15 = pt(m - 15); if (p && p15) KEYS.forEach(function (k) { if (p[k] != null && p15[k] != null && Math.abs(p[k] - p15[k]) < 0.01) o[k] = p[k]; }); return o; }
      for (var m = 65; m <= 125; m++) { if (!st[m] || !st[m - 1]) continue; var c = st[m][0], pc = st[m - 1][0], LV = lvAt(m), names = Object.keys(LV);
        for (var i = 0; i < names.length; i++) { var x = LV[names[i]], touch = Math.abs(c - x) <= 0.06 * U || (st[m][1] >= x && st[m][2] <= x); if (!touch) continue;
          var side = pc > x ? "C" : pc < x ? "P" : null; if (!side) continue; var sg = side === "C" ? 1 : -1, arm = false;
          for (var j = m - 30; j < m; j++) if (st[j] && sg * (st[j][0] - x) >= 0.4 * U) { arm = true; break; } if (!arm) continue;
          var dv = sg * (c - V.vw[m]) / U; if (dv < -0.37 || dv > 0.05) continue; TUL.push({ m: m, side: side, lvl: names[i] }); break; } } })();
    function tulTrade(t, ex) { var p = pickOtm1(D, t.side, t.m); if (!p) return null; var a = p.q[0], paid = p.q[3], peak = -9;
      var r = walk(D, t.side, p, t.m + 60, function (j, q) { if (ex.trailFrom != null) { peak = Math.max(peak, q[0] - a); if (peak >= ex.trailFrom && q[0] - a <= peak - 0.30) return "trail"; }
        if (q[0] <= a - ex.stop) return "stop"; if (ex.tgt && q[2] >= paid + ex.tgt) return { why: "target", px: paid + ex.tgt }; return null; }); if (r) { r.K = p.K; r.paid = paid; } return r; }
    // Tulip v3.2: Tue–Fri, max 2/day, one at a time, one loss = done, stop −0.30 / target +2.00 / 60 min, 1 strike OTM
    if (dow >= 2 && dow <= 5) { var n = 0, busy = -1, lost = false; TUL.forEach(function (t) { if (n >= 2 || lost || t.m <= busy) return; var r = tulTrade(t, { stop: 0.30, tgt: 2.00 }); if (!r) return;
      add("Tulip v3.2", t.side, t.m, r, { K: r.K, paid: r.paid, level: t.lvl }); n++; busy = r.out; if (r.usd <= 0) lost = true; }); }
    // Playbook home run: any weekday, max 2/day, one at a time, stop −0.30, trail from +1.20 (0.30 under the peak), 60 min
    (function () { var n = 0, busy = -1; TUL.forEach(function (t) { if (n >= 2 || t.m <= busy) return; var r = tulTrade(t, { stop: 0.30, trailFrom: 1.20 }); if (!r) return;
      add("Playbook home run", t.side, t.m, r, { K: r.K, paid: r.paid, level: t.lvl }); n++; busy = r.out; }); })();
    // SNIPER (rubber band = stretch under red 304: A+ 3.0, B 1.5)
    (function () { for (var m = 5; m <= 130; m++) { var s = st[m]; if (!s) continue; var band = V[304][m] - s[0]; if (band < 1.5) continue; var F = fr(m), net = F ? F.d.reduce(function (t, z) { return t + z[2]; }, 0) : 0;
      if (e8up(st, m) && (band >= 3.0 || net > 0)) { var p = pick(D, "C", m, 0.70, 1.50, 0.35); if (!p) continue; var buyer = X.day === 1, paid = p.q[3];
        var rs = walk(D, "C", p, 146, function (j, q) { if (!buyer && q[2] >= paid + 0.75) return { why: "target", px: paid + 0.75 }; var x = st[j]; if (buyer && x && x[0] > V[304][j]) return "hit 304"; return null; });
        if (!rs) continue; add("sniper", "C", m, rs, { K: p.K, paid: paid }); return; }
      if (band < 3.0 && net <= 0) { for (var j = m + 1; j <= 140; j++) { var x = st[j]; if (!x) continue; if (x[0] < V[304][j] && e8dn(st, j)) { var pp = pick(D, "P", j, 0.70, 1.50, 0.35); if (!pp) break; var pd = pp.q[3];
            add("sniper", "P", j, walk(D, "P", pp, 146, function (k, q) { return q[2] >= pd + 0.75 ? { why: "target", px: pd + 0.75 } : null; }), { K: pp.K, paid: pd }); break; } } return; } } })();
    // BOX SCALP: last 30 min 1.0–2.0 wide, both edges visited 2+ times, lines spread >= 1.5, no break look, wait for a bar 0.15 back off the edge;
    // calls at the floor / puts at the ceiling, +0.30 target, out on a close 0.25 outside the box, option stop 0.30, 30 min
    (function () { var lastEdge = {};
      for (var m = 30; m <= 145; m++) { if (!st[m]) continue; var W = st.slice(m - 30, m).filter(Boolean); if (W.length < 27) continue;
        var Hh = Math.max.apply(null, W.map(function (s) { return s[1]; })), Lo = Math.min.apply(null, W.map(function (s) { return s[2]; })), w = Hh - Lo; if (w < 1.0 || w > 2.0) continue;
        var visits = function (f) { var n = 0, lastM = -99; for (var j = m - 30; j < m; j++) if (st[j] && f(st[j]) && j - lastM >= 5) { n++; lastM = j; } return n; };
        if (visits(function (s) { return s[1] >= Hh - 0.15; }) < 2 || visits(function (s) { return s[2] <= Lo + 0.15; }) < 2) continue;
        var pc = st[m - 1][0]; if (pc > Hh || pc < Lo) continue; var dir = 0;
        if (st[m][2] <= Lo + 0.10 && st[m][0] >= Lo - 0.25) dir = 1; else if (st[m][1] >= Hh - 0.10 && st[m][0] <= Hh + 0.25) dir = -1; if (!dir) continue;
        if (lastEdge[dir] != null && m - lastEdge[dir] < 10) continue;
        var edge = dir > 0 ? Lo : Hh, avg20 = 0; for (var i = 0; i < 20; i++) avg20 += (vol[m - 1 - i] || 0); avg20 = avg20 / 20 || 1; var volx = (vol[m] || 0) / avg20, sp10 = st[m - 10] ? -dir * (st[m][0] - st[m - 10][0]) : 0;
        if (sp10 >= 1.5 && volx >= 1.5) continue;
        var lv = LN.map(function (n) { return V[n][m]; }); if (Math.max.apply(null, lv) - Math.min.apply(null, lv) < 1.5) continue;
        var em = null; for (var j = m + 1; j <= m + 5 && st[j]; j++) { if (dir * (st[j][0] - edge) < -0.25) break; if (dir * (st[j][0] - edge) >= 0.15) { em = j; break; } } if (em == null) continue;
        lastEdge[dir] = m; var side = dir > 0 ? "C" : "P", p = pick(D, side, em, 0.5, 1.5, 0); if (!p) continue;
        var paid = p.q[3], a = p.q[0], outLvl = dir > 0 ? Lo - 0.25 : Hh + 0.25;
        add("box scalp", side, em, walk(D, side, p, p.j + 30, function (j, q) { if (q[0] <= a - 0.30) return "stop"; if (st[j] && dir * (st[j][0] - outLvl) < 0) return "broke box"; if (q[2] >= paid + 0.30) return { why: "target", px: paid + 0.30 }; return null; }),
          { K: p.K, paid: paid, box: [+Lo.toFixed(2), +Hh.toFixed(2)] }); } })();
    return out;
  }
  // ---------- combine with the card ----------
  // card = the card's trades this morning: [{side:"C"|"P", m, mf}]  (mf = fill minute)
  function run(D, H, hist, V, t0, card, day) {
    var sig = signals(D, H, hist, V, t0, day), trades = [];
    var cm = function (a) { return a.mf != null ? a.mf : a.m; };
    sig.forEach(function (t) {
      var before = card.filter(function (a) { return cm(a) < t.m; }).sort(function (x, y) { return cm(y) - cm(x); })[0], cs = before ? before.side : null;
      if (!cs) { if (t.src === "box scalp") trades.push(Object.assign({ how: "box (before the card)" }, t)); return; }
      if (cs === t.side) { trades.push(Object.assign({ how: "agree" }, t)); return; }
      var L = ladder(D, cs, Math.floor(t.m)); if (L) trades.push({ how: "flip", src: t.src, side: cs, m: t.m, usd: L.usd, why: L.why, out: L.out, K: L.K, paid: L.paid, flipped_from: t.side });
    });
    return { trades: trades, signals: sig };
  }
  var api = { run: run, signals: signals, ladder: ladder };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.V95OPT = api;
})(this);
