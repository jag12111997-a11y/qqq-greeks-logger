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
                       "auction_live.json", "options_latest.json"]);
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

export class Relay extends DurableObject {
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
