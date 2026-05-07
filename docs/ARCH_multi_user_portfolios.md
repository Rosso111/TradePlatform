# Architektur: Multi-User & Multi-Portfolio System
**Branch:** `feature/multi-user-portfolios`  
**Status:** Entwurf — zur Freigabe  
**Referenz:** SPEC_multi_user_portfolios.md

---

## 1. Systemübersicht

```
┌─────────────────────────────────────────────────────────────┐
│                        Browser / UI                         │
│  Login · Portfolio-Switcher · Proposal-Panel · Charts       │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTPS / WebSocket
┌────────────────────────▼────────────────────────────────────┐
│                    Flask Application                        │
│                                                             │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │   Auth   │  │  API Routes  │  │   WebSocket (SocketIO)│ │
│  │Middleware│  │  Blueprints  │  │   Portfolio-Broadcast │ │
│  └──────────┘  └──────┬───────┘  └───────────────────────┘ │
│                        │                                    │
│  ┌─────────────────────▼──────────────────────────────────┐ │
│  │                  Service Layer                         │ │
│  │                                                        │ │
│  │  PortfolioService  │  StrategyResolver                 │ │
│  │  ProposalGenerator │  TradingCycleOrchestrator         │ │
│  │  IBKRConnector     │  SimRunner / LiveRunner           │ │
│  └─────────────────────┬──────────────────────────────────┘ │
│                        │                                    │
│  ┌─────────────────────▼──────────────────────────────────┐ │
│  │                  Data Layer                            │ │
│  │  SQLAlchemy Models  │  Alembic Migrations              │ │
│  └─────────────────────┬──────────────────────────────────┘ │
└───────────────────────-│────────────────────────────────────┘
                         │
           ┌─────────────┴──────────────┐
           │                            │
    ┌──────▼──────┐            ┌────────▼────────┐
    │ PostgreSQL  │            │   IB Gateway    │
    │  (Tradebot) │            │  Port 4002/4001 │
    └─────────────┘            └─────────────────┘
```

---

## 2. Datenbankarchitektur

### 2.1 Entity-Relationship-Übersicht

```
users ──────────────────────────────────────────────────────┐
  │                                                          │
  │ 1:N                                                      │ 1:N
  ▼                                                          ▼
portfolios ──────────── strategy_rules              strategies
  │           N:1 ──► strategies                       (user_id oder NULL
  │                                                     für System-Strategien)
  │ 1:1
  ▼
accounts (cash, equity)

  │ 1:N
  ├──► positions (offene Positionen)
  ├──► trades (Trade-Historie)
  ├──► signals (generierte Signale)
  ├──► equity_history (tägliche Snapshots)
  └──► daily_proposals
           │ 1:N
           └──► proposed_orders
```

### 2.2 Tabellen-Schema

#### `users`
```sql
id            SERIAL PRIMARY KEY
username      VARCHAR(80) UNIQUE NOT NULL        -- U-01
email         VARCHAR(120) UNIQUE
password_hash VARCHAR(255) NOT NULL              -- U-01
role          VARCHAR(20) DEFAULT 'user'         -- 2.1 Rollen
is_active     BOOLEAN DEFAULT TRUE               -- U-07, U-11
created_at    TIMESTAMP DEFAULT NOW()
```
*Implements: U-01, U-06, U-07, U-10, U-11*

#### `strategies`
```sql
id          SERIAL PRIMARY KEY
user_id     INTEGER REFERENCES users(id)         -- NULL = System-Strategie (S-02)
name        VARCHAR(100) NOT NULL
description TEXT
is_system   BOOLEAN DEFAULT FALSE                -- S-02, S-05
params      JSONB NOT NULL                       -- alle Scoring/Risiko-Parameter (4.1)
created_at  TIMESTAMP DEFAULT NOW()
```
*Implements: S-01, S-02, S-05, DB-06*

#### `strategy_rules`
```sql
id          SERIAL PRIMARY KEY
strategy_id INTEGER REFERENCES strategies(id) ON DELETE CASCADE
level       VARCHAR(20) NOT NULL                 -- 'market' | 'sector' | 'stock'
key         VARCHAR(50) NOT NULL                 -- z.B. 'US', 'Technology', 'NVDA'
overrides   JSONB NOT NULL                       -- nur überschriebene Felder (S-08)
UNIQUE(strategy_id, level, key)
```
*Implements: S-06, S-08, S-09*

#### `portfolios`
```sql
id               SERIAL PRIMARY KEY
user_id          INTEGER REFERENCES users(id) NOT NULL   -- P-06
name             VARCHAR(100) NOT NULL
type             VARCHAR(20) NOT NULL                    -- 'sim'|'ibkr_paper'|'ibkr_live'
mode             VARCHAR(20) NOT NULL                    -- 'auto'|'approval'
status           VARCHAR(20) DEFAULT 'active'            -- P-02, P-03
currency         VARCHAR(10) DEFAULT 'EUR'
starting_capital NUMERIC(15,2) NOT NULL
strategy_id      INTEGER REFERENCES strategies(id)
ibkr_account_id  VARCHAR(50)                             -- P-11
created_at       TIMESTAMP DEFAULT NOW()
```
*Implements: P-01, P-02, P-05, P-06, P-08, P-09, P-10, P-12, P-13*

#### `accounts` *(geändert)*
```sql
-- NEU:
portfolio_id  INTEGER REFERENCES portfolios(id) UNIQUE NOT NULL  -- P-05
-- bestehende Felder bleiben unverändert
```
*Implements: P-05, DB-01, DB-02*

#### `positions` *(geändert)*
```sql
-- NEU:
portfolio_id  INTEGER REFERENCES portfolios(id) NOT NULL         -- P-05
-- bestehende Felder bleiben unverändert
```
*Implements: P-05, DB-01, DB-02*

#### `trades` *(geändert)*
```sql
-- NEU:
portfolio_id  INTEGER REFERENCES portfolios(id) NOT NULL         -- P-05
-- bestehende Felder bleiben unverändert
```
*Implements: P-05, DB-01, DB-02*

#### `signals` *(geändert)*
```sql
-- NEU:
portfolio_id  INTEGER REFERENCES portfolios(id)                  -- nullable (system-weit)
-- bestehende Felder bleiben unverändert
```
*Implements: DB-02*

#### `equity_history` *(geändert)*
```sql
-- NEU:
portfolio_id  INTEGER REFERENCES portfolios(id) NOT NULL
-- bestehende Felder bleiben unverändert
```
*Implements: P-05, DB-02*

#### `daily_proposals`
```sql
id            SERIAL PRIMARY KEY
portfolio_id  INTEGER REFERENCES portfolios(id) NOT NULL
proposal_date DATE NOT NULL
generated_at  TIMESTAMP DEFAULT NOW()                   -- PR-03
status        VARCHAR(30) DEFAULT 'open'                -- PR-11
executed_at   TIMESTAMP
notes         TEXT
UNIQUE(portfolio_id, proposal_date)                     -- PR-01
```
*Implements: PR-01, PR-03, PR-11*

#### `proposed_orders`
```sql
id             SERIAL PRIMARY KEY
proposal_id    INTEGER REFERENCES daily_proposals(id) ON DELETE CASCADE
stock_id       INTEGER REFERENCES stocks(id)
action         VARCHAR(10) NOT NULL                      -- 'BUY' | 'SELL'
shares_proposed NUMERIC(12,4) NOT NULL
est_price_eur  NUMERIC(15,4)
score          NUMERIC(6,2)
reason         TEXT                                      -- PR-04
approved       BOOLEAN DEFAULT TRUE                      -- PR-05, PR-06
executed       BOOLEAN DEFAULT FALSE                     -- PR-08
executed_at    TIMESTAMP
fill_price     NUMERIC(15,4)                             -- PR-08
```
*Implements: PR-04, PR-05, PR-06, PR-07, PR-08, PR-09, PR-10*

### 2.3 Migrationsstrategie

Migration via **Alembic** in zwei Schritten: *(Implements: DB-03, DB-04, DB-05)*

**Schritt 1 — Neue Tabellen anlegen (additive, kein Datenverlust):**
```
- users anlegen, Default-Admin einfügen
- strategies anlegen, Default-Strategie aus config.py einfügen
- portfolios anlegen, Default-Portfolio einfügen (id=1, type=sim, mode=auto)
- strategy_rules anlegen
- daily_proposals, proposed_orders anlegen
```

**Schritt 2 — Bestehende Tabellen erweitern:**
```
- accounts.portfolio_id  → DEFAULT 1, dann NOT NULL
- positions.portfolio_id → DEFAULT 1, dann NOT NULL
- trades.portfolio_id    → DEFAULT 1, dann NOT NULL
- signals.portfolio_id   → DEFAULT 1 (nullable bleibt)
- equity_history.portfolio_id → DEFAULT 1, dann NOT NULL
```

*Jeder Schritt ist einzeln rollback-fähig (DB-05).*

---

## 3. Backend-Architektur

### 3.1 Service Layer — Komponenten

```
services/
  auth_service.py          -- Login, Session, Token-Prüfung
  portfolio_service.py     -- CRUD, portfolio-aware Abfragen
  strategy_resolver.py     -- Hierarchie auflösen (Portfolio→Markt→Sektor→Aktie)
  proposal_generator.py    -- Tages-Proposals erstellen (8:00 Uhr Job)
  trading_orchestrator.py  -- wählt sim_runner oder live_runner je nach Portfolio-Typ
  sim_runner.py            -- bestehender run_trading_cycle (umbenannt)
  live_runner.py           -- bestehender IBKR Live Runner (portfolio-aware gemacht)
  ibkr_connector.py        -- bestehend, unverändert
  algorithm.py             -- bestehend, portfolio-aware Parameter via StrategyResolver
```

### 3.2 `StrategyResolver` *(Implements: S-06, S-07, S-08, S-09, S-10)*

Löst zur Laufzeit den effektiven Parametersatz für eine Aktie auf:

```python
# Implements: S-06, S-07, S-08, S-09, S-10
class StrategyResolver:
    def resolve(self, portfolio: Portfolio, stock: Stock) -> dict:
        """
        Gibt vollständigen Parametersatz zurück:
        Portfolio-Default → Markt-Override → Sektor-Override → Aktien-Override
        """
        params = dict(portfolio.strategy.params)          # Basis (S-09)
        for level, key in [
            ('market',  stock.region),
            ('sector',  stock.sector),
            ('stock',   stock.symbol),
        ]:
            rule = self._find_rule(portfolio.strategy, level, key)
            if rule:
                params.update(rule.overrides)             # S-07, S-08
        return params                                     # S-10
```

### 3.3 `ProposalGenerator` *(Implements: PR-01 bis PR-06)*

```python
# Implements: PR-01, PR-02, PR-03, PR-04, PR-05
class ProposalGenerator:
    def generate_for_portfolio(self, portfolio: Portfolio, date: date):
        """Erstellt DailyProposal auf Basis gestriger Schlusskurse."""
        # PR-01: maximal ein Proposal pro Portfolio und Tag
        # PR-02: gestrige Schlusskurse als Basis
        # PR-04: Score + Begründung pro ProposedOrder
        # PR-05: approved=True als Default
```

### 3.4 `TradingOrchestrator` *(Implements: P-03, P-08, P-09, P-12, P-13)*

```python
# Implements: P-03, P-08, P-09, P-12, P-13
class TradingOrchestrator:
    def run_cycle(self, portfolio: Portfolio):
        if not portfolio.is_active():     # P-03
            return
        if portfolio.mode == 'auto':      # P-12
            if portfolio.type == 'sim':   # P-08
                sim_runner.run(portfolio)
            else:                         # P-09
                live_runner.run(portfolio)
        # approval-Modus: kein automatischer Trade (P-13)
```

### 3.5 Auth Middleware *(Implements: U-04, U-05)*

```python
# Implements: U-04, U-05
@app.before_request
def require_auth():
    """Prüft Session/Token bei jeder Anfrage."""
    # Ausnahmen: /api/auth/login, statische Dateien
```

**Entscheidung E-01:** Session via **Flask-Login**
- Einfacher zu implementieren
- Reicht für Single-Server-Betrieb
- JWT kann später als zweite Auth-Methode ergänzt werden (z.B. für Mobile)

---

## 4. API-Architektur

### 4.1 Blueprint-Struktur

```
routes/
  api.py           -- bestehend, erweitert um portfolio_id Parameter (API-27, API-28)
  auth.py          -- NEU: Login/Logout/Me (API-01 bis API-03)
  users.py         -- NEU: User-Verwaltung Admin (API-04 bis API-08)
  portfolios.py    -- NEU: Portfolio-CRUD (API-09 bis API-14)
  strategies.py    -- NEU: Strategie + Regeln (API-15 bis API-22)
  proposals.py     -- NEU: Proposal-Management (API-23 bis API-26)
```

### 4.2 Rückwärtskompatibilität *(Implements: API-27, API-28)*

Alle bestehenden Endpunkte in `routes/api.py` werden mit einem Decorator erweitert:

```python
# Implements: API-27, API-28
def portfolio_context(f):
    """Liest portfolio_id aus Query-Parameter oder nimmt Standard-Portfolio des Users."""
    @wraps(f)
    def decorated(*args, **kwargs):
        portfolio_id = request.args.get('portfolio_id') or \
                       current_user.default_portfolio_id
        g.portfolio = Portfolio.query.get_or_404(portfolio_id)
        return f(*args, **kwargs)
    return decorated
```

### 4.3 Autorisierung

| Regel | Beschreibung | Requirement |
|-------|-------------|-------------|
| Eingeloggt | Jeder Endpunkt außer `/api/auth/login` | U-04 |
| Eigentümer | User sieht nur eigene Portfolios/Strategien | P-06 |
| Admin | `/api/users/*` nur für Admin | API-04 bis API-08 |
| IBKR Live | Portfolio-Typ `ibkr_live` nur Admin freischaltbar | P-10 |

---

## 5. Scheduler-Architektur

```
APScheduler
  │
  ├── trading_cycle      alle 15 Min   → TradingOrchestrator.run_all_active()
  │                                      iteriert über alle aktiven Auto-Portfolios
  │                                      [P-03, P-12]
  │
  ├── proposal_generator 08:00 MEZ     → ProposalGenerator.run_all_approval()
  │                                      iteriert über alle aktiven Approval-Portfolios
  │                                      [PR-01, PR-03]
  │
  ├── proposal_expiry    22:00 MEZ     → setzt offene Proposals auf 'expired'
  │                                      [PR-09]
  │
  └── equity_broadcast   jede Minute  → WebSocket Push an verbundene Clients
```

---

## 6. Frontend-Architektur

### 6.1 Seitenstruktur

```
/ (index.html)
  ├── Login-Screen          (wenn nicht eingeloggt)         [UI-01, UI-02]
  └── App (wenn eingeloggt)
        ├── Header
        │   └── Portfolio-Switcher (Dropdown)               [UI-03]
        ├── Sidebar / Navigation
        │   ├── Dashboard (bestehend)
        │   ├── Portfolios                                   [UI-04]
        │   ├── Strategien                                   [UI-05]
        │   └── Proposals (nur Approval-Modus)              [UI-06]
        └── Main Content
            ├── bestehende Charts/Tabellen (portfolio-aware) [UI-09]
            └── Proposals-Panel                              [UI-06, UI-07, UI-08]
```

### 6.2 State-Management

- Aktives Portfolio: `localStorage` + URL-Parameter `?p=<id>`
- Auth-State: Session-Cookie (Flask-Login)
- Portfolio-Updates: WebSocket wie bisher, gefiltert nach aktivem Portfolio

### 6.3 Proposals-Panel *(Implements: UI-06, UI-07, UI-08)*

```
┌─────────────────────────────────────────────────────┐
│  Vorschläge für heute — Portfolio: "Momentum EU"    │
│  Generiert: 08:02 Uhr  ·  Gültig bis: 22:00 Uhr   │
├──────┬────────┬────────┬────────┬────────┬──────────┤
│      │ Symbol │ Action │ Stück  │ Score  │ Ausführen│
├──────┼────────┼────────┼────────┼────────┼──────────┤
│  ✓   │ TSM    │ KAUFEN │  5     │  70.4  │  [Toggle]│
│  ✓   │ AVGO   │ KAUFEN │  5     │  68.8  │  [Toggle]│
│  ✗   │ MS     │ KAUFEN │ 10     │  66.5  │  [Toggle]│
│  ✓   │ CSCO   │ VERKAUF│ 20     │  32.1  │  [Toggle]│
├──────┴────────┴────────┴────────┴────────┴──────────┤
│              [Alle ausführen]  [Alle deaktivieren]  │
└─────────────────────────────────────────────────────┘
```

---

## 7. Traceability-Matrix

### Code → Requirements

Jede Datei/Klasse/Funktion enthält am Anfang einen Kommentar:

```python
# Implements: P-01, P-02, P-05, P-06
class PortfolioService:
    ...

    def create(self, user_id, data):
        # Implements: P-01, P-05
        ...

    def set_status(self, portfolio_id, status):
        # Implements: P-02, P-03
        ...
```

### Test → Requirements

```python
class TestPortfolioIsolation:
    """Tests für P-06: User-Isolation"""

    def test_user_cannot_see_other_users_portfolio(self):
        """P-06"""
        ...

    def test_user_cannot_access_other_users_positions(self):
        """P-06"""
        ...
```

### Vollständige Requirement-Abdeckung

| Bereich | IDs | Abgedeckt durch |
|---------|-----|-----------------|
| Ziele | G-01 bis G-06 | Integration Tests |
| User | U-01 bis U-11 | `auth_service.py`, `users.py`, `test_auth.py` |
| Portfolio | P-01 bis P-11 | `portfolio_service.py`, `test_portfolio.py` |
| Strategie | S-01 bis S-10 | `strategy_resolver.py`, `test_strategy.py` |
| Proposal | PR-01 bis PR-11 | `proposal_generator.py`, `test_proposals.py` |
| Datenbank | DB-01 bis DB-06 | Alembic-Migration, `test_migration.py` |
| API | API-01 bis API-28 | `routes/*.py`, `test_api.py` |
| UI | UI-01 bis UI-10 | Frontend-Code (manuell testbar) |

---

## 8. Architektur-Entscheidungsregister (ADR)

Jede Entscheidung dokumentiert: Was wurde gewählt, welche Alternativen wurden geprüft, und warum fiel die Wahl so.

---

### ADR-01: Programmiersprache — Python

**Entscheidung:** Python 3.12 bleibt die einzige Programmiersprache.

**Alternativen geprüft:**
- TypeScript/Node.js: Gutes Ecosystem für Web-APIs, aber keine Finanz-Bibliotheken (yfinance, ib_insync, pandas)
- Go: Performant, aber kein Finanz-Ecosystem

**Warum Python:**
- `ib_insync` (IBKR-Verbindung) ist Python-only
- `yfinance` (Kursdaten), `pandas` (Zeitreihen) sind Python-nativ
- Gesamter bestehender Code ist Python — Neuentwicklung in anderer Sprache bedeutet komplette Neuentwicklung
- Daten-Science-Bibliotheken für Scoring-Algorithmus und Backtesting verfügbar

**Referenz:** G-01 (autonomes Trading), G-06 (IBKR-Anbindung)

---

### ADR-02: Web-Framework — Flask

**Entscheidung:** Flask 3.x bleibt das Web-Framework.

**Alternativen geprüft:**
- **FastAPI:** Moderne async-API, automatische OpenAPI-Docs, bessere Performance. Aber: erfordert komplette Umschreibung der Routes; Flask-SocketIO hat kein direktes Äquivalent.
- **Django:** Full-Stack mit ORM, Auth eingebaut. Aber: zu schwer für diesen Use-Case; bestehende SQLAlchemy-Modelle müssten auf Django-ORM migriert werden.

**Warum Flask:**
- Gesamter bestehender Code ist Flask — Migration würde Wochen kosten ohne neuen Wert
- Flask-SocketIO ermöglicht WebSocket-Portfolio-Broadcast (jede Minute) ohne Extra-Server
- Flask-Login passt zu Session-basierter Auth (ADR-06)
- Blueprints strukturieren die neuen API-Bereiche klar
- `@app.before_request` für Auth-Middleware direkt verfügbar

**Referenz:** G-02 (WebSocket-Updates), U-04 (Auth)

---

### ADR-03: Datenbank — PostgreSQL (ausschließlich)

**Entscheidung:** PostgreSQL 15+, psycopg3-Treiber. SQLite-Support wurde bereits entfernt (config.py wirft RuntimeError wenn DB_BACKEND != 'postgres').

**Alternativen geprüft:**
- **SQLite:** Einfach, kein Server. Aber: kein JSONB, keine konkurrierenden Schreibzugriffe bei mehreren Portfolios, keine produktionstaugliche Isolation
- **MySQL/MariaDB:** Produktionstauglich, aber kein JSONB — Strategy-Parameter-Hierarchie (S-08) braucht JSONB-Updates
- **MongoDB:** Flexibles Schema, JSONB-ähnlich. Aber: keine ACID-Transaktionen über Tabellen, kein SQLAlchemy-ORM, komplette Umschreibung

**Warum PostgreSQL:**
- `JSONB` für `strategies.params` und `strategy_rules.overrides` (S-08) — partial updates ohne Deserialisierung des ganzen Objekts
- `UNIQUE`-Constraints über mehrere Spalten (z.B. `(portfolio_id, date)` für equity_history)
- `CASCADE DELETE` für FK-Integrität (SimulationRun → SimulationTrade etc.)
- `SELECT ... FOR UPDATE` für konkurrente Handelszyklen mehrerer Portfolios
- Bereits produktiv in Betrieb (192.168.0.165:5432, DB "Tradebot")
- Performance-Indexes (B-Tree auf `run_id, date DESC`) bereits angelegt

**Referenz:** DB-01 bis DB-06, S-08, G-02

---

### ADR-04: ORM — SQLAlchemy 2.x + Alembic

**Entscheidung:** SQLAlchemy bleibt das ORM; Alembic wird neu eingeführt für Migrations-Management.

**Alternativen geprüft:**
- **Raw SQL:** Maximale Kontrolle, kein Overhead. Aber: alle Model-Relationships müssten manuell verwaltet werden; bei 15+ Tabellen zu aufwändig
- **Tortoise ORM (async):** Würde gut zu FastAPI passen. Aber: anderes Framework (FastAPI ausgeschlossen in ADR-02)

**Warum SQLAlchemy:**
- Alle bestehenden Models sind SQLAlchemy — Weiterverwendung direkt
- Relationship-Definitionen (`backref`, `lazy='dynamic'`) für Portfolio→Positions etc.
- `db.session.bulk_save_objects()` für Performance beim Kurs-Import

**Warum Alembic (neu):**
- Aktuell: `db.create_all()` beim Start — funktioniert nur für neue Tabellen, kann keine bestehenden ändern
- Für Multi-Portfolio: `accounts.portfolio_id` muss zu bestehender Tabelle ergänzt werden — das geht nicht mit `create_all()`
- Alembic: versionierte Migrations-Scripts, rollback-fähig, `alembic upgrade head` / `alembic downgrade -1`
- Zwei-Schritt-Migration ohne Datenverlust möglich (DB-04, DB-05)

**Referenz:** DB-03, DB-04, DB-05

---

### ADR-05: Scheduler — APScheduler

**Entscheidung:** APScheduler (BackgroundScheduler) bleibt der Task-Scheduler.

**Alternativen geprüft:**
- **Celery + Redis:** Produktions-grade, verteilte Tasks, Retries. Aber: Redis als Extra-Dependency; overkill für Single-Server-Betrieb
- **cron (systemd):** Einfach. Aber: kein direkter Zugriff auf Flask-App-Kontext, kein WebSocket-Push aus dem Job
- **threading.Timer:** Zu primitiv, kein Cron-Syntax

**Warum APScheduler:**
- Bereits integriert und produktiv im Einsatz (15-Min-Handelszyklus)
- Zugriff auf Flask-App-Kontext über `with app.app_context()`
- Cron-Trigger für `08:00 MEZ` (ProposalGenerator) und `22:00 MEZ` (Proposal-Expiry)
- Interval-Trigger für `equity_broadcast` (jede Minute)
- `replace_existing=True` verhindert doppelte Jobs bei App-Neustart
- Background-Scheduler: kein Block des Haupt-Threads

**Referenz:** PR-03 (08:00 Proposal), PR-09 (22:00 Expiry), G-02 (Equity-Broadcast)

---

### ADR-06: Authentifizierung — Flask-Login (Session-basiert)

**Entscheidung:** Flask-Login mit Server-Side-Sessions (verschlüsselter Cookie).

**Alternativen geprüft:**
- **JWT (JSON Web Tokens):** Stateless, gut für Mobile/APIs. Aber: kein eingebauter Revocation-Mechanismus; Token bleibt bis Ablauf gültig auch wenn User deaktiviert (U-11); komplizierter Refresh-Token-Flow
- **OAuth2 / Google Login:** Gut für externe User. Aber: Dependency auf externen Service; overkill für internen Tool-Charakter dieser Applikation
- **Basic Auth:** Zu primitiv, passwort-im-Header

**Warum Flask-Login:**
- Server-Side-Session: User-Deaktivierung (U-11) wirkt sofort — nächster Request schlägt fehl
- `@login_required` Decorator auf Routes — 3 Zeilen statt komplexer Middleware
- `current_user` Proxy in allen Route-Funktionen direkt verfügbar
- `UserMixin` für Standard-User-Objekt
- Session-Cookie ist httpOnly + signed — kein XSS-Risiko
- JWT kann später als zweite Auth-Methode für Mobile-Clients ergänzt werden (U-04)

**Referenz:** U-01 bis U-05, U-11

---

### ADR-07: IBKR-Anbindung — ib_insync

**Entscheidung:** `ib_insync` bleibt die IBKR-Bibliothek.

**Alternativen geprüft:**
- **IB-Native TWS API (ibapi):** Offizielle API. Aber: callback-basiert, kein async, schwer testbar, deutlich mehr Boilerplate für Market-Orders mit Fill-Bestätigung
- **Direktes Socket-Protokoll:** Maximale Kontrolle. Aber: undokumentiert, wartungsintensiv

**Warum ib_insync:**
- `await ib.connectAsync()` — async-fähig, passt zum Background-Event-Loop
- `ib.placeOrder(contract, order)` + `trade.orderStatus.status` — einfache Fill-Überwachung
- `ib.accountValues()`, `ib.positions()` — direkte Konto-Abfragen
- `ib.qualifyContractsAsync(contract)` — Contract-Validierung vor Order
- Aktiv gepflegt (GitHub), gute Dokumentation
- Im Connector bereits produktiv in Betrieb (Paper-Trading CSCO-Test erfolgreich)

**Referenz:** P-09, P-11 (IBKR Live-Trading)

---

### ADR-08: Strategie-Speicherung — von JSON-Datei zu PostgreSQL-Tabellen

**Entscheidung:** Strategien werden von `data/strategies.json` in DB-Tabellen (`strategies`, `strategy_rules`) migriert.

**Warum nicht JSON-Datei beibehalten:**
- Concurrent-Write-Probleme: Wenn zwei Portfolios gleichzeitig dieselbe Strategie lesen/schreiben, gibt es Race-Conditions
- Kein FK-Bezug: `simulation_runs.strategy_name` ist ein String-Verweis, keine DB-Beziehung
- Kein User-Bezug: JSON-Datei ist global, nicht pro User
- Keine Parameter-Hierarchie: Portfolio→Markt→Sektor→Aktie erfordert separate `strategy_rules`-Tabelle

**Warum PostgreSQL-Tabellen:**
- JSONB für `params` und `overrides` — flexible Schema-Evolution ohne Migration für neue Parameter
- FK-Integrität: `portfolio.strategy_id → strategies.id`
- User-Isolation: `strategies.user_id` (NULL = System-Strategie, S-02)
- `strategy_rules` Tabelle für Override-Hierarchie (S-06, S-08, S-09)
- Bestehende JSON-Strategien werden als System-Strategien migriert (user_id=NULL)

**Referenz:** S-01, S-02, S-05, S-06, S-07, S-08, S-09, DB-06

---

### ADR-09: Offene Entscheidungen (noch zu klären)

| ID | Frage | Stand |
|----|-------|-------|
| E-02 | Proposal-Benachrichtigung | Telegram (besteht) + UI-Panel |
| E-03 | IBKR Multi-Account | Ein Gateway, verschiedene `client_id` pro Portfolio |
| E-04 | Strategie-Sharing | Nur innerhalb User + System-Strategien (kein User-zu-User) |
| E-05 | Portfolio-Währung | EUR als einzige Basiswährung (bestehende Logik) |
| E-06 | IBKR Live für alle Portfolios | Erst nach stabilen Paper-Tests; Admin-Freigabe erforderlich |

---

*Dieses Dokument ist zur Freigabe gedacht. Jede Architekturentscheidung verweist auf die Requirements aus SPEC_multi_user_portfolios.md.*  
*Nach Freigabe: Implementierung startet mit Schritt 1 (Alembic-Migration).*
