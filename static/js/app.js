import { api } from './api.js';
import {
  fmtEUR, fmtNum, fmtPct, fmtDateTime, fmtTime,
  setEl, setStatusDot, hideLoading, showDataLoadingBanner,
  hideDataLoadingBanner, showToast, addLogEntry,
} from './ui.js';
import { loadSimulations, initSimulationControls } from './simulations.js';

let socket = null;
let candleChart = null;
let candleSeries = null;
let equityChart = null;
let equitySeries = null;

const state = {
  account: {},
  positions: [],
  watchlist: [],
  trades: [],
  equity: [],
  signals: [],
  strategies: [],
  approvedLiveStrategies: [],
  activeStrategy: null,
  selectedLabStrategyId: null,
  selectedSymbol: null,
  activeTab: 'dashboard',
};

window.switchTab = switchTab;
window.triggerCycle = triggerCycle;
window.triggerOptimize = triggerOptimize;
window.selectSymbol = selectSymbol;

document.addEventListener('DOMContentLoaded', () => {
  initWebSocket();
  initNavigation();
  initPeriodButtons();
  initSimulationControls();
  initStrategyEditor();
  loadAll();
  setTimeout(() => hideLoading(), 5000);
});

function initWebSocket() {
  socket = io();

  socket.on('connect', () => setStatusDot(true));
  socket.on('disconnect', () => setStatusDot(false));
  socket.on('portfolio_update', (data) => {
    state.account = { ...state.account, ...data };
    updateHeaderEquity(data);
    renderPositionsMini(data.positions || []);
  });
  socket.on('trading_actions', (data) => {
    const actions = data.actions || [];
    actions.forEach((msg) => {
      addLogEntry(msg);
      if (msg.includes('KAUF')) showToast(msg, 'buy');
      else if (msg.includes('VERKAUF')) showToast(msg, 'sell');
    });
    loadAll();
  });
  socket.on('status', (data) => {
    const msg = data.message || '';
    const loadingMsg = document.getElementById('loading-msg');
    if (loadingMsg) loadingMsg.textContent = msg;
    showDataLoadingBanner(msg);
    if (msg.includes('bereit')) {
      hideLoading();
      hideDataLoadingBanner();
      loadAll();
    }
  });
}

async function loadAll() {
  await Promise.all([
    loadAccount(),
    loadPositions(),
    loadWatchlist(),
    loadTrades(),
    loadEquity(),
    loadSimulations(),
    loadStrategies(),
  ]);
}

async function loadAccount() {
  try {
    state.account = await api.getAccount();
    renderAccountStats();
    checkLoadingDone();
  } catch (e) {
    console.warn('Account:', e);
  }
}

async function loadPositions() {
  try {
    state.positions = await api.getPositions();
    renderPositionsTable();
    renderPositionsMini(state.positions);
    renderSectorChart();
  } catch (e) {
    console.warn('Positions:', e);
  }
}

async function loadWatchlist() {
  try {
    state.watchlist = await api.getWatchlist();
    renderWatchlist();
    if (!state.selectedSymbol && state.watchlist.length > 0) {
      selectSymbol(state.watchlist[0].symbol);
    }
    checkLoadingDone();
  } catch (e) {
    console.warn('Watchlist:', e);
  }
}

async function loadTrades() {
  try {
    state.trades = await api.getTrades(100);
    renderTradesTable();
    renderTradeFeed();
  } catch (e) {
    console.warn('Trades:', e);
  }
}

async function loadEquity() {
  try {
    state.equity = await api.getEquity(60);
    renderEquityChart();
  } catch (e) {
    console.warn('Equity:', e);
  }
}

async function loadPricesForChart(symbol, days = 90) {
  try {
    const data = await api.getPrices(symbol, days);
    renderCandleChart(data);
  } catch (e) {
    console.warn('Prices:', e);
  }
}

async function loadAlgoParams() {
  try {
    const params = await api.getAlgoParams();
    renderAlgoParams(params);
  } catch (e) {
    console.warn('Algo:', e);
  }
}

async function loadStrategies() {
  try {
    const data = await api.getStrategies();
    state.strategies = data.strategies || [];
    state.approvedLiveStrategies = data.approved_live_strategies || [];
    state.activeStrategy = data.active_strategy || null;
    renderStrategyEditor();
    populateSimulationStrategySelect();
  } catch (e) {
    console.warn('Strategies:', e);
  }
}

function checkLoadingDone() {
  if (state.account && state.account.cash_eur !== undefined) {
    hideLoading();
  }
}

function renderAccountStats() {
  const a = state.account;
  if (!a) return;

  setEl('stat-equity', fmtEUR(a.equity_eur));
  setEl('stat-cash', fmtEUR(a.cash_eur));
  setEl('stat-positions', a.open_positions ?? '-');
  setEl('stat-winrate', a.win_rate ? `${a.win_rate}%` : '-');
  setEl('stat-trades', a.total_trades ?? '-');
  setEl('stat-commission', fmtEUR(a.total_commission));

  const pnl = a.total_return_eur ?? 0;
  const pct = a.total_return_pct ?? 0;
  const pnlEl = document.getElementById('stat-pnl');
  if (pnlEl) {
    pnlEl.textContent = `${fmtEUR(pnl)} (${fmtPct(pct)})`;
    pnlEl.className = `stat-value ${pnl >= 0 ? 'positive' : 'negative'}`;
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

function renderWatchlist() {
  const container = document.getElementById('watchlist-items');
  if (!container) return;

  container.innerHTML = state.watchlist.map((item) => {
    const isActive = item.symbol === state.selectedSymbol;
    const isPos = item.in_portfolio;
    const scoreClass = item.action === 'BUY' ? 'score-buy'
      : item.action === 'SELL' ? 'score-sell' : 'score-hold';
    const changeClass = (item.change_pct || 0) >= 0 ? 'positive' : 'negative';
    return `
      <div class="watchlist-item ${isActive ? 'active' : ''} ${isPos ? 'in-portfolio' : ''}" data-symbol="${item.symbol}">
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

  container.querySelectorAll('[data-symbol]').forEach((item) => {
    item.addEventListener('click', () => selectSymbol(item.dataset.symbol));
  });
}

function selectSymbol(symbol) {
  state.selectedSymbol = symbol;
  const activePeriodBtn = document.querySelector('.period-btn.active');
  const days = activePeriodBtn ? parseInt(activePeriodBtn.dataset.days, 10) : 90;
  loadPricesForChart(symbol, days);
  renderWatchlist();
}

function renderCandleChart(data) {
  const container = document.getElementById('chart-container');
  if (!container) return;

  const prices = data.prices || [];
  if (prices.length === 0) {
    renderChartPlaceholder('Keine Preisdaten für diesen Zeitraum vorhanden.');
    return;
  }

  const last = prices[prices.length - 1];
  const prev = prices[prices.length - 2];
  const change = prev ? ((last.close - prev.close) / prev.close * 100) : 0;
  setEl('chart-symbol-text', data.symbol);
  setEl('chart-price-text', `${fmtNum(last.close_eur)} €`);
  const chEl = document.getElementById('chart-change-text');
  if (chEl) {
    chEl.textContent = `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`;
    chEl.className = `chart-change ${change >= 0 ? 'positive' : 'negative'}`;
  }

  const chartData = prices
    .map((p) => {
      const [year, month, day] = String(p.date || '').split('-').map(Number);
      const open = Number(p.open);
      const high = Number(p.high);
      const low = Number(p.low);
      const close = Number(p.close);

      if (!year || !month || !day) return null;
      if ([open, high, low, close].some((value) => Number.isNaN(value))) return null;

      return {
        time: { year, month, day },
        open,
        high,
        low,
        close,
      };
    })
    .filter(Boolean);

  if (chartData.length === 0) {
    renderChartPlaceholder('Chartdaten konnten nicht aufbereitet werden.');
    return;
  }

  if (!candleChart) {
    container.innerHTML = '';
    candleChart = LightweightCharts.createChart(container, {
      layout: { background: { type: 'solid', color: '#1c2128' }, textColor: '#8b949e' },
      grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#30363d' },
      timeScale: { borderColor: '#30363d', timeVisible: true },
      width: container.clientWidth || 600,
      height: container.clientHeight || 340,
    });

    candleSeries = candleChart.addCandlestickSeries({
      upColor: '#3fb950', downColor: '#f85149',
      borderUpColor: '#3fb950', borderDownColor: '#f85149',
      wickUpColor: '#3fb950', wickDownColor: '#f85149',
    });

    window.addEventListener('resize', () => {
      if (candleChart) candleChart.resize(container.clientWidth || 600, container.clientHeight || 340);
    });
  }

  try {
    candleSeries.setData(chartData);
    candleChart.timeScale().fitContent();
  } catch (error) {
    console.error('Candle chart render failed:', error, chartData.slice(0, 3));
    renderChartPlaceholder(`Chartfehler: ${error.message}`);
  }
}

function renderChartPlaceholder(message) {
  const container = document.getElementById('chart-container');
  if (!container) return;
  container.innerHTML = `<div class="empty-state" style="height:100%;display:flex;align-items:center;justify-content:center;color:var(--text-muted)">${message}</div>`;
  candleChart = null;
  candleSeries = null;
}

function renderPositionsTable() {
  const rowsHtml = state.positions.length === 0
    ? `<tr><td colspan="9" class="empty-state" style="padding:30px;text-align:center;color:var(--text-muted)">Keine offenen Positionen</td></tr>`
    : state.positions.map((p) => {
      const pnlClass = p.unrealized_pnl_eur >= 0 ? 'positive' : 'negative';
      return `
        <tr data-symbol="${p.symbol}" style="cursor:pointer">
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

  ['positions-tbody', 'positions-tbody-full'].forEach((id) => {
    const tbody = document.getElementById(id);
    if (tbody) tbody.innerHTML = rowsHtml;
  });

  document.querySelectorAll('[data-symbol]').forEach((row) => {
    if (row.closest('#watchlist-items')) return;
    row.addEventListener('click', () => selectSymbol(row.dataset.symbol));
  });
}

function renderPositionsMini(positions) {
  const container = document.getElementById('positions-mini');
  if (!container) return;

  if (!positions || positions.length === 0) {
    container.innerHTML = '<div class="empty-state" style="padding:16px;text-align:center;color:var(--text-muted);font-size:.8rem">Keine offenen Positionen</div>';
    return;
  }

  container.innerHTML = positions.map((p) => {
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

function renderTradesTable() {
  const tbody = document.getElementById('trades-tbody');
  if (!tbody) return;

  if (state.trades.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" style="padding:30px;text-align:center;color:var(--text-muted)">Noch keine Trades</td></tr>';
    return;
  }

  tbody.innerHTML = state.trades.map((t) => {
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

  container.innerHTML = recent.map((t) => {
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

function renderEquityChart() {
  const container = document.getElementById('equity-chart');
  if (!container || state.equity.length === 0) return;

  if (!equityChart) {
    equityChart = LightweightCharts.createChart(container, {
      layout: { background: { type: 'solid', color: '#161b22' }, textColor: '#8b949e' },
      grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
      rightPriceScale: { borderColor: '#30363d' },
      timeScale: { borderColor: '#30363d', timeVisible: false },
      width: container.clientWidth,
      height: container.clientHeight,
    });

    equitySeries = equityChart.addAreaSeries({
      lineColor: '#388bfd', topColor: 'rgba(56,139,253,.3)', bottomColor: 'rgba(56,139,253,0)', lineWidth: 2,
    });

    window.addEventListener('resize', () => {
      if (equityChart) equityChart.resize(container.clientWidth, container.clientHeight);
    });
  }

  equitySeries.setData(state.equity.map((h) => ({ time: h.date, value: h.equity_eur })));
  equityChart.timeScale().fitContent();
}

function renderSectorChart() {
  const html = (() => {
    const bySector = {};
    let total = 0;
    state.positions.forEach((p) => {
      const val = (p.current_price_eur || p.entry_price_eur) * p.shares;
      bySector[p.sector] = (bySector[p.sector] || 0) + val;
      total += val;
    });

    if (total === 0) {
      return '<div style="color:var(--text-muted);font-size:.78rem">Keine Positionen</div>';
    }

    return Object.entries(bySector)
      .sort((a, b) => b[1] - a[1])
      .map(([sector, val]) => {
        const pct = (val / total * 100).toFixed(1);
        return `
          <div class="sector-bar-item">
            <div class="sector-bar-label"><span>${sector}</span><span>${pct}%</span></div>
            <div class="sector-bar-track"><div class="sector-bar-fill" style="width:${pct}%"></div></div>
          </div>`;
      }).join('');
  })();

  const main = document.getElementById('sector-bars');
  const side = document.getElementById('sector-bars-panel');
  if (main) main.innerHTML = html;
  if (side) side.innerHTML = html;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function renderAlgoParams(params) {
  const container = document.getElementById('algo-grid');
  if (!container) return;

  container.innerHTML = params.map((p) => `
    <div class="algo-card">
      <div class="algo-symbol">${p.symbol.split('.')[0]} <span style="color:var(--text-muted);font-size:.72rem">${p.name}</span></div>
      <div class="algo-metric"><span>Sharpe</span><span class="${p.sharpe_ratio > 0 ? 'positive' : 'negative'}">${p.sharpe_ratio.toFixed(2)}</span></div>
      <div class="algo-metric"><span>Backtest</span><span class="${p.backtest_return > 0 ? 'positive' : 'negative'}">${p.backtest_return > 0 ? '+' : ''}${p.backtest_return.toFixed(1)}%</span></div>
      <div class="algo-metric"><span>RSI</span><span>${p.rsi_period}</span></div>
      <div class="algo-metric"><span>EMA</span><span>${p.ema_fast}/${p.ema_slow}</span></div>
    </div>`).join('');
}

function initNavigation() {
  document.querySelectorAll('.tab-btn, .nav-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      if (tab) switchTab(tab);
    });
  });
}

function initStrategyEditor() {
  const liveSelect = document.getElementById('strategy-select');
  const labSelect = document.getElementById('strategy-lab-select');
  const saveBtn = document.getElementById('btn-strategy-save');

  if (liveSelect) {
    liveSelect.addEventListener('change', async () => {
      const strategyId = liveSelect.value;
      try {
        await api.setActiveStrategy(strategyId);
        state.activeStrategy = strategyId;
        renderStrategyEditor();
        showToast('Aktive Live-Strategie gewechselt', 'info');
      } catch (error) {
        showToast(`Live-Strategie konnte nicht aktiviert werden: ${error.message}`, 'info');
      }
    });
  }

  if (labSelect) {
    labSelect.addEventListener('change', () => {
      state.selectedLabStrategyId = labSelect.value;
      renderStrategyLabEditor();
    });
  }

  if (saveBtn) {
    saveBtn.addEventListener('click', saveStrategyFromEditor);
  }
}

function renderStrategyEditor() {
  const select = document.getElementById('strategy-select');
  const approvedList = document.getElementById('approved-strategies-list');
  if (select) {
    const approvedIds = state.approvedLiveStrategies || [];
    const approvedStrategies = (state.strategies || []).filter((strategy) => approvedIds.includes(strategy.id));

    if (!approvedStrategies.length) {
      select.innerHTML = '<option value="">Keine freigegebenen Strategien</option>';
      select.disabled = true;
    } else {
      select.disabled = false;
      select.innerHTML = approvedStrategies.map((strategy) => `
        <option value="${escapeHtml(strategy.id)}" ${strategy.id === state.activeStrategy ? 'selected' : ''}>${escapeHtml(strategy.name || strategy.id)}</option>
      `).join('');
    }
  }

  if (approvedList) {
    const approvedIds = state.approvedLiveStrategies || [];
    const approvedStrategies = (state.strategies || []).filter((strategy) => approvedIds.includes(strategy.id));
    approvedList.innerHTML = approvedStrategies.length
      ? approvedStrategies.map((strategy) => `<div class="algo-card"><div class="algo-symbol">${escapeHtml(strategy.name || strategy.id)}</div><div class="strategy-editor-help">${escapeHtml(strategy.description || '')}</div><div class="algo-metric"><span>ID</span><span>${escapeHtml(strategy.id)}</span></div></div>`).join('')
      : '<div class="empty-state" style="padding:20px;color:var(--text-muted)">Noch keine Strategien für Live freigegeben.</div>';
  }

  renderStrategyLabEditor();
}

function renderStrategyLabEditor() {
  const select = document.getElementById('strategy-lab-select');
  const nameInput = document.getElementById('strategy-name');
  const descriptionInput = document.getElementById('strategy-description');
  const jsonInput = document.getElementById('strategy-json');
  if (!select || !nameInput || !descriptionInput || !jsonInput) return;

  const previousValue = state.selectedLabStrategyId || select.value;
  select.innerHTML = (state.strategies || []).map((strategy) => `
    <option value="${escapeHtml(strategy.id)}">${escapeHtml(strategy.name || strategy.id)}</option>
  `).join('');

  const selectedId = previousValue || (state.strategies[0] && state.strategies[0].id);
  const current = (state.strategies || []).find((strategy) => strategy.id === selectedId) || state.strategies[0];
  if (!current) return;

  state.selectedLabStrategyId = current.id;
  select.value = current.id;
  nameInput.value = current.name || '';
  descriptionInput.value = current.description || '';
  jsonInput.value = JSON.stringify({
    id: current.id,
    enabled: current.enabled,
    mode: current.mode,
    params: current.params || {},
  }, null, 2);
}

async function saveStrategyFromEditor() {
  const select = document.getElementById('strategy-lab-select');
  const nameInput = document.getElementById('strategy-name');
  const descriptionInput = document.getElementById('strategy-description');
  const jsonInput = document.getElementById('strategy-json');
  if (!select || !nameInput || !descriptionInput || !jsonInput) return;

  try {
    const parsed = JSON.parse(jsonInput.value);
    const strategyId = parsed.id || select.value;
    const payload = {
      ...parsed,
      id: strategyId,
      name: nameInput.value || strategyId,
      description: descriptionInput.value || '',
    };
    await api.updateStrategy(strategyId, payload);
    await loadStrategies();
    showToast('Teststrategie gespeichert', 'info');
  } catch (error) {
    showToast(`Strategie konnte nicht gespeichert werden: ${error.message}`, 'info');
  }
}

function populateSimulationStrategySelect() {
  const select = document.getElementById('simulation-strategy-select');
  if (!select) return;

  select.innerHTML = (state.strategies || []).map((strategy) => `
    <option value="${escapeHtml(strategy.id)}" ${strategy.id === (state.activeStrategy || 'default_v1') ? 'selected' : ''}>${escapeHtml(strategy.name || strategy.id)}</option>
  `).join('');
}

function switchTab(tab) {
  state.activeTab = tab;
  document.querySelectorAll('.tab-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.nav-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.view-panel').forEach((p) => p.classList.toggle('active', p.dataset.tab === tab));

  if (tab === 'algorithm') loadAlgoParams();
  if (tab === 'simulations') loadSimulations();
  if (tab === 'strategies') loadStrategies();
}

function initPeriodButtons() {
  document.querySelectorAll('.period-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.period-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      if (state.selectedSymbol) {
        loadPricesForChart(state.selectedSymbol, parseInt(btn.dataset.days, 10));
      }
    });
  });
}

async function triggerCycle() {
  const btn = document.getElementById('btn-run-cycle');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Läuft...';
  }
  try {
    const data = await api.runTradingCycle();
    if (data.actions?.length) {
      data.actions.forEach((a) => addLogEntry(a));
      showToast(`${data.actions.length} Aktionen ausgeführt`, 'info');
    } else {
      showToast('Kein Handlungsbedarf', 'info');
    }
    await loadAll();
  } catch (e) {
    showToast(`Fehler beim Zyklus: ${e.message}`, 'info');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '▶ Zyklus ausführen';
    }
  }
}

async function triggerOptimize() {
  const btn = document.getElementById('btn-optimize');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Optimiert...';
  }
  try {
    await api.runOptimization();
    showToast('Optimierung abgeschlossen', 'info');
    loadAlgoParams();
  } catch (e) {
    showToast(`Fehler: ${e.message}`, 'info');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '⚙ Neu optimieren';
    }
  }
}
