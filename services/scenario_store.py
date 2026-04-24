import json
from copy import deepcopy
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SCENARIO_FILE = BASE_DIR / 'data' / 'scenarios.json'

DEFAULT_DATA = {
    'scenarios': [],
    'scenario_batches': []
}


def _ensure_file():
    SCENARIO_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not SCENARIO_FILE.exists():
        SCENARIO_FILE.write_text(json.dumps(DEFAULT_DATA, indent=2, ensure_ascii=False), encoding='utf-8')



def load_scenario_data():
    _ensure_file()
    data = json.loads(SCENARIO_FILE.read_text(encoding='utf-8'))
    data.setdefault('scenarios', [])
    data.setdefault('scenario_batches', [])
    return data



def save_scenario_data(data):
    _ensure_file()
    SCENARIO_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    return data



def list_scenarios():
    return load_scenario_data()



def upsert_scenario(payload):
    data = load_scenario_data()
    scenarios = data.get('scenarios', [])
    scenario_id = payload.get('id')
    if not scenario_id:
        raise ValueError('Scenario-ID fehlt')

    for idx, scenario in enumerate(scenarios):
        if scenario.get('id') == scenario_id:
            scenarios[idx] = payload
            save_scenario_data(data)
            return deepcopy(payload)

    scenarios.append(payload)
    save_scenario_data(data)
    return deepcopy(payload)



def get_scenario(scenario_id):
    data = load_scenario_data()
    for scenario in data.get('scenarios', []):
        if scenario.get('id') == scenario_id:
            return deepcopy(scenario)
    return None



def delete_scenario(scenario_id):
    data = load_scenario_data()
    scenarios = data.get('scenarios', [])
    remaining = [scenario for scenario in scenarios if scenario.get('id') != scenario_id]
    if len(remaining) == len(scenarios):
        raise ValueError(f'Szenario {scenario_id} nicht gefunden')
    data['scenarios'] = remaining
    save_scenario_data(data)
    return True



def create_scenario_batch(batch_payload):
    data = load_scenario_data()
    batches = data.get('scenario_batches', [])
    batch_id = batch_payload.get('id')
    if not batch_id:
        raise ValueError('Batch-ID fehlt')

    for batch in batches:
        if batch.get('id') == batch_id:
            raise ValueError(f'Batch {batch_id} existiert bereits')

    batches.append(batch_payload)
    save_scenario_data(data)
    return deepcopy(batch_payload)



def update_scenario_batch(batch_id, patch):
    data = load_scenario_data()
    batches = data.get('scenario_batches', [])
    for idx, batch in enumerate(batches):
        if batch.get('id') == batch_id:
            merged = {**batch, **patch}
            batches[idx] = merged
            save_scenario_data(data)
            return deepcopy(merged)
    raise ValueError(f'Batch {batch_id} nicht gefunden')



def get_scenario_batch(batch_id):
    data = load_scenario_data()
    for batch in data.get('scenario_batches', []):
        if batch.get('id') == batch_id:
            return deepcopy(batch)
    return None



def delete_scenario_batch(batch_id):
    data = load_scenario_data()
    batches = data.get('scenario_batches', [])
    remaining = [batch for batch in batches if batch.get('id') != batch_id]
    if len(remaining) == len(batches):
        raise ValueError(f'Batch {batch_id} nicht gefunden')
    data['scenario_batches'] = remaining
    save_scenario_data(data)
    return True
