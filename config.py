import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Datenbank
DATABASE_PATH = os.path.join(BASE_DIR, 'data', 'trading.db')
SQLALCHEMY_DATABASE_URI = f'sqlite:///{DATABASE_PATH}'
SQLALCHEMY_TRACK_MODIFICATIONS = False

# Trading-Parameter
STARTING_CAPITAL = 10000.0          # EUR
MAX_POSITIONS = 10                   # Maximale gleichzeitige Positionen
MAX_POSITIONS_PER_SECTOR = 3        # Max Positionen pro Sektor
RISK_PER_TRADE = 0.02               # 2% Kapital-Risiko pro Trade
MAX_POSITION_SIZE = 0.20            # Max 20% des Portfolios pro Position
MIN_POSITION_SIZE = 0.03            # Min 3% des Portfolios pro Position

# Handelskosten
COMMISSION_RATE = 0.001             # 0.1% Provision pro Trade
MIN_COMMISSION = 1.0                # Mindestprovision EUR
SPREAD_RATE = 0.0005                # 0.05% Spread (jede Seite)

# Stop-Loss & Take-Profit
DEFAULT_STOP_LOSS_PCT = 0.05        # 5% Stop-Loss
ATR_STOP_MULTIPLIER = 2.0           # Stop = Einstieg - 2x ATR
DEFAULT_TAKE_PROFIT_PCT = 0.15      # 15% Take-Profit
TRAILING_STOP_PCT = 0.03            # 3% Trailing-Stop

# Algorithmus
BACKTESTING_DAYS = 365              # 1 Jahr Backtest
SIGNAL_THRESHOLD_BUY = 65          # Score >= 65 = Kaufsignal
SIGNAL_THRESHOLD_SELL = 35         # Score <= 35 = Verkaufssignal
MIN_SCORE_IMPROVEMENT = 10         # Mindestverbesserung für Positionswechsel

# Scheduler (autonomer Handel)
TRADING_INTERVAL_MINUTES = 15      # Alle 15 Minuten prüfen
DATA_UPDATE_INTERVAL_HOURS = 1     # Kursdaten stündlich aktualisieren

# Basis-Währung
BASE_CURRENCY = 'EUR'

# Flask
SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
DEBUG = os.environ.get('DEBUG', 'true').lower() == 'true'

# Aktien-Universum: ~60 Aktien aus verschiedenen Regionen & Sektoren
STOCK_UNIVERSE = [
    # === DEUTSCHLAND (XETRA) ===
    {'symbol': 'SAP.DE',  'name': 'SAP SE',            'sector': 'Technology',    'region': 'DE', 'currency': 'EUR'},
    {'symbol': 'ALV.DE',  'name': 'Allianz SE',         'sector': 'Financials',   'region': 'DE', 'currency': 'EUR'},
    {'symbol': 'SIE.DE',  'name': 'Siemens AG',         'sector': 'Industrials',  'region': 'DE', 'currency': 'EUR'},
    {'symbol': 'DTE.DE',  'name': 'Deutsche Telekom',   'sector': 'Telecom',      'region': 'DE', 'currency': 'EUR'},
    {'symbol': 'BAS.DE',  'name': 'BASF SE',            'sector': 'Materials',    'region': 'DE', 'currency': 'EUR'},
    {'symbol': 'BMW.DE',  'name': 'BMW AG',             'sector': 'Automotive',   'region': 'DE', 'currency': 'EUR'},
    {'symbol': 'MBG.DE',  'name': 'Mercedes-Benz',      'sector': 'Automotive',   'region': 'DE', 'currency': 'EUR'},
    {'symbol': 'ADS.DE',  'name': 'adidas AG',          'sector': 'ConsumerDisc', 'region': 'DE', 'currency': 'EUR'},
    {'symbol': 'MRK.DE',  'name': 'Merck KGaA',         'sector': 'Healthcare',   'region': 'DE', 'currency': 'EUR'},
    {'symbol': 'RWE.DE',  'name': 'RWE AG',             'sector': 'Energy',       'region': 'DE', 'currency': 'EUR'},

    # === ÖSTERREICH (WIEN) ===
    {'symbol': 'VER.VI',  'name': 'Verbund AG',         'sector': 'Energy',       'region': 'AT', 'currency': 'EUR'},
    {'symbol': 'OMV.VI',  'name': 'OMV AG',             'sector': 'Energy',       'region': 'AT', 'currency': 'EUR'},
    {'symbol': 'EBS.VI',  'name': 'Erste Group Bank',   'sector': 'Financials',   'region': 'AT', 'currency': 'EUR'},
    {'symbol': 'RBI.VI',  'name': 'Raiffeisen Bank Int','sector': 'Financials',   'region': 'AT', 'currency': 'EUR'},

    # === SCHWEIZ (SIX) ===
    {'symbol': 'NESN.SW', 'name': 'Nestlé SA',          'sector': 'ConsumerStap', 'region': 'CH', 'currency': 'CHF'},
    {'symbol': 'NOVN.SW', 'name': 'Novartis AG',        'sector': 'Healthcare',   'region': 'CH', 'currency': 'CHF'},
    {'symbol': 'ROG.SW',  'name': 'Roche Holding AG',   'sector': 'Healthcare',   'region': 'CH', 'currency': 'CHF'},
    {'symbol': 'UBSG.SW', 'name': 'UBS Group AG',       'sector': 'Financials',   'region': 'CH', 'currency': 'CHF'},
    {'symbol': 'ABBN.SW', 'name': 'ABB Ltd',            'sector': 'Industrials',  'region': 'CH', 'currency': 'CHF'},

    # === EUROPA (EURONEXT etc.) ===
    {'symbol': 'MC.PA',   'name': 'LVMH',               'sector': 'ConsumerDisc', 'region': 'FR', 'currency': 'EUR'},
    {'symbol': 'OR.PA',   'name': "L'Oréal",            'sector': 'ConsumerStap', 'region': 'FR', 'currency': 'EUR'},
    {'symbol': 'AIR.PA',  'name': 'Airbus SE',          'sector': 'Industrials',  'region': 'EU', 'currency': 'EUR'},
    {'symbol': 'TTE.PA',  'name': 'TotalEnergies SE',   'sector': 'Energy',       'region': 'FR', 'currency': 'EUR'},
    {'symbol': 'ASML.AS', 'name': 'ASML Holding NV',   'sector': 'Technology',   'region': 'NL', 'currency': 'EUR'},
    {'symbol': 'PHIA.AS', 'name': 'Philips NV',         'sector': 'Healthcare',   'region': 'NL', 'currency': 'EUR'},
    {'symbol': 'INGA.AS', 'name': 'ING Groep NV',       'sector': 'Financials',   'region': 'NL', 'currency': 'EUR'},

    # === GROSSBRITANNIEN ===
    {'symbol': 'HSBA.L',  'name': 'HSBC Holdings',      'sector': 'Financials',   'region': 'UK', 'currency': 'GBP'},
    {'symbol': 'SHEL.L',  'name': 'Shell PLC',          'sector': 'Energy',       'region': 'UK', 'currency': 'GBP'},
    {'symbol': 'AZN.L',   'name': 'AstraZeneca PLC',    'sector': 'Healthcare',   'region': 'UK', 'currency': 'GBP'},
    {'symbol': 'BP.L',    'name': 'BP PLC',             'sector': 'Energy',       'region': 'UK', 'currency': 'GBP'},
    {'symbol': 'ULVR.L',  'name': 'Unilever PLC',       'sector': 'ConsumerStap', 'region': 'UK', 'currency': 'GBP'},
    {'symbol': 'GSK.L',   'name': 'GSK PLC',            'sector': 'Healthcare',   'region': 'UK', 'currency': 'GBP'},

    # === USA ===
    {'symbol': 'AAPL',    'name': 'Apple Inc.',          'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'MSFT',    'name': 'Microsoft Corp.',     'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'GOOGL',   'name': 'Alphabet Inc.',       'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'AMZN',    'name': 'Amazon.com Inc.',     'sector': 'ConsumerDisc', 'region': 'US', 'currency': 'USD'},
    {'symbol': 'NVDA',    'name': 'NVIDIA Corp.',        'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'JPM',     'name': 'JPMorgan Chase',      'sector': 'Financials',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'JNJ',     'name': 'Johnson & Johnson',   'sector': 'Healthcare',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'XOM',     'name': 'Exxon Mobil Corp.',   'sector': 'Energy',       'region': 'US', 'currency': 'USD'},
    {'symbol': 'META',    'name': 'Meta Platforms Inc.', 'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'TSLA',    'name': 'Tesla Inc.',          'sector': 'Automotive',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'BRK-B',   'name': 'Berkshire Hathaway',  'sector': 'Financials',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'V',       'name': 'Visa Inc.',           'sector': 'Financials',   'region': 'US', 'currency': 'USD'},

    # === JAPAN (TSE) ===
    {'symbol': '7203.T',  'name': 'Toyota Motor Corp.',  'sector': 'Automotive',   'region': 'JP', 'currency': 'JPY'},
    {'symbol': '6758.T',  'name': 'Sony Group Corp.',    'sector': 'Technology',   'region': 'JP', 'currency': 'JPY'},
    {'symbol': '9984.T',  'name': 'SoftBank Group',      'sector': 'Technology',   'region': 'JP', 'currency': 'JPY'},
    {'symbol': '6861.T',  'name': 'Keyence Corp.',       'sector': 'Technology',   'region': 'JP', 'currency': 'JPY'},
    {'symbol': '7974.T',  'name': 'Nintendo Co. Ltd.',   'sector': 'Technology',   'region': 'JP', 'currency': 'JPY'},

    # === CHINA / HONGKONG ===
    {'symbol': '9988.HK', 'name': 'Alibaba Group',       'sector': 'Technology',   'region': 'CN', 'currency': 'HKD'},
    {'symbol': '0700.HK', 'name': 'Tencent Holdings',    'sector': 'Technology',   'region': 'CN', 'currency': 'HKD'},
    {'symbol': '9618.HK', 'name': 'JD.com Inc.',         'sector': 'ConsumerDisc', 'region': 'CN', 'currency': 'HKD'},
    {'symbol': '3690.HK', 'name': 'Meituan',             'sector': 'ConsumerDisc', 'region': 'CN', 'currency': 'HKD'},

    # === SÜDKOREA (KRX) ===
    {'symbol': '005930.KS','name': 'Samsung Electronics','sector': 'Technology',   'region': 'KR', 'currency': 'KRW'},
    {'symbol': '000660.KS','name': 'SK Hynix Inc.',      'sector': 'Technology',   'region': 'KR', 'currency': 'KRW'},
    {'symbol': '051910.KS','name': 'LG Chem Ltd.',       'sector': 'Materials',    'region': 'KR', 'currency': 'KRW'},

    # === AUSTRALIEN (ASX) ===
    {'symbol': 'BHP.AX',  'name': 'BHP Group Ltd.',      'sector': 'Materials',    'region': 'AU', 'currency': 'AUD'},
    {'symbol': 'CBA.AX',  'name': 'Commonwealth Bank',   'sector': 'Financials',   'region': 'AU', 'currency': 'AUD'},
    {'symbol': 'CSL.AX',  'name': 'CSL Ltd.',            'sector': 'Healthcare',   'region': 'AU', 'currency': 'AUD'},
    {'symbol': 'RIO.AX',  'name': 'Rio Tinto Ltd.',      'sector': 'Materials',    'region': 'AU', 'currency': 'AUD'},
]

# Wechselkurs-Paare (Basis: EUR)
FOREX_PAIRS = ['EURUSD=X', 'EURGBP=X', 'EURJPY=X', 'EURCHF=X',
               'EURHKD=X', 'EURKRW=X', 'EURAUD=X']
