"""
Report Generator — tägliche, wöchentliche, monatliche, quartalsweise und jährliche Berichte.
Speichert Markdown-Dateien unter reports/ und gibt einen Telegram-HTML-String zurück.
"""
import calendar
import logging
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

VIENNA = ZoneInfo('Europe/Vienna')
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(BASE_DIR, 'reports')

_WEEKDAY_NAMES = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']
_MONTH_NAMES = [
    '', 'Jänner', 'Februar', 'März', 'April', 'Mai', 'Juni',
    'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember',
]


# ── Datum-Hilfsfunktionen ─────────────────────────────────────────────────────

def _utc_range(local_start: datetime, local_end: datetime) -> tuple[datetime, datetime]:
    s = local_start.astimezone(timezone.utc).replace(tzinfo=None)
    e = local_end.astimezone(timezone.utc).replace(tzinfo=None)
    return s, e


def _day_utc(d: date):
    start = datetime(d.year, d.month, d.day, tzinfo=VIENNA)
    return _utc_range(start, start + timedelta(days=1))


def _week_utc(d: date):
    monday = d - timedelta(days=d.weekday())
    friday = monday + timedelta(days=4)
    start = datetime(monday.year, monday.month, monday.day, tzinfo=VIENNA)
    end = datetime(friday.year, friday.month, friday.day, 23, 59, 59, tzinfo=VIENNA)
    return _utc_range(start, end)


def _month_utc(d: date):
    _, last_day = calendar.monthrange(d.year, d.month)
    start = datetime(d.year, d.month, 1, tzinfo=VIENNA)
    end = datetime(d.year, d.month, last_day, 23, 59, 59, tzinfo=VIENNA)
    return _utc_range(start, end)


def _quarter_utc(d: date):
    q = (d.month - 1) // 3
    q_start = q * 3 + 1
    q_end = q_start + 2
    _, last_day = calendar.monthrange(d.year, q_end)
    start = datetime(d.year, q_start, 1, tzinfo=VIENNA)
    end = datetime(d.year, q_end, last_day, 23, 59, 59, tzinfo=VIENNA)
    return _utc_range(start, end)


def _year_utc(y: int):
    start = datetime(y, 1, 1, tzinfo=VIENNA)
    end = datetime(y, 12, 31, 23, 59, 59, tzinfo=VIENNA)
    return _utc_range(start, end)


# ── Daten-Abfragen ────────────────────────────────────────────────────────────

def _get_trades(portfolio_id: int, start: datetime, end: datetime):
    from models import Trade
    return (Trade.query
            .filter(Trade.portfolio_id == portfolio_id,
                    Trade.executed_at >= start,
                    Trade.executed_at < end)
            .order_by(Trade.executed_at)
            .all())


def _open_positions(portfolio_id: int):
    from models import Position
    return Position.query.filter_by(portfolio_id=portfolio_id).all()


def _unrealized_pnl(pos) -> float:
    curr = pos.current_price_eur or pos.entry_price_eur
    return (curr - pos.entry_price_eur) * pos.shares


# ── Markdown-Bausteine ────────────────────────────────────────────────────────

def _buy_table(trades) -> str:
    if not trades:
        return '*Keine Käufe.*\n'
    lines = [
        f"### Käufe ({len(trades)})\n",
        "| Symbol | Name | Stück | Kurs (EUR) | Investiert (EUR) |",
        "|--------|------|------:|-----------:|-----------------:|",
    ]
    for t in trades:
        name = (t.stock.name or '')[:28]
        lines.append(f"| {t.stock.symbol} | {name} | {t.shares:.1f} | {t.price_eur:.2f} | {t.total_eur:,.0f} |")
    total = sum(t.total_eur for t in trades)
    lines.append(f"\n**Gesamt investiert:** {total:,.0f} EUR\n")
    return '\n'.join(lines)


def _sell_table(trades) -> str:
    if not trades:
        return '*Keine Verkäufe.*\n'
    lines = [
        f"### Verkäufe ({len(trades)})\n",
        "| Symbol | Name | Stück | Kurs (EUR) | Erlös (EUR) | P&L (EUR) | P&L (%) | Grund |",
        "|--------|------|------:|-----------:|------------:|----------:|--------:|-------|",
    ]
    for t in trades:
        name = (t.stock.name or '')[:25]
        pnl_eur = t.pnl_eur or 0
        pnl_pct = t.pnl_pct or 0
        sign = '+' if pnl_eur >= 0 else ''
        reason = (t.reason or '')[:35]
        lines.append(
            f"| {t.stock.symbol} | {name} | {t.shares:.1f} | {t.price_eur:.2f} "
            f"| {t.total_eur:,.0f} | {sign}{pnl_eur:,.0f} | {sign}{pnl_pct:.1f}% | {reason} |"
        )
    total = sum(t.total_eur for t in trades)
    pnl = sum(t.pnl_eur or 0 for t in trades)
    sign = '+' if pnl >= 0 else ''
    lines.append(f"\n**Gesamt Erlöse:** {total:,.0f} EUR | **Realisiertes P&L:** {sign}{pnl:,.0f} EUR\n")
    return '\n'.join(lines)


def _portfolio_block(portfolio, trades, label: str = '') -> tuple[str, dict]:
    """Markdown-Block für ein Portfolio + Statistik-Dict."""
    from models import Account
    buys = [t for t in trades if t.action == 'BUY']
    sells = [t for t in trades if t.action == 'SELL']
    positions = _open_positions(portfolio.id)
    unrealized = sum(_unrealized_pnl(p) for p in positions)
    realized = sum(t.pnl_eur or 0 for t in sells)
    account = Account.query.filter_by(portfolio_id=portfolio.id).first()
    equity = account.equity_eur if account else 0

    header = f"## {portfolio.name}"
    if label:
        header += f" — {label}"
    header += f" ({portfolio.type})\n"

    unreal_sign = '+' if unrealized >= 0 else ''
    real_sign = '+' if realized >= 0 else ''

    footer = (
        f"**Offene Positionen:** {len(positions)}  \n"
        f"**Realisiertes P&L:** {real_sign}{realized:,.0f} EUR  \n"
        f"**Unrealisiertes P&L:** {unreal_sign}{unrealized:,.0f} EUR  \n"
        f"**Portfolio-Equity:** {equity:,.0f} EUR\n"
    )

    section = '\n'.join([header, _buy_table(buys), _sell_table(sells), footer, '---\n'])

    stats = {
        'buy_total': sum(t.total_eur for t in buys),
        'sell_total': sum(t.total_eur for t in sells),
        'realized_pnl': realized,
        'unrealized_pnl': unrealized,
        'buy_count': len(buys),
        'sell_count': len(sells),
        'equity': equity,
    }
    return section, stats


def _summary_table(portfolio_summaries: list[tuple[str, dict]]) -> str:
    lines = [
        "## Gesamtübersicht\n",
        "| Portfolio | Käufe | Verkäufe | Real. P&L | Unreal. P&L | Equity |",
        "|-----------|------:|---------:|----------:|------------:|-------:|",
    ]
    totals = {k: 0.0 for k in ('buy_total', 'sell_total', 'realized_pnl', 'unrealized_pnl', 'equity')}
    for name, s in portfolio_summaries:
        r = f"{'+' if s['realized_pnl'] >= 0 else ''}{s['realized_pnl']:,.0f}"
        u = f"{'+' if s['unrealized_pnl'] >= 0 else ''}{s['unrealized_pnl']:,.0f}"
        lines.append(
            f"| {name} | {s['buy_total']:,.0f} EUR | {s['sell_total']:,.0f} EUR "
            f"| {r} EUR | {u} EUR | {s['equity']:,.0f} EUR |"
        )
        for k in totals:
            totals[k] += s.get(k, 0)
    r = f"{'+' if totals['realized_pnl'] >= 0 else ''}{totals['realized_pnl']:,.0f}"
    u = f"{'+' if totals['unrealized_pnl'] >= 0 else ''}{totals['unrealized_pnl']:,.0f}"
    lines.append(
        f"| **Gesamt** | **{totals['buy_total']:,.0f} EUR** | **{totals['sell_total']:,.0f} EUR** "
        f"| **{r} EUR** | **{u} EUR** | **{totals['equity']:,.0f} EUR** |"
    )
    return '\n'.join(lines)


def _save(subdir: str, filename: str, content: str) -> str:
    path = os.path.join(REPORTS_DIR, subdir)
    os.makedirs(path, exist_ok=True)
    filepath = os.path.join(path, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    log.info("Report gespeichert: %s", filepath)
    return filepath


def _tg_summary(title: str, totals: dict, filepath: str) -> str:
    buy_c = int(totals.get('buy_count', 0))
    sell_c = int(totals.get('sell_count', 0))
    buy_t = totals.get('buy_total', 0)
    sell_t = totals.get('sell_total', 0)
    rpnl = totals.get('realized_pnl', 0)
    upnl = totals.get('unrealized_pnl', 0)
    r_sign = '+' if rpnl >= 0 else ''
    u_sign = '+' if upnl >= 0 else ''
    pnl_emoji = '🟢' if rpnl >= 0 else '🔴'
    rel_path = os.path.relpath(filepath, BASE_DIR)
    return (
        f"📊 <b>{title}</b>\n\n"
        f"🟢 Käufe: {buy_c} | {buy_t:,.0f} EUR\n"
        f"🔴 Verkäufe: {sell_c} | {sell_t:,.0f} EUR\n\n"
        f"{pnl_emoji} Realisiert: {r_sign}{rpnl:,.0f} EUR\n"
        f"📈 Unrealisiert: {u_sign}{upnl:,.0f} EUR\n\n"
        f"📄 <code>{rel_path}</code>"
    )


# ── Öffentliche Report-Funktionen ─────────────────────────────────────────────

def generate_daily_report(app, report_date: date | None = None) -> tuple[str, str]:
    """Tagesbericht. Gibt (filepath, telegram_html) zurück."""
    if report_date is None:
        report_date = date.today()
    start, end = _day_utc(report_date)
    weekday = _WEEKDAY_NAMES[report_date.weekday()]
    title = f"Tagesabschluss — {weekday}, {report_date.strftime('%d.%m.%Y')}"

    with app.app_context():
        from models import Portfolio
        portfolios = Portfolio.query.filter_by(status='active').all()
        summaries = []
        blocks = [f"# {title}\n\n*Generiert: {datetime.now(VIENNA).strftime('%d.%m.%Y %H:%M')} Uhr*\n"]
        for p in portfolios:
            trades = _get_trades(p.id, start, end)
            block, stats = _portfolio_block(p, trades)
            blocks.append(block)
            summaries.append((p.name, stats))
        blocks.append(_summary_table(summaries))

    md = '\n'.join(blocks)
    filepath = _save('daily', f"{report_date.isoformat()}.md", md)

    totals = {k: sum(s.get(k, 0) for _, s in summaries) for k in
              ('buy_total', 'sell_total', 'realized_pnl', 'unrealized_pnl', 'buy_count', 'sell_count')}
    return filepath, _tg_summary(title, totals, filepath)


def generate_weekly_report(app, week_date: date | None = None) -> tuple[str, str]:
    """Wochenbericht (Mo–Fr). week_date: beliebiger Tag der Woche, Default: letzte Woche."""
    if week_date is None:
        today = date.today()
        week_date = today - timedelta(days=today.weekday() + (7 if today.weekday() == 5 else 0))
    monday = week_date - timedelta(days=week_date.weekday())
    friday = monday + timedelta(days=4)
    start, end = _week_utc(week_date)
    iso_week = monday.isocalendar()[1]
    title = f"Wochenabschluss — KW {iso_week:02d} ({monday.strftime('%d.%m.')}–{friday.strftime('%d.%m.%Y')})"

    with app.app_context():
        from models import Portfolio
        portfolios = Portfolio.query.filter_by(status='active').all()
        summaries = []
        blocks = [f"# {title}\n\n*Generiert: {datetime.now(VIENNA).strftime('%d.%m.%Y %H:%M')} Uhr*\n"]

        for p in portfolios:
            # Alle Trades der Woche
            all_trades = _get_trades(p.id, start, end)
            # Pro Tag aufschlüsseln
            day_sections = []
            for offset in range(5):  # Mo–Fr
                d = monday + timedelta(days=offset)
                d_start, d_end = _day_utc(d)
                day_trades = [t for t in all_trades if d_start <= t.executed_at < d_end]
                if day_trades:
                    day_sections.append(f"#### {_WEEKDAY_NAMES[d.weekday()]} {d.strftime('%d.%m.')}\n")
                    day_buys = [t for t in day_trades if t.action == 'BUY']
                    day_sells = [t for t in day_trades if t.action == 'SELL']
                    day_sections.append(_buy_table(day_buys))
                    day_sections.append(_sell_table(day_sells))

            block, stats = _portfolio_block(p, all_trades)
            # Tageszusammenfassung vor Portfolio-Footer einfügen
            if day_sections:
                day_detail = f"### Tagesübersicht\n\n" + '\n'.join(day_sections)
                block = block.replace(_buy_table([t for t in all_trades if t.action == 'BUY']),
                                      day_detail + '\n### Wochengesamt\n\n' +
                                      _buy_table([t for t in all_trades if t.action == 'BUY']))
            blocks.append(block)
            summaries.append((p.name, stats))
        blocks.append(_summary_table(summaries))

    md = '\n'.join(blocks)
    filepath = _save('weekly', f"{monday.isocalendar()[0]}-W{iso_week:02d}.md", md)
    totals = {k: sum(s.get(k, 0) for _, s in summaries) for k in
              ('buy_total', 'sell_total', 'realized_pnl', 'unrealized_pnl', 'buy_count', 'sell_count')}
    return filepath, _tg_summary(title, totals, filepath)


def generate_monthly_report(app, report_month: date | None = None) -> tuple[str, str]:
    """Monatsbericht. report_month: beliebiger Tag des Monats, Default: letzter Monat."""
    if report_month is None:
        today = date.today()
        report_month = (today.replace(day=1) - timedelta(days=1))
    d = report_month
    start, end = _month_utc(d)
    title = f"Monatsabschluss — {_MONTH_NAMES[d.month]} {d.year}"

    with app.app_context():
        from models import Portfolio
        portfolios = Portfolio.query.filter_by(status='active').all()
        summaries = []
        blocks = [f"# {title}\n\n*Generiert: {datetime.now(VIENNA).strftime('%d.%m.%Y %H:%M')} Uhr*\n"]
        for p in portfolios:
            trades = _get_trades(p.id, start, end)
            block, stats = _portfolio_block(p, trades)
            blocks.append(block)
            summaries.append((p.name, stats))
        blocks.append(_summary_table(summaries))

    md = '\n'.join(blocks)
    filepath = _save('monthly', f"{d.year}-{d.month:02d}.md", md)
    totals = {k: sum(s.get(k, 0) for _, s in summaries) for k in
              ('buy_total', 'sell_total', 'realized_pnl', 'unrealized_pnl', 'buy_count', 'sell_count')}
    return filepath, _tg_summary(title, totals, filepath)


def generate_quarterly_report(app, report_quarter: date | None = None) -> tuple[str, str]:
    """Quartalsbericht. report_quarter: beliebiger Tag des Quartals, Default: letztes Quartal."""
    if report_quarter is None:
        today = date.today()
        report_quarter = (today.replace(day=1) - timedelta(days=1))
    d = report_quarter
    q = (d.month - 1) // 3 + 1
    start, end = _quarter_utc(d)
    title = f"Quartalsabschluss — Q{q} {d.year}"

    with app.app_context():
        from models import Portfolio
        portfolios = Portfolio.query.filter_by(status='active').all()
        summaries = []
        blocks = [f"# {title}\n\n*Generiert: {datetime.now(VIENNA).strftime('%d.%m.%Y %H:%M')} Uhr*\n"]
        for p in portfolios:
            trades = _get_trades(p.id, start, end)
            block, stats = _portfolio_block(p, trades)
            blocks.append(block)
            summaries.append((p.name, stats))
        blocks.append(_summary_table(summaries))

    md = '\n'.join(blocks)
    filepath = _save('quarterly', f"{d.year}-Q{q}.md", md)
    totals = {k: sum(s.get(k, 0) for _, s in summaries) for k in
              ('buy_total', 'sell_total', 'realized_pnl', 'unrealized_pnl', 'buy_count', 'sell_count')}
    return filepath, _tg_summary(title, totals, filepath)


def generate_yearly_report(app, year: int | None = None) -> tuple[str, str]:
    """Jahresbericht. Default: letztes Jahr."""
    if year is None:
        year = date.today().year - 1
    start, end = _year_utc(year)
    title = f"Jahresabschluss — {year}"

    with app.app_context():
        from models import Portfolio
        portfolios = Portfolio.query.filter_by(status='active').all()
        summaries = []
        blocks = [f"# {title}\n\n*Generiert: {datetime.now(VIENNA).strftime('%d.%m.%Y %H:%M')} Uhr*\n"]
        for p in portfolios:
            trades = _get_trades(p.id, start, end)
            block, stats = _portfolio_block(p, trades)
            blocks.append(block)
            summaries.append((p.name, stats))
        blocks.append(_summary_table(summaries))

    md = '\n'.join(blocks)
    filepath = _save('yearly', f"{year}.md", md)
    totals = {k: sum(s.get(k, 0) for _, s in summaries) for k in
              ('buy_total', 'sell_total', 'realized_pnl', 'unrealized_pnl', 'buy_count', 'sell_count')}
    return filepath, _tg_summary(title, totals, filepath)
