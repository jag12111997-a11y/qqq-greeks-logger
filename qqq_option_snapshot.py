"""Export the latest complete calls/puts capture for the dashboard calculator."""
import datetime as dt
import json
import math
import os
from pathlib import Path

OPTION_SNAPSHOT_PATH = Path('market-dash/options_latest.json')


def number(value):
    if value is None or value == '' or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def write_option_snapshot(call_rows, put_rows, path=OPTION_SNAPSHOT_PATH):
    # Avoid dropping a user's selected side after a partially failed request.
    if not call_rows or not put_rows:
        return False
    contracts = []
    for kind, rows in [('call', call_rows), ('put', put_rows)]:
        for row in rows:
            strike, spot = number(row.get('strike')), number(row.get('spot'))
            expiry = row.get('expiration')
            if not expiry or strike is None or spot is None or spot <= 0:
                continue
            bid, ask = number(row.get('bid')), number(row.get('ask'))
            mid = (bid + ask) / 2 if bid is not None and ask is not None and 0 < bid <= ask else None
            contract = dict(id=f'{expiry}-{kind}-{strike:g}', kind=kind,
                            expiration=expiry, strike=strike, spot=spot, bid=bid, ask=ask,
                            mid=mid, stamp=row.get('run_time'), quote_stamp=row.get('quote_time'))
            for key in ('delta','gamma','theta','iv','volume'):
                contract[key] = number(row.get(key))
            if contract['iv'] is not None:
                contract['iv'] *= 100
            contracts.append(contract)
    if not contracts or {c['kind'] for c in contracts} != {'call', 'put'}:
        return False
    contracts.sort(key=lambda c:(c['expiration'],c['strike'],c['kind']))
    payload = dict(symbol='QQQ', feed='indicative',
                   generated_utc=max(dt.datetime.strptime(c['stamp'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=dt.timezone.utc) for c in contracts).isoformat(), contracts=contracts)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, separators=(',', ':'), allow_nan=False))
    os.replace(temp, path)
    return True
