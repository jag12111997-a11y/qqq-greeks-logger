/* 3v3 v9.2 morning map — builds the engine's input for one morning from the dashboard's own files.
   Used by the live card (history files + today's live feeds) and by the nightly paper log (history + logger CSVs).
   All prices are real (unadjusted). Minutes are counted from 6:30 PT = 9:30 New York time, so daylight saving is handled.
*/
(function (root) {
  "use strict";
  // 9:30 New York time for a session date, as epoch seconds (works in summer and winter).
  function open930(day) {
    var p = day.split("-").map(Number), t = Date.UTC(p[0], p[1] - 1, p[2], 13, 30) / 1000;
    var f = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
    var q = {}; f.formatToParts(new Date(t * 1000)).forEach(function (x) { q[x.type] = x.value; });
    return t + (570 - (Number(q.hour) * 60 + Number(q.minute))) * 60;
  }
  function isoWeek(day) {
    var p = day.split("-").map(Number), d = new Date(Date.UTC(p[0], p[1] - 1, p[2])), dow = d.getUTCDay() || 7;
    d.setUTCDate(d.getUTCDate() + 4 - dow);
    var y0 = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
    return d.getUTCFullYear() * 100 + Math.ceil(((d - y0) / 86400000 + 1) / 7);
  }
  function session(c) { c = c.slice(0, 390); var h = -1e9, l = 1e9; c.forEach(function (x) { h = Math.max(h, x.high); l = Math.min(l, x.low); }); return { o: c[0].open, h: h, l: l, c: c[c.length - 1].close }; }
  // Yesterday's value area: 70% of the session's volume around the point of control, typical price in 0.10 bins.
  function valueArea(c) {
    c = c.slice(0, 390);
    var vol = {}, tot = 0, anyVol = c.some(function (x) { return (x.volume || 0) > 0; });
    c.forEach(function (x) {
      var px = (x.high + x.low + x.close) / 3, b = Math.round(roundHalfEven(px / 0.10)) * 0.10, v = anyVol ? (x.volume || 0) : 1;
      vol[b] = (vol[b] || 0) + v; tot += v;
    });
    var lev = Object.keys(vol).map(Number).sort(function (a, b) { return a - b; });
    var poc = lev[0]; lev.forEach(function (b) { if (vol[b] > vol[poc]) poc = b; });
    var i = lev.indexOf(poc), lo = i, hi = i, acc = vol[poc];
    while (acc < 0.70 * tot && (lo > 0 || hi < lev.length - 1)) {
      var up = hi < lev.length - 1 ? vol[lev[hi + 1]] : -1, dn = lo > 0 ? vol[lev[lo - 1]] : -1;
      if (up >= dn) { hi++; acc += up; } else { lo--; acc += dn; }
    }
    return { yval: lev[lo], yvah: lev[hi], poc: poc };
  }
  function roundHalfEven(x) { var r = Math.round(x); return (Math.abs(x % 1) === 0.5 && r % 2 !== 0) ? r - 1 : r; }

  /* hist: {date: candles[]} for previous sessions (at least the last 12; 15+ recommended for the week candle).
     today: {date, candles[] (1-min bars, epoch 'time' = bar start), points[] (GEX, epoch 'time'),
             quotes: [{t: epoch seconds of the snapshot, side: 'C'|'P', strike, bid, ask, iv (fraction), delta}],
             complete: bool} */
  function buildDay(hist, today) {
    var day = today.date, t0 = open930(day);
    var prev = Object.keys(hist).filter(function (d) { return d < day; }).sort();
    var y = session(hist[prev[prev.length - 1]]), y2 = session(hist[prev[prev.length - 2]]);
    var wk = {}; prev.forEach(function (d) { wk[isoWeek(d)] = session(hist[d]).c; });
    var cw = isoWeek(day), ks = Object.keys(wk).map(Number).filter(function (k) { return k < cw; }).sort(function (a, b) { return a - b; });
    var week = ks.length >= 2 ? (wk[ks[ks.length - 1]] > wk[ks[ks.length - 2]] ? 1 : -1) : 0;
    var p10 = prev.slice(-10).map(function (d) { return session(hist[d]); });
    var ctx = { PDH: y.h, PDL: y.l, PDC: y.c, day: y.c > y2.c ? 1 : -1, week: week };
    var lv = { pdh: y.h, pdl: y.l, pdc: y.c, h10: Math.max.apply(null, p10.map(function (s) { return s.h; })), l10: Math.min.apply(null, p10.map(function (s) { return s.l; })) };
    // EMA8 carried from yesterday's session into today
    var a = 2 / 9, yc = hist[prev[prev.length - 1]].slice(0, 390), e = yc[0].close;
    for (var i = 1; i < yc.length; i++) e = a * yc[i].close + (1 - a) * e;
    var bars = {}; (today.candles || []).forEach(function (c) { var m = Math.floor((c.time - t0) / 60); if (m >= 0 && m < 390) bars[m] = c; });
    var maxM = Math.max.apply(null, Object.keys(bars).map(Number).concat([-1]));
    var st = [], lastC = null;
    for (var m = 0; m <= maxM; m++) {
      var c = bars[m];
      if (c) { e = a * c.close + (1 - a) * e; st.push([c.close, c.high, c.low, e]); lastC = c.close; }
      else if (lastC !== null) { e = a * lastC + (1 - a) * e; st.push([lastC, lastC, lastC, e]); }   // a minute the feed skipped
      else {   // the feed skipped the first minute(s): hold the first bar's open so the morning still runs (no crash)
        var f0 = null; for (var jj = m + 1; jj <= maxM; jj++) if (bars[jj]) { f0 = bars[jj].open; break; }
        if (f0 === null) st.push(null); else { e = a * f0 + (1 - a) * e; st.push([f0, f0, f0, e]); lastC = f0; }
      }
    }
    var pts = [], P = {};
    (today.points || []).forEach(function (p) { var k = Math.floor((p.time - t0) / 60); if (k >= 0) P[k] = p; });
    var lastP = {};
    for (m = 0; m <= Math.max(maxM, 150); m++) {
      if (P[m]) lastP = P[m];
      var o = {}; ["put_wall", "call_wall", "oi_magnet", "max_pos_gamma", "max_neg_gamma"].forEach(function (kk) { if (lastP[kk]) o[kk] = lastP[kk]; });
      pts.push(o);
    }
    var opt = { C: {}, P: {} };
    (today.quotes || []).forEach(function (r) {
      if (!(r.bid > 0) || !(r.ask > 0)) return;
      var mm = Math.floor((r.t - t0) / 60), sec = (r.t - t0) - mm * 60, ks2 = String(r.strike);
      var b = opt[r.side][ks2] || (opt[r.side][ks2] = {});
      b[mm] = [(r.bid + r.ask) / 2, r.iv, r.bid, r.ask, r.delta, sec];
    });
    return { st: st, ctx: ctx, lv: lv, pts: pts, opt: opt, va: valueArea(yc), complete: !!today.complete,
      info: { yesterday: prev[prev.length - 1], y: y, open: st[0] ? st[0][0] : null, t0: t0 } };
  }
  var api = { buildDay: buildDay, open930: open930, valueArea: valueArea, isoWeek: isoWeek };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.V92MAP = api;
})(this);
