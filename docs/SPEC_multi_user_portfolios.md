# Spezifikation: Multi-User & Multi-Portfolio System
**Branch:** `feature/multi-user-portfolios`  
**Status:** Entwurf — zur Freigabe

---

## 1. Ziele

- Mehrere Portfolios pro User, unbegrenzt
- Mehrere User pro Installation
- Pro Portfolio: eigene Strategie, eigenes Kapital, eigener Handelsmodus
- Strategie-Parameter auf vier Ebenen einstellbar (Portfolio → Markt → Sektor → Aktie)
- Handelsmodus: automatisch ODER tägliche Vorschlagsliste mit manuellem Approval
- Bestehende Daten bleiben erhalten (Migration, kein Datenverlust)

---

## 2. User-System

### 2.1 User-Rollen

| Rolle | Rechte |
|-------|--------|
| `admin` | Alle User sehen, alle Portfolios verwalten, System-Einstellungen |
| `user` | Eigene Portfolios und Strategien verwalten |

### 2.2 Authentifizierung
- Login via Username + Passwort (bcrypt-Hash)
- Session-basiert (Flask-Login) oder JWT — **offen für Entscheidung**
- Kein externer OAuth (kein Google/GitHub) in erster Version

### 2.3 User-Verwaltung
- Admin kann User anlegen, deaktivieren, Passwort zurücksetzen
- User kann eigenes Passwort ändern
- Kein Self-Registration (admin legt User an)

---

## 3. Portfolio-System

### 3.1 Portfolio-Eigenschaften

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `name` | String | Frei wählbar, z.B. "Momentum EU", "IBKR Paper" |
| `type` | Enum | `sim` / `ibkr_paper` / `ibkr_live` |
| `mode` | Enum | `auto` / `approval` |
| `status` | Enum | `active` / `inactive` |
| `starting_capital` | Float | Startkapital in EUR |
| `currency` | String | Basis-Währung (default: EUR) |
| `strategy_id` | FK | Standard-Strategie für dieses Portfolio |
| `ibkr_account_id` | String | Nur bei Typ ibkr_paper/live: Account-ID (z.B. DUP859792) |

### 3.2 Portfolio-Typen

**`sim`** — Reine Simulation
- Keine IBKR-Verbindung
- Preise aus DB (Yahoo Finance via yfinance)
- Ideal für Backtesting-ähnlichen Vorwärtsbetrieb

**`ibkr_paper`** — IBKR Paper Trading
- Verbindung zu IB Gateway Port 4002
- Echte Market Orders auf IBKR Paper-Konto
- Positionen werden in IBKR UND in lokaler DB geführt

**`ibkr_live`** — IBKR Live Trading (spätere Ausbaustufe)
- Verbindung zu IB Gateway Port 4001
- Echtes Geld — zusätzliche Sicherheitsabfrage vor Ausführung
- Nur für Admin freischaltbar

### 3.3 Handelsmodi

**`auto`** — Vollautomatisch
- Zyklus läuft alle 15 Minuten (konfigurierbar)
- Signal → sofortige Ausführung ohne manuellen Eingriff

**`approval`** — Tägliche Vorschlagsliste
- Einmal täglich, konfigurierbare Zeit (default: 8:00 Uhr MEZ)
- Basis: gestrige Schlusskurse + aktuelle Signallage
- Ergebnis: Liste von Kauf-/Verkaufsvorschlägen pro Portfolio
- User togglet einzeln an/aus im UI
- "Jetzt ausführen"-Button → alle aktivierten Orders werden gesendet
- Nicht ausgeführte Vorschläge verfallen am selben Tag um 22:00 Uhr (Börsenende)

---

## 4. Strategie-System

### 4.1 Strategie-Objekt

Eine Strategie enthält alle Parameter die das Scoring und den Handel steuern:

```
Scoring-Parameter:
  - rsi_weight, macd_weight, ema_weight, bb_weight
  - analyst_weight, sector_weight
  - buy_threshold (default: 65)
  - sell_threshold (default: 35)

Risiko-Parameter:
  - risk_per_trade (default: 2%)
  - max_positions (default: 10)
  - max_positions_per_sector (default: 3)
  - max_position_size (default: 20%)
  - min_position_size (default: 3%)

Stop/Profit-Parameter:
  - default_stop_loss_pct (default: 5%)
  - atr_stop_multiplier (default: 2.0)
  - default_take_profit_pct (default: 15%)
  - trailing_stop_pct (default: 3%)

Handelskosten:
  - commission_rate (default: 0.1%)
  - min_commission (default: 1 EUR)
  - spread_rate (default: 0.05%)
```

### 4.2 Überschreibungs-Hierarchie

Feingranularere Regel gewinnt immer:

```
Portfolio-Default (Basis)
  └── Markt-Regel (z.B. region="US")
        └── Sektor-Regel (z.B. sector="Technology")
              └── Aktien-Regel (z.B. symbol="NVDA")
```

**Beispiel:**
```
Portfolio "Momentum":
  buy_threshold: 65, trailing_stop: 3%, max_position_size: 20%

  Markt US:
    buy_threshold: 70  (strenger für US)

  Sektor Technology:
    trailing_stop: 5%, max_position_size: 15%

  Aktie NVDA:
    max_position_size: 8%  (NVDA nie mehr als 8%)
    buy_threshold: 75
```

Effektiv für NVDA (US, Technology):
- `buy_threshold`: 75 (Aktien-Regel)
- `trailing_stop`: 5% (Sektor-Regel)
- `max_position_size`: 8% (Aktien-Regel)
- alles andere: Portfolio-Default

### 4.3 Strategie-Verwaltung
- Jeder User kann eigene Strategien erstellen und benennen
- Strategien können zwischen Portfolios desselben Users geteilt werden
- Admin kann System-Strategien anlegen die alle User nutzen können
- Eine Strategie gehört immer einem User oder dem System

---

## 5. Tages-Proposal-System (Approval-Modus)

### 5.1 Ablauf

```
08:00 Uhr MEZ
  → Scheduler startet Proposal-Generator für alle aktiven Approval-Portfolios
  → Pro Portfolio:
      1. Gestrige Schlusskurse + aktuelle Indikatoren berechnen
      2. Strategie-Hierarchie auflösen (welche Parameter gelten für welche Aktie)
      3. Signale generieren (BUY / SELL / HOLD)
      4. DailyProposal anlegen mit Liste von ProposedOrders
      5. Telegram-Benachrichtigung an User (optional)

08:00 - Börsenöffnung (15:30 MEZ für US)
  → User öffnet UI, sieht Vorschlagsliste
  → Togglet einzelne Orders an/aus
  → Drückt "Jetzt ausführen"
  → Alle aktivierten Orders werden ausgeführt

22:00 Uhr MEZ
  → Nicht ausgeführte Proposals verfallen automatisch (Status: expired)
```

### 5.2 Proposal-Objekt

Ein `DailyProposal` enthält:
- Portfolio, Datum, Generierungszeit
- Status: `open` / `partially_executed` / `executed` / `expired`
- Liste von `ProposedOrder`:
  - Symbol, Name, Sektor
  - Action: `BUY` / `SELL`
  - Vorgeschlagene Stückzahl + Schätzpreis
  - Score + Begründung (welche Indikatoren haben ausgelöst)
  - `approved`: true/false (User-Toggle)
  - `executed`: true/false (nach Ausführung)

---

## 6. Datenbankarchitektur (neu)

### 6.1 Neue Tabellen

```
users
  id, username, email, password_hash, role, is_active, created_at

portfolios
  id, user_id, name, type, mode, status, currency,
  starting_capital, strategy_id, ibkr_account_id, created_at

strategies
  id, user_id, name, description, is_system, params (JSON), created_at

strategy_rules
  id, strategy_id, level (market/sector/stock),
  key (z.B. "US" / "Technology" / "NVDA"),
  overrides (JSON — nur die überschriebenen Felder)

daily_proposals
  id, portfolio_id, proposal_date, generated_at, status,
  executed_at, notes

proposed_orders
  id, proposal_id, stock_id, action, shares_proposed,
  est_price_eur, score, reason, approved, executed,
  executed_at, fill_price
```

### 6.2 Geänderte Tabellen (portfolio_id hinzufügen)

```
accounts       → + portfolio_id (FK, unique)
positions      → + portfolio_id (FK)
trades         → + portfolio_id (FK)
signals        → + portfolio_id (FK, nullable — für system-weite Signale)
equity_history → + portfolio_id (FK)
```

### 6.3 Migration bestehender Daten

Strategie:
1. Einen Default-User anlegen (`admin`, Passwort aus .env)
2. Ein Default-Portfolio anlegen ("Default", Typ: `sim`, Modus: `auto`)
3. Alle bestehenden `accounts`, `positions`, `trades`, `signals`, `equity_history`
   bekommen `portfolio_id = 1` (das Default-Portfolio)
4. Bestehende Strategie-Parameter aus `config.py` werden als System-Strategie "Default v1" gespeichert

Kein Datenverlust — alle bestehenden Daten bleiben vollständig erhalten.

---

## 7. API-Endpunkte (neu/geändert)

### User & Auth
```
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/me
POST /api/users              (admin only)
GET  /api/users              (admin only)
PUT  /api/users/:id/password
```

### Portfolios
```
GET    /api/portfolios               (eigene Portfolios)
POST   /api/portfolios               (neues Portfolio)
GET    /api/portfolios/:id           (Detail + aktueller Stand)
PUT    /api/portfolios/:id           (bearbeiten)
PATCH  /api/portfolios/:id/status    (aktiv/inaktiv)
DELETE /api/portfolios/:id           (nur wenn keine offenen Positionen)
```

### Strategien
```
GET    /api/strategies               (eigene + System-Strategien)
POST   /api/strategies               (neue Strategie)
PUT    /api/strategies/:id
DELETE /api/strategies/:id
GET    /api/strategies/:id/rules     (Überschreibungsregeln)
POST   /api/strategies/:id/rules
DELETE /api/strategies/:id/rules/:rule_id
```

### Proposals (Approval-Modus)
```
GET  /api/portfolios/:id/proposals          (alle Proposals)
GET  /api/portfolios/:id/proposals/today    (heutiger Proposal)
PATCH /api/proposals/:id/orders/:order_id   (approve: true/false)
POST /api/proposals/:id/execute             (alle approved ausführen)
```

### Bestehende Endpunkte
Alle bestehenden `/api/account`, `/api/positions`, `/api/trades` etc. bekommen
einen optionalen `?portfolio_id=X` Parameter. Ohne Parameter: aktives Portfolio
des eingeloggten Users.

---

## 8. UI-Änderungen

### 8.1 Neue Elemente
- **Login-Screen** (wird beim Start gezeigt wenn nicht eingeloggt)
- **Portfolio-Switcher** (Dropdown oben im Header — aktives Portfolio wählen)
- **Portfolio-Verwaltung** (Seite: Liste aller Portfolios, anlegen/bearbeiten/deaktivieren)
- **Strategie-Editor** (Seite: Parameter einstellen, Regeln pro Markt/Sektor/Aktie)
- **Proposals-Panel** (nur im Approval-Modus sichtbar):
  - Tabelle mit heutigen Vorschlägen
  - Toggle pro Zeile (ausführen / überspringen)
  - "Alle ausführen"-Button
  - Status-Anzeige nach Ausführung

### 8.2 Bestehende UI
- Alle bestehenden Charts, Tabellen, Signale etc. bleiben erhalten
- Zeigen immer Daten des aktuell gewählten Portfolios

---

## 9. Offene Punkte / Entscheidungen

| # | Frage | Optionen |
|---|-------|----------|
| 1 | Authentifizierung | Session (Flask-Login) vs. JWT |
| 2 | Proposal-Benachrichtigung | Nur UI / Telegram / E-Mail |
| 3 | Mehrere IBKR-Konten gleichzeitig | Ein Gateway für alle oder pro Portfolio eigener Gateway? |
| 4 | Strategie-Sharing | Nur innerhalb eines Users, oder User können Strategien teilen? |
| 5 | Portfolio-Währung | Immer EUR, oder auch USD-basierte Portfolios? |
| 6 | IBKR Live | Direkt mit einbauen oder spätere Ausbaustufe? |

---

## 10. Bauplan / Reihenfolge

1. **DB-Migration** — neue Tabellen, Migration bestehender Daten, Alembic-Skript
2. **Backend Models** — User, Portfolio, Strategy, StrategyRule, DailyProposal, ProposedOrder
3. **Auth-System** — Login/Logout, Session, Middleware
4. **Portfolio-Service** — CRUD, portfolio-aware Trading-Zyklus
5. **Strategie-Resolver** — Hierarchie auflösen (Portfolio→Markt→Sektor→Aktie)
6. **Proposal-Generator** — Tages-Vorschläge generieren (8:00 Uhr Job)
7. **API-Endpunkte** — alle neuen + bestehende mit portfolio_id
8. **UI** — Login, Portfolio-Switcher, Strategie-Editor, Proposals-Panel
9. **Testing** — End-to-End Test mit Paper-Portfolio
10. **Merge** → master

---

*Dieses Dokument ist zur Freigabe gedacht. Änderungen und Anmerkungen bitte direkt kommunizieren.*
