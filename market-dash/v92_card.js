/* 3v3 v9.2 Game Plan card — calm, v9.2 rules only, signals only (you place every trade in Webull).
   Needs v92_engine.js (window.V92) and v92_map.js (window.V92MAP).
   Units: option prices in 0.00; P&L in dollars at 1 contract.
   V92CARD.mount(el, {base: './'})                       live mode (today, polls every 20 s)
   V92CARD.mount(el, {base: './', day: 'YYYY-MM-DD', at: 'H:MM'})   replay a past morning at a PT clock time
*/
(function (root) {
  "use strict";
  var RELAY = "https://gex-relay.jag12111997.workers.dev/v1/";
  var HOLIDAYS = { "2026-11-26": "Thanksgiving: market closed", "2026-12-25": "Christmas: market closed" };
  var EARLY = { "2026-11-27": "Early close 10:00 AM PT", "2026-12-24": "Early close 10:00 AM PT" };
  var RELEASES = { "2026-07-02": "Jobs report", "2026-07-14": "CPI", "2026-07-30": "PCE", "2026-08-07": "Jobs report", "2026-08-12": "CPI",
    "2026-08-26": "PCE", "2026-09-04": "Jobs report", "2026-09-11": "CPI", "2026-09-30": "PCE", "2026-10-02": "Jobs report", "2026-10-14": "CPI",
    "2026-10-29": "PCE", "2026-11-10": "CPI" };

  function pad(n) { return String(n).padStart(2, "0"); }
  function clock(m) { var t = 390 + m; return ((Math.floor(t / 60) + 11) % 12 + 1) + ":" + pad(t % 60); }
  function usd(v) { if (v == null || !isFinite(v)) return "—"; var r = Math.round(v); return (r > 0 ? "+$" : r < 0 ? "−$" : "$") + Math.abs(r).toLocaleString(); }
  function p2(v) { return v == null || !isFinite(v) ? "—" : Number(v).toFixed(2); }
  function q2(v) { return v == null || !isFinite(v) ? "—" : Number(v).toFixed(2); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function nyDate(ms) {
    var f = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit", weekday: "short" });
    var p = {}; f.formatToParts(new Date(ms)).forEach(function (x) { p[x.type] = x.value; });
    return { date: p.year + "-" + p.month + "-" + p.day, wd: p.weekday };
  }
  function ageMin(stamp) {
    if (!stamp) return Infinity;
    var t = Date.parse(/[zZ]$|[+-]\d\d:?\d\d$/.test(stamp) ? stamp : String(stamp).replace(" ", "T") + "Z");
    return isFinite(t) ? (Date.now() - t) / 60000 : Infinity;
  }
  function getJSON(url, ms) {
    var ac = typeof AbortController !== "undefined" ? new AbortController() : null, tm = ac ? setTimeout(function () { ac.abort(); }, ms || 15000) : null;
    return fetch(url, ac ? { signal: ac.signal, cache: "no-cache" } : { cache: "no-cache" })
      .then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
      .finally(function () { if (tm) clearTimeout(tm); });
  }
  function getText(url) { return fetch(url, { cache: "no-cache" }).then(function (r) { return r.ok ? r.text() : null; }).catch(function () { return null; }); }
  function live(base, name) {
    return getJSON(RELAY + name, 8000).then(function (rj) {
      if (rj && ageMin(rj.generated_utc || rj.updated_utc) < 6) return rj;
      return getJSON(base + name + "?t=" + Date.now(), 20000).then(function (pj) {
        var a = String((pj && (pj.generated_utc || pj.updated_utc)) || ""), b = String((rj && (rj.generated_utc || rj.updated_utc)) || "");
        return a > b ? pj : (rj || pj);
      });
    });
  }
  function parseCSV(txt, day, side) {
    if (!txt) return [];
    var L = txt.trim().split("\n"), h = L[0].split(","), ix = {}; h.forEach(function (k, i) { ix[k] = i; });
    var out = [];
    for (var i = 1; i < L.length; i++) {
      var v = L[i].split(","); if (v[ix.expiration] !== day) continue;
      var n = function (k) { var x = v[ix[k]]; return x === "" || x == null ? null : Number(x); };
      var s = side, dl = n("delta");
      if (!s) { if (dl === null) continue; s = dl >= 0 ? "C" : "P"; }
      out.push({ t: Date.parse(v[ix.run_time].replace(" ", "T") + "Z") / 1000, side: s, strike: n("strike"), bid: n("bid"), ask: n("ask"), iv: n("iv"), delta: dl });
    }
    return out;
  }
  function csvQuotes(base, day) {
    var d = base + "../data/" + day + "/am/";
    return Promise.all([getText(d + "qqq_greeks_calls_" + day + "_am.csv"), getText(d + "qqq_greeks_puts_" + day + "_am.csv")]).then(function (r) {
      if (r[0] || r[1]) return parseCSV(r[0], day, "C").concat(parseCSV(r[1], day, "P"));
      return getText(d + "qqq_greeks_" + day + "_am.csv").then(function (t) { return parseCSV(t, day, null); });
    });
  }

  var HIST = {};   // date -> candles (cached for the page's life)
  function history(base, day) {
    return getJSON(base + "history/index.json?t=" + Math.floor(Date.now() / 3600000)).then(function (ix) {
      var days = ((ix && ix.days) || []).filter(function (d) { return d < day; }).slice(-20);
      return Promise.all(days.map(function (d) {
        if (HIST[d]) return Promise.resolve();
        return getJSON(base + "history/" + d + ".json").then(function (j) { if (j && j.candles && j.candles.length >= 300) HIST[d] = j.candles; });
      })).then(function () { var h = {}; days.forEach(function (d) { if (HIST[d]) h[d] = HIST[d]; }); return h; });
    });
  }

  // Yesterday's VIX close from the dashboard's own market snapshot (taken after that session closed). Observation only.
  function ptMin(f) { var h = Number(f.slice(11, 13)), m = Number(f.slice(14, 16)); return ((h % 12) + (f.slice(17, 19) === "PM" ? 12 : 0)) * 60 + m; }
  function vixYday(base, hist) {
    var ds = Object.keys(hist || {}).sort(), d = ds[ds.length - 1];
    if (!d) return Promise.resolve(null);
    return getJSON(base + "data/" + d + "/pm/market_dash_" + d + "_pm.json").then(function (j) {
      var v = j && j.markets && j.markets.VIX, f = j && j.fetched_pt;
      return v && f && f.slice(0, 10) === d && ptMin(f) >= 13 * 60 + 30 && v.last > 0 ? Number(v.last) : null;
    }).catch(function () { return null; });
  }
  function withVix(base, X) { return vixYday(base, X.hist).then(function (v) { X.vix = v; return X; }); }

  // ---------------- data for one refresh ----------------
  function loadReplay(o) {
    var day = o.day, p = o.at.split(":").map(Number), atM = (p[0] < 6 ? p[0] + 12 : p[0]) * 60 + p[1] - 390;
    return Promise.all([history(o.base, day), getJSON(o.base + "history/" + day + ".json"), csvQuotes(o.base, day), getJSON(o.base + "v92_log/index.json?t=" + Math.floor(Date.now() / 600000))]).then(function (r) {
      var t0 = V92MAP.open930(day), cut = t0 + atM * 60;
      var today = r[1] || { candles: [], points: [] };
      return { day: day, atM: atM, hist: r[0], replay: true, stale: null, log: r[3],
        today: { date: day, complete: false,
          candles: (today.candles || []).filter(function (c) { return c.time + 60 <= cut; }),
          points: (today.points || []).filter(function (x) { return x.time <= cut + 10; }),
          frames: (today.frames || []).filter(function (x) { return x.t <= cut + 10; }),
          quotes: (r[2] || []).filter(function (q) { return q.t <= cut + 10; }) } };
    }).then(function (X) { return withVix(o.base, X); });
  }
  var QKEY = function (d) { return "v92q-" + d; };
  function loadLive(o) {
    var now = Date.now(), nd = nyDate(now), day = nd.date, t0 = V92MAP.open930(day), atM = Math.floor((now / 1000 - t0) / 60);
    return Promise.all([history(o.base, day), live(o.base, "auction_live.json"), live(o.base, "gex_intraday.json"), live(o.base, "options_latest.json"),
      live(o.base, "gex_live.json"), atM >= 0 ? csvQuotes(o.base, day) : Promise.resolve([]), getJSON(o.base + "v92_log/index.json?t=" + Math.floor(Date.now() / 600000)),
      live(o.base, "gex_frames.json")]).then(function (r) {
      var au = r[1], gi = r[2], ol = r[3], gl = r[4], gf = r[7];
      var Q = {};
      try { Q = JSON.parse(localStorage.getItem(QKEY(day)) || "{}"); } catch (e) { Q = {}; }
      function addQ(q) { if (q && q.t && q.bid > 0 && q.ask > 0) Q[q.side + "|" + q.strike + "|" + Math.floor((q.t - t0) / 60)] = q; }
      (r[5] || []).forEach(addQ);
      if (ol && ol.contracts) ol.contracts.forEach(function (c) {
        if (c.expiration !== day) return;
        var t = Date.parse(String(c.stamp).replace(" ", "T") + "Z") / 1000;
        addQ({ t: t, side: c.kind === "call" ? "C" : "P", strike: Number(c.strike), bid: Number(c.bid), ask: Number(c.ask), iv: c.iv == null ? null : Number(c.iv) / 100, delta: c.delta == null ? null : Number(c.delta) });
      });
      try { localStorage.setItem(QKEY(day), JSON.stringify(Q)); } catch (e) {}
      var fresh = au && au.session_date === day;
      var stale = atM >= 0 && atM < 150 ? Math.max(ageMin(au && au.generated_utc), ageMin(ol && ol.generated_utc)) : null;
      return { day: day, wd: nd.wd, atM: atM, hist: r[0], replay: false, stale: stale, gexRegime: gl && gl.regime, log: r[6],
        today: { date: day, complete: false, candles: fresh ? (au.candles || []) : [], points: (gi && gi.session_date === day) ? (gi.points || []) : [], frames: (gf && gf.session_date === day) ? (gf.frames || []) : [],
          quotes: Object.keys(Q).map(function (k) { return Q[k]; }) } };
    }).then(function (X) { return withVix(o.base, X); });
  }

  // ---- v9.5: the user's MAIN QQQ 1m lines ("the ribbon") + session VWAP; null if anything is missing ----
  var UL = [8, 14, 35, 52, 80, 120, 250, 304, 400], USTK = [[8, 52, 304], [14, 80, 250], [35, 120, 400]];
  function userLines(hist, st, candles, t0) {
    try {
      var ds = Object.keys(hist).sort().slice(-3); if (ds.length < 3 || !st.length) return null;
      var yc = []; ds.forEach(function (d) { hist[d].slice(0, 390).forEach(function (k) { yc.push(k.close); }); });
      var f = null; st.forEach(function (x) { if (f === null && x) f = x[0]; }); if (f === null) return null;
      var c = st.map(function (x) { return x ? x[0] : f; }), cl = yc.concat(c), V = { c: c };
      UL.forEach(function (n) { var a = 2 / (n + 1), e = null, out = []; cl.forEach(function (x, i) { e = e === null ? x : a * x + (1 - a) * e; if (i >= yc.length) out.push(e); }); V[n] = out; });
      var bars = {}; (candles || []).forEach(function (k) { var m = Math.floor((k.time - t0) / 60); if (m >= 0 && m < 390) bars[m] = k; });
      var pv = 0, vv = 0, last = c[0];
      V.vw = c.map(function (x, m) { var b = bars[m]; if (b && b.volume > 0) { pv += (b.high + b.low + b.close) / 3 * b.volume; vv += b.volume; } if (vv > 0) last = pv / vv; return last; });
      if (!(vv > 0)) return null;
      for (var i = 0; i < UL.length; i++) if (V[UL[i]].some(function (v) { return !isFinite(v); })) return null;
      return V;
    } catch (e) { return null; }
  }
  function sizeFor(UV, t, HT) {   // 3 contracts on A trades when the user's lines agree; B (failed poke) always 1
    if (!UV || t.kind === "fp") return 1;
    var sg = t.side === "C" ? 1 : -1, m = t.m;
    if (t.kind.indexOf("vb") >= 0 && HT && HT.daily === -sg && HT.weekly === -sg) return 1;   // bounce call against daily + weekly trend
    if (t.kind === "trap") {
      var o = USTK.map(function (k) { return k.every(function (n, i) { return i === 0 || UV[k[i - 1]][m] > UV[n][m]; }) ? 1 : k.every(function (n, i) { return i === 0 || UV[k[i - 1]][m] < UV[n][m]; }) ? -1 : 0; });
      var fan = o.every(function (x) { return x === 1; }) ? 1 : o.every(function (x) { return x === -1; }) ? -1 : 0;
      var v = UL.map(function (n) { return UV[n][m]; }), sp = Math.max.apply(null, v) - Math.min.apply(null, v);
      return fan === -sg && sp >= 5 ? 3 : 1;
    }
    return sg * (UV[8][m] - UV[304][m]) > 0 ? 3 : 1;
  }

  // ---- options (paper-tested add-ons; see v95_options.js) ----
  function OPTM() { return root.V95OPT || (typeof V95OPT !== "undefined" ? V95OPT : null); }
  var OPTXT = "6 extra setups ride the card's side · against it → flipped · before its first buy, box only · none after the card's 2nd stop · box + Playbook 2× (flips 1×), the rest 1×";
  var OPWHY = { target: "target hit", stop: "stop hit", "broke box": "QQQ closed outside the box", trail: "trail hit", "trail (3 ticks)": "trail: 3 EMA8 ticks against you",
    crossback: "QQQ closed back through red 304", "hit 304": "QQQ reached red 304", time: "time limit" };
  function optExit(t, D, fill) {
    var a = t.aMid, pd = t.paid, C = t.side === "C";
    if (t.how === "flip") return "stop " + q2(t.stopNow != null ? t.stopNow : a - 0.50) + (t.stopNow != null && t.stopNow > a - 0.5 ? " (moved up)" : "") + " · at " + q2(a + 0.30) + " stop → " + q2(a - 0.15) +
      " · at " + q2(a + 0.50) + " trail (sell after 3 EMA8 ticks against)" + (t.armed ? " · ARMED" : "") + " · out by " + clock(Math.min(fill + 90, 146));
    switch (t.src) {
      case "box scalp": return "sell at " + q2(pd + 0.30) + " (+0.30) · out if QQQ closes " + (t.box ? (C ? "below " + q2(t.box[0] - 0.25) : "above " + q2(t.box[1] + 0.25)) : "outside the box") + " · stop " + q2(a - 0.30) + " · out by " + clock(Math.min(fill + 30, 150));
      case "Playbook home run": return "stop " + q2(a - 0.30) + " · after +1.20, trail 0.30 under the high · out by " + clock(Math.floor(t.m) + 60);
      case "Tulip v3.2": return "stop " + q2(a - 0.30) + " · target " + q2(pd + 2.00) + " (+2.00) · out by " + clock(Math.floor(t.m) + 60);
      case "turn entry": return "stop " + q2(a - 0.55) + " · sell when QQQ closes back under red 304 after crossing above it · out by 8:56";
      case "calls checkpoint": return "no stop · sell when QQQ closes back under red 304 after crossing above it · out by 8:56";
      case "sniper": return C && D.ctx.day === 1 ? "sell when QQQ crosses above red 304 · out by 8:56" : "sell at " + q2(pd + 0.75) + " (+0.75) · out by 8:56";
    }
    return "";
  }

  // ---------------- what the card says ----------------
  function model(X) {
    var day = X.day, atM = X.atM, wd = X.wd || new Date(day + "T12:00:00Z").toUTCString().slice(0, 3);
    var vm = { day: day, atM: atM, release: RELEASES[day], early: EARLY[day], lines: [], tickets: [], closed: [], replay: X.replay };
    if (X.log && X.log.days) {
      var past = X.log.days.filter(function (r) { return r.date < day; }).slice(-10), eq = 0, pk = 0, dd = 0;
      past.forEach(function (r) { eq += r.usd; pk = Math.max(pk, eq); dd = Math.max(dd, pk - eq); });
      var combo = past.reduce(function (a, r) { return a + (r.combo_usd == null ? r.usd : r.combo_usd); }, 0);
      if (past.length) vm.meter = { n: past.length, usd: eq, dip: dd, fwd: X.log.totals && X.log.totals.forward, combo: combo,
        comboAll: X.log.totals && X.log.totals.shadow_combo && X.log.totals.shadow_combo.all, comboFwd: X.log.totals && X.log.totals.shadow_combo && X.log.totals.shadow_combo.forward,
        v95: past.reduce(function (a, r) { return a + (r.v95_sized_usd == null ? 0 : r.v95_sized_usd); }, 0), v95All: X.log.totals && X.log.totals.shadow_v95 && X.log.totals.shadow_v95.all,
        v95Fwd: X.log.totals && X.log.totals.shadow_v95 && X.log.totals.shadow_v95.forward,
        optAll: X.log.totals && X.log.totals.shadow_options && X.log.totals.shadow_options.all, optFwd: X.log.totals && X.log.totals.shadow_options && X.log.totals.shadow_options.forward };
    }
    if (HOLIDAYS[day] || wd === "Sat" || wd === "Sun") { vm.stage = "offday"; vm.head = HOLIDAYS[day] || "Weekend: no trading."; return vm; }
    if (Object.keys(X.hist).length < 12) { vm.stage = "loading"; vm.head = "Loading history…"; return vm; }
    var D = V92MAP.buildDay(X.hist, X.today), c = D.ctx;
    D.now = atM;
    vm.D = D;
    var sideDay = c.day > 0 ? "C" : "P", trapLine = c.day > 0 ? c.PDL - 1 : c.PDH + 1;
    var weekSide = c.week > 0 ? "C" : "P", fwSide = c.week > 0 ? "P" : "C";
    var name = function (s) { return s === "C" ? "calls" : "puts"; };
    vm.map = { yday: c.day > 0 ? "green" : "red", week: c.week > 0 ? "up" : "down", trapSide: sideDay, trapLine: trapLine, fwSide: fwSide, va: D.va };
    if (X.vix != null) {
      vm.vix = X.vix;
      if (c.day < 0 && X.vix >= 16.5) vm.vixNote = "VIX " + p2(X.vix) + " after a red day: in the backtest QQQ was up by 9:00 on 9 of 12 such mornings (bounce). Observation, not a rule.";
    }
    var R = null, hasBars = D.st.length > 0 && D.st[0];
    var vbDay = c.day < 0 && X.vix != null && X.vix >= 16.5, UV = hasBars ? userLines(X.hist, D.st, (X.today && X.today.candles) || [], D.info && D.info.t0) : null;
    var opt = { causal: true, trailMode: "three" };
    if (vbDay) { opt.vb = true; opt.vbStart = 60; opt.vbMax = 1; }
    if (UV) { opt.fp = "ma17"; opt.fpVwap = true; opt.fpDay = true; opt.fpYield = true; opt.fpYieldTo = ["trap", "gap", "fw"]; opt.fpFlip = true; opt.fpStart = 30; opt.fpEnd = 120; D.ind = { m17: UV[14], vw: UV.vw }; }
    vm.v95 = hasBars ? !!UV : null; vm.vbDay = vbDay;
    // daily/weekly trend before this morning: the paper log's row for a replayed day, or its "next morning" read for today
    var HT = null, lastH = Object.keys(X.hist).sort().slice(-1)[0];
    if (X.log && X.log.days) { var row = X.log.days.filter(function (r) { return r.date === day; })[0]; if (row && row.htf) HT = row.htf; }
    if (!HT && X.log && X.log.htf_next && X.log.htf_next.through === lastH) HT = X.log.htf_next;
    var vbSmall = vbDay && HT && HT.daily === -1 && HT.weekly === -1;
    if (hasBars) { try { R = V92.runDay(D, opt); } catch (e) { R = null; vm.err = String(e); } }
    var lastBar = D.st.length - 1, close = hasBars ? D.st[lastBar][0] : null;
    var vbTxt = vbDay ? "calls after 7:30 (red yesterday · VIX " + p2(X.vix) + ")" : "off";
    var fpTxt = (sideDay === "C" ? "calls if QQQ pokes below " + p2(c.PDL) : "puts if QQQ pokes above " + p2(c.PDH)) + " and fails (1×)";
    // ---- the map lines (fixed for the day) ----
    vm.lines.push(["Trap", name(sideDay) + (sideDay === "C" ? " below " : " above ") + p2(trapLine) +
      " (yesterday " + (sideDay === "C" ? "low " + p2(c.PDL) : "high " + p2(c.PDH)) + (sideDay === "C" ? " − 1.00)" : " + 1.00)")]);
    if (!hasBars) {
      vm.lines.push(["Bounce", vbTxt + (vbSmall ? " · 1× (daily & weekly both down)" : "")]);
      vm.lines.push(["Failed poke", fpTxt]);
      vm.lines.push(["Options", OPTXT]);
      vm.lines.push(["Gap-and-go", name(weekSide) + " if the open is " + (weekSide === "C" ? "≥ " + p2(c.PDC + 2.5) : "≤ " + p2(c.PDC - 2.5)) + " and the nearest GEX level is 1.00+ away"]);
      vm.lines.push(["Fade the week", name(fwSide) + " after 7:30 only if QQQ is inside yesterday's value area (" + p2(D.va.yval) + "–" + p2(D.va.yvah) + ") at 7:00"]);
      vm.stage = atM < 0 ? "prep" : "waiting"; vm.head = atM < 0 ? (name(sideDay) === name(weekSide) ? "A " + name(sideDay) + " morning." : "Map for today.") : "Waiting for the first bars…";
      return vm;
    }
    var gap = D.st[0][0] - c.PDC, gArmed = R && (R.gapArmed.C || R.gapArmed.P);
    vm.lines.push(["Gap-and-go", "gap " + (gap >= 0 ? "+" : "") + p2(gap) + " → " + (gArmed ? name(R.gapArmed.C ? "C" : "P") + " ARMED (needs the nearest GEX level 1.00+ away at the turn)" : "off")]);
    var trapSeen = R && (R.zoneFirst.C !== null || R.zoneFirst.P !== null);
    var fwTxt;
    if (lastBar < 30) fwTxt = name(fwSide) + " waits for the 7:00 read (value area " + p2(D.va.yval) + "–" + p2(D.va.yvah) + ")";
    else if (gArmed) fwTxt = "off (gap-and-go armed)";
    else if (trapSeen) fwTxt = "off (trap zone reached)";
    else fwTxt = R && R.fwOk ? name(fwSide) + " ON after 7:30 (inside value at 7:00)" : "off (outside yesterday's value at 7:00)";
    vm.lines.push(["Fade the week", fwTxt]);
    vm.lines.push(["Bounce", vbTxt + (vbSmall ? " · 1× (daily & weekly both down)" : "")]);
    vm.lines.push(["Failed poke", UV ? fpTxt : "off (your lines / VWAP missing) · v9.4 signals"]);
    vm.lines.push(["Options", UV && OPTM() ? OPTXT : "off (your lines / VWAP missing)"]);
    // ---- watch line: one steady sentence + live distance ----
    var zf = R ? R.zoneFirst[sideDay] : null;
    var dist = sideDay === "C" ? close - trapLine : trapLine - close;
    vm.watch = zf !== null ? name(sideDay).toUpperCase() + " TRAP ZONE reached at " + clock(zf) + " · buying EMA8 turns " + (sideDay === "C" ? "up" : "down")
      : name(sideDay).toUpperCase() + " TRAP: QQQ " + p2(close) + " · " + p2(Math.max(0, dist)) + " from the trigger";
    // ---- trades ----
    var stops = 0, last = null;
    (R ? R.trades : []).forEach(function (t) {
      if (t.why === "none") return;
      var S = t.side === "C" ? "CALL" : "PUT", kind = { trap: "trap", gap: "gap-and-go", fw: "fade the week", flip: "flip", vb: "VIX bounce call", fp: "failed poke (B)" }[t.kind] || ("re-entry (" + t.kind.slice(3) + ")");
      var ct = sizeFor(UV, t, HT);
      var base = { S: S, K: t.K, kind: kind + (ct === 3 ? " · your lines agree" : ""), ct: ct, mf: t.mf, ask: t.ask, stop: t.aMid - 0.50, move: t.aMid + 0.30, moveTo: t.aMid - 0.15, arm: t.aMid + 0.50,
        timeOut: Math.min(t.mf + 90, 146), fake: t.fakeout, iv: t.iv, sg: t.side === "C" ? 1 : -1, aMid: t.aMid,
        q0: D.st[t.mf] ? D.st[t.mf][0] : (D.st[t.m] ? D.st[t.m][0] : null), dl: Math.abs(t.delta || 0), qNow: close };
      if (t.why === "stop" && t.kind !== "fp") stops++;
      if (t.why === "open" || t.why === "sell_now") {
        var Kq = D.opt[t.side][String(t.K)] || {}, ms = Object.keys(Kq).map(Number).filter(function (m) { return m <= atM; }), lq = ms.length ? Kq[Math.max.apply(null, ms)] : null;
        base.bid = lq ? lq[2] : null; base.pnl = lq ? (lq[2] - t.ask) * 100 * ct : null;
        base.curStop = t.stop; base.trail = t.hw >= 0.50; base.moved = t.ev && t.ev.some(function (e) { return e[1] === "move_stop"; });
        base.state = t.why === "sell_now" ? "sell" : (atM - t.mf <= 5 ? "buy" : "in");
        base.qchk = qqqCheck(D, t, atM);
        base.why = t.why === "sell_now" ? "trail: 3 EMA8 ticks against you" : null;
        vm.tickets.push(base);
      } else {
        base.out = t.out; base.px = t.px; base.res = t.res * 100 * ct; base.why = { stop: "stop", q8: "trail", time: "time exit", yield: "gave way" }[t.why] || t.why;
        if (atM - t.out <= 3) { base.state = "sell"; base.why = base.why === "stop" ? "stop hit" : base.why === "time exit" ? "time exit" : base.why === "gave way" ? "B gives way: a v9.2 setup fired the other way" : "trail: 3 EMA8 ticks against you"; vm.tickets.push(base); }
        else vm.closed.push(base);
        if (t.why === "stop" && t.kind !== "fp") last = { t: t, base: base };
      }
    });
    // re-entry watch right after a stop
    if (last && stops < 2 && last.t.kind.indexOf("re-") !== 0 && last.t.kind !== "flip" && atM <= last.t.out + 45 && (last.t.brk === null || last.t.brk > atM) &&
      !vm.tickets.some(function (k) { return k.state !== "sell"; })) {
      vm.reentry = "Re-entry watch until " + clock(last.t.out + 45) + " · same side on the next EMA8 turn · cancelled if QQQ breaks " + p2(last.t.fakeout);
    }
    vm.stops = stops; vm.dayPnl = vm.closed.reduce(function (s, b) { return s + b.res; }, 0);
    // ---- OPTIONS: 6 extra setups in the card's direction (agree = as fired · against = flipped to the card's side · box before the card's first buy) ----
    vm.optClosed = [];
    if (OPTM() && UV && R) {
      try {
        var cardT = R.trades.filter(function (t) { return t.why !== "none"; }).map(function (t) { return { side: t.side, m: t.m, mf: t.mf, out: t.out, why: t.why, kind: t.kind }; });
        var OP = OPTM().run(D, { candles: X.today.candles || [], points: X.today.points || [], frames: X.today.frames || [] }, X.hist, UV, D.info && D.info.t0, cardT, day);
        OP.trades.forEach(function (t) {
          var S = t.side === "C" ? "CALL" : "PUT", fill = t.fill != null ? t.fill : Math.floor(t.m) + 1;
          var ct = t.ct || 1, how = t.how === "agree" ? "with the card" : t.how === "flip" ? "flipped to the card's side (" + (t.flipped_from === "C" ? "call" : "put") + " signal)" : "box (before the card)";
          var k = { opt: true, S: S, K: t.K, ct: ct, mf: fill, ask: t.paid, kind: "OPTION · " + t.src + (ct > 1 ? " " + ct + "×" : "") + " · " + how,
            exitTxt: (ct > 1 || t.src === "Tulip v3.2" ? t.src + (ct > 1 ? " (" + ct + "×)" : "") + ": " : "") + optExit(t, D, fill), sg: t.side === "C" ? 1 : -1, box: t.box, level: t.level, levelPx: t.levelPx };
          if (t.why === "open") { k.bid = t.bid; k.pnl = t.usd * ct; k.state = atM - fill <= 5 ? "buy" : "in"; }
          else { k.out = t.out; k.res = t.usd * ct; k.px = t.sold; k.why = OPWHY[t.why] || t.why; k.state = atM - t.out <= 3 ? "sell" : "closed"; }
          // same touch, same contract, same state (Tulip + Playbook) -> ONE ticket
          var twin = (k.state === "closed" ? vm.optClosed : vm.tickets).filter(function (o) { return o.opt && o.S === k.S && String(o.K) === String(k.K) && o.mf === k.mf && o.state === k.state && (k.state === "buy" || k.state === "in" || (o.out === k.out && o.why === k.why)); })[0];
          if (twin) { twin.ct += k.ct; twin.kind += " + " + k.kind.replace("OPTION · ", ""); twin.exitTxt += " | " + k.exitTxt; if (k.pnl != null) twin.pnl = (twin.pnl || 0) + k.pnl; if (k.res != null) twin.res = (twin.res || 0) + k.res; return; }
          if (k.state === "closed") vm.optClosed.push(k); else vm.tickets.push(k);
        });
      } catch (e) { vm.optErr = String(e); }
    }
    vm.optPnl = vm.optClosed.reduce(function (s, b) { return s + b.res; }, 0);
    // ---- headline (changes only on real events) ----
    var sell = vm.tickets.filter(function (k) { return k.state === "sell"; }), buy = vm.tickets.filter(function (k) { return k.state === "buy"; }), inT = vm.tickets.filter(function (k) { return k.state === "in"; });
    if (sell.length) { vm.stage = "sell"; vm.head = "SELL NOW · " + sell[0].S + " " + sell[0].K; }
    else if (buy.length) { vm.stage = "buy"; vm.head = "BUY " + (buy[0].ct || 1) + "× " + buy[0].K + " " + buy[0].S + " @ " + q2(buy[0].ask); }
    else if (inT.length) { vm.stage = "in"; vm.head = "In trade · " + inT.map(function (k) { return k.S + " " + k.K + " " + usd(k.pnl); }).join(" · "); }
    else if (stops >= 2) { vm.stage = "done"; vm.head = "DONE for today: 2 stops."; }
    else if (atM >= 150) { vm.stage = "recap"; vm.head = "Morning over · " + vm.closed.length + " trade" + (vm.closed.length === 1 ? "" : "s") + " · " + usd(vm.dayPnl); }
    else if (vm.reentry) { vm.stage = "reentry"; vm.head = "Stopped. Watching for the re-entry."; }
    else { vm.stage = "watch"; vm.head = zf !== null ? name(sideDay).toUpperCase() + " trap zone: wait for the turn." : (gArmed ? "Gap-and-go " + name(R.gapArmed.C ? "C" : "P") + " armed: wait for the turn." : "Watching. No setup yet."); }
    if (X.stale != null && X.stale > 2) { vm.stale = Math.round(X.stale); }
    vm.gexRegime = X.gexRegime;
    return vm;
  }

  // ---- QQQ check: how the underlying is moving since the buy vs the backtest's usual paths (observation only) ----
  var WIN = [[0, 0], [1, 0.05], [2, 0.02], [3, 0.19], [5, 0.45], [10, 0.51], [15, 0.86], [20, 1.12], [30, 1.54], [45, 1.98], [60, 2.07]];
  var LOSE = [[0, 0], [1, -0.12], [2, -0.22], [3, -0.31], [5, -0.48], [10, -0.66], [15, -0.72]];
  function interp(tab, x) {
    if (x >= tab[tab.length - 1][0]) return tab[tab.length - 1][1];
    for (var i = 1; i < tab.length; i++) if (x <= tab[i][0]) { var a = tab[i - 1], b = tab[i]; return a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0]); }
    return 0;
  }
  function qqqCheck(D, t, atM) {
    var st = D.st, sg = t.side === "C" ? 1 : -1, s0 = st[t.mf];
    if (!s0) return null;
    var q0 = s0[0], now = Math.min(atM, st.length - 1), fav = 0, adv = 0, fa = null, ad = null, a1 = null;
    while (now > t.mf && !st[now]) now--;
    for (var j = t.mf + 1; j <= now; j++) {
      var s = st[j]; if (!s) continue;
      fav = Math.max(fav, sg * ((sg > 0 ? s[1] : s[2]) - q0)); adv = Math.min(adv, sg * ((sg > 0 ? s[2] : s[1]) - q0));
      if (fa === null && fav >= 0.5) fa = j - t.mf; if (ad === null && adv <= -0.5) ad = j - t.mf; if (a1 === null && adv <= -1.0) a1 = j - t.mf;
    }
    var mins = Math.max(0, now - t.mf), mv = st[now] ? sg * (st[now][0] - q0) : 0;
    var shape = a1 !== null && (fa === null || a1 < fa) ? "wrong shape so far (−1.00 before +0.50)" :
      fa !== null && (ad === null || fa < ad) ? (fa <= 10 ? "straight go" : "slow start") :
      fa !== null && ad !== null ? "dip then go" : (ad !== null ? "dipping (−0.50 seen)" : "still forming");
    var txt = "QQQ " + (mv >= 0 ? "+" : "") + p2(mv) + " since the buy (" + mins + " min) · winners were usually " + (interp(WIN, mins) >= 0 ? "+" : "") + p2(interp(WIN, mins)) + " by now";
    if (mins <= 15) txt += ", stopped trades " + p2(interp(LOSE, mins));
    return { txt: txt, shape: shape, ref: "normal win ≈ QQQ +2 · stop ≈ QQQ −1 · observation, not a rule" };
  }

  // ---------------- drawing ----------------
  var CSS = ".v92{font-family:ui-monospace,Menlo,monospace;color:#f0e9ff;background:#0c0710;border:1px solid #2a1a4a;border-radius:12px;padding:14px;display:flex;flex-direction:column;gap:10px}" +
    ".v92 .top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.v92 .lab{color:#b0408f;font-size:11px;letter-spacing:2px}.v92 .sp{flex:1}" +
    ".v92 .pill{font-size:11px;font-weight:700;padding:3px 10px;border-radius:999px;background:rgba(154,122,154,.18);color:#c8a0d0}" +
    ".v92 .pill.buy{background:rgba(47,191,109,.18);color:#2fbf6d}.v92 .pill.sell{background:rgba(236,72,72,.22);color:#ff6b6b}.v92 .pill.in{background:rgba(96,165,250,.18);color:#60a5fa}" +
    ".v92 .head{font-size:18px;font-weight:700;line-height:1.3}.v92 .sub{color:#a99fd6;font-size:12px}" +
    ".v92 .lines{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;font-size:12px}.v92 .lines b{color:#c8a0d0;font-weight:600;white-space:nowrap}" +
    ".v92 .watch{border:1px dashed #3a2a5a;border-radius:8px;padding:8px 10px;font-size:13px}" +
    ".v92 .tk{border-radius:10px;padding:10px 12px;border:1px solid #2a1a4a;background:#140b22;display:flex;flex-direction:column;gap:6px}" +
    ".v92 .tk.buy{border-color:#2fbf6d}.v92 .tk.sell{border-color:#ff6b6b;background:rgba(236,72,72,.10)}.v92 .tk.in{border-color:#60a5fa}" +
    ".v92 .tk .big{font-size:20px;font-weight:800}.v92 .tk.sell .big{color:#ff6b6b}.v92 .tk.buy .big{color:#2fbf6d}" +
    ".v92 .row{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:#d7cfff}.v92 .row span{white-space:nowrap}.v92 .ctx{color:#8a7fb8;font-size:11px}" +
    ".v92 .lad{display:flex;flex-direction:column;gap:3px;font-size:13px}.v92 .rung{display:grid;grid-template-columns:44px 22px 1fr auto;align-items:center;gap:4px}" +
    ".v92 .rung b{font-weight:700}.v92 .rung i{font-style:normal;color:#6a5a8a}.v92 .rung em{font-style:normal;color:#8a7fb8;font-size:11px}" +
    ".v92 .rung.here{color:#ffd166;font-weight:700}.v92 .rung.here i{color:#ffd166}.v92 .rung.stop span{color:#ff6b6b}.v92 .rung.dim{color:#8a7fb8}.v92 .rung.hit span{color:#2fbf6d}" +
    ".v92 .closed{font-size:12px;color:#a99fd6}.v92 .up{color:#2fbf6d}.v92 .dn{color:#ff6b6b}" +
    ".v92 .oxt{font-size:12px;color:#d7cfff;line-height:1.4}.v92.stale{filter:grayscale(1);opacity:.7}.v92 .warn{color:#e0ab45;font-size:12px}.v92 .qchk{color:#a99fd6}";
  function ivTag(iv) { if (iv == null) return null; return "IV " + iv.toFixed(2) + (iv < 0.20 ? " (low)" : iv > 0.23 ? " (high)" : " (mid)"); }
  function optTicketHtml(k, vm) {
    if (k.state === "sell") return '<div class="tk sell"><div class="big">SELL NOW · ' + k.S + " " + k.K + '</div><div class="ctx">' + esc(k.kind) + '</div><div class="row"><span>' + esc(k.why) + "</span>" +
      (k.px != null ? "<span>" + q2(k.px) + " → " + usd(k.res) + "</span>" : "") + "<span>sell by hand at the bid</span></div></div>";
    var head = k.state === "buy" ? "BUY " + k.ct + "× " + k.K + " " + k.S + " @ " + q2(k.ask) : (k.ct > 1 ? k.ct + "× " : "") + k.S + " " + k.K + " · " + usd(k.pnl);
    return '<div class="tk ' + k.state + '"><div class="big">' + esc(head) + '</div><div class="ctx">' + esc(k.kind) + "</div>" +
      '<div class="oxt">' + esc(k.exitTxt) + "</div>" + '<div class="row">' + (k.bid != null ? "<span>bid " + q2(k.bid) + "</span>" : "") + "<span>signal " + clock(k.mf) + " (" + Math.max(0, vm.atM - k.mf) + " min ago)</span></div></div>";
  }
  function ticketHtml(k, vm) {
    if (k.opt) return optTicketHtml(k, vm);
    var ctx = [ivTag(k.iv), vm.gexRegime ? (/NEG/.test(vm.gexRegime) ? "GEX negative (moves accelerate)" : "GEX positive (pinning)") : null, vm.release ? vm.release + " morning (weaker in the backtest)" : null]
      .filter(Boolean).join(" · ");
    if (k.state === "sell") {
      return '<div class="tk sell"><div class="big">SELL NOW · ' + k.S + " " + k.K + "</div>" +
        '<div class="row"><span>' + esc(k.why) + "</span>" + (k.bid != null ? "<span>bid " + q2(k.bid) + " · " + usd(k.pnl) + "</span>" : "") +
        (k.px != null ? "<span>replay fill " + q2(k.px) + " → " + usd(k.res) + "</span>" : "") + "<span>sell by hand at the bid</span></div></div>";
    }
    var head = k.state === "buy" ? "BUY " + (k.ct || 1) + "× " + k.K + " " + k.S + " @ " + q2(k.ask) : (k.ct > 1 ? k.ct + "× " : "") + k.S + " " + k.K + " · " + usd(k.pnl);
    return '<div class="tk ' + k.state + '"><div class="big">' + esc(head) + '</div><div class="ctx">' + esc(k.kind) + "</div>" + ladderHtml(k, vm) + "</div>";
    return '<div class="tk ' + k.state + '"><div class="big">' + esc(head) + "</div>" +
      '<div class="row"><span>' + esc(k.kind) + "</span><span>signal " + clock(k.mf) + " (" + Math.max(0, vm.atM - k.mf) + " min ago)</span>" +
      (k.state === "in" ? "<span>bid " + q2(k.bid) + "</span>" : "") + "</div>" +
      '<div class="row"><span>stop ' + q2(k.moved ? k.moveTo : k.stop) + (k.moved ? " (moved up)" : "") + "</span><span>at " + q2(k.move) + " → stop " + q2(k.moveTo) + "</span><span>at " + q2(k.arm) + " → trail (sell after 3 EMA8 ticks against)" + (k.trail ? " · ARMED" : "") + "</span><span>out by " + clock(k.timeOut) + "</span></div>" +
      '<div class="row"><span>fake-out line: QQQ ' + q2(k.fake) + "</span><span>in trade " + Math.max(0, vm.atM - k.mf) + " min · typical 15–40</span></div>" +
      (k.qchk ? '<div class="row qchk"><span>' + esc(k.qchk.txt) + "</span><span>shape: " + esc(k.qchk.shape) + '</span></div><div class="ctx">' + esc(k.qchk.ref) + "</div>" : "") +
      (ctx ? '<div class="ctx">' + esc(ctx) + "</div>" : "") + "</div>";
  }
  // ---- the exit ladder: every level top to bottom, the dot follows the live bid ----
  function ladderHtml(k, vm) {
    var ct = k.ct || 1, stopPx = k.moved ? k.moveTo : k.stop;
    var qAt = function (px) { return k.q0 != null && k.dl > 0.05 ? k.q0 + k.sg * (px - k.aMid) / k.dl : null; };
    var R = [
      { p: k.arm, t: k.trail ? "TRAIL ON · sell after 3 EMA8 ticks against" : "trail on (run it)", c: k.trail ? "hit" : "" },
      { p: k.move, t: k.moved ? "stop moved up ✓" : "stop moves to " + q2(k.moveTo), c: k.moved ? "hit" : "" },
      { p: k.ask, t: "bought", c: "dim", line: "┄┄" },
      { p: stopPx, t: "SELL (stop" + (k.moved ? ", moved up" : "") + ")  " + usd((stopPx - k.ask) * 100 * ct), c: "stop" }];
    var now = k.bid != null ? k.bid : null;
    if (now != null) R.push({ p: now, t: "YOU ARE HERE  " + usd(k.pnl != null ? k.pnl : (now - k.ask) * 100 * ct), c: "here", line: "●", q: k.qNow });
    R.sort(function (a, b) { return b.p - a.p || (a.c === "here" ? -1 : 1); });
    var h = '<div class="lad">';
    R.forEach(function (r) {
      var q = r.q != null ? r.q : qAt(r.p);
      h += '<div class="rung ' + r.c + '"><b>' + q2(r.p) + "</b><i>" + (r.line || "──") + "</i><span>" + esc(r.t) + "</span><em>" + (q != null && isFinite(q) ? "QQQ " + q2(q) : "") + "</em></div>";
    });
    var mins = Math.max(0, vm.atM - k.mf);
    return h + '</div><div class="ctx">Time limit ' + clock(k.timeOut) + " · min " + mins + (k.qchk ? " · " + esc(k.qchk.shape) : "") + " · no more " + (k.sg > 0 ? "calls" : "puts") + " if QQQ " + (k.sg > 0 ? "< " : "> ") + q2(k.fake) + "</div>";
  }
  function render(el, vm) { el.innerHTML = build(vm); }
  function build(vm) {
    var pill = { buy: ["BUY", "buy"], sell: ["SELL NOW", "sell"], "in": ["IN TRADE", "in"], done: ["DONE", ""], recap: ["RECAP", ""], reentry: ["RE-ENTRY WATCH", ""],
      watch: ["WATCH", ""], prep: ["PREP", ""], waiting: ["OPEN", ""], offday: ["SIT OUT", ""], loading: ["…", ""] }[vm.stage] || ["", ""];
    var h = '<style>' + CSS + '</style><div class="v92' + (vm.stale ? " stale" : "") + '">' +
      '<div class="top"><span class="lab">3V3 v9.5 · GAME PLAN</span><span class="sp"></span>' + (vm.release ? '<span class="pill">' + esc(vm.release) + "</span>" : "") +
      '<span class="pill ' + pill[1] + '">' + pill[0] + "</span></div>" +
      '<div class="head">' + esc(vm.head) + "</div>";
    if (vm.stale) h += '<div class="warn">Data is ' + vm.stale + " min old: don't act until it refreshes.</div>";
    if (vm.early) h += '<div class="sub">' + esc(vm.early) + " (the 6:30–9:00 window is unaffected)</div>";
    if (vm.map) h += '<div class="sub">Yesterday ' + vm.map.yday + " · last week " + vm.map.week + " · value area " + p2(vm.map.va.yval) + "–" + p2(vm.map.va.yvah) + (vm.vix != null ? " · VIX yesterday " + p2(vm.vix) : "") + "</div>";
    if (vm.lines.length) h += '<div class="lines">' + vm.lines.map(function (l) { return "<b>" + esc(l[0]) + "</b><span>" + esc(l[1]) + "</span>"; }).join("") + "</div>";
    if (vm.watch && vm.stage !== "recap") h += '<div class="watch">' + esc(vm.watch) + "</div>";
    vm.tickets.slice().sort(function (a, b) { return b.mf - a.mf; }).forEach(function (k) { h += ticketHtml(k, vm); });
    if (vm.reentry) h += '<div class="watch">' + esc(vm.reentry) + "</div>";
    if (vm.closed.length) h += '<div class="closed">Today: ' + vm.closed.map(function (b) { return (b.ct > 1 ? b.ct + "× " : "") + b.S + " " + b.K + " " + clock(b.mf) + "–" + clock(b.out) + " " + b.why + ' <span class="' + (b.res >= 0 ? "up" : "dn") + '">' + usd(b.res) + "</span>"; }).join(" · ") +
      " · total " + usd(vm.dayPnl) + (vm.stops ? " · stops " + vm.stops + "/2" : "") + "</div>";
    if (vm.optClosed && vm.optClosed.length) h += '<div class="closed">Options today: ' + vm.optClosed.map(function (b) { return (b.ct > 1 ? b.ct + "× " : "") + b.S + " " + b.K + " " + clock(b.mf) + "–" + clock(b.out) + " " + b.why + ' <span class="' + (b.res >= 0 ? "up" : "dn") + '">' + usd(b.res) + "</span>"; }).join(" · ") +
      " · total " + usd(vm.optPnl) + "</div>";
    if (vm.optErr) h += '<div class="warn">Options error: ' + esc(vm.optErr) + "</div>";
    if (vm.meter) h += (vm.meter.v95All ? '<div class="closed">Last ' + vm.meter.n + ' mornings (v9.5, paper log): <span class="' + (vm.meter.v95 >= 0 ? "up" : "dn") + '">' + usd(vm.meter.v95) + '</span> · all ' + usd(vm.meter.v95All.usd_sized) + ' (1 contract ' + usd(vm.meter.v95All.usd_1ct) + ')' +
        (vm.meter.v95Fwd && vm.meter.v95Fwd.mornings ? ' · since Nov 2: ' + usd(vm.meter.v95Fwd.usd_sized) : '') + '</div>' : '') +
      (vm.meter.optAll ? '<div class="closed">Options (paper log): ' + vm.meter.optAll.addons + ' extra trades ' + usd(vm.meter.optAll.addons_usd) + ' · card + options ' + usd(vm.meter.optAll.total_usd) +
        (vm.meter.optFwd && vm.meter.optFwd.addons ? ' · forward: ' + usd(vm.meter.optFwd.addons_usd) : '') + '</div>' : '');
    h += '<div class="ctx">' + (vm.v95 === false ? "v9.4 rules (v9.5 extras off: your lines/VWAP missing)" : "v9.5 rules: v9.2 setups + VIX bounce + 3-tick trail + failed poke (B) · 3× when your lines agree" + (OPTM() ? " · + options" : "")) + ' · signals only, you place every trade · ' + (vm.replay ? "REPLAY of " + vm.day + " at " + clock(vm.atM) : (vm.stage !== "offday" && vm.atM >= 0 && vm.atM < 150 ? "live · " + clock(vm.atM) + " PT" : "")) + "</div></div>";
    return h;
  }
  // For the dashboard: one refresh -> {html, vm}
  function html(o) {
    o = o || {}; o.base = o.base || "./";
    return (o.day ? loadReplay(o) : loadLive(o)).then(function (X) { var vm = model(X); return { html: build(vm), vm: vm }; });
  }
  function mount(el, o) {
    o = o || {}; o.base = o.base || "./";
    var go = function () {
      return (o.day ? loadReplay(o) : loadLive(o)).then(function (X) { var vm = model(X); render(el, vm); return vm; })
        .catch(function (e) { el.innerHTML = '<div class="v92" style="font-family:monospace;color:#f0e9ff">v9.2 card error: ' + esc(e && e.message || e) + "</div>"; });
    };
    var p = go();
    if (!o.day) { var t = setInterval(go, 20000); el._v92stop = function () { clearInterval(t); }; }
    return p;
  }
  var api = { mount: mount, model: model, loadReplay: loadReplay, loadLive: loadLive, html: html, build: build };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.V92CARD = api;
})(this);
