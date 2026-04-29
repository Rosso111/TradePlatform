# Spezifikation: Multi-User & Multi-Portfolio System
**Branch:** `feature/multi-user-portfolios`  
**Status:** Entwurf — zur Freigabe

---

## 1. Ziele

| ID | Anforderung |
|----|-------------|
| G-01 | Jeder User kann unbegrenzt viele Portfolios anlegen |
| G-02 | Mehrere User pro Installation, voneinander isoliert |
| G-03 | Pro Portfolio: eigene Strategie, eigenes Kapital, eigener Handelsmodus |
| G-04 | Strategie-Parameter auf vier Ebenen konfigurierbar (Portfolio → Markt → Sektor → Aktie) |
| G-05 | Handelsmodus: vollautomatisch ODER tägliche Vorschlagsliste mit manuellem Approval |
| G-06 | Bestehende Daten bleiben bei Migration vollständig erhalten |

---

## 2. User-System

### 2.1 User-Rollen

| Rolle | Rechte |
|-------|--------|
| `admin` | Alle User sehen, alle Portfolios verwalten, System-Einstellungen |
| `user` | Eigene Portfolios und Strategien verwalten |

### 2.2 Authentifizierung

| ID | Anforderung |
|----|-------------|
| U-01 | Login via Username + Passwort (bcrypt-Hash) |
| U-02 | Session-basiert (Flask-Login) oder JWT — **offen für Entscheidung** |
| U-03 | Kein externer OAuth (kein Google/GitHub) in erster Version |
| U-04 | Jede API-Anfrage wird auf gültige Session/Token geprüft |
| U-05 | Abgelaufene Sessions werden automatisch ungültig |

### 2.3 User-Verwaltung

| ID | Anforderung |
|----|-------------|
| U-06 | Admin kann User anlegen |
| U-07 | Admin kann User deaktivieren (kein Login, Daten bleiben erhalten) |
| U-08 | Admin kann Passwort eines Users zurücksetzen |
| U-09 | User kann eigenes Passwort ändern |
| U-10 | Kein Self-Registration — Admin legt User an |
| U-11 | Deaktivierter User kann sich nicht einloggen, aber seine Daten bleiben erhalten |

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

### 3.2 Portfolio-Anforderungen

| ID | Anforderung |
|----|-------------|
| P-01 | User kann beliebig viele Portfolios anlegen |
| P-02 | Portfolio kann aktiv oder inaktiv geschaltet werden |
| P-03 | Inaktive Portfolios führen keinen Handelszyklus aus |
| P-04 | Portfolio kann nur gelöscht werden wenn keine offenen Positionen vorhanden |
| P-05 | Jedes Portfolio hat eine eigene Kasse, eigene Positionen, eigene Trade-Historie |
| P-06 | Portfolios verschiedener User sind voneinander isoliert (kein cross-user Zugriff) |
| P-07 | Admin kann alle Portfolios aller User einsehen |

### 3.3 Portfolio-Typen

| ID | Anforderung |
|----|-------------|
| P-08 | Typ `sim`: reine Simulation, Preise aus DB, keine IBKR-Verbindung |
| P-09 | Typ `ibkr_paper`: echte Market Orders auf IBKR Paper-Konto (Port 4002) |
| P-10 | Typ `ibkr_live`: echte Market Orders auf IBKR Live-Konto (Port 4001), nur Admin freischaltbar |
| P-11 | Bei `ibkr_paper`/`ibkr_live`: Positionen werden in IBKR UND in lokaler DB geführt |

### 3.4 Handelsmodi

| ID | Anforderung |
|----|-------------|
| P-12 | Modus `auto`: Signale werden sofort ohne manuellen Eingriff ausgeführt |
| P-13 | Modus `approval`: Signale erzeugen Vorschläge, keine automatische Ausführung |
| P-14 | Im Approval-Modus: Vorschlagsgenerierung einmal täglich um 8:00 Uhr MEZ |
| P-15 | Basis der Vorschläge: gestrige Schlusskurse + Indikatoren |
| P-16 | Vorschläge verfallen täglich um 22:00 Uhr MEZ automatisch (Status: `expired`) |
| P-17 | User kann pro Vorschlag einzeln aktivieren oder deaktivieren |
| P-18 | "Jetzt ausführen"-Button sendet alle aktivierten Vorschläge auf einmal |

---

## 4. Strategie-System

### 4.1 Strategie-Parameter

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

### 4.2 Strategie-Anforderungen

| ID | Anforderung |
|----|-------------|
| S-01 | Jeder User kann eigene Strategien erstellen und benennen |
| S-02 | Admin kann System-Strategien anlegen die alle User nutzen können |
| S-03 | Strategien können zwischen Portfolios desselben Users geteilt werden |
| S-04 | Eine Strategie kann mehreren Portfolios zugewiesen werden |
| S-05 | System-Strategien können von Usern nicht verändert werden (nur kopiert) |

### 4.3 Überschreibungs-Hierarchie

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
    buy_threshold: 70

  Sektor Technology:
    trailing_stop: 5%, max_position_size: 15%

  Aktie NVDA:
    max_position_size: 8%
    buy_threshold: 75
```

Effektiv für NVDA (US, Technology):
- `buy_threshold`: 75 (Aktien-Regel gewinnt)
- `trailing_stop`: 5% (Sektor-Regel gewinnt)
- `max_position_size`: 8% (Aktien-Regel gewinnt)
- alles andere: Portfolio-Default

### 4.4 Regel-Anforderungen

| ID | Anforderung |
|----|-------------|
| S-06 | Überschreibungsregeln können auf Ebene Markt, Sektor oder Aktie definiert werden |
| S-07 | Aktien-Regel überschreibt Sektor-Regel überschreibt Markt-Regel überschreibt Portfolio-Default |
| S-08 | Nur die tatsächlich überschriebenen Parameter werden gespeichert (kein vollständiges Objekt) |
| S-09 | Fehlende Parameter werden von der nächst höheren Ebene geerbt |
| S-10 | Der aufgelöste Parametersatz ist zur Laufzeit vollständig berechenbar |

---

## 5. Tages-Proposal-System (Approval-Modus)

### 5.1 Ablauf

```
08:00 Uhr MEZ
  → Scheduler startet Proposal-Generator für alle aktiven Approval-Portfolios
  → Pro Portfolio:
      1. Gestrige Schlusskurse + aktuelle Indikatoren berechnen
      2. Strategie-Hierarchie auflösen pro Aktie
      3. Signale generieren (BUY / SELL / HOLD)
      4. DailyProposal anlegen mit Liste von ProposedOrders
      5. Telegram-Benachrichtigung an User (optional)

08:00 - 15:30 MEZ (vor US-Börsenöffnung)
  → User öffnet UI, sieht Vorschlagsliste
  → Togglet einzelne Orders an/aus
  → Drückt "Jetzt ausführen"
  → Alle aktivierten Orders werden ausgeführt

22:00 Uhr MEZ
  → Nicht ausgeführte Proposals → Status: expired
```

### 5.2 Proposal-Anforderungen

| ID | Anforderung |
|----|-------------|
| PR-01 | Pro Portfolio und Tag wird maximal ein DailyProposal erstellt |
| PR-02 | Basis der Proposals: Schlusskurse des Vortages |
| PR-03 | Proposals werden um 8:00 Uhr MEZ automatisch generiert |
| PR-04 | Jeder ProposedOrder enthält: Symbol, Action, Stückzahl, Schätzpreis, Score, Begründung |
| PR-05 | Default-Zustand jedes ProposedOrder: `approved = true` |
| PR-06 | User kann `approved` pro Order auf `false` setzen |
| PR-07 | "Ausführen"-Button führt alle `approved = true` Orders aus |
| PR-08 | Nach Ausführung: Order erhält `executed = true` + `fill_price` |
| PR-09 | Nicht ausgeführte Orders werden um 22:00 Uhr auf `expired` gesetzt |
| PR-10 | Bereits ausgeführte Orders können nicht rückgängig gemacht werden |
| PR-11 | Proposal-Status: `open` → `partially_executed` / `executed` / `expired` |

---

## 6. Datenbankarchitektur

### 6.1 Neue Tabellen

```sql
users
  id, username, email, password_hash, role, is_active, created_at

portfolios
  id, user_id, name, type, mode, status, currency,
  starting_capital, strategy_id, ibkr_account_id, created_at

strategies
  id, user_id (nullable für System-Strategien), name, description,
  is_system, params (JSONB), created_at

strategy_rules
  id, strategy_id, level (market/sector/stock),
  key (z.B. "US" / "Technology" / "NVDA"),
  overrides (JSONB)

daily_proposals
  id, portfolio_id, proposal_date, generated_at, status,
  executed_at, notes

proposed_orders
  id, proposal_id, stock_id, action, shares_proposed,
  est_price_eur, score, reason, approved, executed,
  executed_at, fill_price
```

### 6.2 Geänderte bestehende Tabellen

```sql
accounts       → + portfolio_id (FK, unique)
positions      → + portfolio_id (FK)
trades         → + portfolio_id (FK)
signals        → + portfolio_id (FK, nullable)
equity_history → + portfolio_id (FK)
```

### 6.3 DB-Anforderungen

| ID | Anforderung |
|----|-------------|
| DB-01 | Migration darf keine bestehenden Daten löschen oder verändern |
| DB-02 | Alle bestehenden Datensätze werden einem Default-Portfolio (id=1) zugewiesen |
| DB-03 | Ein Default-Admin-User wird beim ersten Start angelegt |
| DB-04 | Migration wird als Alembic-Skript ausgeführt (versioniert, wiederholbar) |
| DB-05 | Rollback der Migration muss möglich sein |
| DB-06 | Strategie-Parameter aus config.py werden als System-Strategie "Default v1" gespeichert |

---

## 7. API-Endpunkte

### Auth

| ID | Method | Endpoint | Beschreibung |
|----|--------|----------|--------------|
| API-01 | POST | `/api/auth/login` | Login, gibt Session/Token zurück |
| API-02 | POST | `/api/auth/logout` | Session ungültig machen |
| API-03 | GET | `/api/auth/me` | Eingeloggter User + seine Portfolios |

### User-Verwaltung (Admin)

| ID | Method | Endpoint | Beschreibung |
|----|--------|----------|--------------|
| API-04 | GET | `/api/users` | Alle User auflisten |
| API-05 | POST | `/api/users` | Neuen User anlegen |
| API-06 | PUT | `/api/users/:id` | User bearbeiten |
| API-07 | PATCH | `/api/users/:id/status` | Aktivieren/Deaktivieren |
| API-08 | PUT | `/api/users/:id/password` | Passwort zurücksetzen |

### Portfolios

| ID | Method | Endpoint | Beschreibung |
|----|--------|----------|--------------|
| API-09 | GET | `/api/portfolios` | Eigene Portfolios |
| API-10 | POST | `/api/portfolios` | Neues Portfolio anlegen |
| API-11 | GET | `/api/portfolios/:id` | Detail + aktueller Stand |
| API-12 | PUT | `/api/portfolios/:id` | Portfolio bearbeiten |
| API-13 | PATCH | `/api/portfolios/:id/status` | Aktiv/Inaktiv |
| API-14 | DELETE | `/api/portfolios/:id` | Löschen (nur ohne offene Positionen) |

### Strategien

| ID | Method | Endpoint | Beschreibung |
|----|--------|----------|--------------|
| API-15 | GET | `/api/strategies` | Eigene + System-Strategien |
| API-16 | POST | `/api/strategies` | Neue Strategie |
| API-17 | PUT | `/api/strategies/:id` | Strategie bearbeiten |
| API-18 | DELETE | `/api/strategies/:id` | Löschen (nicht wenn in Verwendung) |
| API-19 | GET | `/api/strategies/:id/rules` | Überschreibungsregeln |
| API-20 | POST | `/api/strategies/:id/rules` | Neue Regel |
| API-21 | PUT | `/api/strategies/:id/rules/:rid` | Regel bearbeiten |
| API-22 | DELETE | `/api/strategies/:id/rules/:rid` | Regel löschen |

### Proposals

| ID | Method | Endpoint | Beschreibung |
|----|--------|----------|--------------|
| API-23 | GET | `/api/portfolios/:id/proposals` | Alle Proposals |
| API-24 | GET | `/api/portfolios/:id/proposals/today` | Heutiger Proposal |
| API-25 | PATCH | `/api/proposals/:id/orders/:oid` | approved true/false setzen |
| API-26 | POST | `/api/proposals/:id/execute` | Alle approved Orders ausführen |

### Bestehende Endpunkte (rückwärtskompatibel)

| ID | Anforderung |
|----|-------------|
| API-27 | Alle bestehenden Endpunkte (`/api/account`, `/api/positions` etc.) bleiben funktionsfähig |
| API-28 | Optionaler Parameter `?portfolio_id=X` — ohne Parameter: Standard-Portfolio des Users |

---

## 8. UI-Anforderungen

| ID | Anforderung |
|----|-------------|
| UI-01 | Login-Screen wird angezeigt wenn kein gültiger Session/Token vorhanden |
| UI-02 | Nach Login: Weiterleitung zum letzten aktiven Portfolio |
| UI-03 | Portfolio-Switcher im Header — Dropdown zum Wechseln des aktiven Portfolios |
| UI-04 | Portfolio-Verwaltungsseite: Liste, anlegen, bearbeiten, aktiv/inaktiv |
| UI-05 | Strategie-Editor: alle Parameter bearbeiten, Regeln pro Markt/Sektor/Aktie |
| UI-06 | Proposals-Panel nur im Approval-Modus sichtbar |
| UI-07 | Proposals-Panel: Tabelle mit Toggle pro Zeile (ausführen / überspringen) |
| UI-08 | "Alle ausführen"-Button mit Bestätigungsdialog |
| UI-09 | Alle bestehenden Charts und Tabellen zeigen Daten des aktiven Portfolios |
| UI-10 | Fehler und Erfolgsmeldungen werden als Toast/Notification angezeigt |

---

## 9. Offene Punkte — Entscheidung erforderlich

| # | Frage | Option A | Option B |
|---|-------|----------|----------|
| E-01 | Authentifizierung | Session (Flask-Login, einfacher) | JWT (stateless, für spätere Mobile-App besser) |
| E-02 | Proposal-Benachrichtigung | Nur UI | Telegram zusätzlich |
| E-03 | Mehrere IBKR-Konten | Ein Gateway für alle Portfolios | Pro IBKR-Portfolio eigene Gateway-Instanz |
| E-04 | Strategie-Sharing | Nur innerhalb eines Users | User können Strategien teilen |
| E-05 | Portfolio-Währung | Immer EUR | Auch USD-basierte Portfolios möglich |
| E-06 | IBKR Live | Direkt mit einbauen | Spätere Ausbaustufe (nach Live-Tests mit Paper) |

---

## 10. Bauplan

| Schritt | Was | Tests |
|---------|-----|-------|
| 1 | Alembic einrichten + Migration schreiben | DB-01 bis DB-06 |
| 2 | Models: User, Portfolio, Strategy, StrategyRule, DailyProposal, ProposedOrder | alle Model-Tests |
| 3 | Auth-System (Login/Logout/Session) | U-01 bis U-11 |
| 4 | Portfolio-Service (CRUD + portfolio-aware Zyklus) | P-01 bis P-11 |
| 5 | Strategie-Resolver (Hierarchie auflösen) | S-06 bis S-10 |
| 6 | Proposal-Generator (8:00 Uhr Job) | PR-01 bis PR-11 |
| 7 | API-Endpunkte | API-01 bis API-28 |
| 8 | UI (Login, Switcher, Strategie-Editor, Proposals-Panel) | UI-01 bis UI-10 |
| 9 | End-to-End Test mit Paper-Portfolio | G-01 bis G-06 |
| 10 | Review + Merge → master | — |

---

*Dieses Dokument ist zur Freigabe gedacht. Bitte Änderungen und Anmerkungen direkt kommunizieren.*  
*Anforderungs-IDs (G-xx, U-xx, P-xx, S-xx, PR-xx, DB-xx, API-xx, UI-xx) dienen als Referenz für Tests.*
