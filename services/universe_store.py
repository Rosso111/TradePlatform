import json
from copy import deepcopy
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
UNIVERSE_FILE = BASE_DIR / 'data' / 'universes.json'
DEFAULT_UNIVERSE_ID = 'global_core'

DEFAULT_DATA = {
    'active_universe': DEFAULT_UNIVERSE_ID,
    'universes': [
        {
            'id': 'global_core',
            'name': 'Global Core',
            'description': 'Breites internationales Kernuniversum über Regionen und Sektoren.',
            'symbols': [
                'SAP.DE', 'ALV.DE', 'SIE.DE', 'NESN.SW', 'NOVN.SW', 'ROG.SW',
                'ASML.AS', 'MC.PA', 'AIR.PA', 'HSBA.L', 'SHEL.L', 'AZN.L',
                'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'JPM', 'JNJ', 'XOM',
                '7203.T', '6758.T', '9988.HK', '0700.HK', '005930.KS', 'BHP.AX'
            ]
        },
        {
            'id': 'europe_core',
            'name': 'Europe Core',
            'description': 'Europa-Fokus mit etablierten Large Caps aus mehreren Sektoren.',
            'symbols': [
                'SAP.DE', 'ALV.DE', 'SIE.DE', 'DTE.DE', 'BAS.DE', 'BMW.DE', 'ADS.DE',
                'NESN.SW', 'NOVN.SW', 'ROG.SW', 'UBSG.SW', 'ABBN.SW',
                'MC.PA', 'OR.PA', 'AIR.PA', 'TTE.PA', 'ASML.AS', 'PHIA.AS', 'INGA.AS',
                'HSBA.L', 'SHEL.L', 'AZN.L', 'BP.L', 'ULVR.L', 'GSK.L'
            ]
        },
        {
            'id': 'us_growth',
            'name': 'US Growth',
            'description': 'US-Wachstumstitel und dominante Plattform-/Tech-Werte.',
            'symbols': [
                'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA'
            ]
        },
        {
            'id': 'defensive_quality',
            'name': 'Defensive Quality',
            'description': 'Defensivere Qualitätsaktien aus Healthcare, Staples und stabilen Large Caps.',
            'symbols': [
                'NESN.SW', 'NOVN.SW', 'ROG.SW', 'AZN.L', 'GSK.L', 'JNJ', 'ULVR.L', 'MRK.DE', 'DTE.DE'
            ]
        },
        {
            'id': 'trend_candidates',
            'name': 'Trend Candidates',
            'description': 'Werte mit typischerweise stärkerem Momentum-/Trendprofil.',
            'symbols': [
                'NVDA', 'MSFT', 'META', 'ASML.AS', 'SAP.DE', 'AIR.PA', '6758.T', '005930.KS'
            ]
        }
    ]
}


def _ensure_file():
    UNIVERSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not UNIVERSE_FILE.exists():
        UNIVERSE_FILE.write_text(json.dumps(DEFAULT_DATA, indent=2, ensure_ascii=False), encoding='utf-8')


def load_universe_data():
    _ensure_file()
    data = json.loads(UNIVERSE_FILE.read_text(encoding='utf-8'))
    data.setdefault('active_universe', DEFAULT_UNIVERSE_ID)
    data.setdefault('universes', [])
    return data


def save_universe_data(data):
    _ensure_file()
    UNIVERSE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    return data


def list_universes():
    return load_universe_data()


def get_universe(universe_id=None):
    data = load_universe_data()
    target_id = universe_id or data.get('active_universe') or DEFAULT_UNIVERSE_ID
    for universe in data.get('universes', []):
        if universe.get('id') == target_id:
            out = deepcopy(universe)
            out.setdefault('symbols', [])
            return out
    universes = data.get('universes', [])
    if universes:
        out = deepcopy(universes[0])
        out.setdefault('symbols', [])
        return out
    return None
