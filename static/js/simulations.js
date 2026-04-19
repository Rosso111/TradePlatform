import { api } from './api.js';
import { fmtEUR, fmtPct, fmtDateTime, showToast } from './ui.js';

let simulationChart = null;
let simulationEquitySeries = null;
let simulationBenchmarkSeries = null;
let simulationRefreshTimer = null;
let simulationChartPointer = { x: 0, y: 0, clientX: 0, clientY: 0 };
let currentSimulationChartEvents = [];
let currentSimulationRuns = [];
let currentSimulationRunId = null;
const SIMULATION_REFRESH_MS = 3000;

export async function loadSimulations() {
  try {
    const runs = await api.getSimulations();
    currentSimulationRuns = runs;
    renderSimulationRuns(runs);
    if (runs.length > 0) {
      const targetRunId = runs.some((run) => Number(run.id) === Number(currentSimulationRunId))
        ? currentSimulationRunId
        : runs[0].id;
      await loadSimulationDetail(targetRunId);
    } else {
      currentSimulationRunId = null;
      renderSimulationEmpty();
    }
    updateSimulationAutoRefresh();
  } catch (error) {
    console.warn('Simulations:', error);
    renderSimulationError(error.message);
  }
}

export function initSimulationControls() {
  const form = document.getElementById('simulation-form');
  if (!form) return;

  const startDateInput = form.querySelector('input[name="start_date"]');
  const endDateInput = form.querySelector('input[name="end_date"]');

  if (startDateInput && endDateInput) {
    startDateInput.addEventListener('change', () => {
      const start = new Date(startDateInput.value);
      if (Number.isNaN(start.getTime())) return;
      start.setDate(start.getDate() + 1);
      const yyyy = start.getFullYear();
      const mm = String(start.getMonth() + 1).padStart(2, '0');
      const dd = String(start.getDate()).padStart(2, '0');
      endDateInput.value = `${yyyy}-${mm}-${dd}`;
    });
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const submitBtn = form.querySelector('button[type="submit"]');
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Simulation läuft...';
    }

    try {
      const formData = new FormData(form);
      const payload = {
        name: formData.get('name') || undefined,
        start_date: formData.get('start_date'),
        end_date: formData.get('end_date'),
        initial_capital_eur: Number(formData.get('initial_capital_eur') || 10000),
        strategy_id: formData.get('strategy_id') || undefined,
        auto_start: true,
      };
      const result = await api.createSimulation(payload);
      showToast('Simulation erstellt', 'info');
      await loadSimulations();
      if (result?.run?.id) {
        await loadSimulationDetail(result.run.id);
      }
    } catch (error) {
      showToast(`Simulation fehlgeschlagen: ${error.message}`, 'info');
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Replay starten';
      }
    }
  });
}

async function loadSimulationDetail(runId) {
  currentSimulationRunId = Number(runId);

  try {
    const [run, metrics, equity, benchmark, trades, decisions] = await Promise.all([
      api.getSimulation(runId),
      api.getSimulationMetrics(runId),
      api.getSimulationEquity(runId),
      api.getSimulationBenchmark(runId),
      api.getSimulationTrades(runId),
      api.getSimulationDecisions(runId),
    ]);

    const chartEvents = buildSimulationChartEvents(trades || [], decisions || []);
    currentSimulationChartEvents = chartEvents;
    renderSimulationSummary(run, metrics);
    renderSimulationChart(equity, benchmark?.points || [], chartEvents);
    renderSimulationChartEvents(chartEvents);
    highlightSelectedRun(runId);
    updateSimulationAutoRefresh();
  } catch (error) {
    console.warn('Simulation detail:', error);
    renderSimulationError(error.message);
  }
}

function renderSimulationRuns(runs) {
  const container = document.getElementById('simulation-runs');
  if (!container) return;

  if (!runs.length) {
    container.innerHTML = '<div class="empty-state" style="padding:18px;color:var(--text-muted)">Noch keine Simulationsläufe</div>';
    return;
  }

  container.innerHTML = runs.map((run) => {
      const isRunning = String(run.status).toUpperCase() === 'RUNNING';
      const deleteTitle = isRunning ? 'Laufende Simulationen bitte erst abbrechen' : 'Simulation löschen';
      return `
      <div class="simulation-run-row">
        <button class="simulation-run-item" data-run-id="${run.id}">
          <div class="simulation-run-head">
            <strong>${escapeHtml(run.name || `Run #${run.id}`)}</strong>
            <span class="simulation-status ${run.status}">${escapeHtml(run.status)}</span>
          </div>
          <div class="simulation-run-meta">${escapeHtml(run.start_date || '-')} → ${escapeHtml(run.end_date || '-')}</div>
          <div class="simulation-run-metrics">
            <span>${fmtEUR(run.final_equity_eur)}</span>
            <span class="${(run.total_return_pct || 0) >= 0 ? 'positive' : 'negative'}">${fmtPct(run.total_return_pct || 0)}</span>
          </div>
        </button>
        ${isRunning ? `<button class="btn btn-secondary simulation-cancel-btn" data-cancel-run-id="${run.id}" title="Laufende Simulation abbrechen">Abbrechen</button>` : ''}
        <button class="btn btn-secondary simulation-delete-btn" data-delete-run-id="${run.id}" title="${deleteTitle}" ${isRunning ? 'disabled' : ''}>Löschen</button>
      </div>
    `;
  }).join('');

  container.querySelectorAll('[data-run-id]').forEach((btn) => {
    btn.addEventListener('click', () => loadSimulationDetail(Number(btn.dataset.runId)));
  });

  container.querySelectorAll('[data-cancel-run-id]').forEach((btn) => {
    btn.addEventListener('click', async (event) => {
      event.preventDefault();
      event.stopPropagation();

      const runId = Number(btn.dataset.cancelRunId);
      const run = currentSimulationRuns.find((item) => Number(item.id) === runId);
      const runLabel = run?.name || `Run #${runId}`;
      const confirmed = window.confirm(`Laufende Simulation "${runLabel}" wirklich abbrechen?`);
      if (!confirmed) return;

      btn.disabled = true;

      try {
        await api.cancelSimulation(runId);
        showToast('Abbruch angefordert', 'info');
        await loadSimulations();
      } catch (error) {
        showToast(`Simulation konnte nicht abgebrochen werden: ${error.message}`, 'info');
        btn.disabled = false;
      }
    });
  });

  container.querySelectorAll('[data-delete-run-id]').forEach((btn) => {
    btn.addEventListener('click', async (event) => {
      event.preventDefault();
      event.stopPropagation();

      const runId = Number(btn.dataset.deleteRunId);
      const run = currentSimulationRuns.find((item) => Number(item.id) === runId);
      const runLabel = run?.name || `Run #${runId}`;
      const confirmed = window.confirm(`Simulation "${runLabel}" wirklich löschen?`);
      if (!confirmed) return;

      btn.disabled = true;

      try {
        await api.deleteSimulation(runId);

        showToast('Simulation gelöscht', 'info');
        if (Number(currentSimulationRunId) === runId) {
          currentSimulationRunId = null;
        }
        await loadSimulations();
      } catch (error) {
        showToast(`Simulation konnte nicht gelöscht werden: ${error.message}`, 'info');
        btn.disabled = false;
      }
    });
  });
}

function hasRunningSimulations() {
  return currentSimulationRuns.some((run) => String(run.status).toUpperCase() === 'RUNNING');
}

function stopSimulationAutoRefresh() {
  if (simulationRefreshTimer) {
    clearInterval(simulationRefreshTimer);
    simulationRefreshTimer = null;
  }
}

function isSimulationsViewActive() {
  const panel = document.querySelector('.view-panel[data-tab="simulations"]');
  if (!panel) return true;
  return panel.classList.contains('active');
}

async function refreshSimulationState() {
  const runs = await api.getSimulations();
  currentSimulationRuns = runs;
  renderSimulationRuns(runs);

  if (!runs.length) {
    currentSimulationRunId = null;
    renderSimulationEmpty();
    stopSimulationAutoRefresh();
    return;
  }

  const selectedRunExists = runs.some((run) => Number(run.id) === Number(currentSimulationRunId));
  const targetRunId = selectedRunExists ? currentSimulationRunId : runs[0].id;
  await loadSimulationDetail(targetRunId);

  if (!hasRunningSimulations()) {
    stopSimulationAutoRefresh();
  }
}

function updateSimulationAutoRefresh() {
  if (!isSimulationsViewActive() || !hasRunningSimulations()) {
    stopSimulationAutoRefresh();
    return;
  }

  if (simulationRefreshTimer) {
    return;
  }

  simulationRefreshTimer = setInterval(async () => {
    try {
      await refreshSimulationState();
    } catch (error) {
      console.warn('Simulation auto-refresh:', error);
    }
  }, SIMULATION_REFRESH_MS);
}

function highlightSelectedRun(runId) {
  document.querySelectorAll('.simulation-run-item').forEach((item) => {
    item.classList.toggle('active', Number(item.dataset.runId) === Number(runId));
  });
}

function renderSimulationSummary(run, metrics) {
  const summary = document.getElementById('simulation-summary');
  if (!summary) return;

  const progress = run.progress || {};
  const progressPct = Number(progress.progress_pct || 0);
  const isRunning = String(run.status || '').toUpperCase() === 'RUNNING';
  const isCancelRequested = String(run.status || '').toUpperCase() === 'CANCEL_REQUESTED';
  const progressLabel = progress.total_days
    ? `${progress.processed_days || 0} / ${progress.total_days} Tage`
    : '–';
  const currentStepLabel = progress.latest_snapshot_date || progress.current_date || '–';

  summary.innerHTML = `
    <div class="stats-bar">
      <div class="stat-card"><div class="stat-label">Run</div><div class="stat-value">${escapeHtml(run.name || `Run #${run.id}`)}</div><div class="stat-sub">${escapeHtml(run.status)}</div></div>
      <div class="stat-card"><div class="stat-label">Fortschritt</div><div class="stat-value">${progressPct.toFixed(1)}%</div><div class="stat-sub">${escapeHtml(progressLabel)}</div></div>
      <div class="stat-card"><div class="stat-label">Aktueller Stand</div><div class="stat-value">${escapeHtml(currentStepLabel)}</div><div class="stat-sub">${run.latest_snapshot ? `Equity: ${fmtEUR(run.latest_snapshot.equity_eur)}` : 'Noch kein Snapshot'}</div></div>
      <div class="stat-card"><div class="stat-label">Return</div><div class="stat-value ${(metrics.total_return_pct || 0) >= 0 ? 'positive' : 'negative'}">${fmtPct(metrics.total_return_pct || 0)}</div><div class="stat-sub">${isRunning || isCancelRequested ? `Live Equity: ${fmtEUR(metrics.live?.equity_eur || 0)}` : `Final: ${fmtEUR(metrics.final_equity_eur)}`}</div></div>
      <div class="stat-card"><div class="stat-label">Benchmark</div><div class="stat-value ${(metrics.benchmark_return_pct || 0) >= 0 ? 'positive' : 'negative'}">${fmtPct(metrics.benchmark_return_pct || 0)}</div><div class="stat-sub">Outperformance: ${metrics.outperformance_pct == null ? '-' : fmtPct(metrics.outperformance_pct)}</div></div>
      <div class="stat-card"><div class="stat-label">Trades</div><div class="stat-value">${metrics.total_trades || 0}</div><div class="stat-sub">Win Rate: ${fmtPct(metrics.win_rate || 0)}</div></div>
    </div>
    <div class="card" style="margin-top:16px">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:12px">
        <div>
          <div class="card-title" style="margin-bottom:6px">Laufstatus</div>
          <div class="simulation-progress-meta">${isCancelRequested ? 'Abbruch angefordert – Simulation stoppt beim nächsten sicheren Schritt.' : isRunning ? 'Simulation läuft.' : 'Simulation ist nicht aktiv.'}</div>
          <div class="simulation-progress-meta" style="margin-top:6px">Strategie: ${escapeHtml(run.strategy_name || run.strategy_id || 'default_v1')}</div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          ${(!isRunning && !isCancelRequested && (run.strategy_name || run.strategy_id)) ? `<button class="btn btn-ghost" id="simulation-approve-strategy-btn">Strategie für Live freigeben</button>` : ''}
          ${(isRunning || isCancelRequested) ? `<button class="btn btn-danger" id="simulation-stop-btn" ${isCancelRequested ? 'disabled' : ''}>${isCancelRequested ? 'Stoppen angefordert…' : 'Simulation stoppen'}</button>` : ''}
        </div>
      </div>
      <div class="simulation-progress-bar-wrap">
        <div class="simulation-progress-bar" style="width:${Math.max(0, Math.min(progressPct, 100))}%"></div>
      </div>
      <div class="simulation-progress-meta" style="margin-top:8px">Verarbeitet: ${escapeHtml(progressLabel)} · Letzter verarbeiteter Tag: ${escapeHtml(currentStepLabel)}</div>
      <div class="simulation-progress-meta" style="margin-top:6px">Cash: ${fmtEUR(metrics.live?.cash_eur || 0)} · Positionswert: ${fmtEUR(metrics.live?.positions_value_eur || 0)} · Offene Positionen: ${metrics.live?.open_positions || 0}</div>
    </div>
    <div class="card" style="margin-top:16px">
      <div class="card-title">Letzte Entscheidungen</div>
      <div class="simulation-decisions-mini">
        ${(run.latest_decisions || []).length ? (run.latest_decisions || []).map((item) => `
          <div class="simulation-decision-row">
            <span>${escapeHtml(item.sim_date || '-')}</span>
            <strong>${escapeHtml(item.symbol || '-')}</strong>
            <span>${escapeHtml(item.action || '-')}</span>
            <span>${item.final_score == null ? '-' : Number(item.final_score).toFixed(1)}</span>
            <span>${escapeHtml(item.reason_summary || '-')}</span>
          </div>
        `).join('') : '<div style="color:var(--text-muted)">Noch keine Entscheidungen gespeichert.</div>'}
      </div>
      <div style="margin-top:12px;color:var(--text-muted);font-size:.75rem">Letzter Snapshot: ${run.latest_snapshot ? `${escapeHtml(run.latest_snapshot.sim_date)} · Equity ${fmtEUR(run.latest_snapshot.equity_eur)}` : '–'}</div>
    </div>
  `;

  const approveBtn = document.getElementById('simulation-approve-strategy-btn');
  if (approveBtn) {
    approveBtn.addEventListener('click', async () => {
      const strategyId = run.strategy_name || run.strategy_id;
      if (!strategyId) {
        showToast('Keine Strategie an diesem Run hinterlegt', 'info');
        return;
      }
      try {
        await api.approveStrategyForLive(strategyId);
        showToast('Strategie für Live freigegeben', 'info');
      } catch (error) {
        showToast(`Freigabe fehlgeschlagen: ${error.message}`, 'info');
      }
    });
  }

  const stopBtn = document.getElementById('simulation-stop-btn');
  if (stopBtn) {
    stopBtn.addEventListener('click', async () => {
      const runLabel = run?.name || `Run #${run.id}`;
      const confirmed = window.confirm(`Laufende Simulation "${runLabel}" wirklich abbrechen?`);
      if (!confirmed) return;

      stopBtn.disabled = true;
      stopBtn.textContent = 'Stoppen angefordert…';

      try {
        await api.cancelSimulation(run.id);
        showToast('Abbruch angefordert', 'info');
        await loadSimulations();
      } catch (error) {
        showToast(`Simulation konnte nicht abgebrochen werden: ${error.message}`, 'info');
        stopBtn.disabled = false;
        stopBtn.textContent = 'Simulation stoppen';
      }
    });
  }
}

function resetSimulationChart() {
  if (simulationChart) {
    simulationChart.remove();
  }
  simulationChart = null;
  simulationEquitySeries = null;
  simulationBenchmarkSeries = null;
  hideSimulationChartTooltip();
}

function buildSimulationChartEvents(trades, decisions) {
  const decisionMap = new Map((decisions || []).map((item) => [`${item.sim_date}|${item.symbol}|${item.action}`, item]));
  return (trades || []).map((trade) => {
    const decision = decisionMap.get(`${trade.sim_date}|${trade.symbol}|${trade.action}`);
    return {
      ...trade,
      decision,
      reason: decision?.reason_summary || trade.reason || '-',
      score: decision?.final_score,
    };
  });
}

function showSimulationChartTooltip(contentHtml, left, top) {
  const tooltip = document.getElementById('simulation-chart-tooltip');
  if (!tooltip) return;

  const viewportX = simulationChartPointer.clientX || left;
  const viewportY = simulationChartPointer.clientY || top;
  const spacingX = 12;
  const spacingY = 12;

  tooltip.innerHTML = contentHtml;
  tooltip.style.display = 'block';
  tooltip.style.left = `${viewportX + spacingX}px`;
  tooltip.style.top = `${viewportY - spacingY}px`;
  tooltip.style.transform = 'translateY(-100%)';
}

function hideSimulationChartTooltip() {
  const tooltip = document.getElementById('simulation-chart-tooltip');
  if (!tooltip) return;
  tooltip.style.display = 'none';
  tooltip.innerHTML = '';
}

function renderSimulationChart(equityRows, benchmarkRows, chartEvents = []) {
  const container = document.getElementById('simulation-chart');
  if (!container) return;
  container.style.position = 'relative';
  container.onmousemove = (event) => {
    const rect = container.getBoundingClientRect();
    simulationChartPointer = {
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
      clientX: event.clientX,
      clientY: event.clientY,
    };
  };
  container.onmouseleave = () => {
    hideSimulationChartTooltip();
  };

  const selectedRun = currentSimulationRuns.find((run) => Number(run.id) === Number(currentSimulationRunId));
  const isSelectedRunRunning = String(selectedRun?.status || '').toUpperCase() === 'RUNNING';

  if (!equityRows.length) {
    if (isSelectedRunRunning) {
      resetSimulationChart();
      hideSimulationChartTooltip();
      container.innerHTML = '<div class="empty-state" style="padding:30px;color:var(--text-muted)">Simulation läuft – erste Chartdaten werden geladen …</div>';
      return;
    }

    resetSimulationChart();
    hideSimulationChartTooltip();
    container.innerHTML = '<div class="empty-state" style="padding:30px;color:var(--text-muted)">Noch keine Simulationsdaten für den Chart</div>';
    return;
  }

  if (container.querySelector('.empty-state')) {
    container.innerHTML = '';
  }

  const width = container.clientWidth || container.offsetWidth || 600;
  const height = container.clientHeight || container.offsetHeight || 280;

  try {
    if (!simulationChart || !simulationEquitySeries || !simulationBenchmarkSeries) {
      resetSimulationChart();
      simulationChart = LightweightCharts.createChart(container, {
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
        width,
        height,
      });

      simulationEquitySeries = simulationChart.addAreaSeries({
        lineColor: '#3fb950',
        topColor: 'rgba(63,185,80,.30)',
        bottomColor: 'rgba(63,185,80,0)',
        lineWidth: 2,
      });

      simulationBenchmarkSeries = simulationChart.addLineSeries({
        color: '#58a6ff',
        lineWidth: 2,
        lineStyle: 2,
      });
    } else {
      simulationChart.resize(width, height);
    }

    const equityData = equityRows.map((row) => ({
      time: row.sim_date,
      value: Number(row.equity_eur),
    })).filter((row) => !Number.isNaN(row.value));

    simulationEquitySeries.setData(equityData);

    simulationBenchmarkSeries.setData((benchmarkRows || []).map((row) => ({
      time: row.sim_date,
      value: Number(row.value_eur),
    })).filter((row) => !Number.isNaN(row.value)));

    const equityMap = new Map(equityData.map((row) => [row.time, row.value]));
    const markers = (chartEvents || []).map((event) => ({
      time: event.sim_date,
      position: String(event.action).toUpperCase() === 'BUY' ? 'belowBar' : 'aboveBar',
      color: String(event.action).toUpperCase() === 'BUY' ? '#3fb950' : '#ff7b72',
      shape: String(event.action).toUpperCase() === 'BUY' ? 'arrowUp' : 'arrowDown',
      text: '',
      size: 0.35,
    })).filter((marker) => equityMap.has(marker.time));

    const eventsByDate = new Map();
    (chartEvents || []).forEach((event) => {
      const key = event.sim_date;
      if (!eventsByDate.has(key)) {
        eventsByDate.set(key, []);
      }
      eventsByDate.get(key).push(event);
    });

    if (simulationEquitySeries.setMarkers) {
      simulationEquitySeries.setMarkers(markers);
    }

    if (simulationChart.subscribeCrosshairMove) {
      simulationChart.subscribeCrosshairMove((param) => {
        if (!param || !param.time || !param.point) {
          hideSimulationChartTooltip();
          return;
        }

        const hoveredEvents = eventsByDate.get(param.time);
        if (!hoveredEvents || !hoveredEvents.length) {
          hideSimulationChartTooltip();
          return;
        }

        const contentHtml = hoveredEvents.map((event) => `
          <div style="margin-bottom:8px">
            <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:4px">
              <strong class="${String(event.action).toUpperCase() === 'BUY' ? 'positive' : 'negative'}">${escapeHtml(event.action || '-')}</strong>
              <span>${escapeHtml(event.symbol || '-')}</span>
              <span>${escapeHtml(event.sim_date || '-')}</span>
            </div>
            <div>Preis: ${fmtEUR(event.price_eur || event.price || 0)} · Stück: ${Number(event.shares || 0).toFixed(4)}</div>
            <div>Betrag: ${fmtEUR(event.total_eur || 0)}${event.score == null ? '' : ` · Score: ${Number(event.score).toFixed(1)}`}</div>
            <div style="color:var(--text-primary)">Grund: ${escapeHtml(event.reason || '-')}</div>
          </div>
        `).join('');

        showSimulationChartTooltip(
          contentHtml,
          simulationChartPointer.clientX || param.point.x,
          simulationChartPointer.clientY || param.point.y,
        );
      });
    }

    simulationChart.timeScale().fitContent();
  } catch (error) {
    console.warn('Simulation chart render failed:', error);
    resetSimulationChart();
    hideSimulationChartTooltip();
    container.innerHTML = '<div class="empty-state" style="padding:30px;color:var(--text-muted)">Chart konnte gerade nicht gezeichnet werden. Bitte kurz neu laden.</div>';
  }
}

function renderSimulationChartEvents(events) {
  const list = document.getElementById('simulation-chart-events');
  const inspector = document.getElementById('simulation-chart-inspector');
  if (!list || !inspector) return;

  if (!events.length) {
    list.innerHTML = '<div class="empty-state" style="padding:12px;color:var(--text-muted)">Noch keine Kauf-/Verkaufsereignisse.</div>';
    inspector.innerHTML = '<div style="color:var(--text-muted)">Wähle später einen Kauf oder Verkauf aus, um die Begründung zu sehen.</div>';
    return;
  }

  list.innerHTML = events.map((event, index) => `
    <button class="simulation-chart-event ${index === 0 ? 'active' : ''}" data-event-index="${index}">
      <span>${escapeHtml(event.sim_date || '-')}</span>
      <strong class="${String(event.action).toUpperCase() === 'BUY' ? 'positive' : 'negative'}">${escapeHtml(event.action || '-')}</strong>
      <span>${escapeHtml(event.symbol || '-')}</span>
      <span>${escapeHtml(event.reason || '-')}</span>
    </button>
  `).join('');

  const renderInspector = (event) => {
    inspector.innerHTML = `
      <div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:8px">
        <strong>${escapeHtml(event.action || '-')} ${escapeHtml(event.symbol || '-')}</strong>
        <span>Datum: ${escapeHtml(event.sim_date || '-')}</span>
        <span>Preis: ${fmtEUR(event.price_eur || event.price || 0)}</span>
        <span>Score: ${event.score == null ? '-' : Number(event.score).toFixed(1)}</span>
      </div>
      <div style="margin-bottom:6px"><strong>Begründung:</strong> ${escapeHtml(event.reason || '-')}</div>
      <div style="color:var(--text-muted)">P&L: ${fmtEUR(event.pnl_eur || 0)} · Tradesumme: ${fmtEUR(event.total_eur || 0)}</div>
    `;
  };

  renderInspector(events[0]);

  list.querySelectorAll('[data-event-index]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const index = Number(btn.dataset.eventIndex);
      list.querySelectorAll('.simulation-chart-event').forEach((item) => item.classList.remove('active'));
      btn.classList.add('active');
      renderInspector(events[index]);
    });
  });
}

function renderSimulationEmpty() {
  const summary = document.getElementById('simulation-summary');
  const chart = document.getElementById('simulation-chart');
  const events = document.getElementById('simulation-chart-events');
  const inspector = document.getElementById('simulation-chart-inspector');
  resetSimulationChart();
  if (summary) summary.innerHTML = '<div class="empty-state" style="padding:30px;color:var(--text-muted)">Erstelle den ersten Replay-Run, um Metriken zu sehen.</div>';
  if (chart) chart.innerHTML = '<div class="empty-state" style="padding:30px;color:var(--text-muted)">Noch keine Simulationsdaten vorhanden.</div>';
  if (events) events.innerHTML = '';
  if (inspector) inspector.innerHTML = '';
}

function renderSimulationError(message) {
  const summary = document.getElementById('simulation-summary');
  const chart = document.getElementById('simulation-chart');
  const events = document.getElementById('simulation-chart-events');
  const inspector = document.getElementById('simulation-chart-inspector');
  if (summary) {
    summary.innerHTML = `<div class="empty-state" style="padding:30px;color:var(--red-bright)">Fehler beim Laden der Simulationen: ${escapeHtml(message)}</div>`;
  }
  if (chart && !simulationChart) {
    chart.innerHTML = '<div class="empty-state" style="padding:30px;color:var(--text-muted)">Chart derzeit nicht verfügbar.</div>';
  }
  if (events) events.innerHTML = '';
  if (inspector) inspector.innerHTML = '';
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}
