// GEX live relay.
//
//   PUT /v1/<file>   logger only (Authorization: Bearer RELAY_KEY), JSON body
//   GET /v1/<file>   the GEX Chart page; ETag + If-None-Match, no caching
//
// Everything lives in one SQLite-backed Durable Object, so a GET always sees the
// latest PUT (no eventual consistency). Two logger runs overlap for a few
// minutes at the noon handoff, so the per-minute files are merged minute by
// minute and the single-snapshot files keep whichever copy is newer.
import { DurableObject } from "cloudflare:workers";

const FILES = new Set(["gex_live.json", "gex_intraday.json", "gex_frames.json",
                       "auction_live.json", "options_latest.json", "vix_live.json"]);
const MINUTE_LISTS = { "gex_intraday.json": ["points", "time"], "gex_frames.json": ["frames", "t"] };
const ORIGINS = new Set(["https://jag12111997-a11y.github.io"]);
const MAX_BYTES = 1_500_000;

function headers(req, extra) {
  const h = new Headers(extra || {});
  const origin = req.headers.get("Origin");
  if (origin && ORIGINS.has(origin)) {
    h.set("Access-Control-Allow-Origin", origin);
    h.set("Access-Control-Allow-Methods", "GET, OPTIONS");
    h.set("Access-Control-Allow-Headers", "If-None-Match");
    h.set("Access-Control-Expose-Headers", "ETag");
    h.set("Access-Control-Max-Age", "86400");
  }
  h.set("Vary", "Origin");
  h.set("X-Content-Type-Options", "nosniff");
  return h;
}

function reply(req, status, body, extra) {
  return new Response(body, { status, headers: headers(req, extra) });
}

async function sameSecret(given, expected) {
  if (!expected) return false;
  const enc = new TextEncoder();
  const [a, b] = await Promise.all([crypto.subtle.digest("SHA-256", enc.encode(given)),
                                    crypto.subtle.digest("SHA-256", enc.encode(expected))]);
  return crypto.subtle.timingSafeEqual(a, b);
}

function stamp(doc) {
  return (doc && (doc.generated_utc || doc.updated_utc)) || null;
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === "OPTIONS") return reply(req, 204, null);
    if (url.pathname === "/" || url.pathname === "/v1/") {
      return reply(req, 200, "gex relay", { "Content-Type": "text/plain" });
    }
    // Live pull (read-only): the dashboard asks for fresh bars / option quotes / VIX; the Durable Object
    // fetches them from Alpaca (keys stay here) or Cboe at most once per 15 s and everyone shares that copy.
    const lv = url.pathname.match(/^\/live\/(bars|options|vix|night)$/);
    if (lv) {
      if (req.method !== "GET") return reply(req, 405, "method not allowed");
      const day = url.searchParams.get("day");
      if (day && !/^\d{4}-\d\d-\d\d$/.test(day)) return reply(req, 400, "bad day");
      const relay = env.RELAY.get(env.RELAY.idFromName("qqq"));
      const out = await relay.live(lv[1], day || null);
      return reply(req, out.ok ? 200 : 502, out.body, { "Content-Type": "application/json", "Cache-Control": "no-store" });
    }
    const m = url.pathname.match(/^\/v1\/([a-z_]+\.json)$/);
    if (!m || !FILES.has(m[1])) return reply(req, 404, "not found", { "Content-Type": "text/plain" });
    const name = m[1];
    const relay = env.RELAY.get(env.RELAY.idFromName("qqq"));

    if (req.method === "GET" || req.method === "HEAD") {
      const rec = await relay.read(name);
      if (!rec) return reply(req, 404, "not yet", { "Content-Type": "text/plain", "Cache-Control": "no-store" });
      const h = { "Content-Type": "application/json", "Cache-Control": "no-cache", "ETag": rec.etag };
      if (req.headers.get("If-None-Match") === rec.etag) return reply(req, 304, null, h);
      return reply(req, 200, req.method === "HEAD" ? null : rec.body, h);
    }

    if (req.method === "PUT") {
      const auth = req.headers.get("Authorization") || "";
      if (!(await sameSecret(auth, "Bearer " + (env.RELAY_KEY || "")))) return reply(req, 401, "unauthorized");
      const body = await req.text();
      if (body.length > MAX_BYTES) return reply(req, 413, "too large");
      let doc;
      try { doc = JSON.parse(body); } catch (e) { return reply(req, 400, "not json"); }
      if (!doc || typeof doc !== "object" || Array.isArray(doc)) return reply(req, 400, "not an object");
      const out = await relay.write(name, doc);
      return reply(req, 200, JSON.stringify(out), { "Content-Type": "application/json" });
    }
    return reply(req, 405, "method not allowed");
  },
};

// ---- live pull helpers ----
const DATA = "https://data.alpaca.markets";
const PULL_TTL_MS = 15000;
function nyParts(ms) {
  const f = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const p = {}; for (const x of f.formatToParts(new Date(ms))) p[x.type] = x.value;
  return p;
}
function openUtcMs(day) {          // 9:30 New York on `day` as epoch ms (works in EST and EDT)
  for (const h of [13, 14]) {        // 9:30 ET is 13:30Z in summer, 14:30Z in winter
    const ms = Date.parse(day + "T" + h + ":30:00Z"), p = nyParts(ms);
    if (p.hour === "09" && p.minute === "30") return ms;
  }
  return Date.parse(day + "T14:30:00Z");
}
function iso(ms) { return new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z"); }

export class Relay extends DurableObject {
  constructor(ctx, env) { super(ctx, env); this.env = env; this.mem = {}; }

  async live(kind, day) {
    const key = kind + "|" + (day || ""), hit = this.mem[key];
    if (hit && Date.now() - hit.at < PULL_TTL_MS) return hit.out;
    if (hit && hit.pending) return hit.pending;
    const pending = this.fetchLive(kind, day).then((out) => {
      this.mem[key] = { at: Date.now(), out }; return out;
    }).catch((e) => {
      const out = { ok: false, body: JSON.stringify({ error: String(e && e.message || e).slice(0, 200) }) };
      this.mem[key] = { at: Date.now() - PULL_TTL_MS + 3000, out }; return out;   // retry a failure after 3 s
    });
    this.mem[key] = { ...(hit || { at: 0 }), pending };
    return pending;
  }

  async alpaca(path, params) {
    const u = new URL(DATA + path);
    for (const [k, v] of Object.entries(params)) if (v != null) u.searchParams.set(k, String(v));
    const r = await fetch(u, { headers: { "APCA-API-KEY-ID": this.env.ALPACA_API_KEY || "", "APCA-API-SECRET-KEY": this.env.ALPACA_API_SECRET || "" } });
    if (!r.ok) throw new Error("alpaca " + r.status);
    return r.json();
  }

  async bars(day) {
    const now = Date.now(), today = day || (() => { const p = nyParts(now); return p.year + "-" + p.month + "-" + p.day; })();
    const open = openUtcMs(today), end = Math.min(now, open + 389 * 60000 + 59000);
    if (end <= open) return { session_date: today, candles: [] };
    const out = []; let token = null;
    for (let i = 0; i < 4; i++) {
      const j = await this.alpaca("/v2/stocks/QQQ/bars", { timeframe: "1Min", start: iso(open), end: iso(end), feed: "iex", limit: 10000, adjustment: "raw", sort: "asc", page_token: token });
      for (const b of j.bars || []) out.push({ time: Math.floor(Date.parse(b.t) / 1000), open: b.o, high: b.h, low: b.l, close: b.c, volume: b.v });
      token = j.next_page_token; if (!token) break;
    }
    return { session_date: today, candles: out };
  }

  // Night before the open (high-IV layer + info lines): yesterday's close, after-hours / premarket prices (IEX has no
  // 8 PM - 4 AM session, so the 4:00 AM ET price carries the overnight move), and daily stats for the crash-odds line.
  async night(day) {
    const now = Date.now(), today = day || (() => { const p = nyParts(now); return p.year + "-" + p.month + "-" + p.day; })();
    const open = openUtcMs(today);
    const dj = await this.alpaca("/v2/stocks/QQQ/bars", { timeframe: "1Day", start: iso(open - 130 * 86400000), end: iso(open - 3600000), feed: "sip", limit: 200, adjustment: "split", sort: "asc" });
    const daily = (dj.bars || []).filter((b) => b.t.slice(0, 10) < today);
    if (daily.length < 22) throw new Error("not enough daily bars");
    const prev = daily[daily.length - 1], prevDay = prev.t.slice(0, 10), prevClose = prev.c;
    const closeMs = openUtcMs(prevDay) + 390 * 60000;                     // 4:00 PM ET yesterday
    const end = Math.min(now - 60000, open);
    const bars = []; let token = null;
    for (let i = 0; i < 4 && end > closeMs; i++) {
      const j = await this.alpaca("/v2/stocks/QQQ/bars", { timeframe: "1Min", start: iso(closeMs), end: iso(end), feed: "iex", limit: 10000, adjustment: "raw", sort: "asc", page_token: token });
      for (const b of j.bars || []) bars.push({ t: Date.parse(b.t), o: b.o, c: b.c });
      token = j.next_page_token; if (!token) break;
    }
    const at = (ms) => { let v = null; for (const b of bars) { if (b.t < ms) v = b.c; else break; } return v; };   // last close before ms
    const ah = bars.filter((b) => b.t < closeMs + 4 * 3600000), pre = bars.filter((b) => b.t >= open - 330 * 60000);
    const lc = daily.slice(-21).map((b) => b.c), rets = [];
    for (let i = 1; i < lc.length; i++) rets.push(Math.log(lc[i] / lc[i - 1]));
    const mean = rets.reduce((a, b) => a + b, 0) / rets.length;
    const sd = Math.sqrt(rets.reduce((a, b) => a + (b - mean) * (b - mean), 0) / (rets.length - 1));
    const ma50 = daily.slice(-50).reduce((a, b) => a + b.c, 0) / Math.min(50, daily.length);
    return { session_date: today, prev_day: prevDay, prev_close: prevClose,
      after_hours_last: ah.length ? ah[ah.length - 1].c : null,
      pre_first: pre.length ? pre[0].o : null,                      // ~4:00 AM ET = yesterday's close + after-hours + overnight
      pre_0900: at(open - 30 * 60000),                              // 9:00 AM ET = 6:00 AM PT
      pre_last: pre.length ? pre[pre.length - 1].c : null, pre_last_t: pre.length ? iso(pre[pre.length - 1].t) : null,
      night_bars: bars.length, rv20: Math.round(sd * Math.sqrt(252) * 1000) / 10, ma50: Math.round(ma50 * 100) / 100,
      below50: prevClose < ma50 };
  }

  async fetchLive(kind, day) {
    const gen = iso(Date.now());
    if (kind === "night") {
      const n = await this.night(day);
      return { ok: true, body: JSON.stringify({ generated_utc: gen, feed: "iex (night) + sip (daily)", ...n }) };
    }
    if (kind === "vix") {
      const r = await fetch("https://cdn.cboe.com/api/global/delayed_quotes/quotes/_VIX.json", { headers: { "User-Agent": "qqq-greeks-logger" } });
      if (!r.ok) throw new Error("cboe " + r.status);
      const d = ((await r.json()) || {}).data || {};
      if (!(d.current_price > 0)) throw new Error("no vix");
      return { ok: true, body: JSON.stringify({ generated_utc: gen, symbol: "VIX", last: d.current_price, pct: d.price_change_percent, src_time: d.last_trade_time, source: "Cboe delayed quote (~15 min)" }) };
    }
    if (kind === "bars") {
      const b = await this.bars(day);
      return { ok: true, body: JSON.stringify({ generated_utc: gen, feed: "iex", ...b }) };
    }
    // options: today's 0DTE calls + puts within ±15 of spot (spot = last 1-min close)
    const bo = await this.live("bars", day);              // reuse the shared bars copy (no extra Alpaca call)
    if (!bo.ok) throw new Error("bars unavailable");
    const b = JSON.parse(bo.body), today = b.session_date, last = b.candles.length ? b.candles[b.candles.length - 1].close : null;
    if (!last) return { ok: true, body: JSON.stringify({ generated_utc: gen, session_date: today, contracts: [] }) };
    const t = Math.floor(Date.now() / 1000), contracts = [];
    for (const type of ["call", "put"]) {
      const j = await this.alpaca("/v1beta1/options/snapshots/QQQ", { feed: "indicative", type, expiration_date: today, strike_price_gte: Math.floor(last - 15), strike_price_lte: Math.ceil(last + 15), limit: 1000 });
      for (const [sym, s] of Object.entries(j.snapshots || {})) {
        const q = s.latestQuote || {}, strike = Number(sym.slice(-8)) / 1000;
        if (!(q.bp > 0) || !(q.ap > 0)) continue;
        contracts.push({ side: type === "call" ? "C" : "P", strike, bid: q.bp, ask: q.ap, iv: s.impliedVolatility == null ? null : s.impliedVolatility,
          delta: s.greeks && s.greeks.delta != null ? s.greeks.delta : null, t, quote_t: q.t || null });
      }
    }
    return { ok: true, body: JSON.stringify({ generated_utc: gen, feed: "indicative", session_date: today, spot: last, contracts }) };
  }

  async read(name) {
    return (await this.ctx.storage.get(name)) || null;
  }

  async write(name, doc) {
    const prev = await this.ctx.storage.get(name);
    let next = doc, how = "replaced";
    if (prev) {
      const old = JSON.parse(prev.body);
      const list = MINUTE_LISTS[name];
      if (list && old.session_date && old.session_date === doc.session_date) {
        const [key, tk] = list, rows = new Map();
        for (const src of [old, doc]) {           // the incoming copy wins a shared minute
          for (const r of src[key] || []) {
            if (r && r[tk] != null) rows.set(Math.floor(Number(r[tk]) / 60), r);
          }
        }
        next = { ...doc, [key]: [...rows.keys()].sort((a, b) => a - b).map((k) => rows.get(k)) };
        const stamps = [stamp(old), stamp(doc)].filter(Boolean).sort();
        if (stamps.length) next.updated_utc = stamps[stamps.length - 1];
        how = "merged";
      } else if (list && old.session_date && doc.session_date && old.session_date > doc.session_date) {
        return { kept: "a later session is already stored", session_date: old.session_date };
      } else if (!list || old.session_date === doc.session_date) {
        const a = stamp(old), b = stamp(doc);
        if (a && b && a > b) return { kept: "newer copy already stored", stamp: a };
      }
    }
    const body = JSON.stringify(next);
    const digest = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(body));
    const etag = '"' + [...new Uint8Array(digest)].slice(0, 10).map((x) => x.toString(16).padStart(2, "0")).join("") + '"';
    await this.ctx.storage.put(name, { body, etag, at: Date.now() });
    return { ok: how, bytes: body.length, etag };
  }
}
