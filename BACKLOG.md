# TradePlatform — Backlog

Offene Themen, geplante Features und technische Schulden.
Status: `[ ]` offen · `[~]` in Arbeit · `[x]` erledigt

---

## Kritische Bugs / Datenintegrität

- [x] **Atomare Trade-Ausführung** (`trading_engine.py`, `live_runner.py`) — try/except mit explizitem rollback um den gesamten Trade-Block. (PR #8)
- [x] **Fallback-Wechselkurs für alle Währungen** (`data_fetcher.py`) — bei yfinance-Ausfall: DB-Fallback (letzter bekannter Kurs aus `exchange_rates`-Tabelle), dann hardcodierter Näherungswert. Fehlende Währungen SEK/NOK/DKK/CAD/CNY ergänzt. `to_eur()` loggt Warnung statt silent 1.0. (PR #11)
- [ ] **Float-Rounding akkumuliert sich in Equity** (`data_fetcher.py`, `app.py:425`) — `close_eur` wird ohne Rounding gespeichert; über viele Zyklen entstehen Drift-Fehler. Fix: `round(..., 4)` konsequent bei allen Währungsumrechnungen.
- [ ] **Partial Fill wird ignoriert** (`live_runner.py:199`) — `fill_price_usd, _ = conn.place_market_order(...)` verwirft `fill_qty`; bei Partial Fill weicht DB-Position vom echten IBKR-Bestand ab. Fix: `fill_qty` prüfen und bei Abweichung warnen.
- [ ] **Fehlender Kurspreis → Stop-Loss nicht geprüft** (`trading_engine.py:287`) — wenn `Price.latest` nicht existiert, wird `continue` ausgeführt; SL/TP-Check wird übersprungen. Fix: Positions ohne aktuellen Kurs als stale markieren und separat loggen.

---

## Sicherheit

- [x] **`SECRET_KEY` Default ist bekannt** (`config.py`) — raises RuntimeError wenn nicht gesetzt in Produktion. (PR #9)
- [x] **`DEBUG=true` als Default** (`config.py`) — Default auf `false` geändert. (PR #9)
- [ ] **Admin-Passwort wird in stdout/Logs gedruckt** (`app.py:215`) — kann in Container-Logs oder Bash-History landen. Fix: Passwort nicht loggen, nur Hinweis "Admin angelegt, bitte ändern".
- [ ] **Kein CSRF-Schutz auf POST-Endpoints** (`routes/portfolios.py`, `routes/trading.py`) — alle state-ändernden Endpoints ohne CSRF-Token. Fix: `flask-wtf` CSRF-Protection oder `SameSite=Strict` Cookie-Policy.
- [ ] **Strategy-Params ohne Schema-Validierung** (`routes/strategies.py`) — User kann beliebige Keys in JSON-Feld schreiben. Fix: Whitelist erlaubter Parameter mit Typ- und Range-Prüfung.

---

## Architektur & Code-Qualität

- [ ] **Duplizierte Position-Sizing-Logik** (`trading_engine.py:54`, `live_runner.py:29`) — `calc_position_size()` und `_calc_shares()` sind fast identisch; Bug muss 2× gefixt werden. Fix: gemeinsames `services/position_sizing.py` extrahieren.
- [ ] **`_get_portfolio_snapshot()` ist God-Function** (`app.py:464`) — mischt DB-Queries, Business-Logik, WebSocket-Serialisierung und Fallback-Logik in ~45 Zeilen. Fix: aufteilen in `_calculate_portfolio_metrics()` + Serialisierungs-Wrapper.
- [ ] **`trading_engine.py` ist zu groß** (500+ Zeilen, 5 verschiedene Concerns) — Position-Sizing, Trade-Execution, SL/TP-Checks, Equity-Update und Hauptschleife in einer Datei. Fix: schrittweise in `execution.py`, `risk_management.py`, `equity.py` aufteilen.
- [ ] **Services nehmen `app`-Objekt als Parameter** (`trading_engine.py:417`, `live_runner.py:307`) — macht Unit-Testing ohne vollständige Flask-App unmöglich. Fix: Abhängigkeiten (session, config, fx_rates) injizierbar machen.
- [ ] Code-Kommentare von Deutsch auf Englisch übersetzen (kein Eile, schrittweise)
- [ ] Toter Code / ungenutzte Imports aufräumen (nach größeren Feature-Sprints)

---

## IBKR / Live-Trading

- [ ] **Live-Trading mit echtem Geld aktivieren** — Portfolio mit `type='ibkr_live'` anlegen, echte IBKR Account-ID eintragen, Live Gateway auf Port 4001 einrichten. Empfehlung: zuerst im `approval`-Modus starten (System schlägt vor, User bestätigt per Telegram) bevor auf `auto` gewechselt wird. Voraussetzungen: echte IBKR Account-ID, Live Gateway als separater Systemd-Service auf Port 4001.
- [ ] **Orderbuch / Order-History** — Übersicht aller platzierten IBKR-Orders mit Status (Filled, PreSubmitted, Submitted, Cancelled). Felder: Symbol, Action, Stückzahl, Fill-Preis, Zeitstempel, Account, Status. Datenquelle: entweder IBKR-API (`ib.trades()` / `ib.executions()`) live abfragen oder eigene `ibkr_orders`-Tabelle in der DB führen die bei jedem `place_market_order`-Aufruf befüllt wird. UI: eigener Tab im IBKR-Panel mit Filtermöglichkeit nach Status/Datum.
- [ ] **Position-Reconciliation DB ↔ IBKR** — nach jedem Trade DB-Positionen mit IBKR-Positionen abgleichen; Abweichungen in `position_reconciliation_logs` protokollieren (Priority 5 aus Spec)
- [ ] **Circuit-Breaker-Status ans Frontend melden** (`ibkr_connector.py`) — wenn der Circuit-Breaker auslöst (5 Failures → 5 Min Pause), weiß das Frontend nicht davon; IBKR-Portfolio läuft scheinbar normal. Fix: Portfolio-Status-Flag `ibkr_disconnected` setzen, UI zeigt Warnung.
- [ ] Per-Order Error-Handling vertiefen — aktuell gibt `execute_live_buy/-sell` `(False, msg)` zurück; Retry-Logik oder Dead-Letter-Queue bei transienten Fehlern überlegen
- [ ] IBKR-Kontostand automatisch in `Account.equity_eur` synchronisieren (statt nur aus DB-Positionen berechnen)

---

## Strategie & Algorithmus

- [x] StrategyResolver implementiert (S-06..S-10) — Hierarchie Global → Strategy → Markt → Sektor → Aktie
- [ ] **UI-Editor für Strategy Rules** (UI-05) — Frontend-Oberfläche zum Anlegen/Bearbeiten von Markt-/Sektor-/Aktien-Regeln
- [ ] Mehrere Strategien pro Portfolio testen / A-B-Vergleich
- [ ] Weitere Algorithmus-Modi (z.B. Mean-Reversion, Momentum-only)
- [ ] **Analyst-Score in adaptive Strategie einbauen** — aktuell hardcoded `50.0` in `replay_engine.py:2047` und `live_runner`; echten Yahoo-Finance-Konsens verwenden. Schritte: (1) Quick-Fix: `_compute_score_fast(row, params, analyst_score, sector_score)` statt `50.0`; (2) Gewichtung von 10 % auf 15–20 % erhöhen; (3) Optional: weitere Datenquellen (Refinitiv, Visible Alpha) für robusteren Konsens. Ziel: Aktien mit breitem Buy-Konsens werden gegenüber Holds/Downgrades bevorzugt.

---

## Frontend / UI

- [ ] **Mehrsprachigkeit (i18n)** — UI auf Deutsch/Englisch umschaltbar; Basis-Framework (z.B. i18next) einbinden
- [ ] Strategy-Rules-Editor (UI-05, siehe oben)
- [ ] Dark Mode / Theme-Umschalter
- [ ] Mobile-Optimierung (responsive Layout verbessern)

---

## Admin & Benutzerverwaltung

- [ ] E-Mail-Benachrichtigungen bei wichtigen Events (Trade ausgeführt, Stop-Loss, Fehler)
- [ ] Audit-Log für Admin-Aktionen (wer hat was wann geändert)
- [ ] Passwort-Reset per E-Mail (aktuell nur Admin-seitiger Reset)

---

## Performance

- [ ] **N+1-Query in Watchlist-Route** (`routes/trading.py`) — 150 Aktien × 4 Queries = ~600 DB-Queries pro Request (~5–10 Sek.). Fix: einen JOIN mit Subquery für `latest_price`, `latest_signal`, `prev_price` und offene Positionen.
- [ ] **Fehlende Indizes** (`models.py`) — `positions(portfolio_id, stock_id)`, `signals(portfolio_id, date DESC)` fehlen. Fix: in `_init_performance_indexes()` ergänzen.
- [ ] **`EquityHistory`-Query ohne Limit** (`trading_engine.py:395`) — scannt potenziell alle Einträge für `yesterday`. Fix: `.limit(1)` ergänzen.

---

## Testing & Qualitätssicherung

- [ ] Integrationstests für IBKR-Connector (gegen Paper-Gateway mit Mock-Daten)
- [ ] E2E-Test für den kompletten Handelszyklus (sim + live_runner)
- [ ] CI-Pipeline einrichten (GitHub Actions: Tests + Lint bei jedem Push)
- [ ] **Mock-Points für externe APIs** (`data_fetcher.py`, `ibkr_connector.py`) — `fetch_exchange_rates()` und `place_market_order()` rufen direkt yfinance/IBKR auf; kein Interface für Mocking. Fix: `DataFetcherInterface` + `MockDataFetcher` als Test-Double.

---

## Infrastruktur & Deployment

- [x] Branch `feature/multi-user-portfolios` nach GitHub pushen
- [x] **Position-Sizing Hard-Cap** (`config.py`, `trading_engine.py`, `live_runner.py`) — `MAX_POSITION_EUR = 20.000 EUR` verhindert Übergewichtung bei großen Konten (IBKR Paper 760k EUR → war 200k EUR/Trade). (PR #10)
- [x] **Wochenend-Fix** (`app.py`) — `trading_job` und `proposal_generate_job` werden Sa/So übersprungen. (PR #10)
- [x] **Automatische Berichte** (`services/report_generator.py`) — täglich/wöchentlich/monatlich/quartalsweise/jährlich als Markdown + Telegram. (PR #10)
- [ ] Docker-Compose-Setup für einfaches lokales Deployment
- [ ] Produktions-Deployment-Anleitung (Postgres, Gunicorn, Nginx, SSL)
- [ ] Alembic-Migrations-Workflow dokumentieren

---

## Ideen / Nice-to-have

- [ ] Telegram-Bot-Kommandos (Portfolio-Status abfragen, Notfall-Stop)
- [ ] Watchlist-Feature — Aktien beobachten ohne zu kaufen
- [ ] PDF-Report-Export (monatlicher Performance-Bericht)
- [ ] Backtesting direkt über die UI starten (ohne Replay-Engine im Code)
