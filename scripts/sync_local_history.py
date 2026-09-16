#!/usr/bin/env python3
"""Publish appended local option history using a separate checkout, never the user's index."""
import argparse
import csv
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

NAME = re.compile(r'qqq_greeks_(calls|puts)_(\d{4}-\d{2}-\d{2})_(am|pm)\.csv$')


def merge_csv(remote, local):
    def read(text):
        reader = csv.DictReader(io.StringIO(text))
        fields = reader.fieldnames or []
        if not {'run_time', 'expiration', 'strike', 'spot', 'delta', 'gamma'} <= set(fields):
            raise ValueError('Unsupported history CSV header')
        rows = [r for r in reader if None not in r and all(v is not None for v in r.values())]
        return fields, rows
    fields, local_rows = read(local)
    remote_fields, remote_rows = read(remote) if remote.strip() else (fields, [])
    if remote_fields != fields:
        raise ValueError('History headers differ; refusing to replace remote data')
    key = lambda r: (r['run_time'], r['expiration'], r['strike'])
    rows = {key(r): r for r in local_rows}
    rows.update({key(r): r for r in remote_rows})  # Existing published records win conflicts.
    if len(rows) == len(remote_rows):
        return remote
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows[k] for k in sorted(rows))
    return output.getvalue()


def sync(repo, state):
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        marker = state / 'published.json'
        previous = json.loads(marker.read_text()) if marker.exists() else {}
        current, changed = {}, {}
        for path in sorted((repo / 'data').glob('*/*/*.csv')):
            match = NAME.fullmatch(path.name)
            if not match or path.parent.name != match[3] or path.parent.parent.name != match[2]:
                continue
            raw = path.read_bytes()
            relative = path.relative_to(repo).as_posix()
            current[relative] = hashlib.sha256(raw).hexdigest()
            if previous.get(relative) != current[relative]:
                changed[relative] = raw.decode('utf-8-sig')
        if not changed:
            print('Local CSV history unchanged.', flush=True)
            return
        def git(*args, cwd=None):
            return subprocess.run(['/usr/bin/git', *args], cwd=cwd, check=True,
                                  capture_output=True, text=True, timeout=180,
                                  env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}).stdout.strip()
        remote = git('-C', str(repo), 'remote', 'get-url', 'origin')
        checkout = state / 'checkout'
        if not (checkout / '.git').exists():
            # Clone into a temporary directory so interrupted clones can be retried.
            with tempfile.TemporaryDirectory(dir=state) as temporary:
                destination = Path(temporary) / 'repo'
                git('clone', '--depth=1', '--single-branch', '--branch=main', remote, str(destination))
                destination.rename(checkout)
        git('config', 'user.name', 'Local CSV history sync', cwd=checkout)
        git('config', 'user.email', 'local-csv-sync@users.noreply.github.com', cwd=checkout)
        for attempt in range(3):
            git('fetch', '--depth=1', 'origin', 'main', cwd=checkout)
            # Only this script's dedicated checkout is reset; the source repo is never changed.
            git('reset', '--hard', 'origin/main', cwd=checkout)
            for relative, text in changed.items():
                target = checkout / relative
                remote_text = target.read_text() if target.exists() else ''
                merged = merge_csv(remote_text, text)
                if merged != remote_text:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(merged)
            git('add', '--', *changed.keys(), cwd=checkout)
            if not git('diff', '--cached', '--name-only', cwd=checkout):
                break
            git('commit', '-m', 'Sync local option CSV history', cwd=checkout)
            try:
                git('push', 'origin', 'HEAD:main', cwd=checkout)
                break
            except subprocess.CalledProcessError:
                if attempt == 2:
                    raise
        temporary = marker.with_suffix('.tmp')
        temporary.write_text(json.dumps(current))
        temporary.replace(marker)
        print(f'Synced {len(changed)} local history files; local CSVs unchanged.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--state', required=True, type=Path)
    args = parser.parse_args()
    sync(args.repo.resolve(), args.state.resolve())
