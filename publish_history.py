"""Publish captures from an isolated checkout; never rebase the running logger."""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
from scripts.sync_local_history import merge_csv

LIVE_PATHS = ['market-dash/gex_live.json', 'market-dash/gex_intraday.json',
              'market-dash/auction_live.json', 'market-dash/qqq_candle_history.json',
              'market-dash/options_latest.json']


def timestamp(raw):
    try:
        data = json.loads(raw)
        value = data.get('generated_utc') or data.get('updated_utc')
        if not value:
            return None
        stamp = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.replace(tzinfo=datetime.timezone.utc) if stamp.tzinfo is None else stamp
    except (ValueError, TypeError, AttributeError):
        return None


def publish(repo, paths, attempts=5):
    repo = Path(repo).resolve()
    captures = {}
    for name in paths:
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or path.parts[0] not in ('data', 'market-dash'):
            raise ValueError('Unsupported publish path')
        if (repo / path).is_file():
            captures[path.as_posix()] = (repo / path).read_text()
    if not captures:
        return False

    def git(*args, cwd=repo):
        result = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        return result.stdout.strip()

    remote = git('remote', 'get-url', 'origin')
    # Forward checkout@v4's CI authorization only in the child environment,
    # never command arguments or logs. Local runs retain their normal credentials.
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        auth = git('config', '--get-regexp', r'^http\..*\.extraheader$') if os.environ.get('GITHUB_ACTIONS') == 'true' else ''
        env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
        for index, line in enumerate(auth.splitlines()):
            key, value = line.split(' ', 1)
            env[f'GIT_CONFIG_KEY_{index}'] = key
            env[f'GIT_CONFIG_VALUE_{index}'] = value
        if auth:
            env['GIT_CONFIG_COUNT'] = str(len(auth.splitlines()))

        def isolated(*args):
            result = subprocess.run(['git', *args], cwd=directory, env=env, capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip())
            return result.stdout.strip()

        isolated('clone', '--depth=1', '--single-branch', '--branch=main', remote, 'repo')
        directory = directory / 'repo'
        isolated('config', 'user.name', 'qqq-logger-bot')
        isolated('config', 'user.email', 'actions@github.com')
        for attempt in range(attempts):
            if attempt:
                isolated('fetch', '--depth=1', 'origin', 'main')
                isolated('reset', '--hard', 'origin/main')  # Disposable publisher only.
            for name, raw in captures.items():
                target = directory / name
                previous = target.read_text() if target.exists() else ''
                if name.startswith('data/') and name.endswith('.csv'):
                    raw = merge_csv(previous, raw)
                elif previous and timestamp(previous) and timestamp(raw) and timestamp(previous) > timestamp(raw):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(raw)
            isolated('add', '--', *captures)
            if not isolated('diff', '--cached', '--name-only'):
                return False
            isolated('commit', '-m', 'Publish option history and live snapshots')
            try:
                isolated('push', 'origin', 'HEAD:main')
                return True
            except RuntimeError:
                if attempt == attempts - 1:
                    raise
    return False


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--session', choices=['am', 'pm'], required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent
    paths = [str(p.relative_to(repo)) for p in (repo / 'data').glob(f'*/{args.session}/*.csv')]
    publish(repo, paths + LIVE_PATHS)
