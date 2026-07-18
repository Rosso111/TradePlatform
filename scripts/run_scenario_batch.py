#!/usr/bin/env python3
"""Führt einen gespeicherten Scenario-Batch ohne laufenden Server aus.

Beispiel:
  python3 scripts/run_scenario_batch.py --batch batch_at_v1_tuning
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / '.env')

from run_scenario import create_cli_app  # noqa: E402  (gleiches Verzeichnis)
from services.scenario_runner import start_batch  # noqa: E402
from services.scenario_store import get_scenario_batch  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description='Scenario-Batch direkt ausführen')
    parser.add_argument('--batch', required=True, help='Batch-ID aus data/scenarios.json')
    parser.add_argument('--poll-seconds', type=int, default=60)
    args = parser.parse_args()

    app = create_cli_app()
    ok, error = start_batch(app, args.batch)
    if not ok:
        print(f'Start fehlgeschlagen: {error}', file=sys.stderr)
        sys.exit(1)

    # start_batch läuft im Daemon-Thread — Prozess bis zum Ende am Leben halten
    while True:
        time.sleep(args.poll_seconds)
        batch = get_scenario_batch(args.batch) or {}
        status = str(batch.get('status', '')).lower()
        done = batch.get('current_index', 0)
        total = len(batch.get('scenario_ids') or [])
        print(f'[{time.strftime("%H:%M:%S")}] {status} — {done}/{total} Runs', flush=True)
        if status in ('completed', 'failed'):
            for r in batch.get('results') or []:
                print(f"  {r.get('scenario_id')}: return={r.get('total_return_pct')}% "
                      f"dd={r.get('max_drawdown_pct')}% sharpe={r.get('sharpe_ratio')} "
                      f"trades={r.get('total_trades')}", flush=True)
            sys.exit(0 if status == 'completed' else 1)


if __name__ == '__main__':
    main()
