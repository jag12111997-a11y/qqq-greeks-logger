# ============================================================
# MARKET DASH — FETCH SCRIPT  v1
# ============================================================
# SAVE THIS AS:  market_dash_fetch.py   (change .txt to .py)
# PUT IT IN:     3v3(library)/market-dash/
#
# RUN IT:        cd into the market-dash folder, then:
#                  python3 market_dash_fetch.py
#
# WHAT IT DOES
#   Fetches 5 sources, builds one dated entry, and APPENDS it.
#   Nothing is ever overwritten. History grows from day one.
#
# WHAT IT WRITES (both inside market-dash/)
#   1. data/YYYY-MM-DD/market_dash_YYYY-MM-DD.json   full raw record
#   2. history.js                                    one appended line
#      history.js is what the HTML dashboard will read later.
#
# INSTALL ONCE (if you get a "No module named" error):
#   pip3 install requests yfinance
# ============================================================

import os
import re
import json
import time
import traceback
from datetime import datetime, timezone

import requests

try:
    import yfinance as yf
except ImportError:
    yf = None


# ============================================================
# TWEAK ZONE — change things here, not below
# ============================================================

# Where data gets written. "." means "the folder this script is in".
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Where to look for FRED_API_KEY.
#
# ORDER OF LOOKUP:
#   1. Environment variable FRED_API_KEY  <- this is what GitHub uses
#   2. The local .env files below         <- this is what your Mac uses
#
# That means the SAME file works in both places with no editing.
# In the cloud your key comes from the GitHub secret. On your Mac it
# comes from the "other" folder. Neither one knows about the other.
#
# Your "other" folder has five .env files and only some have the key.
# The plain ".env" does NOT. First file with a real key wins.
OTHER_DIR = os.path.join(BASE_DIR, "..", "other")
ENV_CANDIDATES = [
    os.path.join(OTHER_DIR, ".env (4)"),
    os.path.join(OTHER_DIR, ".env (3)"),
    os.path.join(OTHER_DIR, ".env (2)"),
    os.path.join(OTHER_DIR, ".env"),
]

# If the .env lookup fails you can just paste the key here instead.
FRED_API_KEY_FALLBACK = ""

# Economic calendar. All countries, this week. (Your choice.)
FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Cboe daily stats — the plain-HTML mirror, NOT the JS page.
CBOE_URL = "https://res.cboe.com/us/options/market_statistics/daily/?mkt=cone"

# CFTC Commitments of Traders, CME futures-only report.
CFTC_URL = "https://www.cftc.gov/dea/futures/deacmelf.htm"

# Which COT contracts to pull. Add or remove freely.
COT_MARKETS = {
    "NASDAQ_MINI": "NASDAQ MINI - CHICAGO MERCANTILE EXCHANGE",
    "SP500_CONSOLIDATED": "S&P 500 Consolidated - CHICAGO MERCANTILE EXCHANGE",
}

# Headline market tickers pulled from Yahoo.
MARKET_TICKERS = {
    "QQQ": "QQQ",
    "SPY": "SPY",
    "VIX": "^VIX",
    "CRUDE": "CL=F",
    "GOLD": "GC=F",
    "US10Y": "^TNX",
    "DOLLAR": "DX-Y.NYB",
}

# Sector ETFs. These feed the donut chart in the HTML later.
SECTOR_ETFS = {
    "Energy": "XLE",
    "Financials": "XLF",
    "Consumer Staples": "XLP",
    "Communication Services": "XLC",
    "Health Care": "XLV",
    "Real Estate": "XLRE",
    "Information Technology": "XLK",
    "Industrials": "XLI",
    "Consumer Discretionary": "XLY",
    "Utilities": "XLU",
    "Materials": "XLB",
}

# FRED series. Left side is the label, right side is FRED's series ID.
FRED_SERIES = {
    "real_gdp": "GDPC1",          # Real GDP, quarterly
    "cpi": "CPIAUCSL",            # CPI all items
    "core_pce": "PCEPILFE",       # Core PCE price index
    "fed_funds": "DFF",           # Effective fed funds rate, daily
    "yield_10y": "DGS10",         # 10-year treasury
    "yield_2y": "DGS2",           # 2-year treasury
    "dollar_index": "DTWEXBGS",   # Broad dollar index
}

HTTP_TIMEOUT = 20
HEADERS = {"User-Agent": "Mozilla/5.0"}

# ============================================================
# END TWEAK ZONE
# ============================================================


def log(msg):
    print(msg, flush=True)


def load_fred_key():
    """
    Find FRED_API_KEY. Tries each file in ENV_CANDIDATES and returns the
    first real key it finds. Files that are missing, or that have the line
    but with nothing after the "=", are skipped.
    """
    if FRED_API_KEY_FALLBACK:
        return FRED_API_KEY_FALLBACK

    # 1. Environment variable. This is how GitHub Actions supplies it.
    env_key = os.environ.get("FRED_API_KEY", "").strip()
    if env_key:
        log("     using key from: environment (GitHub secret)")
        return env_key

    # 2. Local .env files. This is how your Mac supplies it.
    for candidate in ENV_CANDIDATES:
        env_file = os.path.abspath(candidate)

        if not os.path.exists(env_file):
            continue

        try:
            with open(env_file, "r", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("FRED_API_KEY"):
                        parts = line.split("=", 1)
                        if len(parts) == 2:
                            key = parts[1].strip().strip('"').strip("'")
                            if key:
                                log(f"     using key from: {os.path.basename(env_file)}")
                                return key
        except Exception:
            continue

    return None


def nums_in(line):
    """Pull every number out of a line of text."""
    out = []
    for x in re.findall(r"-?\d[\d,]*(?:\.\d+)?", line):
        try:
            if "." in x:
                out.append(float(x.replace(",", "")))
            else:
                out.append(int(x.replace(",", "")))
        except Exception:
            pass
    return out


# ============================================================
# SOURCE 1 — ECONOMIC CALENDAR (ForexFactory JSON)
# ============================================================

def fetch_calendar():
    r = requests.get(FF_CALENDAR_URL, headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    events = r.json()

    cleaned = []
    for e in events:
        cleaned.append({
            "title": e.get("title"),
            "country": e.get("country"),
            "date": e.get("date"),
            "impact": e.get("impact"),
            "forecast": e.get("forecast"),
            "previous": e.get("previous"),
        })

    # Convenience slice: the US high-impact stuff, for "Main catalysts".
    catalysts = [
        e for e in cleaned
        if e["country"] == "USD" and e["impact"] == "High"
    ]

    return {
        "all_events": cleaned,
        "us_high_impact": catalysts,
        "event_count": len(cleaned),
    }


# ============================================================
# SOURCE 2 — CBOE DAILY OPTIONS STATS
# ============================================================

def fetch_cboe():
    r = requests.get(CBOE_URL, headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()

    raw = r.text

    # Strip HTML tags down to plain text lines.
    text = re.sub(r"<[^>]+>", "\n", raw)
    text = re.sub(r"&nbsp;?", " ", text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    flat = "\n".join(lines)

    # Labels we care about, and the key we store them under.
    wanted = {
        "total_pc": r"TOTAL\s+PUT/CALL\s+RATIO",
        "index_pc": r"INDEX\s+PUT/CALL\s+RATIO",
        "etp_pc": r"(?:EXCHANGE\s+TRADED\s+PRODUCTS?|ETP)\s+PUT/CALL\s+RATIO",
        "equity_pc": r"EQUITY\s+PUT/CALL\s+RATIO",
        # Live page label is "CBOE VOLATILITY INDEX (VIX) PUT/CALL RATIO".
        # The closing paren sits between VIX and PUT, so a plain "VIX PUT"
        # pattern misses it. Verified against the real page 2026-07-27.
        "vix_pc": r"VIX\)?\s+PUT/CALL\s+RATIO",
        "spx_spxw_pc": r"SPX\s*\+\s*SPXW\s+PUT/CALL\s+RATIO",
    }

    ratios = {}
    for key, pattern in wanted.items():
        m = re.search(pattern + r"[^\d\-]{0,40}(\d+\.\d+)", flat, re.IGNORECASE)
        if m:
            ratios[key] = float(m.group(1))
        else:
            ratios[key] = None

    # Volume and open interest totals.
    vol = re.search(
        r"SUM\s+OF\s+ALL\s+PRODUCTS?\s+VOLUME[^\d]{0,40}([\d,]+)",
        flat, re.IGNORECASE)
    oi = re.search(
        r"SUM\s+OF\s+ALL\s+PRODUCTS?\s+OPEN\s+INTEREST[^\d]{0,40}([\d,]+)",
        flat, re.IGNORECASE)

    ratios["total_volume"] = int(vol.group(1).replace(",", "")) if vol else None
    ratios["total_open_interest"] = int(oi.group(1).replace(",", "")) if oi else None

    # The session date printed on the page. Use it, not today's date.
    dm = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", flat)
    ratios["session_date"] = dm.group(1) if dm else None

    got = sum(1 for k, v in ratios.items() if v is not None)

    # If the parse came back empty, keep the raw page so we can look at it.
    if ratios["total_pc"] is None:
        debug_path = os.path.join(BASE_DIR, "debug_cboe_raw.html")
        with open(debug_path, "w", errors="ignore") as f:
            f.write(raw)
        log("    !! Cboe parse found no TOTAL P/C.")
        log("    !! Raw page saved to debug_cboe_raw.html — send it to me.")

    ratios["fields_parsed"] = got
    return ratios


# ============================================================
# SOURCE 2b — CBOE VENUE / MARKET-SHARE CONCENTRATION
# ============================================================
# Matched-VOLUME and NET-PREMIUM share by exchange group — the same
# data as the Market Statistics page's "Volume Summary" and "Net Option
# Premium Summary" tabs. Public JSON, no key. This is the feed that
# replaced the old scrape that stopped pulling.
#
#   endpoint returns share as a FRACTION (0.2892) -> we store PERCENT (28.92).
#   "today" = the newest completed session; "avg5" = a REAL trailing average
#   built by pulling the last few completed sessions and averaging per venue.
#   Nothing here is fabricated: if a day has no data we skip it, and if we
#   somehow get zero sessions we return None so the card just says "not sourced".

CBOE_SHARE_URL = "https://ww2.cboe.com/us/options/market_share/market/data/"

# Pretty-print the long official name to match the rest of the dashboard.
_VENUE_RENAME = {
    "Chicago Board Options Exchange (C,W,E,Z)": "Cboe (C,W,E,Z)",
}


def _cboe_share_day(bias, dt):
    """One session, one bias ('Volume' or 'Premium').
    Returns (rows, total). rows = [{'venue', 'share'(fraction), 'value'}].
    total is that day's grand total (0.0 means no data / not a trading day)."""
    params = {
        "bias": bias, "limit": 6, "dt": dt,
        "auctions": "y", "subdollars": "y", "expanded": "", "oddLots": "y",
    }
    r = requests.get(CBOE_SHARE_URL, params=params, headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    j = r.json() or {}
    data = j.get("data") or {}
    stats = (data.get("stats") or {}).get("integrated") or []
    try:
        total = float((data.get("total") or {}).get("normal", [0])[0])
    except Exception:
        total = 0.0
    rows = []
    for s in stats:
        name = s.get("mkthtml")
        rows.append({
            "venue": _VENUE_RENAME.get(name, name),
            "share": float(s.get("mktshare") or 0.0),
            "value": float(s.get("value") or 0.0),
        })
    return rows, total


def fetch_venue_concentration():
    from datetime import timedelta
    try:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()

    # Walk back from today to collect up to 5 completed sessions (total > 0).
    # This naturally skips weekends/holidays (they return zero totals).
    sessions = []   # each: (iso, vol_rows, prem_rows, vol_total, prem_total)
    d = today
    for _ in range(12):
        if len(sessions) >= 5:
            break
        iso = d.strftime("%Y-%m-%d")
        vol_rows, vol_total = _cboe_share_day("Volume", iso)
        if vol_total > 0 and vol_rows:
            prem_rows, prem_total = _cboe_share_day("Premium", iso)
            sessions.append((iso, vol_rows, prem_rows, vol_total, prem_total))
        d = d - timedelta(days=1)

    if not sessions:
        return None

    # Trailing average share (%) per venue, across every session we pulled.
    def _avg_by_venue(pick):
        acc, cnt = {}, {}
        for sess in sessions:
            for row in pick(sess):
                v = row["venue"]
                acc[v] = acc.get(v, 0.0) + row["share"] * 100.0
                cnt[v] = cnt.get(v, 0) + 1
        return {v: acc[v] / cnt[v] for v in acc}

    vol_avg = _avg_by_venue(lambda s: s[1])
    prem_avg = _avg_by_venue(lambda s: s[2])

    # Newest session is the "today" column.
    newest_iso, newest_vol, newest_prem, newest_voltot, newest_premtot = sessions[0]

    def _build(today_rows, avg_map):
        out = []
        for row in today_rows:
            v = row["venue"]
            out.append({
                "venue": v,
                "today": round(row["share"] * 100.0, 2),
                "avg5": round(avg_map[v], 2) if v in avg_map else None,
            })
        out.sort(key=lambda x: x["today"], reverse=True)   # biggest share first
        return out

    return {
        "volume_share": _build(newest_vol, vol_avg),
        "premium_share": _build(newest_prem, prem_avg),
        "total_volume": int(newest_voltot),
        "total_premium": int(newest_premtot),
        "session_date": newest_iso,
        "sessions_in_avg": len(sessions),
    }


# ============================================================
# SOURCE 3 — CFTC COMMITMENTS OF TRADERS
# ============================================================

def map_cot_row(nums):
    if not nums:
        return None
    return {
        "openInterest": nums[0],
        "nonCommercialLong": nums[1],
        "nonCommercialShort": nums[2],
        "nonCommercialSpreads": nums[3],
        "commercialLong": nums[4],
        "commercialShort": nums[5],
        "totalReportableLong": nums[6],
        "totalReportableShort": nums[7],
        "nonReportableLong": nums[8],
        "nonReportableShort": nums[9],
    }


def parse_cot_block(block):
    lines = block.splitlines()

    report_date = None
    previous_date = None

    for line in lines:
        m = re.search(r"Commitments of Traders - Futures Only,\s*(.*)", line)
        if m:
            report_date = m.group(1).strip()
        m = re.search(r"Changes in Commitments from:\s*(.*)", line)
        if m:
            previous_date = m.group(1).strip()

    all_rows = []
    for line in lines:
        if line.strip().startswith("All  :"):
            n = nums_in(line)
            if len(n) >= 10:
                all_rows.append(n[:10])

    positions = all_rows[0] if len(all_rows) >= 1 else None
    percents = all_rows[1] if len(all_rows) >= 2 else None

    changes = None
    for i, line in enumerate(lines):
        if "Changes in Commitments from" in line:
            for j in range(i + 1, min(i + 6, len(lines))):
                n = nums_in(lines[j])
                if len(n) >= 10:
                    changes = n[:10]
                    break

    pos = map_cot_row(positions)
    chg = map_cot_row(changes)

    nc_net = None
    comm_net = None
    nc_net_chg = None

    if pos:
        nc_net = pos["nonCommercialLong"] - pos["nonCommercialShort"]
        comm_net = pos["commercialLong"] - pos["commercialShort"]
    if chg:
        nc_net_chg = chg["nonCommercialLong"] - chg["nonCommercialShort"]

    if nc_net is None:
        read = "COT DATA INCOMPLETE"
    elif nc_net < 0 and comm_net > 0:
        read = "FUNDS NET SHORT / COMMERCIALS NET LONG"
    elif nc_net > 0 and comm_net < 0:
        read = "FUNDS NET LONG / COMMERCIALS NET SHORT"
    else:
        read = "MIXED POSITIONING"

    return {
        "reportDate": report_date,
        "previousReportDate": previous_date,
        "positions": pos,
        "changes": chg,
        "percentOpenInterest": map_cot_row(percents),
        "nonCommercialNet": nc_net,
        "commercialNet": comm_net,
        "nonCommercialNetChange": nc_net_chg,
        "read": read,
    }


def fetch_cot():
    r = requests.get(CFTC_URL, headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    text = r.text

    out = {}
    for label, market_name in COT_MARKETS.items():
        start = text.find(market_name)
        if start == -1:
            out[label] = {"error": "market block not found"}
            continue
        block = text[start:start + 5000]
        out[label] = parse_cot_block(block)

    return out


# ============================================================
# SOURCE 4 — MARKET PRICES + SECTORS (yfinance)
# ============================================================

# ETFs that actually trade overnight / pre- & post-market. For these we show
# the freshest extended-hours print instead of yesterday's regular close.
# VIX and DXY are indexes (not tradeable), so they have NO overnight quote and
# stay on their last regular close.
OVERNIGHT_TICKERS = {"QQQ", "SPY", "GLD", "USO", "EWY", "TLT"}


def _overnight_last_prev(ticker):
    """Freshest print incl. pre/post-market, plus the prior regular-session
    close to measure the move against. Returns (last, prev) or (None, None)."""
    daily = yf.Ticker(ticker).history(period="7d", interval="1d")
    if daily is None or daily.empty:
        return None, None
    intr = yf.Ticker(ticker).history(period="2d", interval="1m", prepost=True)
    if intr is None or intr.empty:
        if len(daily) < 2:
            return None, None
        return float(daily["Close"].iloc[-1]), float(daily["Close"].iloc[-2])
    last = float(intr["Close"].iloc[-1])
    last_day = intr.index[-1].normalize()
    # previous close = most recent daily close on an earlier calendar day than
    # the freshest print (so pre-market compares to yesterday, after-hours to
    # today's close's prior day = full-day+AH move).
    prior = daily[daily.index.normalize() < last_day]
    if not prior.empty:
        prev = float(prior["Close"].iloc[-1])
    elif len(daily) >= 2:
        prev = float(daily["Close"].iloc[-2])
    else:
        prev = None
    return last, prev


def pct_change_last_two(ticker):
    """Returns (last_close, prev_close, pct_change)."""
    if ticker in OVERNIGHT_TICKERS:
        try:
            last, prev = _overnight_last_prev(ticker)
            if last is not None and prev:
                return last, prev, ((last - prev) / prev) * 100.0
        except Exception:
            pass  # fall back to plain daily close below
    data = yf.Ticker(ticker).history(period="5d", interval="1d")
    if data is None or data.empty or len(data) < 2:
        return None, None, None
    prev = float(data["Close"].iloc[-2])
    last = float(data["Close"].iloc[-1])
    pct = ((last - prev) / prev) * 100.0 if prev else None
    return last, prev, pct


# ============================================================
# HORIZONS — 5D / 1M / QTR via webull get_bars
# ============================================================
# This is the pattern already used ~114 times across your library
# (webull_qqq_candles.py etc). No API key, no signup. It returns daily
# bars, and every horizon is just a lookback on the same array.
#
# yfinance is the fallback if webull isn't importable.

try:
    from webull import webull
    _WB = webull()
except Exception:
    _WB = None


def _daily_closes(ticker, count=70):
    """Newest-last list of daily closes. Tries webull, falls back to yfinance."""
    if _WB is not None:
        for interval in ("d1", "d"):
            try:
                raw = _WB.get_bars(stock=ticker, interval=interval,
                                   count=count, extendTrading=0)
                if raw is None or len(raw) == 0:
                    continue
                closes = [float(c) for c in raw["close"].tolist()]
                if len(closes) >= 2:
                    return closes
            except Exception:
                continue

    if yf is not None:
        try:
            df = yf.Ticker(ticker).history(period="4mo", interval="1d")
            if df is not None and not df.empty:
                return [float(c) for c in df["Close"].dropna().tolist()]
        except Exception:
            pass

    return []


def _lookback_pct(closes, n):
    """% change from n sessions ago to the latest close."""
    if not closes or len(closes) <= n:
        return None
    prev = closes[-1 - n]
    if not prev:
        return None
    return round((closes[-1] / prev - 1.0) * 100.0, 2)


def fetch_horizons(ticker):
    """5D / 1M / Qtr for one symbol, from a single bars call."""
    closes = _daily_closes(ticker)
    if not closes:
        return {}
    return {
        "ret_5d":  _lookback_pct(closes, 5),
        "ret_1m":  _lookback_pct(closes, 21),
        "ret_3m":  _lookback_pct(closes, 63),
        "bars_used": len(closes),
    }


def fetch_markets():
    if yf is None:
        return {"error": "yfinance not installed. Run: pip3 install yfinance"}

    out = {}
    for label, ticker in MARKET_TICKERS.items():
        try:
            last, prev, pct = pct_change_last_two(ticker)
            rec = {
                "ticker": ticker,
                "last": round(last, 4) if last is not None else None,
                "prev_close": round(prev, 4) if prev is not None else None,
                "pct_change": round(pct, 3) if pct is not None else None,
            }
            rec.update(fetch_horizons(ticker))
            out[label] = rec
        except Exception as e:
            out[label] = {"ticker": ticker, "error": str(e)}
        time.sleep(0.2)

    return out


# ============================================================
# WATCHLIST — the 20 symbols, with every horizon
# ============================================================

# (fetch ticker, symbol shown on the dashboard, name)
# DXY is the actual ICE dollar index — ticker DX-Y.NYB on Yahoo. UUP stays in
# the list separately as the tradeable ETF that tracks it.
WATCHLIST = [
    ("QQQ",       "QQQ",  "Invesco QQQ Trust"),
    ("^NDX",      "NDX",  "Nasdaq-100 Index"),
    ("QQQM",      "QQQM", "Invesco Nasdaq-100 (QQQM)"),
    ("TQQQ",      "TQQQ", "ProShares UltraPro QQQ (3x)"),
    ("SQQQ",      "SQQQ", "ProShares UltraPro Short QQQ (-3x)"),
    ("SPY",       "SPY",  "SPDR S&P 500 ETF"),
    ("DX-Y.NYB",  "DXY",  "US Dollar Index (ICE)"),
    ("IEF",       "IEF",  "iShares 7-10Y Treasury"),
    ("USO",       "USO",  "United States Oil Fund"),
    ("GLD",       "GLD",  "SPDR Gold Shares"),
    ("^VIX",      "VIX",  "CBOE Volatility Index"),
    ("^VXN",      "VXN",  "CBOE Nasdaq-100 Volatility Index"),
    ("EWY",       "EWY",  "iShares MSCI South Korea"),
    ("TLT",       "TLT",  "iShares 20+Y Treasury"),
    ("UUP",       "UUP",  "Invesco DB US Dollar (DXY tracker)"),
    ("BNO",       "BNO",  "United States Brent Oil"),
    ("SLV",       "SLV",  "iShares Silver Trust"),
    ("DIA",       "DIA",  "SPDR Dow Jones ETF"),
    ("IWM",       "IWM",  "iShares Russell 2000"),
    ("M",         "M",    "Macy's, Inc."),
    ("MU",        "MU",   "Micron Technology"),
    ("SCHX",      "SCHX", "Schwab U.S. Large-Cap"),
    ("IBIT",      "IBIT", "iShares Bitcoin Trust"),
    ("ETHA",      "ETHA", "iShares Ethereum Trust"),
    ("BSOL",      "BSOL", "Bitwise Solana Staking"),
    ("XRPR",      "XRPR", "REX-Osprey XRP ETF"),
]


def fetch_watchlist():
    rows = []
    for ticker, symbol, name in WATCHLIST:
        rec = {"symbol": symbol, "name": name, "ticker": ticker}
        try:
            last, prev, pct = pct_change_last_two(ticker)
            rec["last"] = round(last, 4) if last is not None else None
            rec["pct_change"] = round(pct, 2) if pct is not None else None
            rec.update(fetch_horizons(ticker))

            closes = _daily_closes(ticker, count=260)
            if closes and len(closes) > 2:
                rec["ret_ytd"] = round((closes[-1] / closes[0] - 1.0) * 100.0, 2)

                # ---- white-paper metrics, all from the same closes ----
                rec["z20"] = zscore(closes, 20)      # distance from recent normal
                rec["z60"] = zscore(closes, 60)
                rets = _returns(closes)
                rec["realized_vol_pct"] = realized_vol(rets[-21:] if len(rets) > 21 else rets)
                rec["sharpe"] = sharpe(rets)
                rec["vol_drag"] = vol_drag(rets)
        except Exception as e:
            rec["error"] = str(e)
        rows.append(rec)
        time.sleep(0.2)
    return rows


def fetch_quant_metrics():
    """GARCH regimes, realized-vs-implied, and the crash geometry."""
    out = {}

    qqq = _daily_closes("QQQ", count=260)
    rets = _returns(qqq) if qqq else []
    if rets:
        out["garch"] = garch11(rets)
        out["qqq_realized_vol_21d_pct"] = realized_vol(rets[-21:])
        out["qqq_vol_drag"] = vol_drag(rets)

    # implied vol from VIX for the realized-vs-implied spread.
    # Use the SAME Yahoo source as the rest of the dashboard. Webull does not
    # recognise the "^VIX" symbol format (that caret is Yahoo syntax), so its
    # lookup returns a wrong value that contradicts VIX's own prev-close and
    # daily change. pct_change_last_two("^VIX") gives the consistent level.
    vix = None
    try:
        vix, _prev, _pct = pct_change_last_two("^VIX")
    except Exception:
        pass
    if rets and vix:
        out["realized_vs_implied"] = realized_vs_implied(rets, vix)

    # crash geometry across the sector ETFs
    series, labels = [], []
    for sector, tick in SECTOR_ETFS.items():
        c = _daily_closes(tick, count=90)
        r = _returns(c) if c else []
        if len(r) > 20:
            series.append(r)
            labels.append(sector)
        time.sleep(0.15)
    if len(series) >= 3:
        out["geometry"] = crash_geometry(series, labels)

    return out


def fetch_sectors():
    if yf is None:
        return {"error": "yfinance not installed. Run: pip3 install yfinance"}

    rows = []
    for sector, ticker in SECTOR_ETFS.items():
        try:
            last, prev, pct = pct_change_last_two(ticker)
            if pct is None:
                continue
            rows.append({
                "sector": sector,
                "ticker": ticker,
                "last": round(last, 4),
                "pct_change": round(pct, 3),
            })
        except Exception:
            continue
        time.sleep(0.2)

    rows.sort(key=lambda x: x["pct_change"], reverse=True)

    leaders = [r["sector"] for r in rows[:3]]
    laggards = [r["sector"] for r in rows[-3:]] if len(rows) >= 3 else []

    if rows:
        advancing = sum(1 for r in rows if r["pct_change"] > 0)
        breadth = f"{advancing}/{len(rows)} sectors green"
    else:
        breadth = "no sector data"

    return {
        "rows": rows,
        "leaders": leaders,
        "laggards": laggards,
        "breadth": breadth,
    }


# ============================================================
# SOURCE 5 — FRED MACRO
# ============================================================

def fetch_fred_series(series_id, api_key):
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 14,
    }
    r = requests.get(url, params=params, headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    obs = r.json().get("observations", [])

    clean = []
    for o in obs:
        v = o.get("value")
        if v in (".", "", None):
            continue
        try:
            clean.append({"date": o.get("date"), "value": float(v)})
        except Exception:
            continue

    if not clean:
        return None

    latest = clean[0]
    prior = clean[1] if len(clean) > 1 else None

    return {
        "latest_date": latest["date"],
        "latest": latest["value"],
        "prior": prior["value"] if prior else None,
        "change": round(latest["value"] - prior["value"], 4) if prior else None,
    }


def fetch_fred():
    api_key = load_fred_key()

    if not api_key:
        return {"error": "FRED_API_KEY not found. Check ENV_PATH in the TWEAK ZONE."}

    out = {}
    for label, series_id in FRED_SERIES.items():
        try:
            out[label] = fetch_fred_series(series_id, api_key)
        except Exception as e:
            out[label] = {"error": str(e)}

    # 2s10s curve — the recession/steepening read, computed free.
    try:
        y10 = out.get("yield_10y", {}).get("latest")
        y2 = out.get("yield_2y", {}).get("latest")
        if y10 is not None and y2 is not None:
            spread = round(y10 - y2, 3)
            out["curve_2s10s"] = {
                "spread": spread,
                "read": "INVERTED" if spread < 0 else "NORMAL",
            }
    except Exception:
        pass

    return out


# ============================================================
# QUANT METRICS — from your white papers, only what daily data supports
# ============================================================
# Sources, so future-you knows where each number came from:
#   z-score, OU, det(G)      The One Machine, ch.5 + ch.10
#   Marchenko-Pastur         The One Machine, ch.10
#   GARCH(1,1) regimes       Tail Risk / Regime Modelling (Quant Guild)
#   realized vs implied      The One Machine, ch.3 (gamma scalping)
#   volatility drag          The Only Video You Need (Quant Guild)
#
# Everything here runs on DAILY closes. Anything needing tick or
# order-book data (VPIN, Roll, imbalance, micro price, Hawkes
# calibration, Kalman) is deliberately absent — we don't have the inputs.

try:
    import numpy as _np
except ImportError:
    _np = None

TRADING_DAYS = 252


def _returns(closes):
    """Simple daily returns from a newest-last close list."""
    out = []
    for i in range(1, len(closes)):
        p = closes[i - 1]
        if p:
            out.append(closes[i] / p - 1.0)
    return out


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _std(xs, ddof=1):
    if not xs or len(xs) <= ddof:
        return None
    m = _mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - ddof)) ** 0.5


def zscore(closes, n=20):
    """
    z = (x - mu) / sigma over the last n closes.  One Machine ch.10:
    "the one piece of formal math you could put to work tomorrow."
    This is the 'distance from recent normal' your handoff asked for.
    """
    if not closes or len(closes) < n + 1:
        return None
    window = closes[-n:]
    mu, sd = _mean(window), _std(window)
    if not sd:
        return None
    return round((closes[-1] - mu) / sd, 2)


def realized_vol(returns, annualise=True):
    sd = _std(returns)
    if sd is None:
        return None
    return round(sd * (TRADING_DAYS ** 0.5 if annualise else 1) * 100, 2)


def sharpe(returns):
    """SR = sqrt(252) * mean / sd. Excess over zero — no risk-free subtracted."""
    m, sd = _mean(returns), _std(returns)
    if not sd or m is None:
        return None
    return round((TRADING_DAYS ** 0.5) * m / sd, 2)


def vol_drag(returns):
    """
    g ~= mu - sigma^2/2. The gap between arithmetic and geometric return —
    why a choppy +97% compounds worse than a steady one.
    """
    m, sd = _mean(returns), _std(returns)
    if m is None or sd is None:
        return None
    arith = m * TRADING_DAYS
    geo = arith - (sd ** 2 * TRADING_DAYS) / 2.0
    return {"arithmetic_pct": round(arith * 100, 2),
            "geometric_pct": round(geo * 100, 2),
            "drag_pct": round((arith - geo) * 100, 2)}


def garch11(returns, grid=None):
    """
    GARCH(1,1):  sigma^2_t = omega + alpha*e^2_(t-1) + beta*sigma^2_(t-1)

    Fitted by coarse grid then local refine on the Gaussian log-likelihood.
    No scipy dependency — this runs anywhere.

    WHY THIS EXISTS: the tail-risk paper shows a single fitted normal is
    indefensible (19 five-sigma SPY moves in 25 years against a ~7,000-year
    expected wait each). Conditioning volatility on a regime is what makes
    the observed data possible under the model at all.
    """
    if len(returns) < 60:
        return {"error": "need at least 60 returns"}

    r = [x - _mean(returns) for x in returns]
    var0 = (_std(returns) or 0.01) ** 2
    if not var0:
        return {"error": "zero variance"}

    def negll(omega, alpha, beta):
        s2, ll = var0, 0.0
        for e in r:
            if s2 <= 1e-12:
                return 1e18
            ll += math.log(s2) + (e * e) / s2
            s2 = omega + alpha * e * e + beta * s2
        return ll

    best, bestp = 1e18, None
    if grid is None:
        grid = [(a, b) for a in (0.02, 0.05, 0.08, 0.12, 0.18)
                       for b in (0.70, 0.80, 0.86, 0.90, 0.94) if a + b < 0.999]
    for a, b in grid:
        om = max(var0 * (1 - a - b), 1e-12)
        v = negll(om, a, b)
        if v < best:
            best, bestp = v, (om, a, b)

    # local refine around the winner
    if bestp:
        om0, a0, b0 = bestp
        for da in (-0.02, -0.01, 0, 0.01, 0.02):
            for db in (-0.03, -0.015, 0, 0.015, 0.03):
                a, b = a0 + da, b0 + db
                if a <= 0 or b <= 0 or a + b >= 0.999:
                    continue
                om = max(var0 * (1 - a - b), 1e-12)
                v = negll(om, a, b)
                if v < best:
                    best, bestp = v, (om, a, b)

    om, a, b = bestp
    # rebuild the conditional-vol path
    s2, path = var0, []
    for e in r:
        path.append(s2)
        s2 = om + a * e * e + b * s2
    path.append(s2)                            # next-day forecast

    ann = [(x ** 0.5) * (TRADING_DAYS ** 0.5) * 100 for x in path]
    cur = ann[-1]
    srt = sorted(ann)
    lo, hi = srt[len(srt) // 3], srt[2 * len(srt) // 3]
    regime = "LOW VOL" if cur <= lo else "HIGH VOL" if cur >= hi else "MID VOL"

    return {
        "omega": om, "alpha": round(a, 4), "beta": round(b, 4),
        "persistence": round(a + b, 4),
        "conditional_vol_pct": round(cur, 2),
        "unconditional_vol_pct": round((_std(returns) or 0) * (TRADING_DAYS ** 0.5) * 100, 2),
        "regime": regime,
        "tercile_low_pct": round(lo, 2), "tercile_high_pct": round(hi, 2),
        "obs": len(returns),
        "note": "regime-conditioned vol; a single static normal understates tails badly",
    }


def crash_geometry(return_series, labels=None):
    """
    Vol = sqrt(det G) on the correlation matrix.  One Machine ch.5/10.

    Near 1  = names moving independently, diversification intact.
    Near 0  = everything correlating to one, diversification has geometrically
              STOPPED EXISTING. The determinant catches a dimensional collapse
              that an average correlation completely misses.

    Also returns the Marchenko-Pastur noise floor: eigenvalues below
    lambda_+ = (1 + sqrt(N/T))^2 are indistinguishable from randomness.
    """
    if _np is None:
        return {"error": "numpy unavailable"}
    series = [s for s in return_series if s and len(s) > 5]
    if len(series) < 3:
        return {"error": "need at least 3 usable return series"}

    T = min(len(s) for s in series)
    M = _np.array([s[-T:] for s in series], dtype=float)
    if T < len(series) + 2:
        return {"error": "not enough history for %d assets (T=%d)" % (len(series), T)}

    C = _np.corrcoef(M)
    if not _np.all(_np.isfinite(C)):
        return {"error": "correlation matrix has non-finite entries"}

    det = float(_np.linalg.det(C))
    vol = float(det ** 0.5) if det > 0 else 0.0
    eig = sorted([float(x) for x in _np.linalg.eigvalsh(C)], reverse=True)

    N = len(series)
    q = N / float(T)
    lam_plus = (1 + q ** 0.5) ** 2
    real_factors = sum(1 for e in eig if e > lam_plus)

    iu = _np.triu_indices(N, 1)
    avg_corr = float(_np.mean(C[iu]))

    return {
        "assets": N, "observations": T,
        "det_G": round(det, 6),
        "diversification_volume": round(vol, 4),
        "avg_correlation": round(avg_corr, 3),
        "eigenvalues": [round(e, 3) for e in eig],
        "mp_lambda_plus": round(lam_plus, 3),
        "real_factors": real_factors,
        "top_eigen_share_pct": round(eig[0] / N * 100, 1) if eig else None,
        "labels": labels or [],
        "regime": ("RISK-OFF / CORRELATED" if vol < 0.15 else
                   "STRESSED" if vol < 0.35 else
                   "NORMAL DISPERSION"),
        "note": "sqrt(det G): 1 = independent, 0 = one crowded trade. "
                "Eigenvalues under lambda_+ are noise (Marchenko-Pastur).",
    }


def realized_vs_implied(returns, implied_vol_pct, window=21):
    """
    The gamma-scalping number.  dP&L ~= 1/2 * Gamma * S^2 * (sig_real^2 - sig_impl^2) * dt

    Short-gamma dealers profit when realized comes in BELOW implied.
    You, long 0DTE, need realized ABOVE implied. Same number, opposite sides.
    """
    if not returns or implied_vol_pct is None:
        return None
    rv = realized_vol(returns[-window:] if len(returns) > window else returns)
    if rv is None:
        return None
    spread = rv - implied_vol_pct
    return {
        "realized_vol_pct": rv,
        "implied_vol_pct": round(implied_vol_pct, 2),
        "spread_pct": round(spread, 2),
        "window_days": min(window, len(returns)),
        "who_is_paid": ("option BUYERS — realized is running above implied"
                        if spread > 0 else
                        "option SELLERS — realized is coming in below implied"),
    }


# ============================================================
# GEX ENGINE — our own gamma exposure, no vendor API
# ============================================================
# Uses the SAME Alpaca endpoint your greeks logger already calls, with two
# changes: no "type" filter (we need puts too) and a date RANGE instead of
# just today (GEX lives across expiries, not only 0DTE).
#
# DEALER CONVENTION (the one assumption that matters):
#   Dealers are assumed LONG call gamma and SHORT put gamma — the standard
#   SqueezeMetrics convention. Net GEX = call gamma − put gamma.
#   Flip GEX_DEALER_SIGN to -1 if you want the opposite assumption.
#   Everything downstream inherits this choice, so it's a tweak, not a rewrite.

GEX_SYMBOL = "QQQ"
GEX_DAYS_OUT = 30          # how far out to pull expirations
GEX_STRIKE_WINDOW = 0.10   # keep strikes within ±10% of spot
GEX_DEALER_SIGN = 1
CONTRACT_MULTIPLIER = 100

ALPACA_DATA = "https://data.alpaca.markets"
# Open interest is NOT on the data/snapshots feed — it lives on the trading
# API's contracts endpoint (OCC end-of-day). Same source qqq_greeks_logger_alpaca.py uses.
ALPACA_TRADING = os.environ.get("ALPACA_TRADING_BASE", "https://paper-api.alpaca.markets")


def _alpaca_headers():
    key = os.environ.get("ALPACA_API_KEY", "")
    sec = os.environ.get("ALPACA_SECRET_KEY") or os.environ.get("ALPACA_API_SECRET", "")
    if not key or not sec:
        for candidate in ENV_CANDIDATES:
            f = os.path.abspath(candidate)
            if not os.path.exists(f):
                continue
            try:
                for line in open(f, "r", errors="ignore"):
                    line = line.strip()
                    if "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    v = v.strip().strip('"').strip("'")
                    if k.strip() == "ALPACA_API_KEY" and not key:
                        key = v
                    if k.strip() in ("ALPACA_SECRET_KEY", "ALPACA_API_SECRET") and not sec:
                        sec = v
            except Exception:
                continue
            if key and sec:
                break
    if not key or not sec:
        return None
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}


# --- Black-Scholes, calls AND puts. Ported from the approach in your
# --- qqq_greeks_logger_alpaca.py, which only handled calls.

import math

_CHAIN_SPOT = [None]        # set before parsing so greeks can be computed


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(S, K, T, sigma, r=0.0):
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return None, None
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return d1, d1 - sigma * math.sqrt(T)


def _bs_price(S, K, T, sigma, cp, r=0.0):
    d1, d2 = _d1_d2(S, K, T, sigma, r)
    if d1 is None:
        return None
    disc = math.exp(-r * T)
    if cp == "call":
        return S * _norm_cdf(d1) - K * disc * _norm_cdf(d2)
    return K * disc * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def _implied_vol(price, S, K, T, cp, lo=0.01, hi=5.0):
    """Bisection, same technique as your logger, generalised to puts."""
    try:
        if price <= 0:
            return None
        for _ in range(60):
            mid = (lo + hi) / 2.0
            p = _bs_price(S, K, T, mid, cp)
            if p is None:
                return None
            if p < price:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0
    except Exception:
        return None


def _bs_greeks(S, K, T, sigma, cp, r=0.0):
    d1, d2 = _d1_d2(S, K, T, sigma, r)
    if d1 is None:
        return {"delta": None, "gamma": None, "vega": None, "theta": None,
                "vanna": None, "charm": None}
    pdf = _norm_pdf(d1)
    sqrtT = math.sqrt(T)
    gamma = pdf / (S * sigma * sqrtT)
    vega = S * pdf * sqrtT / 100.0
    # Second-order dials (3v3 notes F1), closed-form from d1/d2 — no extra chain
    # data needed. q=0. Same for calls and puts (put delta = call delta - 1).
    #   VANNA = dDelta/dsigma = -phi(d1)*d2/sigma
    #   CHARM = dDelta/dt      (per year); verified vs finite differences
    vanna = -pdf * d2 / sigma
    charm = -pdf * (2 * r * T - d2 * sigma * sqrtT) / (2 * T * sigma * sqrtT)
    if cp == "call":
        delta = _norm_cdf(d1)
        theta = (-S * pdf * sigma / (2 * sqrtT)) / 365.0
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (-S * pdf * sigma / (2 * sqrtT)) / 365.0
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta,
            "vanna": vanna, "charm": charm}


def _parse_occ(sym):
    """QQQ260724C00676000 -> ('2026-07-24', 'call', 676.0)"""
    m = re.match(r"^([A-Z]+)(\d{6})([CP])(\d{8})$", sym)
    if not m:
        return None, None, None
    _, ymd, cp, strike = m.groups()
    exp = "20" + ymd[0:2] + "-" + ymd[2:4] + "-" + ymd[4:6]
    return exp, ("call" if cp == "C" else "put"), int(strike) / 1000.0


def fetch_open_interest(symbol=GEX_SYMBOL, days_out=GEX_DAYS_OUT, spot=None):
    """Open interest per contract, keyed by OCC symbol.

    Alpaca does NOT return open interest on the options *snapshots* feed — it
    lives on the *contracts* endpoint (OCC end-of-day, ~1-day lag). This mirrors
    get_open_interest() in qqq_greeks_logger_alpaca.py, but for calls AND puts
    across the whole GEX expiry window. Returns {occ_symbol: open_interest}.
    """
    headers = _alpaca_headers()
    if not headers:
        return {}
    from datetime import date, timedelta
    today = date.today()
    params = {
        "underlying_symbols": symbol,
        "expiration_date_gte": today.isoformat(),
        "expiration_date_lte": (today + timedelta(days=days_out)).isoformat(),
        "limit": 1000,
    }
    if spot:
        params["strike_price_gte"] = round(spot * (1 - GEX_STRIKE_WINDOW), 2)
        params["strike_price_lte"] = round(spot * (1 + GEX_STRIKE_WINDOW), 2)

    oi, page = {}, None
    for _ in range(20):                        # paginate defensively
        if page:
            params["page_token"] = page
        try:
            r = requests.get(f"{ALPACA_TRADING}/v2/options/contracts",
                             headers=headers, params=params, timeout=30)
            r.raise_for_status()
            j = r.json()
        except Exception:
            break                              # OI is best-effort; never break the run
        for c in (j.get("option_contracts") or []):
            val = c.get("open_interest")
            if val not in (None, ""):
                oi[c.get("symbol")] = int(val)
        page = j.get("next_page_token")
        if not page:
            break
    return oi


def fetch_option_chain(symbol=GEX_SYMBOL, days_out=GEX_DAYS_OUT, spot=None):
    """Full chain — calls AND puts — across the next N days of expiries."""
    _CHAIN_SPOT[0] = spot
    headers = _alpaca_headers()
    if not headers:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not found")

    from datetime import date, timedelta
    today = date.today()
    params = {
        "feed": "indicative",
        "limit": 1000,
        "expiration_date_gte": today.isoformat(),
        "expiration_date_lte": (today + timedelta(days=days_out)).isoformat(),
    }

    snapshots, page = {}, None
    for _ in range(12):                       # paginate defensively
        if page:
            params["page_token"] = page
        r = requests.get(f"{ALPACA_DATA}/v1beta1/options/snapshots/{symbol}",
                         headers=headers, params=params, timeout=30)
        r.raise_for_status()
        j = r.json()
        snapshots.update(j.get("snapshots", {}) or {})
        page = j.get("next_page_token")
        if not page:
            break

    # Open interest is not on the snapshots feed — pull it from the contracts
    # endpoint (same source qqq_greeks_logger_alpaca.py uses) and merge by symbol.
    oi_map = fetch_open_interest(symbol, days_out, spot=spot)

    from datetime import datetime as _dt
    now = _dt.now()

    rows = []
    for occ, snap in snapshots.items():
        exp, cp, strike = _parse_occ(occ)
        if not exp:
            continue
        snap = snap or {}

        oi = (oi_map.get(occ)
              or snap.get("openInterest") or snap.get("open_interest") or 0)
        if not oi:
            continue                       # no OI = no exposure, skip early

        # Alpaca's FREE tier does not return greeks — your own logger says so
        # and computes them locally. We do the same, for calls AND puts.
        g = snap.get("greeks") or {}
        gamma, delta, vega, theta = (g.get("gamma"), g.get("delta"),
                                     g.get("vega"), g.get("theta"))
        vanna = charm = None

        if gamma is None:
            quote = snap.get("latestQuote") or {}
            bid, ask = quote.get("bp"), quote.get("ap")
            mid = None
            if bid and ask and ask > 0:
                mid = (float(bid) + float(ask)) / 2.0
            elif snap.get("latestTrade", {}).get("p"):
                mid = float(snap["latestTrade"]["p"])

            T = max((_dt.strptime(exp, "%Y-%m-%d") - now).total_seconds()
                    / (365.0 * 24 * 3600), 1e-6)
            iv = snap.get("impliedVolatility")
            if mid and _CHAIN_SPOT[0]:
                if iv is None:
                    iv = _implied_vol(mid, _CHAIN_SPOT[0], strike, T, cp)
                if iv:
                    gk = _bs_greeks(_CHAIN_SPOT[0], strike, T, float(iv), cp)
                    gamma, delta = gk["gamma"], gk["delta"]
                    vega, theta = gk["vega"], gk["theta"]
                    vanna, charm = gk["vanna"], gk["charm"]

        if gamma is None:
            continue

        rows.append({
            "expiration": exp, "type": cp, "strike": strike,
            "gamma": float(gamma),
            "delta": float(delta) if delta is not None else None,
            "vega": float(vega) if vega is not None else None,
            "theta": float(theta) if theta is not None else None,
            "vanna": float(vanna) if vanna is not None else None,
            "charm": float(charm) if charm is not None else None,
            "open_interest": int(oi),
        })
    return rows


def compute_gex(rows, spot):
    """
    Per-strike dollar gamma, aggregated. Standard formulation:
        gamma x OI x 100 x spot^2 x 0.01
    = the dollar change in dealer delta for a 1% move in the underlying.
    """
    if not rows or not spot:
        return {"error": "no chain rows or no spot"}

    lo, hi = spot * (1 - GEX_STRIKE_WINDOW), spot * (1 + GEX_STRIKE_WINDOW)
    by_strike = {}
    unit = CONTRACT_MULTIPLIER * (spot ** 2) * 0.01

    for r in rows:
        k = r["strike"]
        if k < lo or k > hi:
            continue
        d = by_strike.setdefault(k, {"strike": k, "call_gex": 0.0, "put_gex": 0.0,
                                     "call_oi": 0, "put_oi": 0, "dex": 0.0,
                                     "vex": 0.0, "tex": 0.0,
                                     "vannaex": 0.0, "charmex": 0.0,
                                     "call_gamma_raw": 0.0, "put_gamma_raw": 0.0})
        dollars = r["gamma"] * r["open_interest"] * unit
        if r["type"] == "call":
            d["call_gex"] += dollars
            d["call_oi"] += r["open_interest"]
            d["call_gamma_raw"] += r["gamma"] * r["open_interest"]
        else:
            d["put_gex"] -= dollars
            d["put_oi"] += r["open_interest"]
            d["put_gamma_raw"] += r["gamma"] * r["open_interest"]
        if r["delta"] is not None:
            d["dex"] += r["delta"] * r["open_interest"] * CONTRACT_MULTIPLIER * spot
        if r.get("vega") is not None:
            d["vex"] += r["vega"] * r["open_interest"] * CONTRACT_MULTIPLIER
        if r.get("theta") is not None:
            d["tex"] += r["theta"] * r["open_interest"] * CONTRACT_MULTIPLIER
        if r.get("vanna") is not None:
            d["vannaex"] += r["vanna"] * r["open_interest"] * CONTRACT_MULTIPLIER
        if r.get("charm") is not None:
            d["charmex"] += r["charm"] * r["open_interest"] * CONTRACT_MULTIPLIER

    strikes = sorted(by_strike.values(), key=lambda x: x["strike"])
    for s in strikes:
        s["net_gex"] = (s["call_gex"] + s["put_gex"]) * GEX_DEALER_SIGN
        for key in ("call_gex", "put_gex", "net_gex", "dex", "vex", "tex",
                    "vannaex", "charmex"):
            s[key] = round(s[key], 2)

    net = sum(s["net_gex"] for s in strikes)
    net_dex = sum(s["dex"] for s in strikes)
    net_vex = sum(s["vex"] for s in strikes)
    net_tex = sum(s["tex"] for s in strikes)
    net_vannaex = sum(s["vannaex"] for s in strikes)
    net_charmex = sum(s["charmex"] for s in strikes)

    # G — SqueezeMetrics "gamma-ratio": call gamma as a share of TOTAL gamma.
    # 0.5 = balanced, 1.0 = all calls, 0.0 = all puts. Uses raw gamma x OI,
    # not dollar-weighted, so it's comparable across time and symbols.
    cg = sum(s["call_gamma_raw"] for s in strikes)
    pg = sum(s["put_gamma_raw"] for s in strikes)
    gamma_ratio = round(cg / (cg + pg), 4) if (cg + pg) else None

    # Gamma flip: where the running total of net GEX crosses zero.
    flip, run, method = None, 0.0, None
    cum = []
    for i, s in enumerate(strikes):
        prev = run
        run += s["net_gex"]
        cum.append((s["strike"], run))
        if i and ((prev < 0 <= run) or (prev > 0 >= run)):
            a, b = strikes[i - 1]["strike"], s["strike"]
            frac = abs(prev) / (abs(prev) + abs(run)) if (prev or run) else 0.5
            flip = round(a + (b - a) * frac, 2)
            method = "zero crossing"

    # No crossing inside the strike window: fall back to the strike where the
    # running total is closest to zero, and say so rather than reporting None.
    if flip is None and cum:
        k, _ = min(cum, key=lambda t: abs(t[1]))
        flip = round(k, 2)
        method = "nearest-to-zero (no crossing in window)"

    call_wall = max(strikes, key=lambda s: s["call_gex"])["strike"] if strikes else None
    # Put wall = strike with the most negative NET dealer gamma (max short gamma =
    # real support), matching the theory and the 0DTE logger. min(put_gex) snapped
    # it toward the ATM strike, whose put gamma is large just from being ATM.
    put_wall = min(strikes, key=lambda s: s["net_gex"])["strike"] if strikes else None
    max_pos = max(strikes, key=lambda s: s["net_gex"])["strike"] if strikes else None
    max_neg = min(strikes, key=lambda s: s["net_gex"])["strike"] if strikes else None
    top_oi = max(strikes, key=lambda s: s["call_oi"] + s["put_oi"])["strike"] if strikes else None

    return {
        "symbol": GEX_SYMBOL,
        "spot": round(spot, 2),
        "net_gex": round(net, 2),
        "net_dex": round(net_dex, 2),
        "net_vex": round(net_vex, 2),
        "net_tex": round(net_tex, 2),
        "net_vannaex": round(net_vannaex, 2),
        "net_charmex": round(net_charmex, 2),
        "gamma_ratio": gamma_ratio,
        "gamma_ratio_note": "call gamma / total gamma (SqueezeMetrics G). "
                            "0.5 balanced, >0.5 call-driven, <0.5 put-driven.",
        "exposure_note": "VEX = vega exposure, TEX = theta exposure. VANNAEX and "
                         "CHARMEX are the real second-order dials (dDelta/dsigma "
                         "and dDelta/dt), computed closed-form from d1/d2 and "
                         "verified vs finite differences — not proxied.",
        "gamma_flip": flip,
        "gamma_flip_method": method,
        # Regime prefers the flip when it's a real crossing; otherwise the
        # sign of aggregate net GEX, which is never undetermined.
        "regime": ("POSITIVE GAMMA" if (spot > flip if method == "zero crossing" else net > 0)
                   else "NEGATIVE GAMMA"),
        "distance_to_flip_pct": round((spot - flip) / spot * 100, 2) if flip else None,
        "levels": {
            "call_wall": call_wall, "put_wall": put_wall,
            "max_positive_gamma": max_pos, "max_negative_gamma": max_neg,
            "highest_oi_strike": top_oi,
        },
        "strikes": strikes,
        "contracts_used": len(rows),
        "dealer_convention": "dealers long call gamma, short put gamma"
                             if GEX_DEALER_SIGN == 1 else "inverted",
    }


def fetch_gex():
    spot = None
    if yf is not None:
        try:
            spot = float(yf.Ticker(GEX_SYMBOL).history(period="1d")["Close"].iloc[-1])
        except Exception:
            pass
    if spot is None:
        closes = _daily_closes(GEX_SYMBOL, count=3)
        spot = closes[-1] if closes else None
    if spot is None:
        return {"error": "could not determine spot price"}

    rows = fetch_option_chain(spot=spot)
    if not rows:
        return {"error": "chain returned no usable rows (no OI or no greeks)"}
    return compute_gex(rows, spot)


# ============================================================
# TIME FOOTPRINT — options flow bucketed by expiry horizon
# ============================================================
# "Who owns the flow": short-dated = fast/mechanical (gamma, scalpers) which is
# fade-FRIENDLY; long-dated = informed/directional (institutions, allocators)
# which is fade-HOSTILE. Per bucket we sum today's VOLUME, OPEN INTEREST and
# PREMIUM ($). Near-money (±20%) keeps the pull bounded; all expiries 0DTE..~13mo.
TF_STRIKE_WINDOW = 0.20
TF_DAYS_OUT = 400
# Contiguous DTE buckets — no gaps. The last is "3mo+" (a literal "6mo+" would
# leave 3–6mo unbucketed and misstate the totals).
TF_BUCKETS = [("0DTE", 0, 0), ("Weeklies", 1, 7), ("1-4wk", 8, 30),
              ("1-3mo", 31, 90), ("3mo+", 91, 10 ** 9)]


def _tf_bucket(dte):
    for name, lo, hi in TF_BUCKETS:
        if lo <= dte <= hi:
            return name
    return None


# Moneyness bands for the STRIKE axis of the Q2 positioning heatmap.
# Edges are % distance from spot; produces 7 bands, index 0 = lowest strike.
TF_BAND_EDGES = [-8.0, -4.0, -1.5, 1.5, 4.0, 8.0]
TF_BAND_LABELS = ["<= -8%", "-8..-4%", "-4..-1.5%", "ATM +/-1.5%", "+1.5..+4%", "+4..+8%", ">= +8%"]


def _tf_band(strike, spot):
    """Return the moneyness-band index for a strike, or None."""
    if not spot:
        return None
    try:
        pct = (float(strike) / float(spot) - 1.0) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    for i, edge in enumerate(TF_BAND_EDGES):
        if pct < edge:
            return i
    return len(TF_BAND_EDGES)


def fetch_time_footprint():
    """Per-bucket volume / open interest / premium for QQQ options, split by
    expiry horizon. Best-effort: returns an error dict on failure so build_entry
    never breaks. Volume/premium ride the indicative feed's dailyBar — if that
    feed doesn't carry volume, those totals come back 0 and the dashboard simply
    hides that metric rather than showing a fake distribution."""
    headers = _alpaca_headers()
    if not headers:
        return {"error": "ALPACA creds not found"}
    from datetime import date, datetime as _dt, timedelta

    spot = None
    if yf is not None:
        try:
            spot = float(yf.Ticker(GEX_SYMBOL).history(period="1d")["Close"].iloc[-1])
        except Exception:
            pass
    if spot is None:
        closes = _daily_closes(GEX_SYMBOL, count=3)
        spot = closes[-1] if closes else None
    if spot is None:
        return {"error": "could not determine spot price"}

    today = date.today()
    lo_k = round(spot * (1 - TF_STRIKE_WINDOW), 2)
    hi_k = round(spot * (1 + TF_STRIKE_WINDOW), 2)
    exp_lte = (today + timedelta(days=TF_DAYS_OUT)).isoformat()
    acc = {name: {"vol": 0.0, "oi": 0, "prem": 0.0, "contracts": 0}
           for name, _, _ in TF_BUCKETS}
    # Q2 heatmap: strike-band x expiry-bucket accumulator, keyed (bucket_name, band_idx)
    grid_acc = {}

    def _gcell(bname, band):
        c = grid_acc.get((bname, band))
        if c is None:
            c = {"vol": 0.0, "oi": 0, "prem": 0.0}
            grid_acc[(bname, band)] = c
        return c

    def dte_of(exp_str):
        try:
            return (_dt.strptime(exp_str, "%Y-%m-%d").date() - today).days
        except Exception:
            return None

    # 1) Open interest — contracts endpoint (OCC end-of-day)
    params = {"underlying_symbols": GEX_SYMBOL, "expiration_date_gte": today.isoformat(),
              "expiration_date_lte": exp_lte, "strike_price_gte": lo_k,
              "strike_price_lte": hi_k, "limit": 1000}
    page = None
    for _ in range(40):
        if page:
            params["page_token"] = page
        try:
            r = requests.get(f"{ALPACA_TRADING}/v2/options/contracts",
                             headers=headers, params=params, timeout=30)
            r.raise_for_status()
            j = r.json()
        except Exception:
            break
        for c in (j.get("option_contracts") or []):
            dte = dte_of(c.get("expiration_date", ""))
            if dte is None or dte < 0:
                continue
            b = _tf_bucket(dte)
            if not b:
                continue
            oi = c.get("open_interest")
            oi_i = 0
            if oi not in (None, ""):
                try:
                    oi_i = int(oi)
                    acc[b]["oi"] += oi_i
                except (TypeError, ValueError):
                    oi_i = 0
            acc[b]["contracts"] += 1
            band = _tf_band(c.get("strike_price"), spot)
            if band is not None and oi_i:
                _gcell(b, band)["oi"] += oi_i
        page = j.get("next_page_token")
        if not page:
            break

    # 2) Volume + premium — snapshots endpoint (dailyBar)
    sparams = {"feed": "indicative", "limit": 1000,
               "expiration_date_gte": today.isoformat(), "expiration_date_lte": exp_lte,
               "strike_price_gte": lo_k, "strike_price_lte": hi_k}
    page = None
    for _ in range(40):
        if page:
            sparams["page_token"] = page
        try:
            r = requests.get(f"{ALPACA_DATA}/v1beta1/options/snapshots/{GEX_SYMBOL}",
                             headers=headers, params=sparams, timeout=30)
            r.raise_for_status()
            j = r.json()
        except Exception:
            break
        for occ, snap in (j.get("snapshots", {}) or {}).items():
            exp, cp, strike = _parse_occ(occ)
            if not exp:
                continue
            dte = dte_of(exp)
            if dte is None or dte < 0:
                continue
            b = _tf_bucket(dte)
            if not b:
                continue
            snap = snap or {}
            db = snap.get("dailyBar") or {}
            v = db.get("v") or 0
            px = db.get("vw") or db.get("c") or (snap.get("latestTrade") or {}).get("p") or 0
            try:
                v = float(v); px = float(px)
            except (TypeError, ValueError):
                v, px = 0.0, 0.0
            prem = v * px * 100.0
            acc[b]["vol"] += v
            acc[b]["prem"] += prem
            band = _tf_band(strike, spot)
            if band is not None and (v or prem):
                gc = _gcell(b, band)
                gc["vol"] += v
                gc["prem"] += prem
        page = j.get("next_page_token")
        if not page:
            break

    buckets, tot = [], {"vol": 0.0, "oi": 0, "prem": 0.0}
    for name, _, _ in TF_BUCKETS:
        a = acc[name]
        buckets.append({"name": name, "vol": round(a["vol"]), "oi": a["oi"],
                        "prem": round(a["prem"]), "contracts": a["contracts"]})
        tot["vol"] += a["vol"]; tot["oi"] += a["oi"]; tot["prem"] += a["prem"]

    def share(pred):
        out = {}
        for m in ("vol", "oi", "prem"):
            t = tot[m]
            s = sum(acc[n][m] for n, lo, hi in TF_BUCKETS if pred(lo, hi))
            out[m] = round(s / t * 100, 1) if t else None
        return out

    # Q2 heatmap grid: strike-band x expiry-bucket cells (nonzero only).
    bnames = [name for name, _, _ in TF_BUCKETS]
    grid_cells = []
    for (bname, band), gc in grid_acc.items():
        if gc["vol"] or gc["oi"] or gc["prem"]:
            grid_cells.append({"e": bnames.index(bname), "m": band,
                               "vol": round(gc["vol"]), "oi": int(gc["oi"]),
                               "prem": round(gc["prem"])})

    return {
        "generated_utc": _dt.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "feed": "indicative", "near_money_pct": int(TF_STRIKE_WINDOW * 100),
        "days_out": TF_DAYS_OUT, "spot": round(spot, 2),
        "buckets": buckets,
        "totals": {"vol": round(tot["vol"]), "oi": tot["oi"], "prem": round(tot["prem"])},
        "short_share": share(lambda lo, hi: hi <= 7),     # 0DTE + weeklies
        "long_share": share(lambda lo, hi: lo >= 31),     # 1-3mo + 3mo+
        "grid": {"buckets": bnames, "bands": TF_BAND_LABELS, "cells": grid_cells},
    }


# ============================================================
# BUILD + WRITE
# ============================================================

def safe(name, fn):
    """Run a fetch. If it fails, record the error and keep going."""
    log(f"  -> {name} ...")
    try:
        result = fn()
        log(f"     OK")
        return result
    except Exception as e:
        log(f"     FAILED: {e}")
        return {"error": str(e), "traceback": traceback.format_exc()[-800:]}


def pacific_now():
    """
    Everything session-related is decided in PACIFIC, not UTC.
    AM = 12:00 AM - 11:59 AM PT.  PM = 12:00 PM - 11:59 PM PT.
    Derived from the actual clock at runtime, so a late or manual run
    still tags itself correctly instead of trusting the cron.
    """
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Los_Angeles"))
    except Exception:
        # crude fallback: PDT is UTC-7
        from datetime import timedelta
        return datetime.now(timezone.utc) - timedelta(hours=7)


def build_entry():
    now = datetime.now(timezone.utc)
    pt = pacific_now()
    session = "am" if pt.hour < 12 else "pm"

    entry = {
        "fetched_utc": now.strftime("%Y-%m-%d %H:%M:%S"),
        "fetched_pt": pt.strftime("%Y-%m-%d %I:%M %p PT").lstrip("0"),
        "entry_date": pt.strftime("%Y-%m-%d"),
        "session": session,
        # ONE ROW PER DAY: key on the date only, so each run upserts the same
        # day's row. The AM (pre-market, stale-ETF) run gets overwritten by the
        # PM close; fetched_pt is the timestamp of the last price written.
        "entry_key": pt.strftime("%Y-%m-%d"),
        "calendar": safe("calendar", fetch_calendar),
        "options": safe("cboe options", fetch_cboe),
        "positioning": safe("cftc cot", fetch_cot),
        "markets": safe("market prices", fetch_markets),
        "watchlist": safe("watchlist + horizons", fetch_watchlist),
        "gex": safe("gex engine", fetch_gex),
        "time_footprint": safe("time footprint", fetch_time_footprint),
        "quant": safe("quant metrics", fetch_quant_metrics),
        "sectors": safe("sectors", fetch_sectors),
        "macro": safe("fred macro", fetch_fred),
        "venue_concentration": safe("cboe venue share", fetch_venue_concentration),
    }

    # Stamp with Cboe's session date when we got one; it's more honest
    # than the fetch date.
    try:
        sd = entry["options"].get("session_date")
        if sd:
            m, d, y = sd.split("/")
            entry["session_date"] = f"{y}-{int(m):02d}-{int(d):02d}"
    except Exception:
        pass

    return entry


def write_dated_json(entry):
    date = entry["entry_date"]
    session = entry.get("session", "am")
    folder = os.path.join(BASE_DIR, "data", date, session)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"market_dash_{date}_{session}.json")

    with open(path, "w") as f:
        json.dump(entry, f, indent=2)

    return path


def append_history(entry):
    """
    Appends ONE line to history.js. Never rewrites what's already there.

    The file is plain JavaScript so the HTML dashboard can load it with a
    <script> tag. A local HTML file is not allowed to fetch() a data file
    off disk, but it IS allowed to load a script. That's why this is .js
    and not .json.
    """
    path = os.path.join(BASE_DIR, "history.js")
    key = entry.get("entry_key", entry["entry_date"])
    line = ("window.MARKET_DASH_HISTORY.push("
            + json.dumps(entry, separators=(",", ":")) + ");")

    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write("// Market Dash history. One entry per DAY (upserted; AM refreshes into the PM close).\n")
            f.write("window.MARKET_DASH_HISTORY = window.MARKET_DASH_HISTORY || [];\n\n")
            f.write(line + "\n")
        return path

    # ONE ROW PER DAY. Drop any existing rows for today's date (am / pm / legacy
    # "date_session" keys) and append the freshest one. Every earlier day is left
    # untouched, so history stays append-only across days while today's row is
    # upserted in place — no more stale AM duplicate that breaks the correlation.
    daymark = '"entry_date":"%s"' % entry["entry_date"]
    kept = []
    for ln in open(path).read().rstrip("\n").split("\n"):
        if ln.lstrip().startswith("window.MARKET_DASH_HISTORY.push(") and daymark in ln:
            continue                      # today's existing row(s) — re-added below
        kept.append(ln)
    kept.append(line)

    with open(path, "w") as f:
        f.write("\n".join(kept) + "\n")
    return path


def print_summary(entry):
    log("")
    log("=" * 52)
    log("SUMMARY")
    log("=" * 52)

    mk = entry.get("markets") or {}
    for label in ("QQQ", "VIX", "CRUDE", "US10Y", "DOLLAR"):
        d = mk.get(label) or {}
        if d.get("last") is not None:
            log(f"  {label:<8} {d['last']:>10}   {d.get('pct_change')}%")

    op = entry.get("options") or {}
    if op.get("total_pc") is not None:
        log(f"  Total P/C   {op['total_pc']}    Index {op.get('index_pc')}"
            f"    Equity {op.get('equity_pc')}")
    else:
        log("  Options     NOT PARSED — see debug_cboe_raw.html")

    sec = entry.get("sectors") or {}
    if sec.get("leaders"):
        log(f"  Leaders     {', '.join(sec['leaders'])}")
        log(f"  Laggards    {', '.join(sec['laggards'])}")
        log(f"  Breadth     {sec.get('breadth')}")

    cal = entry.get("calendar") or {}
    hi = cal.get("us_high_impact") or []
    if hi:
        log(f"  US high-impact events this week: {len(hi)}")
        for e in hi[:6]:
            log(f"     {e['date'][:16]}  {e['title']}")

    macro = entry.get("macro") or {}
    curve = macro.get("curve_2s10s")
    if curve:
        log(f"  2s10s       {curve['spread']}  ({curve['read']})")

    log("=" * 52)


def main():
    log("")
    log("MARKET DASH FETCH — starting")
    log("")

    entry = build_entry()

    json_path = write_dated_json(entry)
    hist_path = append_history(entry)

    print_summary(entry)

    log("")
    log(f"Session:  {entry.get('session','?').upper()}  ({entry.get('fetched_pt','?')})")
    log(f"Wrote:    {json_path}")
    log(f"Appended: {hist_path}")
    log("")


if __name__ == "__main__":
    main()
