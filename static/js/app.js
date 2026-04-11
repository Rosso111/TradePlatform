/**
 * Finanz-App – Hauptlogik
 * Verwaltet State, API-Calls, WebSocket und UI-Updates.
 */

const API = '/api';
let socket = null;
let state = {
  account: {},
  positions: [],
  watchlist: [],
  trades: [],
  equity: [],
  signals: [],
  selectedSymbol: null,
  activeTab: 'dashboard',
};

// ─── Init ────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  initWebSocket();
  loadAll();
  initNavigation();
  initPeriodButtons();

  // Overlay nach spätestens 5s ausblenden — App ist nutzbar während Daten noch laden
  setTimeout(() => hideLoading(), 5000);
});

function initWebSocket() {
  socket = io();

  socket.on('connect', () => {
    setStatusDot(true);
  });
  socket.on('disconnect', () => {
    setStatusDot(false);
  });
  socket.on('portfolio_update', (data) => {
    state.account = { ...state.account, ...data };
    updateHeaderEquity(data);
    renderPositionsMini(data.positions || []);
  });
  socket.on('trading_actions', (data) => {
    const actions = data.actions || [];
    actions.forEach(msg => {
      addLogEntry(msg);
      if (msg.includes('KAUF'))    showToast(msg, 'buy');
      else if (msg.includes('VERKAUF')) showToast(msg, 'sell');
    });
    loadAll();
  });
  socket.on('status', (data) => {
    const msg = data.message || '';
    // Overlay-Text aktualisieren (falls noch sichtbar)
    const loadingMsg = document.getElementById('loading-msg');
    if (loadingMsg) loadingMsg.textContent = msg;
    // Fortschritts-Banner in der App anzeigen
    showDataLoadingBanner(msg);
    if (msg.includes('bereit')) {
      hideLoading();
      hideDataLoadingBanner();
      loadAll(); // Watchlist/Daten nach vollständigem Load neu laden
    }
  });
}

// ─── Daten laden ─────────────────────────────────────────────────────────────

async function loadAll() {
  await Promise.all([
    loadAccount(),
    loadPositions(),
    loadWatchlist(),
    loadTrades(),
    loadEquity(),
  ]);
}

async function api(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(`API ${path}: ${r.status}`);
  return r.json();
}

async function loadAccount() {
  try {
    state.account = await api('/account');
    renderAccountStats();
    checkLoadingDone();
  } catch(e) { console.warn('Account:', e); }
}

async function loadPositions() {
  try {
    state.positions = await api('/positions');
    renderPositionsTable();
    renderPositionsMini(state.positions);
    renderSectorChart();
  } catch(e) { console.warn('Positions:', e); }
}

async function loadWatchlist() {
  try {
    state.watchlist = await api('/watchlist');
    renderWatchlist();
    if (!state.selectedSymbol && state.watchlist.length > 0) {
      selectSymbol(state.watchlist[0].symbol);
    }
    checkLoadingDone();
  } catch(e) { console.warn('Watchlist:', e); }
}

async function loadTrades() {
  try {
    state.trades = await api('/trades?limit=100');
    renderTradesTable();
    renderTradeFeed();
  } catch(e) { console.warn('Trades:', e); }
}

async function loadEquity() {
  try {
    state.equity = await api('/equity?days=60');
    renderEquityChart();
  } catch(e) { console.warn('Equity:', e); }
}

async function loadPricesForChart(symbol, days = 90) {
  try {
    const data = await api(`/prices/${symbol}?days=${days}`);
    renderCandleChart(data);
  } catch(e) { console.warn('Prices:', e); }
}

async function loadAlgoParams() {
  try {
    const params = await api('/algo/params');
    renderAlgoParams(params);
  } catch(e) { console.warn('Algo:', e); }
}

function checkLoadingDone() {
  // Overlay sofort ausblenden sobald Account-Daten da sind
  if (state.account && state.account.cash_eur !== undefined) {
    hideLoading();
  }
}

function showDataLoadingBanner(msg) {
  let banner = document.getElementById('data-loading-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'data-loading-banner';
    banner.style.cssText = [
      'position:fixed', 'top:52px', 'left:0', 'right:0', 'z-index:500',
      'background:var(--bg-secondary)', 'border-bottom:1px solid var(--yellow)',
      'padding:7px 16px', 'display:flex', 'align-items:center', 'gap:10px',
      'font-size:.8rem', 'color:var(--yellow)',
    ].join(';');
    banner.innerHTML = `
      <div style="width:12px;height:12px;border:2px solid var(--yellow);border-top-color:transparent;
                  border-radius:50%;animation:spin .8s linear infinite;flex-shrink:0"></div>
      <span id="data-loading-msg"></span>`;
    document.body.appendChild(banner);
  }
  document.getElementById('data-loading-msg').textContent = msg;
}

function hideDataLoadingBanner() {
  const banner = document.getElementById('data-loading-banner');
  if (banner) banner.remove();
}

// ─── Render: Account Stats ───────────────────────────────────────────────────

function renderAccountStats() {
  const a = state.account;
  if (!a) return;

  setEl('stat-equity',    fmtEUR(a.equity_eur));
  setEl('stat-cash',      fmtEUR(a.cash_eur));
  setEl('stat-positions', a.open_positions ?? '-');
  setEl('stat-winrate',   a.win_rate ? a.win_rate + '%' : '-');
  setEl('stat-trades',    a.total_trades ?? '-');
  setEl('stat-commission', fmtEUR(a.total_commission));

  const pnl = a.total_return_eur ?? 0;
  const pct = a.total_return_pct ?? 0;
  const pnlEl = document.getElementById('stat-pnl');
  if (pnlEl) {
    pnlEl.textContent = fmtEUR(pnl) + ' (' + fmtPct(pct) + ')';
    pnlEl.className = 'stat-value ' + (pnl >= 0 ? 'positive' : 'negative');
  }
}

function updateHeaderEquity(data) {
  const el = document.getElementById('header-equity-val');
  if (el) el.textContent = fmtEUR(data.equity_eur);
  const retEl = document.getElementById('header-return');
  if (retEl) {
    const pct = data.total_return_pct || 0;
    retEl.textContent = fmtPct(pct);
    retEl.className = pct >= 0 ? 'positive' : 'negative';
  }
}

// ─── Render: Watchlist ────────────────────────────────────────────────────────

function renderWatchlist() {
  const container = document.getElementById('watchlist-items');
  if (!container) return;

  container.innerHTML = state.watchlist.map(item => {
    const isActive = item.symbol === state.selectedSymbol;
    const isPos = item.in_portfolio;
    const scoreClass = item.action === 'BUY' ? 'score-buy'
                     : item.action === 'SELL' ? 'score-sell' : 'score-hold';
    const changeClass = (item.change_pct || 0) >= 0 ? 'positive' : 'negative';
    return `
    <div class="watchlist-item ${isActive ? 'active' : ''} ${isPos ? 'in-portfolio' : ''}"
         onclick="selectSymbol('${item.symbol}')">
      <span class="wi-symbol">${item.symbol.split('.')[0]}</span>
      <div class="wi-info">
        <div class="wi-name">${item.name}</div>
        <div class="wi-name" style="color:var(--text-muted)">${item.sector} · ${item.region}</div>
      </div>
      <div class="wi-price">
        <span class="wi-price-val">${fmtNum(item.price_eur)} €</span>
        <span class="wi-change ${changeClass}">${item.change_pct >= 0 ? '+' : ''}${item.change_pct?.toFixed(2)}%</span>
      </div>
      ${item.score != null ? `<span class="score-badge ${scoreClass}">${item.score.toFixed(0)}</span>` : ''}
    </div>`;
  }).join('');
}

function selectSymbol(symbol) {
  state.selectedSymbol = symbol;
  const activePeriodBtn = document.querySelector('.period-btn.active');
  const days = activePeriodBtn ? parseInt(activePeriodBtn.dataset.days) : 90;
  loadPricesForChart(symbol, days);
  renderWatchlist();
}

// ─── Render: Candlestick Chart ────────────────────────────────────────────────

let candleChart = null;
let candleSeries = null;

function renderCandleChart(data) {
  const container = document.getElementById('chart-container');
  if (!container) return;

  const prices = data.prices || [];
  if (prices.length === 0) return;

  // Header aktualisieren
  const last = prices[prices.length - 1];
  const prev = prices[prices.length - 2];
  const change = prev ? ((last.close - prev.close) / prev.close * 100) : 0;
  setEl('chart-symbol-text', data.symbol);
  setEl('chart-price-text', fmtNum(last.close_eur) + ' €');
  const chEl = document.getElementById('chart-change-text');
  if (chEl) {
    chEl.textContent = (change >= 0 ? '+' : '') + change.toFixed(2) + '%';
    chEl.className = 'chart-change ' + (change >= 0 ? 'positive' : 'negative');
  }

  // Chart initialisieren oder neu zeichnen
  if (!candleChart) {
    candleChart = LightweightCharts.createChart(container, {
      layout: {
        background: { type: 'solid', color: '#1c2128' },
        textColor: '#8b949e',
      },
      grid: {
        vertLines: { color: '#21262d' },
        horzLines: { color: '#21262d' },
      },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#30363d' },
      timeScale: { borderColor: '#30363d', timeVisible: true },
      width: container.clientWidth,
      height: container.clientHeight,
    });

    candleSeries = candleChart.addCandlestickSeries({
      upColor: '#3fb950', downColor: '#f85149',
      borderUpColor: '#3fb950', borderDownColor: '#f85149',
      wickUpColor: '#3fb950', wickDownColor: '#f85149',
    });

    window.addEventListener('resize', () => {
      if (candleChart) candleChart.resize(container.clientWidth, container.clientHeight);
    });
  }

  const chartData = prices.map(p => ({
    time: p.date,
    open: p.open, high: p.high, low: p.low, close: p.close,
  }));
  candleSeries.setData(chartData);
  candleChart.timeScale().fitContent();
}

// ─── Render: Positionen ───────────────────────────────────────────────────────

function renderPositionsTable() {
  const tbody = document.getElementById('positions-tbody');
  if (!tbody) return;

  if (state.positions.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" class="empty-state" style="padding:30px;text-align:center;color:var(--text-muted)">
      Keine offenen Positionen</td></tr>`;
    return;
  }

  tbody.innerHTML = state.positions.map(p => {
    const pnlClass = p.unrealized_pnl_eur >= 0 ? 'positive' : 'negative';
    return `
    <tr onclick="selectSymbol('${p.symbol}')" style="cursor:pointer">
      <td><span class="symbol-cell">${p.symbol.split('.')[0]}</span></td>
      <td class="name-cell">${p.name}</td>
      <td><span class="badge" style="background:rgba(88,166,255,.1);color:var(--accent)">${p.sector}</span></td>
      <td class="mono">${fmtNum(p.shares)}</td>
      <td class="mono">${fmtNum(p.entry_price_eur)} €</td>
      <td class="mono">${fmtNum(p.current_price_eur)} €</td>
      <td class="mono"><span class="stop-loss">${p.stop_loss ? fmtNum(p.stop_loss) : '-'}</span></td>
      <td class="mono ${pnlClass} bold">${fmtEUR(p.unrealized_pnl_eur)}</td>
      <td class="mono ${pnlClass}">${p.unrealized_pnl_pct >= 0 ? '+' : ''}${p.unrealized_pnl_pct.toFixed(2)}%</td>
    </tr>`;
  }).join('');
}

function renderPositionsMini(positions) {
  const container = document.getElementById('positions-mini');
  if (!container) return;

  if (!positions || positions.length === 0) {
    container.innerHTML = '<div class="empty-state" style="padding:16px;text-align:center;color:var(--text-muted);font-size:.8rem">Keine offenen Positionen</div>';
    return;
  }

  container.innerHTML = positions.map(p => {
    const pnl = p.pnl_eur || p.unrealized_pnl_eur || 0;
    const pnlPct = p.pnl_pct || p.unrealized_pnl_pct || 0;
    const cls = pnl >= 0 ? 'positive' : 'negative';
    return `
    <div class="pos-mini-item">
      <span class="pos-mini-symbol">${(p.symbol || '').split('.')[0]}</span>
      <div class="pos-mini-info">
        <div style="font-size:.75rem;color:var(--text-secondary)">${fmtNum(p.shares || 0)} Stk</div>
        <div style="font-size:.7rem;color:var(--text-muted)">${fmtNum(p.entry_price_eur || 0)} €</div>
      </div>
      <div class="pos-mini-pnl ${cls}">
        <div>${fmtEUR(pnl)}</div>
        <div style="font-size:.7rem">${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(1)}%</div>
      </div>
    </div>`;
  }).join('');
}

// ─── Render: Trades ───────────────────────────────────────────────────────────

function renderTradesTable() {
  const tbody = document.getElementById('trades-tbody');
  if (!tbody) return;

  if (state.trades.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" style="padding:30px;text-align:center;color:var(--text-muted)">
      Noch keine Trades</td></tr>`;
    return;
  }

  tbody.innerHTML = state.trades.map(t => {
    const isBuy = t.action === 'BUY';
    const pnlClass = t.pnl_eur >= 0 ? 'positive' : 'negative';
    return `
    <tr>
      <td style="color:var(--text-muted);font-size:.75rem">${fmtDateTime(t.executed_at)}</td>
      <td><span class="badge ${isBuy ? 'badge-buy' : 'badge-sell'}">${t.action}</span></td>
      <td><span class="symbol-cell">${t.symbol.split('.')[0]}</span></td>
      <td class="name-cell">${t.name}</td>
      <td class="mono">${fmtNum(t.shares)}</td>
      <td class="mono">${fmtNum(t.price_eur)} €</td>
      <td class="mono">${fmtEUR(t.total_eur)}</td>
      <td class="mono ${isBuy ? 'neutral' : pnlClass}">${isBuy ? '-' : fmtEUR(t.pnl_eur)}</td>
    </tr>`;
  }).join('');
}

function renderTradeFeed() {
  const container = document.getElementById('trade-feed');
  if (!container) return;

  const recent = state.trades.slice(0, 8);
  if (recent.length === 0) {
    container.innerHTML = '<div style="color:var(--text-muted);font-size:.78rem;padding:8px 0">Noch keine Trades</div>';
    return;
  }

  container.innerHTML = recent.map(t => {
    const isBuy = t.action === 'BUY';
    const pnlText = !isBuy && t.pnl_eur !== 0
      ? `<span class="${t.pnl_eur >= 0 ? 'positive' : 'negative'}">${fmtEUR(t.pnl_eur)}</span>` : '';
    return `
    <div class="trade-feed-item">
      <span class="badge ${isBuy ? 'badge-buy' : 'badge-sell'}" style="font-size:.65rem">${t.action}</span>
      <div style="flex:1">
        <span style="font-weight:700">${t.symbol.split('.')[0]}</span>
        <span style="color:var(--text-muted)"> ${fmtNum(t.shares)} @ ${fmtNum(t.price_eur)}€</span>
        ${pnlText}
      </div>
      <span class="trade-time">${fmtTime(t.executed_at)}</span>
    </div>`;
  }).join('');
}

// ─── Render: Equity Chart ─────────────────────────────────────────────────────

let equityChart = null;
let equitySeries = null;

function renderEquityChart() {
  const container = document.getElementById('equity-chart');
  if (!container || state.equity.length === 0) return;

  if (!equityChart) {
    equityChart = LightweightCharts.createChart(container, {
      layout: {
        background: { type: 'solid', color: '#161b22' },
        textColor: '#8b949e',
      },
      grid: {
        vertLines: { color: '#21262d' },
        horzLines: { color: '#21262d' },
      },
      rightPriceScale: { borderColor: '#30363d' },
      timeScale: { borderColor: '#30363d', timeVisible: false },
      width: container.clientWidth,
      height: container.clientHeight,
    });

    equitySeries = equityChart.addAreaSeries({
      lineColor: '#388bfd',
      topColor: 'rgba(56,139,253,.3)',
      bottomColor: 'rgba(56,139,253,0)',
      lineWidth: 2,
    });

    window.addEventListener('resize', () => {
      if (equityChart) equityChart.resize(container.clientWidth, container.clientHeight);
    });
  }

  const data = state.equity.map(h => ({ time: h.date, value: h.equity_eur }));
  equitySeries.setData(data);
  equityChart.timeScale().fitContent();
}

// ─── Render: Sektor-Verteilung ────────────────────────────────────────────────

function renderSectorChart() {
  const container = document.getElementById('sector-bars');
  if (!container) return;

  const bySector = {};
  let total = 0;
  state.positions.forEach(p => {
    const val = (p.current_price_eur || p.entry_price_eur) * p.shares;
    bySector[p.sector] = (bySector[p.sector] || 0) + val;
    total += val;
  });

  if (total === 0) {
    container.innerHTML = '<div style="color:var(--text-muted);font-size:.78rem">Keine Positionen</div>';
    return;
  }

  const sorted = Object.entries(bySector).sort((a, b) => b[1] - a[1]);
  container.innerHTML = sorted.map(([sector, val]) => {
    const pct = (val / total * 100).toFixed(1);
    return `
    <div class="sector-bar-item">
      <div class="sector-bar-label">
        <span>${sector}</span>
        <span>${pct}%</span>
      </div>
      <div class="sector-bar-track">
        <div class="sector-bar-fill" style="width:${pct}%"></div>
      </div>
    </div>`;
  }).join('');
}

// ─── Render: Algo-Params ──────────────────────────────────────────────────────

function renderAlgoParams(params) {
  const container = document.getElementById('algo-grid');
  if (!container) return;

  container.innerHTML = params.map(p => `
    <div class="algo-card">
      <div class="algo-symbol">${p.symbol.split('.')[0]} <span style="color:var(--text-muted);font-size:.72rem">${p.name}</span></div>
      <div class="algo-metric"><span>Sharpe</span><span class="${p.sharpe_ratio > 0 ? 'positive' : 'negative'}">${p.sharpe_ratio.toFixed(2)}</span></div>
      <div class="algo-metric"><span>Backtest</span><span class="${p.backtest_return > 0 ? 'positive' : 'negative'}">${p.backtest_return > 0 ? '+' : ''}${p.backtest_return.toFixed(1)}%</span></div>
      <div class="algo-metric"><span>RSI</span><span>${p.rsi_period}</span></div>
      <div class="algo-metric"><span>EMA</span><span>${p.ema_fast}/${p.ema_slow}</span></div>
    </div>`).join('');
}

// ─── Navigation ───────────────────────────────────────────────────────────────

function initNavigation() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      switchTab(tab);
    });
  });
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      if (tab) switchTab(tab);
    });
  });
}

function switchTab(tab) {
  state.activeTab = tab;
  document.querySelectorAll('.tab-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.nav-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.view-panel').forEach(p =>
    p.classList.toggle('active', p.dataset.tab === tab));

  if (tab === 'algorithm') loadAlgoParams();
}

function initPeriodButtons() {
  document.querySelectorAll('.period-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      if (state.selectedSymbol) {
        loadPricesForChart(state.selectedSymbol, parseInt(btn.dataset.days));
      }
    });
  });
}

// ─── Manual Trigger ───────────────────────────────────────────────────────────

async function triggerCycle() {
  const btn = document.getElementById('btn-run-cycle');
  if (btn) { btn.disabled = true; btn.textContent = 'Läuft...'; }
  try {
    const r = await fetch(API + '/trading/run', { method: 'POST' });
    const data = await r.json();
    if (data.actions?.length) {
      data.actions.forEach(a => addLogEntry(a));
      showToast(`${data.actions.length} Aktionen ausgeführt`, 'info');
    } else {
      showToast('Kein Handlungsbedarf', 'info');
    }
    await loadAll();
  } catch(e) {
    showToast('Fehler beim Zyklus: ' + e.message, 'info');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '▶ Zyklus ausführen'; }
  }
}

async function triggerOptimize() {
  const btn = document.getElementById('btn-optimize');
  if (btn) { btn.disabled = true; btn.textContent = 'Optimiert...'; }
  try {
    await fetch(API + '/trading/optimize', { method: 'POST' });
    showToast('Optimierung abgeschlossen', 'info');
    loadAlgoParams();
  } catch(e) {
    showToast('Fehler: ' + e.message, 'info');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '⚙ Optimieren'; }
  }
}

// ─── Log & Toast ──────────────────────────────────────────────────────────────

function addLogEntry(msg) {
  const log = document.getElementById('action-log');
  if (!log) return;
  const cls = msg.includes('KAUF') ? 'log-buy' : msg.includes('VERKAUF') ? 'log-sell' : 'log-info';
  const entry = document.createElement('div');
  entry.className = 'log-entry';
  entry.innerHTML = `<span class="log-time">${new Date().toLocaleTimeString('de-AT')}</span><span class="${cls}">${msg}</span>`;
  log.prepend(entry);
  while (log.children.length > 50) log.removeChild(log.lastChild);
}

function showToast(msg, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = msg;
  container.prepend(toast);
  setTimeout(() => toast.remove(), 5000);
}

// ─── Utils ────────────────────────────────────────────────────────────────────

function fmtEUR(v) {
  if (v == null) return '-';
  return new Intl.NumberFormat('de-AT', { style:'currency', currency:'EUR', minimumFractionDigits:2 }).format(v);
}
function fmtNum(v, digits = 2) {
  if (v == null) return '-';
  return new Intl.NumberFormat('de-AT', { minimumFractionDigits:digits, maximumFractionDigits:4 }).format(v);
}
function fmtPct(v) {
  if (v == null) return '-';
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
}
function fmtDateTime(iso) {
  if (!iso) return '-';
  return new Date(iso).toLocaleString('de-AT', { dateStyle:'short', timeStyle:'short' });
}
function fmtTime(iso) {
  if (!iso) return '-';
  return new Date(iso).toLocaleTimeString('de-AT', { hour:'2-digit', minute:'2-digit' });
}
function setEl(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}
function setStatusDot(online) {
  const dot = document.querySelector('.status-dot');
  if (dot) dot.className = 'status-dot' + (online ? '' : ' loading');
}

function hideLoading() {
  const overlay = document.getElementById('loading-overlay');
  if (overlay) {
    overlay.style.opacity = '0';
    overlay.style.transition = 'opacity .5s';
    setTimeout(() => overlay.remove(), 500);
  }
}
