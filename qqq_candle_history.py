"""Fetch real QQQ candle history using the logger's existing Alpaca data access."""
import datetime as dt
import json
import math
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

HISTORY_PATH = Path('market-dash/qqq_candle_history.json')
NY = ZoneInfo('America/New_York')


def fetch_bars(headers, timeframe, start, end):
    rows, token = [], None
    for _ in range(30):
        params = dict(timeframe=timeframe, start=start.isoformat(), end=end.isoformat(),
                      feed='iex', adjustment='split', sort='asc', limit=10000)
        if token:
            params['page_token'] = token
        response = requests.get('https://data.alpaca.markets/v2/stocks/QQQ/bars',
                                headers=headers, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        rows.extend(payload.get('bars') or [])
        token = payload.get('next_page_token')
        if not token:
            return rows
    raise RuntimeError('Candle history pagination was incomplete; keeping previous history')


def normalize_bars(rows, intraday=False):
    candles = {}
    for row in rows:
        stamp = dt.datetime.fromisoformat(row['t'].replace('Z', '+00:00'))
        if intraday:
            local = stamp.astimezone(NY)
            minutes = local.hour * 60 + local.minute
            if local.weekday() >= 5 or not 570 <= minutes < 960:
                continue
        values = [float(row[k]) for k in ('o', 'h', 'l', 'c', 'v')]
        if not all(math.isfinite(v) for v in values):
            continue
        o, h, l, c, v = values
        if min(o, h, l, c) <= 0 or v < 0 or h < max(o, c, l) or l > min(o, c):
            continue
        time = int(stamp.timestamp())
        candles[time] = dict(time=time, open=o, high=h, low=l, close=c, volume=v)
    return [candles[t] for t in sorted(candles)]


def update_history(headers, path=HISTORY_PATH, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(NY).replace(hour=0, minute=0, second=0, microsecond=0)
    # Completed daily sessions only; current-session bars are merged by the chart.
    daily = normalize_bars(fetch_bars(headers, '1Day', today - dt.timedelta(days=5*366),
                                     today - dt.timedelta(microseconds=1)))
    minute = normalize_bars(fetch_bars(headers, '1Min', today - dt.timedelta(days=45), now),
                            intraday=True)
    if not daily or not minute:
        raise RuntimeError('Candle history returned no usable bars; keeping previous history')
    payload = dict(symbol='QQQ', feed='iex', adjustment='split', generated_utc=now.isoformat(),
                   daily=daily, minute=minute)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, separators=(',', ':'), allow_nan=False))
    os.replace(temp, path)
    return payload
