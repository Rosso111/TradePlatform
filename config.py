import os
from urllib.parse import quote_plus

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Datenbank / Staging
APP_ENV = os.environ.get('APP_ENV', 'live').lower()
DATABASE_FILENAME = 'trading-staging.db' if APP_ENV == 'staging' else 'trading.db'
DATABASE_PATH = os.path.join(BASE_DIR, 'data', DATABASE_FILENAME)

DB_BACKEND = os.environ.get('DB_BACKEND', 'postgres').lower()
POSTGRES_HOST = os.environ.get('POSTGRES_HOST', '')
POSTGRES_PORT = int(os.environ.get('POSTGRES_PORT', '5432'))
POSTGRES_DB = os.environ.get('POSTGRES_DB', '')
POSTGRES_USER = os.environ.get('POSTGRES_USER', '')
POSTGRES_PASSWORD = os.environ.get('POSTGRES_PASSWORD', '')
POSTGRES_SSLMODE = os.environ.get('POSTGRES_SSLMODE', 'prefer')

if DB_BACKEND != 'postgres':
    raise RuntimeError(
        f"Unsupported DB_BACKEND='{DB_BACKEND}'. This application is now PostgreSQL-only."
    )

missing_postgres_env = [
    name for name, value in {
        'POSTGRES_HOST': POSTGRES_HOST,
        'POSTGRES_DB': POSTGRES_DB,
        'POSTGRES_USER': POSTGRES_USER,
        'POSTGRES_PASSWORD': POSTGRES_PASSWORD,
    }.items() if not value
]
if missing_postgres_env:
    raise RuntimeError(
        'Missing required PostgreSQL configuration: ' + ', '.join(missing_postgres_env)
    )

SQLALCHEMY_DATABASE_URI = (
    f"postgresql+psycopg://{quote_plus(POSTGRES_USER)}:{quote_plus(POSTGRES_PASSWORD)}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}?sslmode={quote_plus(POSTGRES_SSLMODE)}"
)

SQLALCHEMY_TRACK_MODIFICATIONS = False

# Runtime
PORT = int(os.environ.get('PORT', '5001' if APP_ENV == 'staging' else '5000'))

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

    # === USA (Kernuniversum) ===
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

    # === USA (momentum_100 – Tech/Software) ===
    {'symbol': 'AMD',     'name': 'Advanced Micro Devices', 'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'CRM',     'name': 'Salesforce Inc.',        'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'NOW',     'name': 'ServiceNow Inc.',        'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'ADBE',    'name': 'Adobe Inc.',             'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'INTU',    'name': 'Intuit Inc.',            'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'SHOP',    'name': 'Shopify Inc.',           'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'PANW',    'name': 'Palo Alto Networks',     'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'CRWD',    'name': 'CrowdStrike Holdings',   'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'ZS',      'name': 'Zscaler Inc.',           'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'DDOG',    'name': 'Datadog Inc.',           'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'ANET',    'name': 'Arista Networks Inc.',   'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'FTNT',    'name': 'Fortinet Inc.',          'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'PLTR',    'name': 'Palantir Technologies',  'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'UBER',    'name': 'Uber Technologies',      'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'NFLX',    'name': 'Netflix Inc.',           'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'TTD',     'name': 'The Trade Desk Inc.',    'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'MDB',     'name': 'MongoDB Inc.',           'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'NET',     'name': 'Cloudflare Inc.',        'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'SNOW',    'name': 'Snowflake Inc.',         'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'PYPL',    'name': 'PayPal Holdings Inc.',   'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'ZM',      'name': 'Zoom Video Comm.',       'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'RBLX',    'name': 'Roblox Corp.',           'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'APP',     'name': 'AppLovin Corp.',         'sector': 'Technology',   'region': 'US', 'currency': 'USD'},

    # === USA (momentum_100 – Semiconductors) ===
    {'symbol': 'QCOM',    'name': 'Qualcomm Inc.',          'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'AVGO',    'name': 'Broadcom Inc.',          'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'AMAT',    'name': 'Applied Materials Inc.', 'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'LRCX',    'name': 'Lam Research Corp.',     'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'KLAC',    'name': 'KLA Corp.',              'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'MRVL',    'name': 'Marvell Technology',     'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'TXN',     'name': 'Texas Instruments Inc.', 'sector': 'Technology',   'region': 'US', 'currency': 'USD'},

    # === USA (momentum_100 – Healthcare/Biotech) ===
    {'symbol': 'LLY',     'name': 'Eli Lilly and Co.',      'sector': 'Healthcare',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'MRNA',    'name': 'Moderna Inc.',           'sector': 'Healthcare',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'REGN',    'name': 'Regeneron Pharma.',      'sector': 'Healthcare',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'VRTX',    'name': 'Vertex Pharma.',         'sector': 'Healthcare',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'ISRG',    'name': 'Intuitive Surgical',     'sector': 'Healthcare',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'TMO',     'name': 'Thermo Fisher Scientific','sector': 'Healthcare',  'region': 'US', 'currency': 'USD'},
    {'symbol': 'UNH',     'name': 'UnitedHealth Group',     'sector': 'Healthcare',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'ABBV',    'name': 'AbbVie Inc.',            'sector': 'Healthcare',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'DXCM',    'name': 'DexCom Inc.',            'sector': 'Healthcare',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'IDXX',    'name': 'IDEXX Laboratories',     'sector': 'Healthcare',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'GILD',    'name': 'Gilead Sciences Inc.',   'sector': 'Healthcare',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'BIIB',    'name': 'Biogen Inc.',            'sector': 'Healthcare',   'region': 'US', 'currency': 'USD'},

    # === USA (momentum_100 – Consumer) ===
    {'symbol': 'COST',    'name': 'Costco Wholesale Corp.', 'sector': 'ConsumerStap', 'region': 'US', 'currency': 'USD'},
    {'symbol': 'HD',      'name': 'Home Depot Inc.',        'sector': 'ConsumerDisc', 'region': 'US', 'currency': 'USD'},
    {'symbol': 'NKE',     'name': 'Nike Inc.',              'sector': 'ConsumerDisc', 'region': 'US', 'currency': 'USD'},
    {'symbol': 'SBUX',    'name': 'Starbucks Corp.',        'sector': 'ConsumerDisc', 'region': 'US', 'currency': 'USD'},
    {'symbol': 'LULU',    'name': 'Lululemon Athletica',    'sector': 'ConsumerDisc', 'region': 'US', 'currency': 'USD'},
    {'symbol': 'MNST',    'name': 'Monster Beverage Corp.', 'sector': 'ConsumerStap', 'region': 'US', 'currency': 'USD'},
    {'symbol': 'CMG',     'name': 'Chipotle Mexican Grill', 'sector': 'ConsumerDisc', 'region': 'US', 'currency': 'USD'},
    {'symbol': 'TJX',     'name': 'TJX Companies Inc.',     'sector': 'ConsumerDisc', 'region': 'US', 'currency': 'USD'},
    {'symbol': 'MCD',     'name': "McDonald's Corp.",       'sector': 'ConsumerDisc', 'region': 'US', 'currency': 'USD'},
    {'symbol': 'ULTA',    'name': 'Ulta Beauty Inc.',       'sector': 'ConsumerDisc', 'region': 'US', 'currency': 'USD'},
    {'symbol': 'DECK',    'name': 'Deckers Outdoor Corp.',  'sector': 'ConsumerDisc', 'region': 'US', 'currency': 'USD'},
    {'symbol': 'KO',      'name': 'Coca-Cola Co.',          'sector': 'ConsumerStap', 'region': 'US', 'currency': 'USD'},
    {'symbol': 'PEP',     'name': 'PepsiCo Inc.',           'sector': 'ConsumerStap', 'region': 'US', 'currency': 'USD'},
    {'symbol': 'PG',      'name': 'Procter & Gamble Co.',   'sector': 'ConsumerStap', 'region': 'US', 'currency': 'USD'},
    {'symbol': 'WMT',     'name': 'Walmart Inc.',           'sector': 'ConsumerStap', 'region': 'US', 'currency': 'USD'},

    # === USA (momentum_100 – Financials) ===
    {'symbol': 'MA',      'name': 'Mastercard Inc.',        'sector': 'Financials',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'SPGI',    'name': 'S&P Global Inc.',        'sector': 'Financials',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'BLK',     'name': 'BlackRock Inc.',         'sector': 'Financials',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'ICE',     'name': 'Intercontinental Exch.', 'sector': 'Financials',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'MSCI',    'name': 'MSCI Inc.',              'sector': 'Financials',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'GS',      'name': 'Goldman Sachs Group',    'sector': 'Financials',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'COIN',    'name': 'Coinbase Global Inc.',   'sector': 'Financials',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'BAC',     'name': 'Bank of America Corp.',  'sector': 'Financials',   'region': 'US', 'currency': 'USD'},

    # === USA (momentum_100 – Industrials/Energy) ===
    {'symbol': 'ENPH',    'name': 'Enphase Energy Inc.',    'sector': 'Energy',       'region': 'US', 'currency': 'USD'},
    {'symbol': 'FSLR',    'name': 'First Solar Inc.',       'sector': 'Energy',       'region': 'US', 'currency': 'USD'},
    {'symbol': 'ODFL',    'name': 'Old Dominion Freight',   'sector': 'Industrials',  'region': 'US', 'currency': 'USD'},
    {'symbol': 'AXON',    'name': 'Axon Enterprise Inc.',   'sector': 'Industrials',  'region': 'US', 'currency': 'USD'},
    {'symbol': 'CPRT',    'name': 'Copart Inc.',            'sector': 'Industrials',  'region': 'US', 'currency': 'USD'},
    {'symbol': 'FICO',    'name': 'Fair Isaac Corp.',       'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'DE',      'name': 'Deere & Co.',            'sector': 'Industrials',  'region': 'US', 'currency': 'USD'},
    {'symbol': 'ETN',     'name': 'Eaton Corp.',            'sector': 'Industrials',  'region': 'US', 'currency': 'USD'},
    {'symbol': 'HON',     'name': 'Honeywell International','sector': 'Industrials',  'region': 'US', 'currency': 'USD'},
    {'symbol': 'GNRC',    'name': 'Generac Holdings Inc.',  'sector': 'Industrials',  'region': 'US', 'currency': 'USD'},

    # === USA/Global (momentum_100 – ADRs & Global Leaders) ===
    {'symbol': 'MELI',    'name': 'MercadoLibre Inc.',      'sector': 'ConsumerDisc', 'region': 'US', 'currency': 'USD'},
    {'symbol': 'SE',      'name': 'Sea Limited',            'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'TSM',     'name': 'Taiwan Semiconductor',   'sector': 'Technology',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'NVO',     'name': 'Novo Nordisk A/S ADR',   'sector': 'Healthcare',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'AMGN',    'name': 'Amgen Inc.',             'sector': 'Healthcare',   'region': 'US', 'currency': 'USD'},
    {'symbol': 'ABNB',    'name': 'Airbnb Inc.',            'sector': 'ConsumerDisc', 'region': 'US', 'currency': 'USD'},

    # === EUROPA (momentum_100) ===
    {'symbol': 'MUV2.DE', 'name': 'Munich Re',              'sector': 'Financials',   'region': 'DE', 'currency': 'EUR'},
    {'symbol': 'RHM.DE',  'name': 'Rheinmetall AG',         'sector': 'Industrials',  'region': 'DE', 'currency': 'EUR'},
    {'symbol': 'EOAN.DE', 'name': 'E.ON SE',                'sector': 'Energy',       'region': 'DE', 'currency': 'EUR'},
    {'symbol': 'LIN.DE',  'name': 'Linde PLC',              'sector': 'Materials',    'region': 'DE', 'currency': 'EUR'},
    {'symbol': 'SAN.PA',  'name': 'Sanofi SA',              'sector': 'Healthcare',   'region': 'FR', 'currency': 'EUR'},
    {'symbol': 'SU.PA',   'name': 'Schneider Electric',     'sector': 'Industrials',  'region': 'FR', 'currency': 'EUR'},
    {'symbol': 'SAF.PA',  'name': 'Safran SA',              'sector': 'Industrials',  'region': 'FR', 'currency': 'EUR'},
    {'symbol': 'AI.PA',   'name': 'Air Liquide SA',         'sector': 'Materials',    'region': 'FR', 'currency': 'EUR'},
    {'symbol': 'KER.PA',  'name': 'Kering SA',              'sector': 'ConsumerDisc', 'region': 'FR', 'currency': 'EUR'},
    {'symbol': 'DG.PA',   'name': 'Vinci SA',               'sector': 'Industrials',  'region': 'FR', 'currency': 'EUR'},
    {'symbol': 'DSY.PA',  'name': 'Dassault Systèmes SE',   'sector': 'Technology',   'region': 'FR', 'currency': 'EUR'},
    {'symbol': 'HEIA.AS', 'name': 'Heineken NV',            'sector': 'ConsumerStap', 'region': 'NL', 'currency': 'EUR'},
    {'symbol': 'PRX.AS',  'name': 'Prosus NV',              'sector': 'Technology',   'region': 'NL', 'currency': 'EUR'},
    {'symbol': 'WKL.AS',  'name': 'Wolters Kluwer NV',      'sector': 'Technology',   'region': 'NL', 'currency': 'EUR'},
    {'symbol': 'STMN.SW', 'name': 'STMicroelectronics',     'sector': 'Technology',   'region': 'CH', 'currency': 'CHF'},

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

    # === BENCHMARK (Regime-Filter — kein Handelsobjekt) ===
    {'symbol': 'SPY',     'name': 'SPDR S&P 500 ETF',    'sector': 'Benchmark',    'region': 'US', 'currency': 'USD'},
]

# Wechselkurs-Paare (Basis: EUR)
FOREX_PAIRS = ['EURUSD=X', 'EURGBP=X', 'EURJPY=X', 'EURCHF=X',
               'EURHKD=X', 'EURKRW=X', 'EURAUD=X']
