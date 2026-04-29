# Ist-Spezifikation: TradePlatform (Stand 2026-04-29)

**Zweck:** Beschreibt was die Applikation heute kann — als Ausgangsbasis für die geplante Erweiterung auf Multi-User & Multi-Portfolio.

**Branch:** `master`  
**Verweis:** SPEC_multi_user_portfolios.md (Soll-Zustand)

---

## 1. Technologie-Stack

| Komponente | Technologie | Begründung |
|------------|------------|-----------|
| Sprache | Python 3.12 | Ecosystem für Finanzdata (yfinance, ib_insync, pandas) |
| Web-Framework | Flask 3.x + Flask-SocketIO | Einfach, bekannt; WebSocket für Portfolio-Push |
| Datenbank | PostgreSQL (psycopg3) | Multi-User-ready, JSONB, produktionsreif; SQLite-Support entfernt |
| ORM | SQLAlchemy 2.x | Model-Relationen, Query-Builder |
| Migrations | — | **FEHLT** — db.create_all() beim Start, keine Alembic-Migrationen |
| Scheduler | APScheduler (BackgroundScheduler) | Bereits integriert; 15-Min-Handelszyklus + 1-Min-Portfolio-Broadcast |
| IBKR-Anbindung | ib_insync | Async-fähig, höhere Abstraktion als TWS-API direkt |
| Kursdaten | yfinance | Kostenfrei, ~160 Symbole, OHLCV |
| Wechselkurse | yfinance (Forex-Pairs) | EUR-basiert, 7 Währungspaare |
| Benachrichtigung | Telegram Bot API | Polling-basiert, bestehende Integration |
| IB-Gateway | IB Gateway 10.x (Java, headless via Xvfb) | Paper-Trading auf Port 4002 |
| Auth | **KEINE** | Offene Applikation, kein Login |
| Deployment | Linux (systemd), Port 5000 | Direkter Start, kein Container |

---

## 2. Datenbank-Schema (Ist-Zustand)

### 2.1 Übersicht

```
stocks ────────────────────────────────────────────────────────────────────┐
  │                                                                         │
  │ 1:N                                                                     │
  ├──► prices          (OHLCV pro Tag, UniqueConstraint stock+date)        │
  ├──► positions       (GLOBAL — kein Portfolio, kein User)                │
  ├──► signals         (GLOBAL — kein Portfolio, kein User)                │
  └──► trades (via FK) (GLOBAL — kein Portfolio, kein User)               │
       ↓                                                                    │
  algo_params          (optimierte Parameter pro Aktie, 1:1 mit stock)    │
                                                                            │
account              (SINGLETÖN — genau 1 Zeile, kein User, kein Portfolio)
positions            (GLOBAL)
trades               (GLOBAL)
equity_history       (GLOBAL — 1 Zeile pro Tag)
exchange_rates       (EUR-Basiswechselkurse, 1 Zeile pro Pair+Datum)

-- Simulation (isoliert, keine Verbindung zum Live-Trading):
simulation_runs ──────────────────────────────────────────────────────────┐
  │                                                                         │
  ├──► simulation_positions   (Positionen im Replay)                       │
  ├──► simulation_trades      (Trades im Replay)                           │
  ├──► decision_logs          (jede Entscheidung des Algorithmus)          │
  └──► simulation_daily_snapshots (täglicher Equity-Snapshot)             │
```

### 2.2 Tabellen

#### `stocks`
```sql
id         SERIAL PRIMARY KEY
symbol     VARCHAR(20) UNIQUE NOT NULL    -- z.B. 'AAPL', 'SAP.DE', '7203.T'
name       VARCHAR(100) NOT NULL
sector     VARCHAR(50) NOT NULL           -- Technology, Healthcare, Financials, ...
region     VARCHAR(10) NOT NULL           -- US, DE, AT, CH, UK, JP, CN, KR, AU, FR, NL, EU
currency   VARCHAR(10) NOT NULL           -- USD, EUR, GBP, CHF, JPY, HKD, KRW, AUD
active     BOOLEAN DEFAULT TRUE
created_at TIMESTAMP
```
**Stand:** ~160 Aktien aus Deutschland, Österreich, Schweiz, Europa, USA, Japan, China, Südkorea, Australien + SPY als Benchmark.  
**Problem für Multi-Portfolio:** Aktien-Universum ist global definiert, nicht pro Portfolio konfigurierbar.

---

#### `prices`
```sql
id        SERIAL PRIMARY KEY
stock_id  INTEGER REFERENCES stocks(id)
date      DATE NOT NULL
open, high, low, close   FLOAT NOT NULL
volume    BIGINT DEFAULT 0
close_eur FLOAT                          -- EUR-umgerechneter Schlusskurs
UNIQUE(stock_id, date)
```
**Stand:** Bis 400 Tage Kursdaten pro Aktie, stündlich aktualisiert (geplant).  
**Problem:** close_eur wird beim Laden berechnet; bei Währungsschwankungen wird nicht rückwirkend angepasst.

---

#### `exchange_rates`
```sql
id    SERIAL PRIMARY KEY
pair  VARCHAR(20) NOT NULL    -- z.B. 'EURUSD'
date  DATE NOT NULL
rate  FLOAT NOT NULL          -- Fremdwährungseinheiten pro 1 EUR
UNIQUE(pair, date)
```
**Stand:** 7 Paare: EURUSD, EURGBP, EURJPY, EURCHF, EURHKD, EURKRW, EURAUD.

---

#### `account` ⚠️ Problem-Tabelle
```sql
id               SERIAL PRIMARY KEY
cash_eur         FLOAT NOT NULL DEFAULT 10000.0
equity_eur       FLOAT NOT NULL DEFAULT 10000.0
total_trades     INTEGER DEFAULT 0
winning_trades   INTEGER DEFAULT 0
total_commission FLOAT DEFAULT 0.0
updated_at       TIMESTAMP
```
**Problem:** Einzige Zeile, kein `portfolio_id`, kein `user_id`.  
**Für Multi-Portfolio:** Wird zu 1 Zeile pro Portfolio.

---

#### `positions` ⚠️ Problem-Tabelle
```sql
id                SERIAL PRIMARY KEY
stock_id          INTEGER REFERENCES stocks(id)
shares            FLOAT NOT NULL
entry_price       FLOAT NOT NULL          -- Originalwährung
entry_price_eur   FLOAT NOT NULL          -- EUR
entry_rate        FLOAT DEFAULT 1.0       -- EUR-Kurs beim Kauf
current_price     FLOAT
current_price_eur FLOAT
stop_loss         FLOAT                   -- Originalwährung
take_profit       FLOAT                   -- Originalwährung
trailing_stop     FLOAT                   -- Aktueller Trailing-Stop
highest_price     FLOAT                   -- Höchstpreis seit Kauf
cost_eur          FLOAT NOT NULL          -- Gesamtkosten inkl. Provision
commission_eur    FLOAT DEFAULT 0.0
opened_at         TIMESTAMP
reason            VARCHAR(200)            -- Kaufbegründung
```
**Problem:** Kein `portfolio_id`.  
**Für Multi-Portfolio:** `portfolio_id INTEGER REFERENCES portfolios(id) NOT NULL` ergänzen.

---

#### `trades` ⚠️ Problem-Tabelle
```sql
id             SERIAL PRIMARY KEY
stock_id       INTEGER REFERENCES stocks(id)
action         VARCHAR(10) NOT NULL       -- 'BUY' oder 'SELL'
shares         FLOAT NOT NULL
price          FLOAT NOT NULL             -- Originalwährung
price_eur      FLOAT NOT NULL
fx_rate        FLOAT DEFAULT 1.0
commission_eur FLOAT DEFAULT 0.0
total_eur      FLOAT NOT NULL
pnl_eur        FLOAT DEFAULT 0.0         -- Realisierter Gewinn/Verlust
pnl_pct        FLOAT DEFAULT 0.0
reason         VARCHAR(200)
executed_at    TIMESTAMP
```
**Problem:** Kein `portfolio_id`.  
**Für Multi-Portfolio:** `portfolio_id INTEGER REFERENCES portfolios(id) NOT NULL` ergänzen.

---

#### `signals`
```sql
id            SERIAL PRIMARY KEY
stock_id      INTEGER REFERENCES stocks(id)
date          DATE NOT NULL
score         FLOAT NOT NULL              -- 0-100
action        VARCHAR(10)                 -- BUY, SELL, HOLD
rsi           FLOAT
macd          FLOAT
macd_signal   FLOAT
ema20, ema50  FLOAT
bb_upper, bb_lower FLOAT
atr           FLOAT
analyst_score FLOAT DEFAULT 50.0
sector_score  FLOAT DEFAULT 50.0
created_at    TIMESTAMP
```
**Stand:** Global generiert, kein Portfolio-Bezug.  
**Für Multi-Portfolio:** Optional `portfolio_id` (nullable = system-weit, gesetzt = portfolio-spezifisch).

---

#### `algo_params`
```sql
id              SERIAL PRIMARY KEY
stock_id        INTEGER REFERENCES stocks(id) UNIQUE
rsi_period      INTEGER DEFAULT 14
rsi_oversold    FLOAT DEFAULT 35.0
rsi_overbought  FLOAT DEFAULT 65.0
ema_fast        INTEGER DEFAULT 20
ema_slow        INTEGER DEFAULT 50
macd_fast       INTEGER DEFAULT 12
macd_slow       INTEGER DEFAULT 26
macd_signal     INTEGER DEFAULT 9
bb_period       INTEGER DEFAULT 20
bb_std          FLOAT DEFAULT 2.0
sharpe_ratio    FLOAT DEFAULT 0.0
backtest_return FLOAT DEFAULT 0.0
optimized_at    TIMESTAMP
```
**Stand:** Globale, pro Aktie optimierte Parameter aus 1-Jahres-Backtest.  
**Problem:** Ein Parametersatz für alle Portfolios.  
**Für Multi-Portfolio:** Bleibt global (Default-Basis); Portfolio-spezifische Overrides kommen über `strategy_rules`.

---

#### `equity_history` ⚠️ Problem-Tabelle
```sql
id              SERIAL PRIMARY KEY
date            DATE NOT NULL UNIQUE      -- nur 1 Eintrag pro Tag global
equity_eur      FLOAT NOT NULL
cash_eur        FLOAT NOT NULL
positions_value FLOAT DEFAULT 0.0
daily_pnl       FLOAT DEFAULT 0.0
```
**Problem:** Kein `portfolio_id`, UNIQUE auf `date` erlaubt nur 1 Portfolio.  
**Für Multi-Portfolio:** `portfolio_id` ergänzen, UNIQUE auf `(portfolio_id, date)` ändern.

---

#### `simulation_runs`
```sql
id                   SERIAL PRIMARY KEY
name                 VARCHAR(120) NOT NULL
mode                 VARCHAR(40) DEFAULT 'historical_replay'
status               VARCHAR(20)           -- queued/running/completed/failed/cancel_requested
strategy_name        VARCHAR(80)           -- referenziert JSON-Strategie (kein FK!)
strategy_version     VARCHAR(32)
universe_name        VARCHAR(80)           -- referenziert JSON-Universum (kein FK!)
start_date, end_date DATE NOT NULL
current_date         DATE                  -- Fortschritt während Replay
step_interval        VARCHAR(10) DEFAULT '1d'
initial_capital_eur  FLOAT DEFAULT 10000.0
final_equity_eur     FLOAT
total_return_pct     FLOAT
benchmark_return_pct FLOAT
max_drawdown_pct     FLOAT
sharpe_ratio         FLOAT
win_rate             FLOAT
profit_factor        FLOAT
total_trades         INTEGER
winning_trades       INTEGER
losing_trades        INTEGER
notes                TEXT                  -- enthält tax_summary als JSON-String
error_message        TEXT
started_at, finished_at TIMESTAMP
created_at, updated_at  TIMESTAMP
```
**Stand:** Isoliert vom Live-Trading, kein User-Bezug.  
**Für Multi-Portfolio:** `user_id` und/oder `portfolio_id` ergänzen (optional, Simulationen können User gehören).

---

#### `simulation_positions`, `simulation_trades`, `decision_logs`, `simulation_daily_snapshots`
Alle 1:N zu `simulation_runs` (CASCADE DELETE). Schema analog zu Live-Tabellen, zusätzlich `sim_date` als Simulationsdatum.

---

## 3. Services / Backend-Logik (Ist-Zustand)

### 3.1 Datei-Übersicht

```
services/
  algorithm.py          -- Scoring-Algorithmus + Backtesting-Optimierung
  data_fetcher.py       -- yfinance-Kursdaten + Wechselkurse
  trading_engine.py     -- Live-Handelszyklus (Simulations-Modus, kein IBKR)
  live_runner.py        -- IBKR-Handelszyklus (NEU, direkte Orders)
  replay_engine.py      -- Historische Simulation (Replay-Engine)
  scenario_store.py     -- JSON-basierter Szenario-Store
  strategy_store.py     -- JSON-basierter Strategie-Store
  universe_store.py     -- JSON-basierter Universum-Store
  telegram_notifier.py  -- Telegram-Benachrichtigungen
  ibkr_connector.py     -- IBKR-Verbindung (NEU)
```

---

### 3.2 `algorithm.py` — Scoring-Algorithmus

**Was es tut:**
- Berechnet für jede Aktie einen Score 0–100 aus technischen Indikatoren
- Indikatoren: RSI, MACD, EMA-Crossover, Bollinger-Bands, ATR, Trend
- Zusatz-Scores: Analyst-Score (50.0 default), Sektor-Score, Regime-Filter (SPY SMA200)
- Gewichtung: `{rsi:25, macd:25, ema_trend:20, bb:15, atr:5, analyst:5, sector:5}`
- `run_optimization_for_all(app)`: optimiert Parameter für alle Aktien via 1-Jahres-Backtest
- Optimierung: Grid-Search über RSI/EMA-Perioden, bewertet nach Sharpe-Ratio

**Wo die Parameter herkommen:**
1. `algo_params` Tabelle (falls vorhanden)
2. Defaults aus config.py falls nicht optimiert

**Problem für Multi-Portfolio:** Parameter sind global — kein Mechanismus um Portfolio-spezifische Gewichtungen zu verwenden.

---

### 3.3 `data_fetcher.py` — Kursdaten

**Was es tut:**
- `fetch_multiple_prices(symbols, days)` → yfinance Batch-Download
- `store_prices_to_db(...)` → Speichert in `prices` Tabelle
- `update_prices_incremental(app, universe)` → Nur fehlende Daten laden
- `fetch_exchange_rates()` → 7 EUR-Forex-Paare

**Problem:** Kein Portfolio-Bezug, global.

---

### 3.4 `trading_engine.py` — Autonomer Handelszyklus (Simulations-Modus)

**Was es tut:**
- `run_trading_cycle(app)` — wird alle 15 Min vom Scheduler aufgerufen
- Liest globales `Account`-Objekt (erste Zeile)
- Generiert Signale über `algorithm.py`
- Kauft wenn: Score >= 65 (SIGNAL_THRESHOLD_BUY), Kapital vorhanden, nicht zu viele Positionen
- Verkauft wenn: Score <= 35 (SIGNAL_THRESHOLD_SELL), SL/TP/Trailing-Stop ausgelöst
- Schreibt in globale `positions`, `trades`, `account` Tabellen
- **Simulations-Modus:** Kein IBKR, fiktive Fills zum aktuellen Kurs
- Regime-Filter: Wenn SPY < SMA200 → kein Kaufen (nur Sells erlaubt)

**Handelsregeln (aus config.py):**
- Max 10 Positionen gleichzeitig
- Max 3 pro Sektor
- Max 20% Portfoliowert pro Position
- Min 3% Portfoliowert pro Position
- 2% Kapital-Risiko pro Trade (bestimmt Stückzahl via ATR-Stop)
- Stop-Loss: max(5%, 2x ATR) unter Einstieg
- Take-Profit: 15% über Einstieg
- Trailing-Stop: 3% unter Höchstpreis seit Kauf
- Provision: 0,1% (min 1 EUR)

**Problem für Multi-Portfolio:**
- `Account.query.first()` — greift immer auf das erste (einzige) Konto zu
- Konfiguration kommt aus `config.py` — global, nicht pro Portfolio

---

### 3.5 `live_runner.py` — IBKR Live-Handelszyklus (NEU)

**Was es tut:**
- `run_live_trading_cycle(app)` — Drop-in-Ersatz für `run_trading_cycle`
- Gleiche Logik wie `trading_engine.py`, aber Orders gehen an IBKR
- Ganze Stückzahlen (keine Fraktionen)
- Fill-Preis aus IBKR-Antwort, nicht berechnet
- `execute_live_buy / execute_live_sell` — schreiben in globale DB-Tabellen

**Aktivierung:** `.env` → `LIVE_TRADING=true` + `app.py` wählt den richtigen Zyklus

**Problem für Multi-Portfolio:** Noch single-account (wie trading_engine), kein `portfolio_id`.

---

### 3.6 `replay_engine.py` — Historische Simulation

**Was es tut:**
- `create_simulation_run(payload)` → Erstellt `SimulationRun` in DB
- `run_historical_replay(app, run_id)` → Führt Simulation Tag für Tag aus
- Läuft in eigenem Thread (Background)
- Unterstützt `cancel_requested` Status (wird beim nächsten Tag geprüft)
- Berechnet am Ende: KeSt-Berechnung (Österreich), Provisions-Summary
- Benchmark: SPY Buy-and-Hold

**Strategie-Laden:** Aus `data/strategies.json` via `strategy_store.py`  
**Universum-Laden:** Aus `data/universes.json` via `universe_store.py`

**Problem für Multi-Portfolio:** Keine User-Zuordnung. Simulationen sind global sichtbar.

---

### 3.7 Strategie-System (Ist-Zustand) ⚠️ Kritisches Problem

**Aktuell:** Strategien sind in `data/strategies.json` als JSON-Datei gespeichert.

```json
{
  "strategies": [
    {
      "id": "default_v1",
      "name": "Standard Strategie v1",
      "active": true,
      "approved_for_live": false,
      "params": {
        "signal_threshold_buy": 65,
        "signal_threshold_sell": 35,
        "max_positions": 10,
        "max_position_size": 0.20,
        ...
      }
    }
  ]
}
```

**`strategy_store.py`:** CRUD auf JSON-Datei
- `list_strategies()` → alle Strategien
- `get_strategy(id)` → eine Strategie
- `upsert_strategy(data)` → anlegen oder updaten
- `set_active_strategy(id)` → setzt `active: true`
- `approve_strategy_for_live(id)` → setzt `approved_for_live: true`

**Problem für Multi-Portfolio:**
1. JSON-Datei ist nicht pro User/Portfolio
2. Keine Datenbank-FK-Integrität
3. Concurrent-Write-Probleme möglich
4. Strategie-Parameter-Hierarchie (Portfolio→Markt→Sektor→Aktie) fehlt komplett

**Für Multi-Portfolio:** Strategien in DB-Tabelle `strategies` + `strategy_rules` migrieren.

---

### 3.8 Universum-System (Ist-Zustand) ⚠️ Problem

**Aktuell:** `data/universes.json` — Liste von benannten Aktien-Gruppen:
```json
{
  "universes": [
    {
      "id": "default_global_stocks",
      "name": "Global Stocks (Default)",
      "symbols": ["AAPL", "MSFT", "SAP.DE", ...]
    },
    {
      "id": "momentum_100",
      "name": "Momentum 100",
      "symbols": [...]
    }
  ]
}
```

**Problem:** JSON-Datei, kein DB-Bezug, kein User-Bezug.  
**Für Multi-Portfolio:** Universum-Auswahl pro Portfolio (FK zu DB-Tabelle oder im Portfolio-Objekt gespeichert).

---

### 3.9 Szenario-System (Ist-Zustand)

**`data/scenarios.json`:** Vordefinierte Simulations-Konfigurationen:
```json
{
  "scenarios": [
    {
      "id": "...",
      "name": "...",
      "start_date": "2020-01-01",
      "end_date": "2024-12-31",
      "initial_capital_eur": 10000,
      "strategy_id": "default_v1",
      "universe_name": "default_global_stocks"
    }
  ],
  "batches": [...]
}
```

**Problem:** Kein User-Bezug.  
**Für Multi-Portfolio:** Szenarien könnten User gehören oder global bleiben (Admin-Scope).

---

### 3.10 Scheduler (Ist-Zustand)

```
APScheduler (BackgroundScheduler, Timezone: Europe/Vienna)
  │
  ├── trading_cycle     alle 15 Min  → trading_engine.run_trading_cycle()
  │                                    ODER live_runner.run_live_trading_cycle()
  │                                    (je nach LIVE_TRADING env var)
  │
  └── equity_broadcast  jede Minute → WebSocket Push (portfolio_update Event)
                                       Liest Account.query.first()
```

**Problem für Multi-Portfolio:**
1. Nur 1 Trading-Zyklus für alle Portfolios
2. equity_broadcast schickt immer das erste (einzige) Konto

---

### 3.11 Telegram-Notifier (Ist-Zustand)

**Was es tut:**
- Polling-basiert (Threading), startet beim App-Start
- Empfängt: `/status`, `/portfolio`, `/positions`, `/buy SYMBOL SHARES`, `/sell SYMBOL SHARES`
- Sendet: Trade-Benachrichtigungen, Fehler-Alerts, Batch-Completion
- Credentials: `TELEGRAM_TOKEN` + `TELEGRAM_CHAT_ID` aus `.env`

**Problem für Multi-Portfolio:** Commands sind global, kein Portfolio-Selektion via Telegram.

---

### 3.12 IBKR-Connector (NEU, Ist-Zustand)

**`services/ibkr_connector.py`:** Singleton `connector = IBKRConnector()`
- Threadsafe asyncio-Loop im Background-Thread
- `connect()`, `disconnect()`, `is_connected()`, `ensure_connected()`
- `place_market_order(symbol, qty, action)` → `(fill_price, fill_qty)`
- `get_account_values()` → `{cash, equity, buying_power, unrealized_pnl}`
- `get_positions()` → Liste offener IBKR-Positionen
- Wartet auf Fill-Status (max 20 Sekunden)

**Problem für Multi-Portfolio:** Ein Gateway-Connector für alle Portfolios. Multi-Account (verschiedene `client_id`) nicht implementiert.

---

## 4. API-Endpunkte (Ist-Zustand)

Alle Endpunkte unter `/api`, kein Authentication-Schutz.

### 4.1 Account & Portfolio

| Methode | Endpunkt | Beschreibung |
|---------|---------|-------------|
| GET | `/api/account` | Kontostand (cash, equity, PnL, Trade-Stats) |
| GET | `/api/positions` | Offene Positionen |
| GET | `/api/portfolio/summary` | Positionen nach Sektor und Region |
| GET | `/api/trades` | Trade-Historie (limit param) |
| GET | `/api/trades/stats` | Trade-Statistiken (Win-Rate, avg PnL) |
| GET | `/api/equity` | Equity-Kurve (days param) |
| GET | `/api/watchlist` | Alle Aktien mit Score und Signal |
| GET | `/api/signals` | Heutige Signale |
| GET | `/api/status` | System-Status (Aktien geladen, Positionen, etc.) |

### 4.2 Kursdaten & Algorithmus

| Methode | Endpunkt | Beschreibung |
|---------|---------|-------------|
| GET | `/api/prices/<symbol>` | Kursdaten einer Aktie (days param) |
| GET | `/api/algo/params` | Optimierte Algorithmus-Parameter |

### 4.3 Strategien & Universa

| Methode | Endpunkt | Beschreibung |
|---------|---------|-------------|
| GET | `/api/strategies` | Alle Strategien (aus JSON) |
| POST | `/api/strategies/active` | Aktive Strategie setzen |
| PUT | `/api/strategies/<id>` | Strategie updaten |
| POST | `/api/strategies/<id>/approve-live` | Für Live-Trading freigeben |
| GET | `/api/universes` | Alle Universen (aus JSON) |

### 4.4 Szenarien & Batches

| Methode | Endpunkt | Beschreibung |
|---------|---------|-------------|
| GET | `/api/scenarios` | Alle Szenarien |
| PUT | `/api/scenarios/<id>` | Szenario updaten |
| DELETE | `/api/scenarios/<id>` | Szenario löschen |
| POST | `/api/scenario-batches` | Batch anlegen |
| GET | `/api/scenario-batches/<id>` | Batch-Status |
| DELETE | `/api/scenario-batches/<id>` | Batch löschen |
| POST | `/api/scenario-batches/<id>/run` | Batch starten |

### 4.5 Simulationen

| Methode | Endpunkt | Beschreibung |
|---------|---------|-------------|
| GET | `/api/simulations` | Alle Simulations-Runs |
| DELETE | `/api/simulations` | Alle Simulations-Runs löschen |
| POST | `/api/simulations` | Neue Simulation erstellen + starten |
| GET | `/api/simulations/<id>` | Detail-Ansicht mit Fortschritt |
| DELETE | `/api/simulations/<id>` | Einzelnen Run löschen |
| POST | `/api/simulations/<id>/cancel` | Laufenden Run abbrechen |
| GET | `/api/simulations/<id>/equity` | Equity-Kurve der Simulation |
| GET | `/api/simulations/<id>/trades` | Trades der Simulation |
| GET | `/api/simulations/<id>/positions` | Positionen der Simulation |
| GET | `/api/simulations/<id>/decisions` | Entscheidungs-Log (filterbar) |
| GET | `/api/simulations/<id>/metrics` | Vollständige Metriken inkl. KeSt |
| GET | `/api/simulations/<id>/benchmark` | Benchmark-Vergleich (Buy & Hold) |

### 4.6 Trading-Trigger

| Methode | Endpunkt | Beschreibung |
|---------|---------|-------------|
| POST | `/api/trading/run` | Manueller Handelszyklus |
| POST | `/api/trading/optimize` | Manuelle Backtesting-Optimierung |

---

## 5. Frontend (Ist-Zustand)

### 5.1 Aufbau

```
static/
  css/style.css         -- Alle Styles (dunkel, kompakt)
  js/
    app.js              -- Hauptlogik, WebSocket-Client
    simulations.js      -- Simulations-UI
    ui.js               -- UI-Hilfsfunktionen
templates/
  index.html            -- Einzel-Seite (SPA), alle Tabs eingebettet
```

**Architektur:** Single-Page-Application ohne Framework — vanilla JS + SocketIO.

### 5.2 Tabs / Bereiche

| Tab | Inhalt |
|-----|--------|
| Dashboard | Equity-Chart, offene Positionen, letzte Trades, Marktregime-Anzeige |
| Watchlist | Alle Aktien mit Score, Signal, Preis, Änderung |
| Trades | Trade-Historie mit PnL |
| Signale | Heutige Signale mit Indikatoren |
| Algorithmus | Optimierte Parameter pro Aktie |
| Strategien | Strategie-Editor (JSON-basiert), Live-Freigabe |
| Simulationen | Replay-Engine UI: Runs erstellen, Fortschritt, Charts, Decisions |
| Szenarien | Szenarien-Editor + Batch-Runner |

### 5.3 WebSocket Events

| Event (Server→Client) | Inhalt |
|----------------------|--------|
| `portfolio_update` | cash, equity, positions, total_return_pct |
| `trading_actions` | Ausgeführte Trades im letzten Zyklus |
| `status` | Status-Meldungen (Datenladen, Fehler) |

---

## 6. Konfiguration (Ist-Zustand)

Alle Parameter in `config.py` (aus `.env` oder Defaults):

### 6.1 Trading-Parameter (global, nicht pro Portfolio)

| Parameter | Wert | Bedeutung |
|-----------|------|-----------|
| `STARTING_CAPITAL` | 10.000 EUR | Startkapital |
| `MAX_POSITIONS` | 10 | Max gleichzeitige Positionen |
| `MAX_POSITIONS_PER_SECTOR` | 3 | Max Positionen pro Sektor |
| `RISK_PER_TRADE` | 0,02 (2%) | Risikobetrag pro Trade |
| `MAX_POSITION_SIZE` | 0,20 (20%) | Max Anteil am Portfolio |
| `MIN_POSITION_SIZE` | 0,03 (3%) | Min Anteil am Portfolio |
| `COMMISSION_RATE` | 0,001 (0,1%) | Provision pro Trade |
| `MIN_COMMISSION` | 1,00 EUR | Mindestprovision |
| `SPREAD_RATE` | 0,0005 (0,05%) | Spread pro Seite |
| `DEFAULT_STOP_LOSS_PCT` | 0,05 (5%) | Stop-Loss |
| `ATR_STOP_MULTIPLIER` | 2,0 | ATR-basierter Stop |
| `DEFAULT_TAKE_PROFIT_PCT` | 0,15 (15%) | Take-Profit |
| `TRAILING_STOP_PCT` | 0,03 (3%) | Trailing-Stop |
| `SIGNAL_THRESHOLD_BUY` | 65 | Score für Kaufsignal |
| `SIGNAL_THRESHOLD_SELL` | 35 | Score für Verkaufssignal |
| `TRADING_INTERVAL_MINUTES` | 15 | Scheduler-Interval |

---

## 7. Problemanalyse: Was muss für Multi-Portfolio geändert werden?

### 7.1 Datenbankänderungen

| Tabelle | Problem | Lösung |
|---------|---------|--------|
| `account` | 1 globale Zeile, kein Portfolio-Bezug | `portfolio_id` FK ergänzen |
| `positions` | Kein Portfolio-Bezug | `portfolio_id` FK ergänzen |
| `trades` | Kein Portfolio-Bezug | `portfolio_id` FK ergänzen |
| `equity_history` | UNIQUE auf `date` (1 pro Tag global) | `portfolio_id` FK + neues UNIQUE |
| `signals` | Global | `portfolio_id` nullable ergänzen |
| `simulation_runs` | Kein User-Bezug | `user_id` FK ergänzen (optional) |
| — | Keine Users-Tabelle | `users` Tabelle neu anlegen |
| — | Keine Portfolios-Tabelle | `portfolios` Tabelle neu anlegen |
| — | Strategien in JSON | `strategies` + `strategy_rules` Tabellen neu |
| — | Kein Proposal-System | `daily_proposals` + `proposed_orders` neu |

### 7.2 Service-Änderungen

| Service | Problem | Lösung |
|---------|---------|--------|
| `trading_engine.py` | `Account.query.first()` — greift immer Konto 1 an | Portfolio-Parameter übergeben |
| `live_runner.py` | Gleiche Problem | Portfolio-Parameter übergeben |
| `algorithm.py` | Globale Parameter | `StrategyResolver` als Parameter-Lieferant |
| `strategy_store.py` | JSON-basiert | Auf DB-Tabellen umstellen |
| `universe_store.py` | JSON-basiert | In DB oder Portfolio-Objekt |
| `telegram_notifier.py` | Kein Portfolio-Kontext | Portfolio-Selektion oder Standard-Portfolio |
| `ibkr_connector.py` | 1 Client-ID | `client_id` pro Portfolio konfigurierbar |

### 7.3 API-Änderungen

| Bereich | Problem | Lösung |
|---------|---------|--------|
| Alle Endpunkte | Kein Auth | Auth-Middleware (Flask-Login) |
| Account/Positions/Trades/Equity | Global | `portfolio_id` Parameter ergänzen |
| Strategien | JSON-basiert | DB-basierte Endpunkte |
| Simulationen | Kein User | User-Kontext ergänzen |
| — | Kein Login/Logout | `/api/auth/*` neu |
| — | Kein User-Management | `/api/users/*` neu (Admin) |
| — | Kein Portfolio-CRUD | `/api/portfolios/*` neu |
| — | Kein Proposal-System | `/api/proposals/*` neu |

### 7.4 Rückwärtskompatibilität

**Alle bestehenden API-Endpunkte müssen weiterhin funktionieren** (API-27, API-28).  
Lösung: `portfolio_id` Query-Parameter mit Default auf das Standard-Portfolio des Users.

---

## 8. Nicht-Funktionale Anforderungen (Ist-Zustand)

| Aspekt | Aktuell |
|--------|---------|
| Skalierung | Single-Server, 1 User |
| Sicherheit | Keine Auth — vollständig offen |
| Deployment | Direkter Python-Prozess unter systemd |
| Monitoring | Logging (stdout), Telegram für kritische Fehler |
| Backup | Keine automatische DB-Sicherung |
| Tests | Keine automatisierten Tests vorhanden |
| Dokumentation | SPEC/ARCH Dokumente (neu) |

---

*Dieses Dokument beschreibt den Ist-Zustand. Der Soll-Zustand ist in SPEC_multi_user_portfolios.md beschrieben.*  
*Für jede Änderung gilt: Additive Migrationen (kein Datenverlust), Rückwärtskompatibilität der API.*
