import { api } from './api.js';
import { fmtEUR, fmtPct, fmtDateTime, showToast } from './ui.js';

let simulationChart = null;
let simulationEquitySeries = null;
let simulationBenchmarkSeries = null;

export async function loadSimulations() {
  try {
    const runs = await api.getSimulations();
    renderSimulationRuns(runs);
    if (runs.length > 0) {
      await loadSimulationDetail(runs[0].id);
    } else {
      renderSimulationEmpty();
    }
  } catch (error) {
    console.warn('Simulations:', error);
    renderSimulationError(error.message);
  }
}

export function initSimulationControls() {
  const form = document.getElementById('simulation-form');
  if (!form) return;

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
  try {
    const [run, metrics, equity, benchmark] = await Promise.all([
      api.getSimulation(runId),
      api.getSimulationMetrics(runId),
      api.getSimulationEquity(runId),
      api.getSimulationBenchmark(runId),
    ]);

    renderSimulationSummary(run, metrics);
    renderSimulationChart(equity, benchmark?.points || []);
    highlightSelectedRun(runId);
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

  container.innerHTML = runs.map((run) => `
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
  `).join('');

  container.querySelectorAll('[data-run-id]').forEach((btn) => {
    btn.addEventListener('click', () => loadSimulationDetail(Number(btn.dataset.runId)));
  });
}

function highlightSelectedRun(runId) {
  document.querySelectorAll('.simulation-run-item').forEach((item) => {
    item.classList.toggle('active', Number(item.dataset.runId) === Number(runId));
  });
}

function renderSimulationSummary(run, metrics) {
  const summary = document.getElementById('simulation-summary');
  if (!summary) return;

  summary.innerHTML = `
    <div class="stats-bar">
      <div class="stat-card"><div class="stat-label">Run</div><div class="stat-value">${escapeHtml(run.name || `Run #${run.id}`)}</div><div class="stat-sub">${escapeHtml(run.status)}</div></div>
      <div class="stat-card"><div class="stat-label">Return</div><div class="stat-value ${(metrics.total_return_pct || 0) >= 0 ? 'positive' : 'negative'}">${fmtPct(metrics.total_return_pct || 0)}</div><div class="stat-sub">Final: ${fmtEUR(metrics.final_equity_eur)}</div></div>
      <div class="stat-card"><div class="stat-label">Benchmark</div><div class="stat-value ${(metrics.benchmark_return_pct || 0) >= 0 ? 'positive' : 'negative'}">${fmtPct(metrics.benchmark_return_pct || 0)}</div><div class="stat-sub">Outperformance: ${metrics.outperformance_pct == null ? '-' : fmtPct(metrics.outperformance_pct)}</div></div>
      <div class="stat-card"><div class="stat-label">Drawdown</div><div class="stat-value negative">${fmtPct(-(metrics.max_drawdown_pct || 0))}</div><div class="stat-sub">Maximaler Rückgang</div></div>
      <div class="stat-card"><div class="stat-label">Sharpe</div><div class="stat-value ${(metrics.sharpe_ratio || 0) >= 0 ? 'positive' : 'negative'}">${Number(metrics.sharpe_ratio || 0).toFixed(2)}</div><div class="stat-sub">Profit Factor: ${Number(metrics.profit_factor || 0).toFixed(2)}</div></div>
      <div class="stat-card"><div class="stat-label">Trades</div><div class="stat-value">${metrics.total_trades || 0}</div><div class="stat-sub">Win Rate: ${fmtPct(metrics.win_rate || 0)}</div></div>
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
}

function renderSimulationChart(equityRows, benchmarkRows) {
  const container = document.getElementById('simulation-chart');
  if (!container) return;

  if (!equityRows.length) {
    container.innerHTML = '<div class="empty-state" style="padding:30px;color:var(--text-muted)">Noch keine Simulationsdaten für den Chart</div>';
    return;
  }

  if (!simulationChart) {
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
      width: container.clientWidth,
      height: container.clientHeight,
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

    window.addEventListener('resize', () => {
      if (simulationChart) {
        simulationChart.resize(container.clientWidth, container.clientHeight);
      }
    });
  }

  simulationEquitySeries.setData(equityRows.map((row) => ({
    time: row.sim_date,
    value: row.equity_eur,
  })));

  simulationBenchmarkSeries.setData((benchmarkRows || []).map((row) => ({
    time: row.sim_date,
    value: row.value_eur,
  })));

  simulationChart.timeScale().fitContent();
}

function renderSimulationEmpty() {
  const summary = document.getElementById('simulation-summary');
  const chart = document.getElementById('simulation-chart');
  if (summary) summary.innerHTML = '<div class="empty-state" style="padding:30px;color:var(--text-muted)">Erstelle den ersten Replay-Run, um Metriken zu sehen.</div>';
  if (chart) chart.innerHTML = '<div class="empty-state" style="padding:30px;color:var(--text-muted)">Noch keine Simulationsdaten vorhanden.</div>';
}

function renderSimulationError(message) {
  const summary = document.getElementById('simulation-summary');
  if (summary) {
    summary.innerHTML = `<div class="empty-state" style="padding:30px;color:var(--red-bright)">Fehler beim Laden der Simulationen: ${escapeHtml(message)}</div>`;
  }
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}
