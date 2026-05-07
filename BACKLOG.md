# TradePlatform — Backlog

Offene Themen, geplante Features und technische Schulden.
Status: `[ ]` offen · `[~]` in Arbeit · `[x]` erledigt

---

## Architektur & Code-Qualität

- [ ] Code-Kommentare von Deutsch auf Englisch übersetzen (kein Eile, schrittweise)
- [ ] Toter Code / ungenutzte Imports aufräumen (nach größeren Feature-Sprints)

---

## IBKR / Live-Trading

- [ ] **Live-Trading mit echtem Geld aktivieren** — Portfolio mit `type='ibkr_live'` anlegen, echte IBKR Account-ID eintragen, Live Gateway auf Port 4001 einrichten. Empfehlung: zuerst im `approval`-Modus starten (System schlägt vor, User bestätigt per Telegram) bevor auf `auto` gewechselt wird. Voraussetzungen: echte IBKR Account-ID, Live Gateway als separater Systemd-Service auf Port 4001.

- [ ] **Orderbuch / Order-History** — Übersicht aller platzierten IBKR-Orders mit Status (Filled, PreSubmitted, Submitted, Cancelled). Felder: Symbol, Action, Stückzahl, Fill-Preis, Zeitstempel, Account, Status. Datenquelle: entweder IBKR-API (`ib.trades()` / `ib.executions()`) live abfragen oder eigene `ibkr_orders`-Tabelle in der DB führen die bei jedem `place_market_order`-Aufruf befüllt wird. UI: eigener Tab im IBKR-Panel mit Filtermöglichkeit nach Status/Datum.
- [ ] **Position-Reconciliation DB ↔ IBKR** — nach jedem Trade DB-Positionen mit IBKR-Positionen abgleichen; Abweichungen in `position_reconciliation_logs` protokollieren (Priority 5 aus Spec)
- [ ] Per-Order Error-Handling vertiefen — aktuell gibt `execute_live_buy/-sell` `(False, msg)` zurück; Retry-Logik oder Dead-Letter-Queue bei transienten Fehlern überlegen
- [ ] IBKR-Kontostand automatisch in `Account.equity_eur` synchronisieren (statt nur aus DB-Positionen berechnen)
- [ ] **IBKR-Connector: Currency/Exchange für non-US-Aktien** — `_place_order_async` in `ibkr_connector.py` nutzt hardcoded `'USD'`; EU-/AU-/sonstige Aktien (z.B. `AI.PA`, `LIN.DE`, `TNE.AX`) schlagen mit Error 200 fehl. Fix: `stock.currency` und `stock.region` aus DB auslesen, daraus Exchange (`SMART`) + Currency (`EUR`/`AUD`/…) ableiten. Symbol-Suffix (`.PA`, `.DE`, `.AX`) vor Übergabe an IBKR entfernen.

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

## Testing & Qualitätssicherung

- [ ] Integrationstests für IBKR-Connector (gegen Paper-Gateway mit Mock-Daten)
- [ ] E2E-Test für den kompletten Handelszyklus (sim + live_runner)
- [ ] CI-Pipeline einrichten (GitHub Actions: Tests + Lint bei jedem Push)

---

## Infrastruktur & Deployment

- [ ] Branch `feature/multi-user-portfolios` nach GitHub pushen
- [ ] Docker-Compose-Setup für einfaches lokales Deployment
- [ ] Produktions-Deployment-Anleitung (Postgres, Gunicorn, Nginx, SSL)
- [ ] Alembic-Migrations-Workflow dokumentieren

---

## Ideen / Nice-to-have

- [ ] Telegram-Bot-Kommandos (Portfolio-Status abfragen, Notfall-Stop)
- [ ] Watchlist-Feature — Aktien beobachten ohne zu kaufen
- [ ] PDF-Report-Export (monatlicher Performance-Bericht)
- [ ] Backtesting direkt über die UI starten (ohne Replay-Engine im Code)
