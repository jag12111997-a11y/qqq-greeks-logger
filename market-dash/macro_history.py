"""Ten years of macro history for the Macro Outlook tab (Nov 16, 2026).

Pulls FRED's public CSV download (no API key needed) and writes
market-dash/macro_history.json. Read-only market data; safe to rerun.
If FRED is down, the old file is kept untouched.
"""
import csv, io, json, os, sys, datetime as dt, urllib.request

SERIES = {
    "real_gdp":  ("GDPC1",    "Real GDP (bn, chained 2017 $)", "Q"),
    "cpi":       ("CPIAUCSL", "CPI, all items (index)",        "M"),
    "core_pce":  ("PCEPILFE", "Core PCE price index",          "M"),
    "fed_funds": ("DFF",      "Fed funds effective rate (%)",  "D"),
    "yield_10y": ("DGS10",    "10-year Treasury (%)",          "D"),
    "yield_2y":  ("DGS2",     "2-year Treasury (%)",           "D"),
    "dollar":    ("DTWEXBGS", "Broad dollar index",            "D"),
    "unrate":    ("UNRATE",   "Unemployment rate (%)",         "M"),
    "payrolls":  ("PAYEMS",   "Nonfarm payrolls (thousands)",  "M"),
}
START = (dt.date.today() - dt.timedelta(days=365 * 10 + 40)).isoformat()
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_history.json")


def fetch(sid):
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=%s&cosd=%s" % (sid, START)
    with urllib.request.urlopen(url, timeout=60) as r:
        text = r.read().decode("utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    pts = []
    for row in rows[1:]:
        if len(row) < 2 or row[1] in ("", "."):
            continue
        try:
            pts.append([row[0], round(float(row[1]), 4)])
        except ValueError:
            continue
    return pts


def main():
    out = {"generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "FRED public CSV", "series": {}}
    for key, (sid, name, freq) in SERIES.items():
        try:
            pts = fetch(sid)
            if len(pts) < 5:
                raise ValueError("too few points")
            if freq == "D":   # keep the last 2 years daily; older history thinned to one point per week (smaller file)
                cut = (dt.date.today() - dt.timedelta(days=730)).isoformat()
                wk, old = {}, []
                for d, v in pts:
                    if d >= cut:
                        continue
                    y, w, _ = dt.date.fromisoformat(d).isocalendar()
                    wk[(y, w)] = [d, v]
                old = sorted(wk.values())
                pts = old + [p for p in pts if p[0] >= cut]
            out["series"][key] = {"id": sid, "name": name, "freq": freq, "points": pts}
            print("ok", key, sid, len(pts), pts[-1])
        except Exception as exc:  # keep going; one bad series shouldn't sink the rest
            print("skip", key, sid, exc, file=sys.stderr)
    if len(out["series"]) < 5:
        print("too many failures; keeping the old file", file=sys.stderr)
        return 0
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print("wrote", OUT, os.path.getsize(OUT), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
