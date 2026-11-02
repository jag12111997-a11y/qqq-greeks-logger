/* 3v3 v9.2 engine — the same rules for the live card, the nightly paper log and the proof.
   Port of the Python replay (replay.py day_run + v8d fade-the-week + v9 value filter + collab/flip run_rules).
   Units: option prices in 0.00 per contract. res = sold − paid − 0.013 fee (×100 = dollars at 1 contract).

   Input for one morning (D):
     st[m]   = [close, high, low, ema8] for QQQ 1-min bar m (m = minutes after 6:30 PT), or null
     ctx     = {PDH, PDL, PDC, day: +1 green / −1 red, week: +1 up / −1 down}
     lv      = {pdh, pdl, pdc, h10, l10}       (yesterday + last 10 sessions, for "room" to levels)
     pts[m]  = {put_wall, call_wall, oi_magnet, max_pos_gamma, max_neg_gamma} (GEX strikes known at minute m)
     opt     = {C:{strike:{minute:[mid, iv, bid, ask, delta, secondInMinute]}}, P:{...}}
     va      = {yval, yvah} yesterday's value area (or null)
     complete = true when the morning's data is final (backtest / nightly log); false while live.
   Shadow tests only (paper log, never the card): opts {trailMode: "three"} = sell after 3 EMA8 ticks against in a row;
   opts {vb:true, vbStart:60, vbMax:1} adds "VIX bounce calls" —
     call EMA8 turns after 7:30, 1 per day, on mornings after a red day with VIX >= 16.5 (decided by the caller).
*/
(function (root) {
  "use strict";
  var DEF = { flush: 1.0, gapx: 2.5, strike: 1.0, SL: 0.50, t_trig: 0.30, t_stop: 0.15, arm: 0.50, hold: 90,
    throttle: 10, gap_maxn: 2, gap_gap: 30, fw_start: 60, LO: 0.80, HI: 1.25, IVMAX: 0.35, END: 146, FEE: 0.013,
    guard: 1.0, window: 45, day_stops: 2, flip_max: 1, causal: false, vb: false, vbBlockPuts: false, vbOnly: false, vbStart: 8, vbMax: 99, trailMode: "tick" };

  function runDay(D, opts) {
    var C = {}, k;
    for (k in DEF) C[k] = DEF[k];
    for (k in (opts || {})) C[k] = opts[k];
    var st = D.st, X = D.ctx, nb = st.length, complete = D.complete !== false;
    var OPT = { C: {}, P: {} };            // side -> strike(Number) -> {mins:[sorted], q:{m:arr}}
    var ORDER = { C: [], P: [] };          // strike order as delivered (tie-break like Python dicts)
    ["C", "P"].forEach(function (s) {
      var src = (D.opt && D.opt[s]) || {};
      Object.keys(src).forEach(function (Ks) {
        var K = Number(Ks), qm = {}, mins = [];
        Object.keys(src[Ks]).forEach(function (ms) { var m = Number(ms); qm[m] = src[Ks][ms]; mins.push(m); });
        mins.sort(function (a, b) { return a - b; });
        OPT[s][K] = { mins: mins, q: qm }; ORDER[s].push(K);
      });
    });
    function S(m) { var r = m >= 0 && m < nb ? st[m] : null; return r ? { close: r[0], high: r[1], low: r[2], e8: r[3] } : null; }
    function e8(m) { var r = m >= 0 && m < nb ? st[m] : null; return r && r[3] != null ? r[3] : null; }
    function q(side, K, m) { var b = OPT[side][K]; return b && b.q[m] ? b.q[m] : null; }
    function nextq(side, K, m) { for (var j = m; j <= m + 3; j++) { var x = q(side, K, j); if (x) return [j, x]; } return [null, null]; }
    function actq(side, K, m) { var x = q(side, K, m); if (x && x[5] >= 55) return [m, x]; return nextq(side, K, m + 1); }
    function nearest(side, m) {
      var c = S(m).close, best = null;
      ORDER[side].forEach(function (K) {
        var x = OPT[side][K].q[m]; if (!x) return;
        var mid = x[0], iv = x[1];
        if (mid >= C.LO && mid <= C.HI && iv != null && iv <= C.IVMAX && (best === null || Math.abs(K - c) < Math.abs(best - c))) best = K;
      });
      return best;
    }
    function nearroom(m, side) {
      var p = (D.pts && m < D.pts.length && D.pts[m]) || {}, c = S(m).close, sg = side === "C" ? 1 : -1;
      var gex = ["put_wall", "call_wall", "oi_magnet", "max_pos_gamma", "max_neg_gamma"].map(function (kk) { return p[kk]; }).filter(function (v) { return v; });
      var lv = [D.lv.pdh, D.lv.pdl, D.lv.pdc, D.lv.h10, D.lv.l10, S(0).close, Math.floor(c / 5) * 5, Math.ceil(c / 5) * 5];
      if (m >= 10) {
        var hi = -1e9, lo = 1e9;
        for (var j = 0; j < m - 5; j++) { var s = S(j); if (s) { hi = Math.max(hi, s.high); lo = Math.min(lo, s.low); } }
        lv.push(hi, lo);
      }
      var near = gex.length ? Math.min.apply(null, gex.map(function (g) { return Math.abs(c - g); })) : 9;
      var ahead = gex.concat(lv).map(function (x) { return sg * (x - c); }).filter(function (v) { return v > 0.25; });
      return [near, ahead.length ? Math.min.apply(null, ahead) : 9];
    }
    function turn(m, side) {
      var sg = side === "C" ? 1 : -1, E = [0, 1, 2, 3, 4].map(function (i) { return e8(m - i); });
      if (E.indexOf(null) >= 0) return false;
      return sg * (E[0] - E[1]) > 0 && sg * (E[1] - E[2]) <= 0 && sg * (E[2] - E[3]) <= 0 && sg * (E[3] - E[4]) <= 0;
    }
    function turned(m, sg) {
      var a = e8(m), b = e8(m - 1), c = e8(m - 2);
      return a !== null && b !== null && c !== null && sg * (a - b) > 0 && sg * (b - c) <= 0;
    }
    // One trade, managed the live way: stop on the observed mid (sell at the bid), +0.30 -> stop −0.15,
    // +0.50 arms the trail (sell on the first EMA8 tick against, at the first quote after that bar closes), time exit.
    function fleg(side, K, m, aMid) {
      var stop = aMid - C.SL, armed = false, hw = 0, sg = side === "C" ? 1 : -1, b = OPT[side][K], ev = [];
      var js = b.mins.filter(function (j) { return j > m && j <= Math.min(m + C.hold, C.END); });
      function sell(j, why) {
        var late = why === "q8", r = late ? actq(side, K, j) : nextq(side, K, j), kk = r[0], x = r[1];
        // live: the trail fired at this bar's close but the next quote isn't in yet -> SELL NOW, fill not known yet
        if (late && x === null && !complete) return { out: Infinity, px: null, why: "sell_now", sellAt: j, hw: hw, ev: ev, stop: stop, mid: b.q[j][0] };
        if (x === null) { kk = j; x = q(side, K, j); }
        var px = x ? x[2] : b.q[j][0];
        return { out: (late && kk) ? kk : j, px: px, why: why, hw: hw, ev: ev };
      }
      if (!js.length) {
        if (!complete && !(D.now != null && D.now > Math.min(m + C.hold, C.END) + 3)) return { out: Infinity, px: null, why: "open", hw: 0, ev: ev, stop: stop, last: m, mid: aMid };
        return { out: m, px: null, why: "none", hw: 0, ev: ev };
      }
      for (var i = 0; i < js.length; i++) {
        var j = js[i], c = b.q[j][0], up = c - aMid;
        hw = Math.max(hw, up);
        if (c <= stop) return sell(j, "stop");
        if (hw >= C.arm) {
          // trail: "tick" = sell on the first EMA8 tick against (v9.2) · "three" = 3 EMA8 ticks against in a row (shadow test)
          var x_ = e8(j), y_ = e8(j - 1);
          if (C.trailMode === "three") {
            var z_ = e8(j - 2), w_ = e8(j - 3);
            if (x_ !== null && y_ !== null && z_ !== null && w_ !== null && sg * (x_ - y_) < 0 && sg * (y_ - z_) < 0 && sg * (z_ - w_) < 0) return sell(j, "q8");
          } else if (x_ !== null && y_ !== null && sg * (x_ - y_) < 0) return sell(j, "q8");
        }
        if (!armed && up >= C.t_trig) { armed = true; stop = aMid - C.t_stop; ev.push([j, "move_stop", stop]); }
        if (hw >= C.arm && ev.every(function (e) { return e[1] !== "trail_armed"; })) ev.push([j, "trail_armed", aMid + C.arm]);
      }
      var last = js[js.length - 1];
      if (!complete && last < Math.min(m + C.hold, C.END) && !(D.now != null && D.now > Math.min(m + C.hold, C.END) + 3)) {
        return { out: Infinity, px: null, why: "open", hw: hw, ev: ev, stop: stop, last: last, mid: b.q[last][0] };
      }
      return sell(last, "time");
    }
    function leg(side, j) {
      var K = nearest(side, j); if (K === null) return null;
      var r = actq(side, K, j), kk = r[0], y = r[1]; if (!y) return null;
      var L = fleg(side, K, kk, y[0]);
      return { K: K, mf: kk, ask: y[3], aMid: y[0], iv: y[1], delta: y[4], out: L.out, px: L.px, why: L.why, hw: L.hw, ev: L.ev,
        stop: L.stop, mid: L.mid, res: L.px === null ? null : L.px - y[3] - C.FEE };
    }

    // ---------- stage A: the signals (trap / gap-and-go / fade-the-week), max 2 open, trap wins ----------
    var o = S(0).close, gapC = o - X.PDC, gapP = X.PDC - o;
    var trades = [], log = [], last = { C: -99, P: -99 }, zone = { C: false, P: false }, trapSeen = {}, gapn = { C: 0, P: 0 }, gaplast = { C: -999, P: -999 };
    var zoneFirst = { C: null, P: null };
    function fwValueOk() {
      if (!D.va) return true;
      var s30 = S(30), c = s30 ? s30.close : S(0).close;
      return D.va.yval <= c && c <= D.va.yvah;
    }
    var mEnd = Math.min(121, nb);
    for (var m = 8; m < mEnd; m++) {
      var s = S(m); if (!s) continue;
      var c = s.close;
      ["C", "P"].forEach(function (side) {
        var sg = side === "C" ? 1 : -1, dayok = X.day === sg, fl = sg > 0 ? X.PDL - c : c - X.PDH, now = dayok && fl >= C.flush;
        zone[side] = now; if (now) { trapSeen[side] = true; if (zoneFirst[side] === null) zoneFirst[side] = m; }
      });
      ["C", "P"].forEach(function (side) {
        if (!turn(m, side) || m - last[side] < C.throttle) return;
        var sg = side === "C" ? 1 : -1, gapway = sg > 0 ? gapC : gapP, isTrap = zone[side], nr = nearroom(m, side), near = nr[0];
        var isGap = !isTrap && gapway >= C.gapx && near >= C.strike && X.week === sg;
        var isVB = C.vb && side === "C" && !isTrap && !isGap && m >= C.vbStart && trades.filter(function (t) { return t.kind === "vb"; }).length < C.vbMax;
        if (C.vb && C.vbBlockPuts && side === "P") return;
        if (C.vbOnly && !isVB) return;
        var gapArmed = (gapC >= C.gapx && X.week === 1) || (gapP >= C.gapx && X.week === -1);
        var isFw = !isTrap && !isGap && !isVB && X.week === -sg && !Object.keys(trapSeen).length && !gapArmed && m >= C.fw_start &&
          !trades.some(function (t) { return t.kind !== "fw"; }) && fwValueOk();
        if (!(isTrap || isGap || isFw || isVB)) return;
        last[side] = m;
        if (isGap && (gapn[side] >= C.gap_maxn || m - gaplast[side] < C.gap_gap)) return;
        if (isGap && trapSeen[side === "C" ? "P" : "C"]) { log.push([m, "block", side]); return; }
        if (trades.filter(function (t) { return t.out > m; }).length >= 2) { log.push([m, "busy", side]); return; }
        var K = nearest(side, m); if (K === null) return;
        var r = actq(side, K, m); if (!r[1]) return;
        var t = { side: side, K: K, kind: isTrap ? "trap" : (isGap ? "gap" : (isVB ? "vb" : "fw")), m: m, mf: r[0], out: Infinity };
        trades.push(t);
        if (isGap) { gapn[side] += 1; gaplast[side] = m; }
        var L = fleg(side, K, r[0], r[1][0]); t.out = L.out;
      });
    }

    // ---------- stage B: 0.50 stop, one re-entry, fake-out line, one flip, 2 stops = done ----------
    var heap = trades.map(function (t, i) { return { m: t.m, n: i, e: { side: t.side, kind: t.kind, m: t.m, src: "model" } }; });
    var cnt = heap.length, stops = 0, flips = 0, blocked = { C: 999, P: 999 }, taken = {}, fin = [];
    function pop() { heap.sort(function (a, b) { return a.m - b.m || a.n - b.n; }); return heap.shift(); }
    function stopsBy(m) { return C.causal ? fin.filter(function (f) { return f.why === "stop" && f.out <= m; }).length : stops; }
    while (heap.length) {
      var it = pop(), mm = it.m, e = it.e;
      if (stopsBy(mm) >= C.day_stops) break;
      var side = e.side, sg = side === "C" ? 1 : -1;
      if (mm >= blocked[side]) continue;
      if (taken[side + mm]) continue;
      var L = leg(side, mm); if (!L) continue;
      taken[side + mm] = true;
      for (k in L) e[k] = L[k];
      fin.push(e);
      var lows = [], j;
      for (j = Math.max(0, mm - 20); j <= mm; j++) { var sj = S(j); if (sj) lows.push(sg > 0 ? sj.low : sj.high); }
      var swing = sg > 0 ? Math.min.apply(null, lows) : Math.max.apply(null, lows);
      e.fakeout = swing - sg * C.guard;                      // QQQ price that breaks the idea
      var brk = null;
      for (j = mm + 1; j <= Math.min(mm + 90, 150); j++) {
        var sb = S(j); if (sb && sg * (swing - (sg > 0 ? sb.low : sb.high)) > C.guard) { brk = j; break; }
      }
      e.brk = brk;
      if (brk !== null) {
        blocked[side] = Math.min(blocked[side], brk);
        if ((flips < C.flip_max && (e.kind === "trap" || e.kind === "re-trap")) || (flips < C.flip_max && e.src === "model")) {
          var osd = side === "C" ? "P" : "C", osg = -sg;
          for (j = brk + 1; j <= Math.min(brk + C.window, 120); j++) {
            if (S(j) && S(j - 2) && turned(j, osg)) { heap.push({ m: j, n: cnt++, e: { side: osd, kind: "flip", m: j, src: "flip" } }); flips++; break; }
          }
        }
      }
      if (L.why === "stop") {
        stops++;
        // re-entry only uses what is already known: blocked if the fake-out line broke before the stop; a later break is caught by the scan below
        if (e.kind !== "flip" && e.kind.indexOf("re-") !== 0 && (brk === null || brk > L.out) && stops < C.day_stops) {
          for (j = L.out + 1; j <= Math.min(L.out + C.window, 120); j++) {
            var sr = S(j);
            if (!sr || !S(j - 2)) continue;
            if (sg * (swing - (sg > 0 ? sr.low : sr.high)) > C.guard) break;
            if (turned(j, sg)) { heap.push({ m: j, n: cnt++, e: { side: side, kind: "re-" + e.kind, m: j, src: "re" } }); break; }
          }
        }
      }
    }
    return { trades: fin, signals: trades, log: log, gap: o - X.PDC, open: o, zoneFirst: zoneFirst,
      gapArmed: { C: gapC >= C.gapx && X.week === 1, P: gapP >= C.gapx && X.week === -1 }, fwOk: fwValueOk() };
  }
  var api = { runDay: runDay, DEF: DEF };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.V92 = api;
})(this);
