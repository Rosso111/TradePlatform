"""
IBKR Connector — verwaltet ib_insync-Verbindungen zum IB Gateway.

IBKRConnector:   Eine Verbindung (host+port). Unterstützt mehrere Accounts
                 über denselben Gateway (account-Parameter pro Order).
IBKRConnectionPool: Verwaltet je eine Verbindung pro (host, port) — typisch
                    eine für Paper (4002) und eine für Live (4001).
"""
import asyncio
import logging
import threading
import time
from typing import Optional

from ib_insync import IB, Stock, MarketOrder, util

log = logging.getLogger(__name__)

# Suffix → (ibkr_exchange, currency) für non-US-Aktien
_SUFFIX_MAP: dict[str, tuple[str, str]] = {
    '.PA': ('SMART', 'EUR'),   # Euronext Paris
    '.DE': ('SMART', 'EUR'),   # Xetra
    '.AS': ('SMART', 'EUR'),   # Euronext Amsterdam
    '.MI': ('SMART', 'EUR'),   # Borsa Italiana
    '.MC': ('SMART', 'EUR'),   # Bolsa Madrid
    '.BR': ('SMART', 'EUR'),   # Euronext Brüssel
    '.LS': ('SMART', 'EUR'),   # Euronext Lissabon
    '.HE': ('SMART', 'EUR'),   # Nasdaq Helsinki
    '.ST': ('SMART', 'SEK'),   # Nasdaq Stockholm
    '.OL': ('SMART', 'NOK'),   # Oslo Børs
    '.CO': ('SMART', 'DKK'),   # Nasdaq Kopenhagen
    '.SW': ('SMART', 'CHF'),   # SIX Swiss Exchange
    '.L':  ('SMART', 'GBP'),   # London Stock Exchange
    '.AX': ('SMART', 'AUD'),   # ASX
    '.T':  ('SMART', 'JPY'),   # Tokyo Stock Exchange
    '.HK': ('SMART', 'HKD'),   # Hong Kong Exchange
    '.KS': ('KSE',   'KRW'),   # Korea Stock Exchange
    '.KQ': ('SMART', 'KRW'),   # KOSDAQ
    '.SS': ('SMART', 'CNY'),   # Shanghai
    '.SZ': ('SMART', 'CNY'),   # Shenzhen
    '.TO': ('SMART', 'CAD'),   # Toronto Stock Exchange
    '.V':  ('SMART', 'CAD'),   # TSX Venture
}


def _resolve_contract(symbol: str, currency: str | None = None) -> Stock:
    """
    Wandelt ein DB-Symbol (z.B. 'AI.PA', 'LIN.DE', 'TNE.AX') in einen
    IBKR-Stock-Contract um. Suffix wird als Exchange/Currency-Hinweis genutzt
    und vor Übergabe an IBKR entfernt.
    """
    for suffix, (exch, ccy) in _SUFFIX_MAP.items():
        if symbol.upper().endswith(suffix.upper()):
            clean = symbol[: -len(suffix)]
            return Stock(clean, exch, currency or ccy)
    # US-Aktie oder unbekanntes Suffix → unverändert, USD
    return Stock(symbol, 'SMART', currency or 'USD')


class OrderPendingError(Exception):
    """Order ist in IBKR angekommen (PreSubmitted/Submitted), aber noch nicht gefüllt."""
    def __init__(self, symbol: str, status: str, order_id: int = 0):
        self.symbol   = symbol
        self.status   = status
        self.order_id = order_id
        super().__init__(f"{symbol}: Order ausstehend (Status: {status})")


# Circuit-Breaker-Schwellen
_MAX_FAILURES = 5
_BACKOFF_SECONDS = 300   # 5 Minuten Pause nach zu vielen Fehlern
_CONNECT_TIMEOUT = 15    # Sekunden bis Gateway antwortet
_ORDER_TIMEOUT   = 30    # Sekunden bis Fill


class IBKRConnector:
    """
    Verbindung zu einem einzelnen IB-Gateway (host + port).
    Thread-sicher; nutzt einen dedizierten asyncio-Loop im Hintergrund.
    Circuit-Breaker verhindert endlose Reconnect-Versuche bei Ausfall.
    """

    def __init__(self, host: str, port: int, client_id: int = 1):
        self._host      = host
        self._port      = port
        self._client_id = client_id
        self._ib: Optional[IB] = None
        self._lock      = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        # Circuit-Breaker-State
        self._consecutive_failures = 0
        self._backoff_until: float = 0.0

    # ── Verbindungs-Management ────────────────────────────────────────────────

    def connect(self) -> bool:
        # Circuit-Breaker: Backoff noch aktiv?
        if time.time() < self._backoff_until:
            remaining = int(self._backoff_until - time.time())
            log.warning("IBKR %s:%s — Backoff aktiv, noch %ds.", self._host, self._port, remaining)
            return False

        if self.is_connected():
            return True

        try:
            self._ensure_loop()
            future = asyncio.run_coroutine_threadsafe(self._connect_async(), self._loop)
            result = future.result(timeout=_CONNECT_TIMEOUT + 5)
            if result:
                self._consecutive_failures = 0
                self._backoff_until = 0.0
            return result
        except Exception as e:
            self._consecutive_failures += 1
            log.error("IBKR %s:%s connect fehlgeschlagen (%d/%d): %s",
                      self._host, self._port, self._consecutive_failures, _MAX_FAILURES, e)
            if self._consecutive_failures >= _MAX_FAILURES:
                self._backoff_until = time.time() + _BACKOFF_SECONDS
                log.error("IBKR %s:%s — Circuit-Breaker ausgelöst, Pause %ds.",
                          self._host, self._port, _BACKOFF_SECONDS)
            return False

    async def _connect_async(self) -> bool:
        self._ib = IB()
        await self._ib.connectAsync(
            self._host, self._port,
            clientId=self._client_id,
            timeout=_CONNECT_TIMEOUT,
        )
        accounts = self._ib.managedAccounts()
        log.info("IBKR %s:%s verbunden — Accounts: %s", self._host, self._port, accounts)
        return True

    def disconnect(self):
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
            log.info("IBKR %s:%s getrennt.", self._host, self._port)

    def is_connected(self) -> bool:
        return self._ib is not None and self._ib.isConnected()

    def ensure_connected(self) -> bool:
        if not self.is_connected():
            log.warning("IBKR %s:%s nicht verbunden — verbinde...", self._host, self._port)
            return self.connect()
        return True

    def _ensure_loop(self):
        if self._loop is None or not self._loop.is_running():
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop, args=(self._loop,), daemon=True,
                name=f"ibkr-loop-{self._port}",
            )
            self._thread.start()
            time.sleep(0.1)

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop):
        asyncio.set_event_loop(loop)
        loop.run_forever()

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_market_order(
        self,
        symbol: str,
        qty: int,
        action: str,
        account: str = '',
        currency: str | None = None,
    ) -> tuple[float, int]:
        """
        Platziert eine Market-Order und wartet auf Fill.
        symbol:   DB-Symbol inkl. Suffix (z.B. 'AI.PA', 'LIN.DE', 'NVDA').
        currency: Override — falls None, wird aus dem Symbol-Suffix abgeleitet.
        account:  IBKR-Account-ID (z.B. 'DU123456'). Leer = Default-Account.
        Gibt (fill_price, fill_qty) zurück.
        """
        if not self.ensure_connected():
            raise ConnectionError(f"IBKR {self._host}:{self._port} nicht erreichbar")
        if qty <= 0:
            raise ValueError(f"Ungültige Stückzahl: {qty}")

        future = asyncio.run_coroutine_threadsafe(
            self._place_order_async(symbol, qty, action, account, currency), self._loop
        )
        try:
            return future.result(timeout=_ORDER_TIMEOUT + 5)
        except TimeoutError:
            # Äußerer Timeout bevor der innere Loop fertig ist → als ausstehend behandeln
            raise OrderPendingError(symbol, 'PreSubmitted')

    async def _place_order_async(
        self, symbol: str, qty: int, action: str, account: str,
        currency: str | None = None,
    ) -> tuple[float, int]:
        contract = _resolve_contract(symbol, currency)
        await self._ib.qualifyContractsAsync(contract)

        order = MarketOrder(action, qty)
        if account:
            order.account = account

        trade = self._ib.placeOrder(contract, order)

        for _ in range(int(_ORDER_TIMEOUT / 0.5)):
            await asyncio.sleep(0.5)
            if trade.orderStatus.status == 'Filled':
                fill_price = trade.orderStatus.avgFillPrice
                fill_qty   = int(trade.orderStatus.filled)
                log.info("IBKR %s %dx %s @ $%.2f — Filled (account=%s)",
                         action, qty, symbol, fill_price, account or 'default')
                return fill_price, fill_qty

        status = trade.orderStatus.status
        if status in ('PreSubmitted', 'Submitted'):
            raise OrderPendingError(symbol, status, trade.order.orderId)
        raise TimeoutError(f"{symbol} Order nicht gefüllt nach {_ORDER_TIMEOUT}s (Status: {status})")

    # ── Kontodaten ────────────────────────────────────────────────────────────

    def get_account_values(self, account: str = '') -> dict:
        """Gibt Kontostand zurück. account: spezifische Account-ID oder leer für Default."""
        if not self.ensure_connected():
            return {}
        future = asyncio.run_coroutine_threadsafe(
            self._account_async(account), self._loop
        )
        return future.result(timeout=10)

    async def _account_async(self, account: str) -> dict:
        await asyncio.sleep(0.5)
        acc = account or (self._ib.managedAccounts()[0] if self._ib.managedAccounts() else '')
        vals_base = {v.tag: v.value for v in self._ib.accountValues(acc) if v.currency in ('BASE', 'EUR')}
        vals_usd  = {v.tag: v.value for v in self._ib.accountValues(acc) if v.currency == 'USD'}
        vals = {**vals_usd, **vals_base}
        return {
            'account':        acc,
            'cash':           float(vals.get('TotalCashValue', 0)),
            'equity':         float(vals.get('NetLiquidation', 0)),
            'buying_power':   float(vals.get('BuyingPower', 0)),
            'unrealized_pnl': float(vals.get('UnrealizedPnL', 0)),
        }

    def get_positions(self, account: str = '') -> list[dict]:
        """Gibt offene IBKR-Positionen zurück, gefiltert nach Account-ID wenn angegeben."""
        if not self.ensure_connected():
            return []
        future = asyncio.run_coroutine_threadsafe(
            self._positions_async(account), self._loop
        )
        return future.result(timeout=10)

    async def _positions_async(self, account: str) -> list[dict]:
        await asyncio.sleep(0.5)
        result = []
        for pos in self._ib.positions():
            if account and pos.account != account:
                continue
            result.append({
                'symbol':   pos.contract.symbol,
                'qty':      pos.position,
                'avg_cost': pos.avgCost,
                'account':  pos.account,
            })
        return result

    def get_portfolio_items(self, account: str = '') -> list[dict]:
        """Portfolio-Positionen inkl. Marktkurs, Marktwert und unrealisiertem P&L."""
        if not self.ensure_connected():
            return []
        future = asyncio.run_coroutine_threadsafe(
            self._portfolio_async(account), self._loop
        )
        return future.result(timeout=10)

    async def _portfolio_async(self, account: str) -> list[dict]:
        await asyncio.sleep(0.5)
        result = []
        for item in self._ib.portfolio():
            if account and item.account != account:
                continue
            avg = item.averageCost or 0
            mkt = item.marketPrice if (item.marketPrice and item.marketPrice > 0) else None
            pnl_pct = round((mkt - avg) / avg * 100, 2) if (avg and mkt) else None
            result.append({
                'symbol':         item.contract.symbol,
                'qty':            item.position,
                'avg_cost':       avg,
                'market_price':   mkt,
                'market_value':   item.marketValue if mkt else None,
                'unrealized_pnl': item.unrealizedPNL if mkt else None,
                'pnl_pct':        pnl_pct,
                'account':        item.account,
            })
        return result

    def __repr__(self):
        status = 'verbunden' if self.is_connected() else 'getrennt'
        return f"IBKRConnector({self._host}:{self._port} client={self._client_id} [{status}])"


# ── Connection-Pool ───────────────────────────────────────────────────────────

class IBKRConnectionPool:
    """
    Verwaltet je eine IBKRConnector-Instanz pro (host, port).
    Typisch: eine für Paper-Trading (Port 4002), eine für Live (Port 4001).
    Thread-sicher.
    """
    _pool: dict[tuple[str, int], IBKRConnector] = {}
    _lock = threading.Lock()

    @classmethod
    def get(cls, host: str, port: int, client_id: int = 1) -> IBKRConnector:
        """Gibt den Connector für (host, port) zurück, legt ihn bei Bedarf an."""
        key = (host, port)
        with cls._lock:
            if key not in cls._pool:
                cls._pool[key] = IBKRConnector(host, port, client_id)
                log.info("IBKRConnectionPool: neuer Connector für %s:%s (client_id=%d)",
                         host, port, client_id)
            return cls._pool[key]

    @classmethod
    def disconnect_all(cls):
        """Trennt alle Verbindungen — für sauberes App-Shutdown."""
        with cls._lock:
            for conn in cls._pool.values():
                try:
                    conn.disconnect()
                except Exception:
                    pass
            cls._pool.clear()

    @classmethod
    def status(cls) -> list[dict]:
        """Gibt Status aller Verbindungen zurück."""
        with cls._lock:
            return [
                {
                    'host': host,
                    'port': port,
                    'connected': conn.is_connected(),
                    'circuit_breaker_active': time.time() < conn._backoff_until,
                }
                for (host, port), conn in cls._pool.items()
            ]


# Backward-compat: Singleton für den Paper-Gateway (Port 4002)
# Wird in Tests und Altcode verwendet; live_runner nutzt IBKRConnectionPool.
connector = IBKRConnector('127.0.0.1', 4002, client_id=1)
