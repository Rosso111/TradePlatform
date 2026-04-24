#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_FILE = ROOT / 'data' / 'scenarios.json'
SWEEP_BATCH_ID = 'batch_sweep_500_all8'



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Szenarien und Batchs aufräumen')
    parser.add_argument('--all', action='store_true', help='Alle Szenarien und Batchs löschen')
    parser.add_argument('--sweep-only', action='store_true', help='Nur Sweep-Szenarien und den Sweep-Batch löschen')
    parser.add_argument('--yes', action='store_true', help='Ohne Rückfrage ausführen')
    args = parser.parse_args()

    if not args.all and not args.sweep_only:
        parser.error('Bitte --all oder --sweep-only angeben')
    return args



def load_data() -> dict:
    if not SCENARIO_FILE.exists():
        return {'scenarios': [], 'scenario_batches': []}
    return json.loads(SCENARIO_FILE.read_text(encoding='utf-8'))



def save_data(data: dict):
    SCENARIO_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCENARIO_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')



def main():
    args = parse_args()
    data = load_data()

    before_scenarios = len(data.get('scenarios', []))
    before_batches = len(data.get('scenario_batches', []))

    if args.all:
        action_text = 'ALLE Szenarien und Batchs löschen'
    else:
        action_text = 'nur Sweep-Szenarien und den Sweep-Batch löschen'

    if not args.yes:
        confirm = input(f'Wirklich {action_text}? [y/N]: ').strip().lower()
        if confirm not in ('y', 'yes', 'j', 'ja'):
            print('Abgebrochen.')
            return

    if args.all:
        data['scenarios'] = []
        data['scenario_batches'] = []
    else:
        data['scenarios'] = [
            s for s in data.get('scenarios', [])
            if not str(s.get('id', '')).startswith('sweep_')
        ]
        data['scenario_batches'] = [
            b for b in data.get('scenario_batches', [])
            if b.get('id') != SWEEP_BATCH_ID
        ]

    save_data(data)

    print('Fertig.')
    print('Szenarien vorher:', before_scenarios)
    print('Szenarien nachher:', len(data.get('scenarios', [])))
    print('Batchs vorher:', before_batches)
    print('Batchs nachher:', len(data.get('scenario_batches', [])))


if __name__ == '__main__':
    main()
