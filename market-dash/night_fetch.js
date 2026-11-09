#!/usr/bin/env node
/* Saves the "night before the open" numbers for each logged morning (high-IV layer: bell put, night lean, crash odds).
   Reads the relay's read-only /live/night?day=YYYY-MM-DD (Alpaca SIP bars, 15+ min old) and writes market-dash/night/<day>.json
   for history days that don't have one yet (from 2026-07-20 on). Prints the paths it wrote. Never throws: a failed day is skipped.
   Usage (from the repo root):  node market-dash/night_fetch.js [YYYY-MM-DD ...] */
"use strict";
const fs = require("fs"), path = require("path");
const ROOT = process.cwd(), MD = path.join(ROOT, "market-dash"), OUT = path.join(MD, "night");
const URL0 = "https://gex-relay.jag12111997.workers.dev/live/night?day=";
(async () => {
  try {
    fs.mkdirSync(OUT, { recursive: true });
    let want = process.argv.slice(2).filter(d => /^\d{4}-\d\d-\d\d$/.test(d));
    if (!want.length) {
      want = fs.readdirSync(path.join(MD, "history")).filter(f => /^\d{4}-\d\d-\d\d\.json$/.test(f)).map(f => f.slice(0, 10))
        .filter(d => d >= "2026-07-20" && !fs.existsSync(path.join(OUT, d + ".json")));
    }
    for (const day of want) {
      try {
        const r = await fetch(URL0 + day, { headers: { "User-Agent": "qqq-greeks-logger" } });
        if (!r.ok) { console.error("night " + day + ": HTTP " + r.status); continue; }
        const j = await r.json();
        if (!j || !(j.prev_close > 0) || j.session_date !== day) { console.error("night " + day + ": bad answer"); continue; }
        const f = path.join(OUT, day + ".json");
        fs.writeFileSync(f, JSON.stringify(j, null, 1));
        console.log(path.relative(ROOT, f));
      } catch (e) { console.error("night " + day + ": " + e.message); }
    }
  } catch (e) { console.error("night_fetch: " + e.message); }
})();
