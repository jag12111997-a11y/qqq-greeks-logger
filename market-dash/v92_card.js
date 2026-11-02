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
          quotes: (r[2] || []).filter(function (q) { return q.t <= cut + 10; }) } };
    }).then(function (X) { return withVix(o.base, X); });
  }
  var QKEY = function (d) { return "v92q-" + d; };
  function loadLive(o) {
    var now = Date.now(), nd = nyDate(now), day = nd.date, t0 = V92MAP.open930(day), atM = Math.floor((now / 1000 - t0) / 60);
    return Promise.all([history(o.base, day), live(o.base, "auction_live.json"), live(o.base, "gex_intraday.json"), live(o.base, "options_latest.json"),
      live(o.base, "gex_live.json"), atM >= 0 ? csvQuotes(o.base, day) : Promise.resolve([]), getJSON(o.base + "v92_log/index.json?t=" + Math.floor(Date.now() / 600000))]).then(function (r) {
      var au = r[1], gi = r[2], ol = r[3], gl = r[4];
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
        today: { date: day, complete: false, candles: fresh ? (au.candles || []) : [], points: (gi && gi.session_date === day) ? (gi.points || []) : [],
          quotes: Object.keys(Q).map(function (k) { return Q[k]; }) } };
    }).then(function (X) { return withVix(o.base, X); });
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
        comboAll: X.log.totals && X.log.totals.shadow_combo && X.log.totals.shadow_combo.all, comboFwd: X.log.totals && X.log.totals.shadow_combo && X.log.totals.shadow_combo.forward };
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
    if (hasBars) { try { R = V92.runDay(D, { causal: true }); } catch (e) { R = null; vm.err = String(e); } }
    var lastBar = D.st.length - 1, close = hasBars ? D.st[lastBar][0] : null;
    // ---- the map lines (fixed for the day) ----
    vm.lines.push(["Trap", name(sideDay) + (sideDay === "C" ? " below " : " above ") + p2(trapLine) +
      " (yesterday " + (sideDay === "C" ? "low " + p2(c.PDL) : "high " + p2(c.PDH)) + (sideDay === "C" ? " − 1.00)" : " + 1.00)")]);
    if (!hasBars) {
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
    // ---- watch line: one steady sentence + live distance ----
    var zf = R ? R.zoneFirst[sideDay] : null;
    var dist = sideDay === "C" ? close - trapLine : trapLine - close;
    vm.watch = zf !== null ? name(sideDay).toUpperCase() + " TRAP ZONE reached at " + clock(zf) + " · buying EMA8 turns " + (sideDay === "C" ? "up" : "down")
      : name(sideDay).toUpperCase() + " TRAP: QQQ " + p2(close) + " · " + p2(Math.max(0, dist)) + " from the trigger";
    // ---- trades ----
    var stops = 0, last = null;
    (R ? R.trades : []).forEach(function (t) {
      if (t.why === "none") return;
      var S = t.side === "C" ? "CALL" : "PUT", kind = { trap: "trap", gap: "gap-and-go", fw: "fade the week", flip: "flip" }[t.kind] || ("re-entry (" + t.kind.slice(3) + ")");
      var base = { S: S, K: t.K, kind: kind, mf: t.mf, ask: t.ask, stop: t.aMid - 0.50, move: t.aMid + 0.30, moveTo: t.aMid - 0.15, arm: t.aMid + 0.50,
        timeOut: Math.min(t.mf + 90, 146), fake: t.fakeout, iv: t.iv };
      if (t.why === "stop") stops++;
      if (t.why === "open" || t.why === "sell_now") {
        var Kq = D.opt[t.side][String(t.K)] || {}, ms = Object.keys(Kq).map(Number).filter(function (m) { return m <= atM; }), lq = ms.length ? Kq[Math.max.apply(null, ms)] : null;
        base.bid = lq ? lq[2] : null; base.pnl = lq ? (lq[2] - t.ask) * 100 : null;
        base.curStop = t.stop; base.trail = t.hw >= 0.50; base.moved = t.ev && t.ev.some(function (e) { return e[1] === "move_stop"; });
        base.state = t.why === "sell_now" ? "sell" : (atM - t.mf <= 5 ? "buy" : "in");
        base.qchk = qqqCheck(D, t, atM);
        base.why = t.why === "sell_now" ? "trail: EMA8 turned against you" : null;
        vm.tickets.push(base);
      } else {
        base.out = t.out; base.px = t.px; base.res = t.res * 100; base.why = { stop: "stop", q8: "trail", time: "time exit" }[t.why] || t.why;
        if (atM - t.out <= 3) { base.state = "sell"; base.why = base.why === "stop" ? "stop hit" : base.why === "time exit" ? "time exit" : "trail: EMA8 turned against you"; vm.tickets.push(base); }
        else vm.closed.push(base);
        if (t.why === "stop") last = { t: t, base: base };
      }
    });
    // re-entry watch right after a stop
    if (last && stops < 2 && last.t.kind.indexOf("re-") !== 0 && last.t.kind !== "flip" && atM <= last.t.out + 45 && (last.t.brk === null || last.t.brk > atM) &&
      !vm.tickets.some(function (k) { return k.state !== "sell"; })) {
      vm.reentry = "Re-entry watch until " + clock(last.t.out + 45) + " · same side on the next EMA8 turn · cancelled if QQQ breaks " + p2(last.t.fakeout);
    }
    vm.stops = stops; vm.dayPnl = vm.closed.reduce(function (s, b) { return s + b.res; }, 0);
    // ---- headline (changes only on real events) ----
    var sell = vm.tickets.filter(function (k) { return k.state === "sell"; }), buy = vm.tickets.filter(function (k) { return k.state === "buy"; }), inT = vm.tickets.filter(function (k) { return k.state === "in"; });
    if (sell.length) { vm.stage = "sell"; vm.head = "SELL NOW · " + sell[0].S + " " + sell[0].K; }
    else if (buy.length) { vm.stage = "buy"; vm.head = "BUY 1× " + buy[0].K + " " + buy[0].S + " @ " + q2(buy[0].ask); }
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
    ".v92 .closed{font-size:12px;color:#a99fd6}.v92 .up{color:#2fbf6d}.v92 .dn{color:#ff6b6b}" +
    ".v92.stale{filter:grayscale(1);opacity:.7}.v92 .warn{color:#e0ab45;font-size:12px}.v92 .qchk{color:#a99fd6}";
  function ivTag(iv) { if (iv == null) return null; return "IV " + iv.toFixed(2) + (iv < 0.20 ? " (low)" : iv > 0.23 ? " (high)" : " (mid)"); }
  function ticketHtml(k, vm) {
    var ctx = [ivTag(k.iv), vm.gexRegime ? (/NEG/.test(vm.gexRegime) ? "GEX negative (moves accelerate)" : "GEX positive (pinning)") : null, vm.release ? vm.release + " morning (weaker in the backtest)" : null]
      .filter(Boolean).join(" · ");
    if (k.state === "sell") {
      return '<div class="tk sell"><div class="big">SELL NOW · ' + k.S + " " + k.K + "</div>" +
        '<div class="row"><span>' + esc(k.why) + "</span>" + (k.bid != null ? "<span>bid " + q2(k.bid) + " · " + usd(k.pnl) + "</span>" : "") +
        (k.px != null ? "<span>replay fill " + q2(k.px) + " → " + usd(k.res) + "</span>" : "") + "<span>sell by hand at the bid</span></div></div>";
    }
    var head = k.state === "buy" ? "BUY 1× " + k.K + " " + k.S + " @ " + q2(k.ask) : k.S + " " + k.K + " · " + usd(k.pnl);
    return '<div class="tk ' + k.state + '"><div class="big">' + esc(head) + "</div>" +
      '<div class="row"><span>' + esc(k.kind) + "</span><span>signal " + clock(k.mf) + " (" + Math.max(0, vm.atM - k.mf) + " min ago)</span>" +
      (k.state === "in" ? "<span>bid " + q2(k.bid) + "</span>" : "") + "</div>" +
      '<div class="row"><span>stop ' + q2(k.moved ? k.moveTo : k.stop) + (k.moved ? " (moved up)" : "") + "</span><span>at " + q2(k.move) + " → stop " + q2(k.moveTo) + "</span><span>at " + q2(k.arm) + " → trail on EMA8" + (k.trail ? " · ARMED" : "") + "</span><span>out by " + clock(k.timeOut) + "</span></div>" +
      '<div class="row"><span>fake-out line: QQQ ' + q2(k.fake) + "</span><span>in trade " + Math.max(0, vm.atM - k.mf) + " min · typical 15–40</span></div>" +
      (k.qchk ? '<div class="row qchk"><span>' + esc(k.qchk.txt) + "</span><span>shape: " + esc(k.qchk.shape) + '</span></div><div class="ctx">' + esc(k.qchk.ref) + "</div>" : "") +
      (ctx ? '<div class="ctx">' + esc(ctx) + "</div>" : "") + "</div>";
  }
  function render(el, vm) { el.innerHTML = build(vm); }
  function build(vm) {
    var pill = { buy: ["BUY", "buy"], sell: ["SELL NOW", "sell"], "in": ["IN TRADE", "in"], done: ["DONE", ""], recap: ["RECAP", ""], reentry: ["RE-ENTRY WATCH", ""],
      watch: ["WATCH", ""], prep: ["PREP", ""], waiting: ["OPEN", ""], offday: ["SIT OUT", ""], loading: ["…", ""] }[vm.stage] || ["", ""];
    var h = '<style>' + CSS + '</style><div class="v92' + (vm.stale ? " stale" : "") + '">' +
      '<div class="top"><span class="lab">3V3 v9.2 · GAME PLAN</span><span class="sp"></span>' + (vm.release ? '<span class="pill">' + esc(vm.release) + "</span>" : "") +
      '<span class="pill ' + pill[1] + '">' + pill[0] + "</span></div>" +
      '<div class="head">' + esc(vm.head) + "</div>";
    if (vm.stale) h += '<div class="warn">Data is ' + vm.stale + " min old: don't act until it refreshes.</div>";
    if (vm.early) h += '<div class="sub">' + esc(vm.early) + " (the 6:30–9:00 window is unaffected)</div>";
    if (vm.map) h += '<div class="sub">Yesterday ' + vm.map.yday + " · last week " + vm.map.week + " · value area " + p2(vm.map.va.yval) + "–" + p2(vm.map.va.yvah) + (vm.vix != null ? " · VIX yesterday " + p2(vm.vix) : "") + "</div>";
    if (vm.vixNote) h += '<div class="sub">' + esc(vm.vixNote) + "</div>";
    if (vm.lines.length) h += '<div class="lines">' + vm.lines.map(function (l) { return "<b>" + esc(l[0]) + "</b><span>" + esc(l[1]) + "</span>"; }).join("") + "</div>";
    if (vm.watch && vm.stage !== "recap") h += '<div class="watch">' + esc(vm.watch) + "</div>";
    vm.tickets.slice().sort(function (a, b) { return b.mf - a.mf; }).forEach(function (k) { h += ticketHtml(k, vm); });
    if (vm.reentry) h += '<div class="watch">' + esc(vm.reentry) + "</div>";
    if (vm.closed.length) h += '<div class="closed">Today: ' + vm.closed.map(function (b) { return b.S + " " + b.K + " " + clock(b.mf) + "–" + clock(b.out) + " " + b.why + ' <span class="' + (b.res >= 0 ? "up" : "dn") + '">' + usd(b.res) + "</span>"; }).join(" · ") +
      " · total " + usd(vm.dayPnl) + (vm.stops ? " · stops " + vm.stops + "/2" : "") + "</div>";
    if (vm.meter) h += '<div class="closed">Last ' + vm.meter.n + ' mornings (paper log): <span class="' + (vm.meter.usd >= 0 ? "up" : "dn") + '">' + usd(vm.meter.usd) + '</span> · worst dip ' + usd(-vm.meter.dip).replace("−", "") + ' of your $150 limit' +
      (vm.meter.fwd && vm.meter.fwd.mornings ? ' · forward test since Nov 2: ' + usd(vm.meter.fwd.usd) + ' on ' + vm.meter.fwd.mornings + ' mornings' : '') + '</div>' +
      (vm.meter.comboAll ? '<div class="closed">Combo shadow (3-tick trail + VIX bounce, paper only): last ' + vm.meter.n + ' <span class="' + (vm.meter.combo >= 0 ? "up" : "dn") + '">' + usd(vm.meter.combo) + '</span> · all ' + usd(vm.meter.comboAll.usd) +
        (vm.meter.comboFwd && vm.meter.comboFwd.mornings ? ' · since Nov 2: ' + usd(vm.meter.comboFwd.usd) : '') + ' · signals above stay v9.2</div>' : '');
    h += '<div class="ctx">v9.2 rules only · signals only, you place every trade · 1 contract · ' + (vm.replay ? "REPLAY of " + vm.day + " at " + clock(vm.atM) : (vm.stage !== "offday" && vm.atM >= 0 && vm.atM < 150 ? "live · " + clock(vm.atM) + " PT" : "")) + "</div></div>";
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
  var api = { mount: mount, model: model, loadReplay: loadReplay, html: html, build: build };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.V92CARD = api;
})(this);
