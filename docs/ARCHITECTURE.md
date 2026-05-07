# TradePlatform — Architekturdokumentation

**Stand:** 2026-05-07  
**Branch:** master  
**Stack:** Python 3.12, Flask, PostgreSQL, SocketIO, IBKR Gateway, yfinance

---

## Inhaltsverzeichnis

1. [Überblick](#1-überblick)
2. [Verzeichnisstruktur](#2-verzeichnisstruktur)
3. [Datenmodelle](#3-datenmodelle)
4. [Services](#4-services)
5. [API-Routen](#5-api-routen)
6. [Konfiguration](#6-konfiguration)
7. [Datenfluss: Trading-Zyklen](#7-datenfluss-trading-zyklen)
8. [Frontend](#8-frontend)
9. [Tests](#9-tests)
10. [Bekannte Schwächen](#10-bekannte-schwächen)

---

## 1. Überblick

TradePlatform ist eine autonome Flask-basierte Handelsplattform mit drei Betriebsmodi:

| Modus | Beschreibung |
|-------|-------------|
| **Simulation** | Virtuelles Portfolio, kein echtes Geld, Paper-Trades in DB |
| **Approval** | Bot schlägt Trades vor, User genehmigt manuell |
| **IBKR Live/Paper** | Echte Orders über Interactive Brokers Gateway |

**Kernkomponenten:**

```
Scheduler (15 Min)
  └─ Signalgenerierung → Trading-Entscheidung → IBKR-Order / Sim-Trade
       ↑                                              ↓
  yfinance (Kursdaten)                         PostgreSQL (DB)
                                                     ↓
                                          SocketIO → Browser (SPA)
```

---

## 2. Verzeichnisstruktur

```
TradePlatform_multiuser/
├── app.py                    # Flask-Factory, Scheduler, WebSocket-Events
├── config.py                 # Alle Parameter (Trading, DB, IBKR, Universum)
├── models.py                 # 21 SQLAlchemy-Tabellen
├── run.py                    # Startskript (SocketIO, Port 5000/5001)
│
├── routes/                   # 9 API-Blueprints
│   ├── api.py                # Legacy-Stub
│   ├── auth.py               # Login/Logout
│   ├── portfolios.py         # Portfolio-CRUD
│   ├── trading.py            # Account, Positionen, Signale
│   ├── simulations.py        # Historische Replays
│   ├── scenarios.py          # Scenario-Batches
│   ├── strategies.py         # Strategie-CRUD
│   ├── proposals.py          # Approval-Workflow
│   ├── ibkr.py               # IBKR-Status und manuelle Orders
│   └── common.py             # Shared Helpers
│
├── services/                 # Business-Logik
│   ├── algorithm.py          # Indikatoren, Scoring, Optimierung
│   ├── trading_engine.py     # Sim-Buy/Sell, Position-Management
│   ├── live_runner.py        # IBKR-Trading-Zyklus
│   ├── ibkr_connector.py     # Connection-Pool, Order-Ausführung
│   ├── proposal_generator.py # Tagesvorschläge für Approval-Modus
│   ├── data_fetcher.py       # yfinance-Integration, Wechselkurse
│   ├── strategy_resolver.py  # Hierarchisches Parameter-Lookup
│   ├── replay_engine.py      # Historische Simulation
│   ├── telegram_notifier.py  # Bot-Commands, Benachrichtigungen
│   ├── strategy_store.py     # Strategie-Persistierung
│   ├── universe_store.py     # Aktien-Universum
│   └── scenario_store.py     # Test-Szenarien
│
├── repositories/             # Data-Access-Layer
│   ├── trading_repo.py       # Trade/Equity-Queries
│   ├── portfolio_repo.py     # Portfolio-Queries
│   └── simulation_repo.py    # Simulation-Queries
│
├── templates/
│   └── index.html            # Single-Page-App (875 Zeilen)
│
├── static/js/
│   ├── api.js                # API-Wrapper (159 Zeilen)
│   ├── app.js                # Haupt-UI-Logik (1690 Zeilen)
│   ├── simulations.js        # Simulation-Management (795 Zeilen)
│   └── ui.js                 # UI-Helper (156 Zeilen)
│
├── tests/                    # pytest-Suites (1542 Zeilen)
│   ├── conftest.py
│   ├── unit/
│   ├── integration/
│   └── system/
│
├── migrations/               # Alembic-Migrations (PostgreSQL)
└── docs/                     # Spezifikationen und Architekturdokumente
```

---

## 3. Datenmodelle

### 3.1 Übersicht (21 Tabellen)

```
┌─────────────────────────────────────────────────────────────┐
│ KERN                                                         │
│  Stock ──< Price                                            │
│  Stock ──< AlgoParams                                       │
│  ExchangeRate                                               │
│                                                             │
│ PORTFOLIO                                                   │
│  User ──< Portfolio ──< Account                            │
│  Portfolio >── Strategy ──< StrategyRule                   │
│  Portfolio ──< Position >── Stock                          │
│  Portfolio ──< Trade >── Stock                             │
│  Portfolio ──< EquityHistory                               │
│  Portfolio ──< Signal >── Stock                            │
│                                                             │
│ APPROVAL                                                    │
│  Portfolio ──< DailyProposal ──< ProposedOrder >── Stock  │
│                                                             │
│ SIMULATION                                                  │
│  User ──< SimulationRun ──< SimulationPosition >── Stock  │
│  SimulationRun ──< SimulationTrade >── Stock              │
│  SimulationRun ──< DecisionLog >── Stock                  │
│  SimulationRun ──< SimulationDailySnapshot                │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Wichtige Felder

**Portfolio**

| Feld | Typ | Beschreibung |
|------|-----|-------------|
| `type` | enum | `sim`, `ibkr_paper`, `ibkr_live` |
| `mode` | enum | `auto`, `approval` |
| `status` | enum | `active`, `inactive` |
| `ibkr_account_id` | str | z.B. `DUP859792` — muss gesetzt sein für IBKR-Trading |

**Position**

| Feld | Typ | Beschreibung |
|------|-----|-------------|
| `entry_price` | float | Einstiegspreis in nativer Währung |
| `entry_price_eur` | float | Einstiegspreis in EUR |
| `stop_loss` | float | Stop-Loss in **nativer Währung** |
| `take_profit` | float | Take-Profit in **nativer Währung** |
| `trailing_stop` | float | Trailing-Stop in **nativer Währung** |
| `highest_price` | float | Höchster Kurs seit Kauf (Trailing-Basis) |
| `reason` | str | Kaufbegründung; enthält `[IBKR PENDING]` wenn außerhalb Börsenzeiten |

> **Wichtig:** `stop_loss`, `take_profit` und `trailing_stop` werden in der **nativen Währung** des Instruments gespeichert (USD für NVDA, AUD für TNE.AX, EUR für europäische Titel). Die Vergleiche in `update_positions()` verwenden ebenfalls native Währung — ein rein numerischer Vergleich mit EUR-Preisen wäre falsch.

**Signal**

| Feld | Typ | Beschreibung |
|------|-----|-------------|
| `score` | float | 0–100, BUY ≥ 65, SELL ≤ 35 |
| `action` | str | `BUY`, `SELL`, `HOLD` |
| `rsi`, `macd`, `macd_signal` | float | Technische Indikatoren |
| `ema20`, `ema50` | float | Moving Averages |

---

## 4. Services

### 4.1 algorithm.py — Signalberechnung & Optimierung

**Technische Indikatoren:**

| Funktion | Beschreibung |
|----------|-------------|
| `calc_rsi(close, period=14)` | RSI via EWM |
| `calc_macd(close, fast=12, slow=26, signal=9)` | MACD-Linie, Signal, Histogram |
| `calc_bollinger(close, period=20, std_dev=2.0)` | Bollinger-Bänder |
| `calc_atr(high, low, close, period=14)` | Average True Range |

**Scoring — `compute_score()` — Gewichtung:**

| Indikator | Gewicht |
|-----------|---------|
| RSI | 25% |
| MACD | 20% |
| EMA-Crossover (Fast/Slow) | 20% |
| Bollinger | 15% |
| Analyst-Score | 10% |
| Sektor-Momentum | 10% |

Ergebnis: Score 0–100 → `BUY` (≥65), `SELL` (≤35), `HOLD`

**Optimierung — `optimize_parameters()`:**  
Brute-Force über Parameter-Grid (RSI, EMA, MACD, Bollinger), Fitness = Sharpe-Ratio. Ergebnis wird pro Aktie als `AlgoParams` gespeichert.

**Reason-Text — `_build_reason(row, params, score, analyst_score, sector_score, action)`:**  
Generiert menschenlesbaren Begründungstext. Filtert Indikatoren nach Signal-Richtung — bei `BUY` nur bullische Signale, bei `SELL` nur bärische.

---

### 4.2 trading_engine.py — Sim-Trading-Zyklus

**Kostenmodell:**

```
Commission = max(Positionswert × 0,1%, 1 EUR)
Spread     = Positionswert × 0,05%
```

**Positionsgröße — `calc_position_size()`:**

```
Risiko pro Trade = 2% des Eigenkapitals
Größe via ATR:   risk / (2 × ATR_EUR) × entry_EUR
Ohne ATR:        equity × 2% / 5%
Score-Faktor:    Anpassung ±50% basierend auf Score
Grenzen:         min 3%, max 20% des Eigenkapitals, max 98% des Cash
```

**Stop-Loss / Take-Profit — `calc_stop_loss()` / `calc_take_profit()`:**

```
Stop-Loss   = entry_price - 2 × ATR  OR  entry × (1 - 5%)
Take-Profit = entry_price + max(2,5 × Risiko, entry × 15%)
```

**Schutz vor Sofort-Wiederkauf nach SL/TP:**  
`update_positions()` gibt `sold_stock_ids` zurück. Die BUY-Schleife in `_execute_cycle_for_portfolio()` überspringt Aktien die im selben Zyklus verkauft wurden.

---

### 4.3 live_runner.py — IBKR-Live-Trading

**Unterschiede zum Sim-Modus:**

| Aspekt | Sim | IBKR |
|--------|-----|------|
| Stückzahl | Bruchteil (float) | Ganzzahl (int) |
| Preisquelle | DB-Schlusskurs | IBKR Fill-Preis |
| Ausführung | Sofort (DB) | Async via Gateway |
| Börse geschlossen | n/a | `OrderPendingError` → `[IBKR PENDING]` in DB |

**Account-Sync:**  
Vor jedem IBKR-Portfoliozyklus wird `get_account_values()` aufgerufen. `account.cash_eur` und `account.equity_eur` werden mit dem echten IBKR-Stand synchronisiert.

**Portfolio-Guard:**  
Portfolios ohne `ibkr_account_id` werden übersprungen — verhindert versehentliche Orders auf den Default-Account.

---

### 4.4 ibkr_connector.py — Connection Pool & Order-Engine

**`IBKRConnectionPool`:**  
Singleton-Pool mit einer Verbindung pro `(host, port)`. Connector wird bei Bedarf erstellt und wiederverwendet.

**Circuit-Breaker:**  
Nach 5 aufeinanderfolgenden Fehlern: 5 Minuten Pause. Verhindert endlose Reconnect-Loops bei Gateway-Ausfall.

**`place_market_order(symbol, qty, action, account, currency)`:**

```
→ _resolve_contract(symbol):  Suffix-Map bestimmt Exchange/Währung
   '.DE' → XETRA/EUR, '.L' → LSE/GBP, '.PA' → Euronext/EUR
   (keine Suffix) → SMART/USD
→ ib.placeOrder(contract, MarketOrder)
→ Warte auf Fill (30s Timeout):
   Filled      → (fill_price, fill_qty)
   PreSubmitted → OrderPendingError(symbol, order_id)
   Timeout     → TimeoutError
```

---

### 4.5 strategy_resolver.py — Hierarchisches Parameter-Lookup

```
Defaults (config.py)
  └─ Strategy.params (global für Portfolio)
       └─ StrategyRule level='market' (Region)
            └─ StrategyRule level='sector' (Branche)
                 └─ StrategyRule level='stock' (Symbol)

Feinste Regel gewinnt.
```

**Beispiel:** `buy_threshold` default=65 → Strategy setzt 70 → Sektor-Regel "Technology" setzt 75 → Ergebnis: 75.

---

### 4.6 data_fetcher.py — Kursdaten & Wechselkurse

- **Quelle:** yfinance (Batches von ~20 Symbolen gleichzeitig)
- **Inkrementell:** Nur neue Tage seit letztem DB-Eintrag werden gespeichert
- **EUR-Konvertierung:** `close_eur = close / fx_rate` direkt beim Speichern
- **Fallback:** Bei API-Fehler hardcodierte Wechselkurse

---

### 4.7 proposal_generator.py — Approval-Modus

Täglich um 08:00 Uhr MEZ:

1. Signale für Vortag berechnen
2. Für alle `mode='approval'`-Portfolios: `DailyProposal` + `ProposedOrder`-Einträge anlegen
3. User genehmigt/lehnt Einzelorders ab
4. `POST /api/proposals/<id>/execute` führt genehmigte Orders aus
5. Um 22:00 Uhr: offene Proposals auf `expired` setzen

---

## 5. API-Routen

### auth
| Methode | Route | Beschreibung |
|---------|-------|-------------|
| POST | `/api/auth/login` | Anmelden, Portfolio aktivieren |
| POST | `/api/auth/logout` | Session beenden |
| GET | `/api/auth/me` | Aktueller User + Portfolios |

### portfolios
| Methode | Route | Beschreibung |
|---------|-------|-------------|
| GET | `/api/portfolios` | Alle Portfolios des Users |
| POST | `/api/portfolios` | Neues Portfolio + Account |
| PUT | `/api/portfolios/<id>` | Name, Mode, Status ändern |
| DELETE | `/api/portfolios/<id>` | Portfolio löschen |
| POST | `/api/portfolios/<id>/activate` | Aktives Portfolio in Session setzen |

### trading
| Methode | Route | Beschreibung |
|---------|-------|-------------|
| GET | `/api/account` | Cash, Equity, P&L |
| GET | `/api/positions` | Offene Positionen |
| GET | `/api/trades` | Trade-History |
| GET | `/api/trades/stats` | Win-Rate, Profit-Factor |
| GET | `/api/signals` | Heutige Signale |
| GET | `/api/equity` | Equity-Kurve (60 Tage) |
| GET | `/api/prices/<symbol>` | OHLCV-Kurse |
| POST | `/api/trading/run` | Handelszyklus manuell triggern |
| POST | `/api/trading/optimize` | Parameter-Optimierung triggern |
| GET | `/api/status` | Scheduler/IBKR/Sync-Status |

### proposals
| Methode | Route | Beschreibung |
|---------|-------|-------------|
| GET | `/api/portfolios/<pid>/proposals/today` | Heutiges Proposal |
| PATCH | `/api/proposals/<pid>/orders/<oid>` | Genehmigung togglen |
| POST | `/api/proposals/<pid>/execute` | Genehmigte Orders ausführen |

### ibkr
| Methode | Route | Beschreibung |
|---------|-------|-------------|
| GET | `/api/ibkr/status` | Gateway-Verbindungsstatus |
| POST | `/api/ibkr/connect` | Manuell reconnecten |
| GET | `/api/ibkr/account` | Echten IBKR-Kontostand abrufen |
| GET | `/api/ibkr/positions` | Echte IBKR-Positionen |
| POST | `/api/ibkr/order` | Manuelle Order (Admin) |

### simulations
| Methode | Route | Beschreibung |
|---------|-------|-------------|
| POST | `/api/simulations` | Neue Simulation starten |
| GET | `/api/simulations/<id>/metrics` | Sharpe, Drawdown, Win-Rate |
| GET | `/api/simulations/<id>/equity` | Daily Equity-Kurve |
| GET | `/api/simulations/<id>/decisions` | DecisionLogs mit Begründung |

### users (Admin)
| Methode | Route | Beschreibung |
|---------|-------|-------------|
| GET | `/api/users` | Alle User |
| POST | `/api/users` | Neuer User |
| PATCH | `/api/users/<id>/status` | Aktivieren/Deaktivieren |
| PUT | `/api/users/<id>/password` | Passwort zurücksetzen |

---

## 6. Konfiguration

### Wichtige Parameter (config.py)

```python
# Kapital & Risiko
STARTING_CAPITAL          = 10_000       # EUR
MAX_POSITIONS             = 10
MAX_POSITIONS_PER_SECTOR  = 3
RISK_PER_TRADE            = 0.02         # 2%
MAX_POSITION_SIZE         = 0.20         # 20% des Eigenkapitals
MIN_POSITION_SIZE         = 0.03         # 3%

# Kosten
COMMISSION_RATE           = 0.001        # 0,1%
MIN_COMMISSION            = 1.0          # EUR
SPREAD_RATE               = 0.0005       # 0,05%

# Stop / Take-Profit
DEFAULT_STOP_LOSS_PCT     = 0.05         # 5%
ATR_STOP_MULTIPLIER       = 2.0
DEFAULT_TAKE_PROFIT_PCT   = 0.15         # 15%
TRAILING_STOP_PCT         = 0.03         # 3%

# Signale
SIGNAL_THRESHOLD_BUY      = 65
SIGNAL_THRESHOLD_SELL     = 35

# Timing
TRADING_INTERVAL_MINUTES  = 15
DATA_UPDATE_INTERVAL_HOURS = 1
BACKTESTING_DAYS          = 365

# IBKR
LIVE_TRADING              = false        # ENV: LIVE_TRADING=true
IBKR_HOST                 = '127.0.0.1'
IBKR_PAPER_PORT           = 4002
IBKR_LIVE_PORT            = 4001
IBKR_CLIENT_ID            = 1
```

### Aktien-Universum

60+ Aktien in 15+ Märkten:

| Region | Beispiele | Währung |
|--------|-----------|---------|
| Deutschland | SAP, Allianz, BASF, BMW | EUR |
| Österreich | Verbund, OMV, Erste | EUR |
| Schweiz | Nestlé, Novartis, ABB | CHF |
| USA (Large Cap) | AAPL, MSFT, NVDA, JPM | USD |
| USA (Momentum) | PANW, CRWD, DDOG, PLTR | USD |
| Europa | LVMH, Airbus, ASML | EUR |
| UK | HSBC, Shell, AZN | GBP |
| Japan | Toyota, Sony, SoftBank | JPY |
| Australien | BHP, CBA, TNE | AUD |
| Korea | Samsung | KRW |
| Benchmark | SPY (nicht handelbar) | USD |

---

## 7. Datenfluss: Trading-Zyklen

### 7.1 Sim-Handelszyklus (alle 15 Minuten)

```
Scheduler
  │
  ├─ 1. Wechselkurse fetchen (yfinance EURUSD=X etc.)
  │
  ├─ 2. Kurse inkrementell updaten (update_prices_incremental)
  │     → Nur neue Tage seit letztem DB-Eintrag
  │     → close_eur = close / fx_rate direkt berechnen
  │
  ├─ 3. Signale generieren (algorithm.generate_signals)
  │     → 60+ Aktien, jeweils 400+ Tage Kurshistorie
  │     → RSI, MACD, EMA, Bollinger, ATR berechnen
  │     → compute_score() → 0-100
  │     → BUY ≥65, SELL ≤35, HOLD sonst
  │     → _build_reason() → Begründungstext (aktionskonform)
  │     → Signale nach Score DESC sortieren
  │
  ├─ 4. Pro Sim-Portfolio (type='sim', mode='auto', status='active'):
  │     │
  │     ├─ update_positions(fx_rates, portfolio_id)
  │     │   → Für jede Position: Latest Price laden
  │     │   → Trailing-Stop aktualisieren
  │     │   → Stop-Loss / Take-Profit prüfen → execute_sell() wenn getroffen
  │     │   → sold_stock_ids zurückgeben (verhindert Sofort-Wiederkauf)
  │     │
  │     ├─ BUY-Signale abarbeiten (sold_stock_ids übersprungen)
  │     │   → Max Positionen/Sektor prüfen
  │     │   → Positionsgröße berechnen (ATR-basiert)
  │     │   → execute_buy() → Position + Trade anlegen
  │     │
  │     └─ SELL-Signale abarbeiten (bei Signalwechsel)
  │         → execute_sell() → Position schließen, P&L berechnen
  │
  ├─ 5. Equity aktualisieren (alle aktiven Portfolios)
  │     → equity_eur = cash_eur + Σ(current_price_eur × shares)
  │     → EquityHistory-Snapshot speichern
  │
  └─ 6. SocketIO-Broadcast
        → 'trading_actions': ausgeführte Aktionen
        → 'portfolio_update': aktueller Portfolio-Snapshot
```

### 7.2 IBKR-Handelszyklus (alle 15 Minuten, wenn LIVE_TRADING=true)

```
Gleicher Ablauf wie Sim, aber für type IN ('ibkr_paper', 'ibkr_live'):

Pro IBKR-Portfolio:
  │
  ├─ Account-Sync von IBKR
  │   → conn.get_account_values(ibkr_account_id)
  │   → account.cash_eur = ibkr_data['cash']    ← echter IBKR-Stand
  │   → account.equity_eur = ibkr_data['equity']
  │
  ├─ update_live_positions() [SL/TP via Kursvergleich]
  │   → Gleich wie Sim, gibt sold_stock_ids zurück
  │
  └─ BUY-Signale → execute_live_buy():
      → Stückzahl als int (keine Bruchteile)
      → Portfolio ohne ibkr_account_id: überspringen
      → conn.place_market_order(symbol, qty, 'BUY', account_id)
      │
      ├─ Börse offen  → Fill (price, qty) → Position + Trade in DB
      └─ Börse zu    → OrderPendingError
                          → DB-Eintrag mit Schätzkurs
                          → Reason: '[IBKR PENDING]'
                          → Order wartet bei IBKR auf Marktöffnung
```

### 7.3 Approval-Zyklus (täglich)

```
08:00 MEZ:
  ├─ generate_daily_proposals() → DailyProposal + ProposedOrder
  └─ notify_signals() → Telegram-Nachricht

User via UI:
  ├─ GET /api/portfolios/<id>/proposals/today
  ├─ PATCH /api/proposals/<id>/orders/<oid>  (approved=true/false)
  └─ POST /api/proposals/<id>/execute
       → execute_buy() / execute_sell() für approved=true Orders

22:00 MEZ:
  └─ expire_stale_proposals() → status='expired'
```

### 7.4 Simulation-Replay (manuell, Hintergrund-Thread)

```
POST /api/simulations → run_historical_replay() in Background-Thread

Für jeden Trading-Tag (start_date → end_date):
  → Signale für Tag T berechnen (generate_signals_for_date)
  → Positionen prüfen (SL/TP)
  → BUY/SELL-Entscheidungen treffen
  → DecisionLog + SimulationTrade speichern
  → SimulationDailySnapshot (Equity-Kurve)

Nach Abschluss:
  → Sharpe-Ratio, Max-Drawdown, Win-Rate, Profit-Factor berechnen
  → Benchmark (SPY) vergleichen
  → status = 'completed'
```

---

## 8. Frontend

**Typ:** Single-Page-Application (SPA), kein Framework  
**Real-time:** SocketIO für Live-Updates  
**Charts:** TradingView Lightweight Charts (Equity-Kurve, Candlestick)

### 8.1 Tabs

| Tab | Beschreibung |
|-----|-------------|
| Dashboard | Equity-Kurve, Account-Übersicht, Top-Signale |
| Proposals | Tagesvorschläge genehmigen/ablehnen (Approval-Modus) |
| Positionen | Offene Positionen mit PnL, SL, TP |
| Trades | Trade-History |
| Signale | Aktuelle Signale mit Indikatoren |
| Strategien | Strategie-Editor, Regel-Hierarchie |
| Simulationen | Backtest starten, Ergebnisse analysieren |
| Admin | User-Verwaltung, IBKR-Status |

### 8.2 WebSocket-Events

| Event | Richtung | Beschreibung |
|-------|----------|-------------|
| `portfolio_update` | Server → Client | Vollständiger Portfolio-Snapshot (jede Minute) |
| `trading_actions` | Server → Client | Ausgeführte Aktionen nach Zyklus |
| `status` | Server → Client | Startup-Fortschritt |
| `request_update` | Client → Server | Explizites Update anfordern |

### 8.3 Key API-Calls (api.js)

```javascript
// Auth
login(username, password), logout(), me()

// Portfolio
getPortfolios(), activatePortfolio(id), createPortfolio(data)

// Trading
getAccount(), getPositions(), getTrades(), getSignals(), getEquity()

// Proposals
getTodayProposal(portfolioId), patchOrderApproval(pid, oid, approved)
executeProposal(proposalId)

// Simulation
createSimulation(payload), getSimulationMetrics(id)

// IBKR
ibkrStatus(), ibkrAccount(portfolioId), ibkrPositions(portfolioId)
```

---

## 9. Tests

```
tests/
├── conftest.py                      # Fixtures: App, DB, Admin-User, Regular-User
├── unit/
│   ├── test_models.py               # Modell-Instantiierung, to_dict()
│   ├── test_proposal_generator.py   # generate_daily_proposals()
│   └── test_strategy_resolver.py    # Hierarchisches Parameter-Lookup
├── integration/
│   ├── test_auth.py                 # Login-Flow, 401-Handling
│   ├── test_portfolios.py           # Portfolio-CRUD, Account-Init
│   ├── test_proposals.py            # Proposal-Generation, Approval, Execution
│   └── test_users.py                # User-CRUD, Password-Reset
└── system/
    ├── test_user_isolation.py        # Multi-User: Portfolio-Sichtbarkeit
    └── test_approval_workflow.py     # End-to-End: Proposal → Execution
```

**Stack:** pytest, Flask-Testing, SQLite in-memory  
**Externe Abhängigkeiten:** yfinance, Telegram, IBKR werden gemockt  
**Abdeckung:** ~40–50% (Schwerpunkt: Auth, Portfolio, Proposal)

---

## 10. Bekannte Schwächen

### 10.1 Performance

| Problem | Impact | Mögliche Lösung |
|---------|--------|-----------------|
| Alle 60+ Aktien sequenziell berechnet | Zyklus kann >14 Min dauern → Overlap | `ThreadPoolExecutor` für Signal-Berechnung |
| 400 Tage Kurshistorie pro Aktie pro Zyklus | Speicher- und Rechenaufwand | Inkrementelles Update: nur neue Tage berechnen |
| Keine DB-Indizes auf `trades.portfolio_id` / `positions.portfolio_id` | Langsamer bei vielen Trades | `CREATE INDEX` manuell anlegen |

### 10.2 Trading-Logik

| Problem | Impact | Mögliche Lösung |
|---------|--------|-----------------|
| Stop-Loss nur per Scheduler (15 Min) | Intraday-Gaps können SL überspringen | IBKR Stop-Orders als echte Conditional-Orders |
| Approval-Proposals basieren auf Yesterday-Daten | Signifikante Preisdivergenz bei Execution | Intraday-Refresh vor Execute |
| Kein Max-Drawdown-Limit auf Account-Ebene | Unbegrenzte Verluste möglich | `account.max_drawdown_pct` + Auto-Stop |

### 10.3 IBKR-Integration

| Problem | Impact | Mögliche Lösung |
|---------|--------|-----------------|
| `[IBKR PENDING]`-Positionen werden nicht mit echtem Fill-Preis aktualisiert | Entry-Preis in DB bleibt Schätzwert | Fill-Callback oder morgendlicher Sync |
| Kein Retry bei fehlgeschlagenen Orders | Order verloren ohne Benachrichtigung | Retry-Queue + Telegram-Alert |
| Keine echten Conditional-Orders bei IBKR | SL/TP nur in DB, nicht beim Broker | `place_stop_order()` / `place_limit_order()` im Connector |
| Circuit-Breaker global, nicht pro Account | Ein fehlerhafter Account blockiert alle | Account-spezifischer Backoff |

### 10.4 Daten

| Problem | Impact | Mögliche Lösung |
|---------|--------|-----------------|
| yfinance ohne Retry-Logik | Stale Prices bei API-Problemen (z.B. ASX) | Exponential Backoff, Alternative-Quellen |
| Analyst-Score hardcoded auf 50 | 10% Score-Gewicht ohne Datenbasis | API-Integration (z.B. Finnhub, Refinitiv) |
| DecisionLogs wachsen unbegrenzt | DB-Größe problematisch bei vielen Simulationen | Retention-Policy (z.B. 2 Jahre) |

### 10.5 Fehlende Features

- **Echtzeit-Preise:** Aktuell nur Tagesschlusskurse via yfinance; kein Intraday-Feed
- **News/Sentiment:** Kein externer Datenfeed für Nachrichten oder VIX
- **Portfolio-übergreifendes Risiko-Management:** Kein Blick auf Gesamtexposure über alle Portfolios
- **Deployment-Dokumentation:** Kein `docker-compose.yml`, keine vollständigen systemd-Unit-Templates
- **API-Authentifizierung:** Rate-Limiting vorhanden, aber kein API-Key-System für externe Clients
