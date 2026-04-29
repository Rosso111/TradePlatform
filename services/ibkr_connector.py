"""
IBKR Connector — verwaltet die ib_insync Verbindung zum IB Gateway.
Stellt Verbindungs-Management und Order-Ausführung bereit.
"""
import asyncio
import logging
import threading
import time
from typing import Optional

from ib_insync import IB, Stock, MarketOrder, util

log = logging.getLogger(__name__)

GATEWAY_HOST = '127.0.0.1'
GATEWAY_PORT = 4002
CLIENT_ID    = 1
TIMEOUT      = 15


class IBKRConnector:
    def __init__(self):
        self._ib: Optional[IB] = None
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

    # ── Verbindung ────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        if self.is_connected():
            return True
        try:
            self._ensure_loop()
            future = asyncio.run_coroutine_threadsafe(self._connect_async(), self._loop)
            return future.result(timeout=TIMEOUT + 5)
        except Exception as e:
            log.error(f"IBKR connect failed: {e}")
            return False

    async def _connect_async(self) -> bool:
        self._ib = IB()
        await self._ib.connectAsync(GATEWAY_HOST, GATEWAY_PORT, clientId=CLIENT_ID, timeout=TIMEOUT)
        log.info(f"IBKR verbunden: Account {self._ib.managedAccounts()}")
        return True

    def disconnect(self):
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
            log.info("IBKR getrennt")

    def is_connected(self) -> bool:
        return self._ib is not None and self._ib.isConnected()

    def ensure_connected(self) -> bool:
        if not self.is_connected():
            log.warning("IBKR nicht verbunden — verbinde...")
            return self.connect()
        return True

    def _ensure_loop(self):
        if self._loop is None or not self._loop.is_running():
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop, args=(self._loop,), daemon=True
            )
            self._thread.start()
            import time; time.sleep(0.1)  # loop starten lassen

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop):
        asyncio.set_event_loop(loop)
        loop.run_forever()

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_market_order(self, symbol: str, qty: int, action: str) -> tuple[float, int]:
        """
        Platziert eine Market-Order. Gibt (fill_price, fill_qty) zurück.
        action: 'BUY' oder 'SELL'
        """
        if not self.ensure_connected():
            raise ConnectionError("IBKR nicht erreichbar")
        if qty <= 0:
            raise ValueError(f"Ungültige Stückzahl: {qty}")

        future = asyncio.run_coroutine_threadsafe(
            self._place_order_async(symbol, qty, action), self._loop
        )
        return future.result(timeout=30)

    async def _place_order_async(self, symbol: str, qty: int, action: str) -> tuple[float, int]:
        contract = Stock(symbol, 'SMART', 'USD')
        await self._ib.qualifyContractsAsync(contract)

        order = MarketOrder(action, qty)
        trade = self._ib.placeOrder(contract, order)

        # Auf Fill warten (max 20s)
        for _ in range(40):
            await asyncio.sleep(0.5)
            if trade.orderStatus.status == 'Filled':
                fill_price = trade.orderStatus.avgFillPrice
                fill_qty   = int(trade.orderStatus.filled)
                log.info(f"IBKR {action} {qty}x {symbol} @ ${fill_price:.2f} — Filled")
                return fill_price, fill_qty

        status = trade.orderStatus.status
        raise TimeoutError(f"{symbol} Order nicht gefüllt (Status: {status})")

    # ── Kontodaten ────────────────────────────────────────────────────────────

    def get_account_values(self) -> dict:
        """Gibt Kontostand-Dict zurück (cash, equity, buying_power)."""
        if not self.ensure_connected():
            return {}
        future = asyncio.run_coroutine_threadsafe(self._account_async(), self._loop)
        return future.result(timeout=10)

    async def _account_async(self) -> dict:
        await asyncio.sleep(1)
        account = self._ib.managedAccounts()[0]
        # BASE enthält den Gesamtwert in der Kontowährung (EUR bei Paper-Konto)
        vals_base = {v.tag: v.value for v in self._ib.accountValues(account) if v.currency in ('BASE', 'EUR')}
        vals_usd  = {v.tag: v.value for v in self._ib.accountValues(account) if v.currency == 'USD'}
        vals = {**vals_usd, **vals_base}
        return {
            'account':        account,
            'cash':           float(vals.get('TotalCashValue', 0)),
            'equity':         float(vals.get('NetLiquidation', 0)),
            'buying_power':   float(vals.get('BuyingPower', 0)),
            'unrealized_pnl': float(vals.get('UnrealizedPnL', 0)),
        }

    def get_positions(self) -> list[dict]:
        """Gibt offene IBKR-Positionen zurück."""
        if not self.ensure_connected():
            return []
        future = asyncio.run_coroutine_threadsafe(self._positions_async(), self._loop)
        return future.result(timeout=10)

    async def _positions_async(self) -> list[dict]:
        await asyncio.sleep(0.5)
        result = []
        for pos in self._ib.positions():
            result.append({
                'symbol': pos.contract.symbol,
                'qty':    pos.position,
                'avg_cost': pos.avgCost,
            })
        return result


# Modul-weite Singleton-Instanz
connector = IBKRConnector()
