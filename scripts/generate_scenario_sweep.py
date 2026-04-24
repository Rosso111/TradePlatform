#!/usr/bin/env python3
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.strategy_store import list_strategies
from services.scenario_store import load_scenario_data, save_scenario_data

START_DATE = '2016-01-01'
END_DATE = '2026-04-22'
UNIVERSE = 'global_core_10y'
CAPITAL = 10000
BASE_EXECUTION = {
    'persist_chunk_days': 4000,
    'cancel_check_interval_days': 100,
    'decision_log_mode': 'normal',
}

TARGET_STRATEGY_IDS = [
    'default_v1',
    'trend_quality_v1',
    'cash_recycling_v1',
    'trend_quality_balanced_v1',
    'trend_quality_defensive_v1',
    'trend_quality_aggressive_v1',
    'score_swing_v1',
    'cash_recycling_light_v1',
]


def clamp(value, lower=None, upper=None):
    if lower is not None:
        value = max(lower, value)
    if upper is not None:
        value = min(upper, value)
    return value



def bool_if_present(params, key, fallback=True):
    return bool(params.get(key, fallback))



def strategy_short_name(strategy):
    return (strategy.get('name') or strategy.get('id') or 'strategy').replace(' ', '_').replace('/', '_')



def build_profiles(strategy):
    params = strategy.get('params', {})
    profiles = []

    def scenario_payload(name_suffix, overrides):
        return {
            'name_suffix': name_suffix,
            'params_override': {**BASE_EXECUTION, **overrides},
        }

    profiles.append(scenario_payload('baseline', {}))

    buy = float(params.get('buy_threshold', 65))
    sell = float(params.get('sell_threshold', 35))
    max_positions = int(params.get('max_positions', 8))
    trailing = float(params.get('trailing_stop_pct', 0.04))

    profiles.append(scenario_payload('profile_conservative', {
        'buy_threshold': clamp(buy + 4, 0, 100),
        'sell_threshold': clamp(sell + 2, 0, 100),
        'max_positions': clamp(max_positions - 2, 1, 30),
        'trailing_stop_pct': round(clamp(trailing - 0.01, 0.01, 0.2), 3),
    }))
    profiles.append(scenario_payload('profile_balanced', {
        'buy_threshold': clamp(buy + 1, 0, 100),
        'sell_threshold': clamp(sell + 1, 0, 100),
        'max_positions': max_positions,
        'trailing_stop_pct': round(trailing, 3),
    }))
    profiles.append(scenario_payload('profile_aggressive', {
        'buy_threshold': clamp(buy - 4, 0, 100),
        'sell_threshold': clamp(sell - 2, 0, 100),
        'max_positions': clamp(max_positions + 3, 1, 30),
        'trailing_stop_pct': round(clamp(trailing + 0.02, 0.01, 0.2), 3),
    }))

    buy_offsets = [-6, -2, 2, 6]
    sell_offsets = [-4, 0, 4]
    max_position_offsets = [-2, 0, 3]
    trailing_offsets = [-0.015, 0.0, 0.02]
    core_count = 0
    for bo in buy_offsets:
        for so in sell_offsets:
            for mpo in max_position_offsets:
                if core_count >= 24:
                    break
                to = trailing_offsets[core_count % len(trailing_offsets)]
                profiles.append(scenario_payload(f'core_{core_count+1:03d}', {
                    'buy_threshold': clamp(buy + bo, 0, 100),
                    'sell_threshold': clamp(sell + so, 0, 100),
                    'max_positions': clamp(max_positions + mpo, 1, 30),
                    'trailing_stop_pct': round(clamp(trailing + to, 0.01, 0.2), 3),
                }))
                core_count += 1
            if core_count >= 24:
                break
        if core_count >= 24:
            break

    mode = strategy.get('mode')
    specific_count = 0
    if mode == 'trend_quality':
        min_rsi = float(params.get('min_rsi', 40))
        max_rsi = float(params.get('max_rsi', 70))
        min_sector = float(params.get('min_sector_score', 50))
        toggles = [
            (True, True, True),
            (True, True, False),
            (True, False, True),
            (False, True, True),
        ]
        for min_rsi_offset in [-4, 0, 4, 8]:
            for max_rsi_offset in [-4, 0, 4, 8, 12]:
                if specific_count >= 20:
                    break
                toggle = toggles[specific_count % len(toggles)]
                profiles.append(scenario_payload(f'filters_{specific_count+1:03d}', {
                    'min_rsi': clamp(min_rsi + min_rsi_offset, 0, 100),
                    'max_rsi': clamp(max_rsi + max_rsi_offset, 0, 100),
                    'min_sector_score': clamp(min_sector + ((specific_count % 5) - 2) * 3, 0, 100),
                    'require_price_above_ema_fast': toggle[0],
                    'require_ema_fast_above_slow': toggle[1],
                    'require_macd_above_signal': toggle[2],
                }))
                specific_count += 1
            if specific_count >= 20:
                break
    elif 'cash_recycling' in strategy.get('id', ''):
        min_profit_sell = float(params.get('min_profit_pct_for_sell', 6))
        min_profit_sideways = float(params.get('min_profit_pct_for_sideways_exit', 3))
        sideways_days = int(params.get('sideways_days', 25))
        sideways_band = float(params.get('sideways_band_pct', 0.06))
        trim_above = float(params.get('trim_position_above_eur', 3000))
        trim_fraction = float(params.get('trim_fraction', 0.33))
        for i in range(20):
            profiles.append(scenario_payload(f'profit_exit_{i+1:03d}', {
                'min_profit_pct_for_sell': clamp(min_profit_sell + ((i % 5) - 2) * 2, 0, 50),
                'min_profit_pct_for_sideways_exit': clamp(min_profit_sideways + ((i % 4) - 1) * 1.5, 0, 30),
                'sideways_days': clamp(sideways_days + ((i % 5) - 2) * 5, 5, 90),
                'sideways_band_pct': round(clamp(sideways_band + ((i % 4) - 1) * 0.01, 0.01, 0.2), 3),
                'trim_position_above_eur': clamp(trim_above + ((i % 5) - 2) * 500, 500, 10000),
                'trim_fraction': round(clamp(trim_fraction + ((i % 4) - 1) * 0.08, 0.1, 0.9), 2),
            }))
    else:
        min_rsi = float(params.get('min_rsi', 40)) if 'min_rsi' in params else 40.0
        max_rsi = float(params.get('max_rsi', 70)) if 'max_rsi' in params else 70.0
        for i in range(20):
            profiles.append(scenario_payload(f'swing_{i+1:03d}', {
                'buy_threshold': clamp(buy + ((i % 5) - 2) * 2, 0, 100),
                'sell_threshold': clamp(sell + ((i % 4) - 1) * 2, 0, 100),
                'max_positions': clamp(max_positions + ((i % 5) - 2), 1, 30),
                'trailing_stop_pct': round(clamp(trailing + ((i % 4) - 1) * 0.01, 0.01, 0.2), 3),
                'min_rsi': clamp(min_rsi + ((i % 5) - 2) * 2, 0, 100),
                'max_rsi': clamp(max_rsi + ((i % 5) - 2) * 2, 0, 100),
            }))

    for i in range(14):
        profiles.append(scenario_payload(f'edge_{i+1:03d}', {
            'buy_threshold': clamp(buy + ((i % 7) - 3) * 3, 0, 100),
            'sell_threshold': clamp(sell + ((i % 5) - 2) * 3, 0, 100),
            'max_positions': clamp(max_positions + ((i % 6) - 3), 1, 30),
            'trailing_stop_pct': round(clamp(trailing + ((i % 6) - 3) * 0.008, 0.01, 0.2), 3),
        }))

    return profiles[:62]



def main():
    strategy_data = list_strategies()
    strategies = [s for s in strategy_data.get('strategies', []) if s.get('id') in TARGET_STRATEGY_IDS]
    strategies_by_id = {s['id']: s for s in strategies}

    scenario_data = load_scenario_data()
    existing_scenarios = [s for s in scenario_data.get('scenarios', []) if not str(s.get('id', '')).startswith('sweep_')]

    generated_scenarios = []
    scenario_ids = []
    counter = 1

    for strategy_id in TARGET_STRATEGY_IDS:
        strategy = strategies_by_id.get(strategy_id)
        if not strategy:
            continue
        variants = build_profiles(strategy)
        short_name = strategy_short_name(strategy)

        for idx, variant in enumerate(variants, start=1):
            scenario_id = f'sweep_{counter:03d}_{strategy_id}'
            scenario_name = f"{strategy.get('name') or strategy_id} — {variant['name_suffix']} — {idx:03d}"
            scenario = {
                'id': scenario_id,
                'name': scenario_name,
                'strategy_id': strategy_id,
                'universe_name': UNIVERSE,
                'start_date': START_DATE,
                'end_date': END_DATE,
                'initial_capital_eur': CAPITAL,
                'notes': f'Broad sweep for {strategy.get("name") or strategy_id}',
                'params_override': variant['params_override'],
            }
            generated_scenarios.append(scenario)
            scenario_ids.append(scenario_id)
            counter += 1

    if len(generated_scenarios) < 500:
        needed = 500 - len(generated_scenarios)
        reference_ids = TARGET_STRATEGY_IDS[:4]
        for i in range(needed):
            strategy_id = reference_ids[i % len(reference_ids)]
            strategy = strategies_by_id[strategy_id]
            scenario_id = f'sweep_{counter:03d}_{strategy_id}'
            scenario_name = f"{strategy.get('name') or strategy_id} — reference_extra — {i+1:03d}"
            base_params = deepcopy(strategy.get('params', {}))
            params_override = {
                **BASE_EXECUTION,
                'buy_threshold': clamp(float(base_params.get('buy_threshold', 65)) + ((i % 5) - 2), 0, 100),
                'sell_threshold': clamp(float(base_params.get('sell_threshold', 35)) + ((i % 5) - 2), 0, 100),
                'max_positions': clamp(int(base_params.get('max_positions', 8)) + ((i % 4) - 1), 1, 30),
                'trailing_stop_pct': round(clamp(float(base_params.get('trailing_stop_pct', 0.04)) + ((i % 4) - 1) * 0.005, 0.01, 0.2), 3),
            }
            scenario = {
                'id': scenario_id,
                'name': scenario_name,
                'strategy_id': strategy_id,
                'universe_name': UNIVERSE,
                'start_date': START_DATE,
                'end_date': END_DATE,
                'initial_capital_eur': CAPITAL,
                'notes': 'Reference extra sweep scenario',
                'params_override': params_override,
            }
            generated_scenarios.append(scenario)
            scenario_ids.append(scenario_id)
            counter += 1

    batch_id = 'batch_sweep_500_all8'
    batch = {
        'id': batch_id,
        'name': 'Broad Sweep 500 across 8 strategies',
        'status': 'queued',
        'created_at': '2026-04-22T21:14:00+00:00',
        'started_at': None,
        'finished_at': None,
        'current_index': 0,
        'scenario_ids': scenario_ids,
        'run_ids': [],
        'results': [],
        'error': None,
    }

    scenario_data['scenarios'] = existing_scenarios + generated_scenarios
    scenario_data['scenario_batches'] = [b for b in scenario_data.get('scenario_batches', []) if b.get('id') != batch_id] + [batch]
    save_scenario_data(scenario_data)

    print(f'Generated scenarios: {len(generated_scenarios)}')
    print(f'Batch ID: {batch_id}')


if __name__ == '__main__':
    main()
