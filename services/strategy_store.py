import json
from pathlib import Path
from copy import deepcopy

BASE_DIR = Path(__file__).resolve().parent.parent
STRATEGY_FILE = BASE_DIR / 'data' / 'strategies.json'

DEFAULT_DATA = {
    'active_strategy': 'default_v1',
    'approved_live_strategies': ['default_v1'],
    'strategies': []
}


def _ensure_file():
    STRATEGY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not STRATEGY_FILE.exists():
        STRATEGY_FILE.write_text(json.dumps(DEFAULT_DATA, indent=2, ensure_ascii=False), encoding='utf-8')


def load_strategy_data():
    _ensure_file()
    data = json.loads(STRATEGY_FILE.read_text(encoding='utf-8'))
    data.setdefault('active_strategy', 'default_v1')
    data.setdefault('approved_live_strategies', ['default_v1'])
    data.setdefault('strategies', [])
    return data


def save_strategy_data(data):
    _ensure_file()
    STRATEGY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    return data


def list_strategies():
    return load_strategy_data()


def get_strategy(strategy_id=None):
    data = load_strategy_data()
    target_id = strategy_id or data.get('active_strategy')
    for strategy in data.get('strategies', []):
        if strategy.get('id') == target_id:
            return deepcopy(strategy)
    if data.get('strategies'):
        return deepcopy(data['strategies'][0])
    return None


def upsert_strategy(strategy_payload):
    data = load_strategy_data()
    strategies = data.get('strategies', [])
    target_id = strategy_payload.get('id')
    if not target_id:
        raise ValueError('Strategie-ID fehlt')

    for idx, strategy in enumerate(strategies):
        if strategy.get('id') == target_id:
            strategies[idx] = strategy_payload
            save_strategy_data(data)
            return strategy_payload

    strategies.append(strategy_payload)
    save_strategy_data(data)
    return strategy_payload


def set_active_strategy(strategy_id):
    data = load_strategy_data()
    if strategy_id not in data.get('approved_live_strategies', []):
        raise ValueError(f'Strategie {strategy_id} ist nicht für Live freigegeben')
    if not any(strategy.get('id') == strategy_id for strategy in data.get('strategies', [])):
        raise ValueError(f'Strategie {strategy_id} nicht gefunden')
    data['active_strategy'] = strategy_id
    save_strategy_data(data)
    return data


def approve_strategy_for_live(strategy_id):
    data = load_strategy_data()
    if not any(strategy.get('id') == strategy_id for strategy in data.get('strategies', [])):
        raise ValueError(f'Strategie {strategy_id} nicht gefunden')
    approved = data.setdefault('approved_live_strategies', [])
    if strategy_id not in approved:
        approved.append(strategy_id)
    save_strategy_data(data)
    return data
