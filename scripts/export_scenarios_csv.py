#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_FILE = ROOT / 'data' / 'scenarios.json'
OUTPUT_FILE = ROOT / 'tmp' / 'scenario_sweep_export.csv'

BASE_COLUMNS = [
    'scenario_id',
    'scenario_name',
    'strategy_id',
    'universe_name',
    'start_date',
    'end_date',
    'initial_capital_eur',
    'notes',
]


def main():
    data = json.loads(SCENARIO_FILE.read_text(encoding='utf-8'))
    scenarios = data.get('scenarios', [])

    param_keys = set()
    for scenario in scenarios:
        for key in (scenario.get('params_override') or {}).keys():
            param_keys.add(key)

    param_columns = sorted(param_keys)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=BASE_COLUMNS + param_columns)
        writer.writeheader()
        for scenario in scenarios:
            row = {
                'scenario_id': scenario.get('id'),
                'scenario_name': scenario.get('name'),
                'strategy_id': scenario.get('strategy_id'),
                'universe_name': scenario.get('universe_name'),
                'start_date': scenario.get('start_date'),
                'end_date': scenario.get('end_date'),
                'initial_capital_eur': scenario.get('initial_capital_eur'),
                'notes': scenario.get('notes'),
            }
            params = scenario.get('params_override') or {}
            for key in param_columns:
                value = params.get(key)
                if isinstance(value, (dict, list)):
                    row[key] = json.dumps(value, ensure_ascii=False)
                else:
                    row[key] = value
            writer.writerow(row)

    print(f'CSV written to: {OUTPUT_FILE}')
    print(f'Scenarios exported: {len(scenarios)}')
    print(f'Parameter columns: {len(param_columns)}')


if __name__ == '__main__':
    main()
