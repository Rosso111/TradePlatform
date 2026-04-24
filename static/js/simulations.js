import { api } from './api.js';
import { fmtEUR, fmtPct, showToast } from './ui.js';

let simulationChart = null;
let simulationEquitySeries = null;
let simulationBenchmarkSeries = null;
let simulationRefreshTimer = null;
let currentSimulationRuns = [];
let currentSimulationRunId = null;
let currentScenarios = [];
let currentScenarioBatch = null;
let currentStrategies = [];
let currentScenarioId = null;
let simulationActionInFlight = false;
const SIMULATION_REFRESH_MS = 7000;

const STRATEGY_PARAM_FIELDS = [
  'buy_threshold', 'sell_threshold', 'max_positions', 'trailing_stop_pct',
  'min_rsi', 'max_rsi', 'min_sector_score', 'decision_log_mode',
  'persist_chunk_days', 'cancel_check_interval_days',
  'require_price_above_ema_fast', 'require_ema_fast_above_slow', 'require_macd_above_signal',
];

const SLIDER_PAIRS = [
  ['buy_threshold_slider', 'buy_threshold'],
  ['sell_threshold_slider', 'sell_threshold'],
  ['trailing_stop_pct_slider', 'trailing_stop_pct'],
  ['min_rsi_slider', 'min_rsi'],
  ['max_rsi_slider', 'max_rsi'],
  ['min_sector_score_slider', 'min_sector_score'],
];

async function loadSimulationUniverseOptions() {
  const select = document.getElementById('simulation-universe-select');
  const scenarioSelect = document.getElementById('scenario-universe-select');
  if (!select) return;

  try {
    const data = await api.getUniverses();
    const universes = Array.isArray(data?.universes) ? data.universes : [];
    const activeUniverse = data?.active_universe;
    const optionsHtml = universes.map((universe) => {
      const selected = universe.id === activeUniverse ? 'selected' : '';
      return `<option value="${String(universe.id)}" ${selected}>${String(universe.name || universe.id)}</option>`;
    }).join('');
    select.innerHTML = optionsHtml;
    if (scenarioSelect) scenarioSelect.innerHTML = optionsHtml;
  } catch (error) {
    console.warn('Universe options:', error);
  }
}

export async function loadSimulations() {
  try {
    const [runs, scenarioData, strategyData] = await Promise.all([
      api.getSimulations(),
      api.getScenarios(),
      api.getStrategies(),
    ]);
    currentSimulationRuns = runs;
    currentScenarios = Array.isArray(scenarioData?.scenarios) ? scenarioData.scenarios : [];
    currentScenarioBatch = Array.isArray(scenarioData?.scenario_batches) && scenarioData.scenario_batches.length
      ? scenarioData.scenario_batches[scenarioData.scenario_batches.length - 1]
      : null;
    currentStrategies = Array.isArray(strategyData?.strategies) ? strategyData.strategies : [];

    populateStrategySelects();
    renderSimulationRuns(runs);
    renderScenarios();

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
  loadSimulationUniverseOptions();
  const form = document.getElementById('simulation-form');
  const scenarioForm = document.getElementById('scenario-form');
  const createBatchBtn = document.getElementById('scenario-batch-create-btn');
  const deleteBatchBtn = document.getElementById('scenario-batch-delete-btn');
  const runBatchBtn = document.getElementById('scenario-batch-run-btn');
  const deleteActiveRunBtn = document.getElementById('simulation-delete-active-btn');
  const deleteAllRunsBtn = document.getElementById('simulation-delete-all-btn');
  const scenarioStrategySelect = document.getElementById('scenario-strategy-select');
  if (!form) return;

  initScenarioSliders();

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
        universe_name: formData.get('universe_name') || undefined,
        auto_start: true,
      };
      const result = await api.createSimulation(payload);
      showToast('Simulation erstellt', 'info');
      await loadSimulations();
      if (result?.run?.id) await loadSimulationDetail(result.run.id);
    } catch (error) {
      showToast(`Simulation fehlgeschlagen: ${error.message}`, 'info');
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Replay starten';
      }
    }
  });

  if (scenarioStrategySelect) {
    scenarioStrategySelect.addEventListener('change', () => {
      applyStrategyDefaultsToScenarioForm(scenarioStrategySelect.value);
    });
  }

  if (scenarioForm) {
    scenarioForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const formData = new FormData(scenarioForm);
      try {
        const paramsRaw = String(formData.get('params_override') || '').trim();
        const paramsOverride = paramsRaw ? JSON.parse(paramsRaw) : {};

        ['buy_threshold', 'sell_threshold', 'max_positions', 'trailing_stop_pct', 'min_rsi', 'max_rsi', 'min_sector_score', 'persist_chunk_days', 'cancel_check_interval_days']
          .forEach((field) => {
            const raw = String(formData.get(field) || '').trim();
            if (!raw) return;
            paramsOverride[field] = Number(raw);
          });

        ['require_price_above_ema_fast', 'require_ema_fast_above_slow', 'require_macd_above_signal'].forEach((field) => {
          paramsOverride[field] = formData.has(field);
        });

        const decisionLogMode = String(formData.get('decision_log_mode') || '').trim();
        if (decisionLogMode) paramsOverride.decision_log_mode = decisionLogMode;

        const payload = {
          id: String(formData.get('id') || '').trim(),
          name: String(formData.get('name') || '').trim(),
          start_date: formData.get('start_date'),
          end_date: formData.get('end_date'),
          initial_capital_eur: Number(formData.get('initial_capital_eur') || 10000),
          strategy_id: formData.get('strategy_id') || undefined,
          universe_name: formData.get('universe_name') || undefined,
          notes: String(formData.get('notes') || '').trim(),
          params_override: paramsOverride,
        };
        await api.updateScenario(payload.id, payload);
        showToast('Szenario gespeichert', 'info');
        await loadSimulations();
      } catch (error) {
        showToast(`Szenario konnte nicht gespeichert werden: ${error.message}`, 'info');
      }
    });
  }

  if (createBatchBtn) {
    createBatchBtn.addEventListener('click', async () => {
      const selectedIds = Array.from(document.querySelectorAll('.scenario-select-checkbox:checked')).map((el) => el.value);
      if (!selectedIds.length) {
        showToast('Bitte mindestens ein Szenario auswählen', 'info');
        return;
      }
      try {
        const result = await api.createScenarioBatch({ scenario_ids: selectedIds });
        currentScenarioBatch = result?.batch || null;
        showToast('Scenario-Batch angelegt', 'info');
        await loadSimulations();
      } catch (error) {
        showToast(`Batch konnte nicht angelegt werden: ${error.message}`, 'info');
      }
    });
  }

  if (deleteBatchBtn) {
    deleteBatchBtn.addEventListener('click', async () => {
      if (!currentScenarioBatch?.id) {
        showToast('Kein Batch zum Löschen vorhanden', 'info');
        return;
      }
      try {
        await api.deleteScenarioBatch(currentScenarioBatch.id);
        currentScenarioBatch = null;
        showToast('Batch gelöscht', 'info');
        await loadSimulations();
      } catch (error) {
        showToast(`Batch konnte nicht gelöscht werden: ${error.message}`, 'info');
      }
    });
  }

  if (runBatchBtn) {
    runBatchBtn.addEventListener('click', async () => {
      if (!currentScenarioBatch?.id) {
        showToast('Bitte zuerst einen Batch anlegen', 'info');
        return;
      }
      try {
        const result = await api.runScenarioBatch(currentScenarioBatch.id);
        currentScenarioBatch = result?.batch || currentScenarioBatch;
        showToast('Scenario-Batch gestartet', 'info');
        await loadSimulations();
      } catch (error) {
        showToast(`Batch konnte nicht gestartet werden: ${error.message}`, 'info');
      }
    });
  }

  if (deleteActiveRunBtn) {
    deleteActiveRunBtn.addEventListener('click', async () => {
      const selectedRun = getSelectedRun();
      if (!selectedRun) {
        showToast('Kein aktiver Run ausgewählt', 'info');
        return;
      }
      const runLabel = selectedRun.name || `Run #${selectedRun.id}`;
      const confirmed = window.confirm(`Run "${runLabel}" wirklich löschen?`);
      if (!confirmed) return;
      await deleteRunById(selectedRun.id);
    });
  }

  if (deleteAllRunsBtn) {
    deleteAllRunsBtn.addEventListener('click', async () => {
      if (!currentSimulationRuns.length) {
        showToast('Keine Runs zum Löschen vorhanden', 'info');
        return;
      }
      const hasActiveRuns = currentSimulationRuns.some((run) => isRunActive(run));
      const confirmed = window.confirm(
        hasActiveRuns
          ? 'Es gibt noch aktive/laufende Runs. Diese müssen erst abgebrochen werden. Abgeschlossene Runs trotzdem gesammelt löschen?'
          : `Wirklich alle ${currentSimulationRuns.length} Runs löschen?`
      );
      if (!confirmed) return;

      await withSimulationAction(async () => {
        try {
          await api.deleteAllSimulations();
          showToast('Alle Runs gelöscht', 'info');
          currentSimulationRunId = null;
          await loadSimulations();
        } catch (error) {
          await refreshRunsOnly();
          showToast(`Runs konnten nicht gelöscht werden: ${error.message}`, 'info');
        }
      });
    });
  }
}

function populateStrategySelects() {
  const simulationStrategySelect = document.getElementById('simulation-strategy-select');
  const scenarioStrategySelect = document.getElementById('scenario-strategy-select');
  const optionsHtml = currentStrategies.map((strategy) => `<option value="${escapeHtml(strategy.id)}">${escapeHtml(strategy.name || strategy.id)}</option>`).join('');
  if (simulationStrategySelect) simulationStrategySelect.innerHTML = optionsHtml;
  if (scenarioStrategySelect) {
    const previous = scenarioStrategySelect.value;
    scenarioStrategySelect.innerHTML = optionsHtml;
    if (previous && currentStrategies.some((strategy) => strategy.id === previous)) {
      scenarioStrategySelect.value = previous;
    }
    if (!scenarioStrategySelect.value && currentStrategies.length) {
      scenarioStrategySelect.value = currentStrategies[0].id;
    }
    applyStrategyDefaultsToScenarioForm(scenarioStrategySelect.value);
  }
}

function applyStrategyDefaultsToScenarioForm(strategyId) {
  const strategy = currentStrategies.find((item) => item.id === strategyId);
  const form = document.getElementById('scenario-form');
  if (!strategy || !form) return;
  const params = strategy.params || {};

  STRATEGY_PARAM_FIELDS.forEach((field) => {
    const input = form.elements.namedItem(field);
    if (!input) return;
    if (input.type === 'checkbox') {
      input.checked = Boolean(params[field]);
    } else if (input.tagName === 'SELECT') {
      input.value = params[field] ?? '';
    } else if (params[field] !== undefined) {
      input.value = params[field];
    } else {
      input.value = '';
    }
  });

  syncScenarioSlidersFromInputs();
}

function initScenarioSliders() {
  SLIDER_PAIRS.forEach(([sliderName, inputName]) => {
    const slider = document.querySelector(`[name="${sliderName}"]`);
    const input = document.querySelector(`[name="${inputName}"]`);
    const valueEl = document.querySelector(`[data-slider-value="${sliderName}"]`);
    if (!slider || !input || !valueEl) return;

    const render = (value) => { valueEl.textContent = String(value); };
    slider.addEventListener('input', () => {
      input.value = slider.value;
      render(slider.value);
    });
    input.addEventListener('input', () => {
      if (input.value === '') return;
      slider.value = input.value;
      render(input.value);
    });
    render(slider.value);
  });
}

function syncScenarioSlidersFromInputs() {
  SLIDER_PAIRS.forEach(([sliderName, inputName]) => {
    const slider = document.querySelector(`[name="${sliderName}"]`);
    const input = document.querySelector(`[name="${inputName}"]`);
    const valueEl = document.querySelector(`[data-slider-value="${sliderName}"]`);
    if (!slider || !input || !valueEl) return;
    if (input.value !== '') slider.value = input.value;
    valueEl.textContent = slider.value;
  });
}

function renderScenarioDetails(scenario) {
  const container = document.getElementById('scenario-detail-view');
  if (!container) return;
  if (!scenario) {
    container.innerHTML = '<div class="empty-state" style="padding:18px;color:var(--text-muted)">Klicke auf ein Szenario, um Details zu sehen.</div>';
    return;
  }

  const params = scenario.params_override || {};
  const paramRows = Object.keys(params).length
    ? Object.entries(params).map(([key, value]) => `<div class="scenario-detail-card"><strong>${escapeHtml(key)}</strong><span>${escapeHtml(String(value))}</span></div>`).join('')
    : '<div class="empty-state" style="padding:12px;color:var(--text-muted)">Keine Overrides gesetzt.</div>';

  container.innerHTML = `
    <div class="scenario-detail-grid">
      <div class="scenario-detail-card"><strong>ID</strong><span>${escapeHtml(scenario.id || '-')}</span></div>
      <div class="scenario-detail-card"><strong>Name</strong><span>${escapeHtml(scenario.name || '-')}</span></div>
      <div class="scenario-detail-card"><strong>Strategie</strong><span>${escapeHtml(scenario.strategy_id || '-')}</span></div>
      <div class="scenario-detail-card"><strong>Universe</strong><span>${escapeHtml(scenario.universe_name || '-')}</span></div>
      <div class="scenario-detail-card"><strong>Start</strong><span>${escapeHtml(scenario.start_date || '-')}</span></div>
      <div class="scenario-detail-card"><strong>Ende</strong><span>${escapeHtml(scenario.end_date || '-')}</span></div>
      <div class="scenario-detail-card"><strong>Kapital</strong><span>${fmtEUR(scenario.initial_capital_eur || 0)}</span></div>
      <div class="scenario-detail-card"><strong>Notizen</strong><span>${escapeHtml(scenario.notes || '-')}</span></div>
    </div>
    <div class="scenario-section">
      <div class="scenario-section-title">Parameter / Overrides</div>
      <div class="scenario-detail-grid">${paramRows}</div>
    </div>
  `;
}

function loadScenarioIntoEditor(scenarioId) {
  const scenario = currentScenarios.find((item) => item.id === scenarioId);
  const form = document.getElementById('scenario-form');
  if (!scenario || !form) return;
  currentScenarioId = scenarioId;

  form.elements.namedItem('id').value = scenario.id || '';
  form.elements.namedItem('name').value = scenario.name || '';
  form.elements.namedItem('start_date').value = scenario.start_date || '';
  form.elements.namedItem('end_date').value = scenario.end_date || '';
  form.elements.namedItem('initial_capital_eur').value = scenario.initial_capital_eur || 10000;
  form.elements.namedItem('strategy_id').value = scenario.strategy_id || '';
  form.elements.namedItem('universe_name').value = scenario.universe_name || '';
  form.elements.namedItem('notes').value = scenario.notes || '';
  form.elements.namedItem('params_override').value = JSON.stringify(scenario.params_override || {}, null, 2);

  applyStrategyDefaultsToScenarioForm(scenario.strategy_id);
  const params = scenario.params_override || {};
  Object.entries(params).forEach(([key, value]) => {
    const input = form.elements.namedItem(key);
    if (!input) return;
    if (input.type === 'checkbox') {
      input.checked = Boolean(value);
    } else {
      input.value = value;
    }
  });
  syncScenarioSlidersFromInputs();
  renderScenarioDetails(scenario);
}

function renderScenarios() {
  const list = document.getElementById('scenario-list');
  const status = document.getElementById('scenario-batch-status');
  if (!list) return;

  if (!currentScenarios.length) {
    list.innerHTML = '<div class="empty-state" style="padding:12px;color:var(--text-muted)">Noch keine Szenarien gespeichert.</div>';
  } else {
    list.innerHTML = currentScenarios.map((scenario) => `
      <div class="scenario-item">
        <input class="scenario-select-checkbox" type="checkbox" value="${escapeHtml(scenario.id)}">
        <button class="btn btn-ghost scenario-load-btn" type="button" data-scenario-id="${escapeHtml(scenario.id)}">
          <div>
            <div class="scenario-item-title">${escapeHtml(scenario.name || scenario.id)}</div>
            <div class="scenario-item-meta">${escapeHtml(scenario.strategy_id || '-')} · ${escapeHtml(scenario.universe_name || '-')}</div>
            <div class="scenario-item-meta">${escapeHtml(scenario.start_date || '-')} → ${escapeHtml(scenario.end_date || '-')} · ${fmtEUR(scenario.initial_capital_eur || 0)}</div>
          </div>
        </button>
        <div class="scenario-item-meta">${escapeHtml(Object.keys(scenario.params_override || {}).join(', ') || 'keine overrides')}</div>
        <button class="btn btn-danger scenario-delete-btn" type="button" data-scenario-id="${escapeHtml(scenario.id)}">Löschen</button>
      </div>
    `).join('');

    list.querySelectorAll('.scenario-load-btn').forEach((btn) => {
      btn.addEventListener('click', () => loadScenarioIntoEditor(btn.dataset.scenarioId));
    });

    list.querySelectorAll('.scenario-delete-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const scenarioId = btn.dataset.scenarioId;
        try {
          await api.deleteScenario(scenarioId);
          if (currentScenarioId === scenarioId) {
            currentScenarioId = null;
            renderScenarioDetails(null);
          }
          showToast('Szenario gelöscht', 'info');
          await loadSimulations();
        } catch (error) {
          showToast(`Szenario konnte nicht gelöscht werden: ${error.message}`, 'info');
        }
      });
    });
  }

  if (currentScenarioId) {
    const scenario = currentScenarios.find((item) => item.id === currentScenarioId);
    if (scenario) renderScenarioDetails(scenario);
  }

  if (status) {
    if (!currentScenarioBatch) {
      status.innerHTML = 'Noch kein Batch angelegt.';
    } else {
      const results = currentScenarioBatch.results || [];
      status.innerHTML = `Batch <strong>${escapeHtml(currentScenarioBatch.name || currentScenarioBatch.id)}</strong> · ID: ${escapeHtml(currentScenarioBatch.id || '-')} · Status: <strong>${escapeHtml(currentScenarioBatch.status || 'queued')}</strong> · Fortschritt: ${Number(currentScenarioBatch.current_index || 0)} / ${Number((currentScenarioBatch.scenario_ids || []).length || 0)} · Runs: ${results.length}`;
    }
  }
}

async function loadSimulationDetail(runId) {
  currentSimulationRunId = Number(runId);
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
    updateSimulationActionButtons();
    return;
  }
  container.innerHTML = runs.map((run) => {
    const runStatus = String(run.status || '').toLowerCase();
    const canCancel = runStatus === 'running';
    const deleteDisabled = isRunActive(run);
    return `
      <div class="simulation-run-row">
        <button class="simulation-run-item" data-run-id="${run.id}">
          <div class="simulation-run-head">
            <strong>${escapeHtml(run.name || `Run #${run.id}`)}</strong>
            <span class="simulation-status ${run.status}">${escapeHtml(run.status)}</span>
          </div>
          <div class="simulation-run-id">ID: ${run.id}</div>
          <div class="simulation-run-meta">${escapeHtml(run.start_date || '-')} → ${escapeHtml(run.end_date || '-')}</div>
          <div class="simulation-run-metrics">
            <span>${fmtEUR(run.final_equity_eur)}</span>
            <span class="${(run.total_return_pct || 0) >= 0 ? 'positive' : 'negative'}">${fmtPct(run.total_return_pct || 0)}</span>
          </div>
        </button>
        <div class="simulation-run-actions">
          ${canCancel ? `<button class="btn btn-ghost simulation-run-cancel-btn" type="button" data-cancel-run-id="${run.id}">Abbrechen</button>` : ''}
          <button class="btn ${deleteDisabled ? 'btn-ghost' : 'btn-danger'} simulation-run-delete-btn" type="button" data-delete-run-id="${run.id}" ${deleteDisabled ? 'disabled' : ''}>Löschen</button>
        </div>
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
      if (!run) {
        await refreshRunsOnly();
        showToast('Run wurde nicht mehr gefunden', 'info');
        return;
      }
      const runLabel = run.name || `Run #${run.id}`;
      const confirmed = window.confirm(`Laufenden Run "${runLabel}" wirklich abbrechen?`);
      if (!confirmed) return;
      await cancelRunById(runId);
    });
  });

  container.querySelectorAll('[data-delete-run-id]').forEach((btn) => {
    btn.addEventListener('click', async (event) => {
      event.preventDefault();
      event.stopPropagation();
      const runId = Number(btn.dataset.deleteRunId);
      const run = currentSimulationRuns.find((item) => Number(item.id) === runId);
      if (!run) {
        await refreshRunsOnly();
        showToast('Run wurde nicht mehr gefunden', 'info');
        return;
      }
      const runLabel = run.name || `Run #${run.id}`;
      const confirmed = window.confirm(`Run "${runLabel}" wirklich löschen?`);
      if (!confirmed) return;
      await deleteRunById(runId);
    });
  });

  updateSimulationActionButtons();
}

function hasRunningSimulations() {
  return currentSimulationRuns.some((run) => String(run.status).toUpperCase() === 'RUNNING');
}

function isRunActive(run) {
  const status = String(run?.status || '').toUpperCase();
  return status === 'RUNNING' || status === 'CANCEL_REQUESTED';
}

function getSelectedRun() {
  return currentSimulationRuns.find((run) => Number(run.id) === Number(currentSimulationRunId)) || null;
}

function updateSimulationActionButtons() {
  const deleteActiveRunBtn = document.getElementById('simulation-delete-active-btn');
  const selectedRun = getSelectedRun();
  if (deleteActiveRunBtn) {
    const canDelete = Boolean(selectedRun) && !isRunActive(selectedRun) && !simulationActionInFlight;
    deleteActiveRunBtn.disabled = !canDelete;
    deleteActiveRunBtn.title = !selectedRun
      ? 'Bitte zuerst einen Run auswählen'
      : isRunActive(selectedRun)
        ? 'Aktive Runs bitte erst abbrechen'
        : 'Ausgewählten Run löschen';
  }
}

async function withSimulationAction(fn) {
  if (simulationActionInFlight) return;
  simulationActionInFlight = true;
  updateSimulationActionButtons();
  try {
    await fn();
  } finally {
    simulationActionInFlight = false;
    updateSimulationActionButtons();
  }
}

async function refreshRunsOnly() {
  const runs = await api.getSimulations();
  currentSimulationRuns = runs;
  renderSimulationRuns(runs);
  if (!runs.length) {
    currentSimulationRunId = null;
    renderSimulationEmpty();
    return;
  }
  if (!runs.some((run) => Number(run.id) === Number(currentSimulationRunId))) {
    currentSimulationRunId = runs[0].id;
    await loadSimulationDetail(currentSimulationRunId);
  } else {
    highlightSelectedRun(currentSimulationRunId);
    updateSimulationActionButtons();
  }
}

async function deleteRunById(runId) {
  await withSimulationAction(async () => {
    try {
      await api.deleteSimulation(runId);
      showToast('Run gelöscht', 'info');
      if (Number(currentSimulationRunId) === Number(runId)) {
        currentSimulationRunId = null;
      }
      await loadSimulations();
    } catch (error) {
      await refreshRunsOnly();
      showToast(`Run konnte nicht gelöscht werden: ${error.message}`, 'info');
    }
  });
}

async function cancelRunById(runId) {
  await withSimulationAction(async () => {
    try {
      await api.cancelSimulation(runId);
      showToast('Abbruch angefordert', 'info');
      await loadSimulations();
    } catch (error) {
      await refreshRunsOnly();
      showToast(`Run konnte nicht abgebrochen werden: ${error.message}`, 'info');
    }
  });
}

function stopSimulationAutoRefresh() {
  if (simulationRefreshTimer) {
    clearInterval(simulationRefreshTimer);
    simulationRefreshTimer = null;
  }
}

function isSimulationsViewActive() {
  const panel = document.querySelector('.view-panel[data-tab="simulations"]');
  return !panel || panel.classList.contains('active');
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
  if (!hasRunningSimulations()) stopSimulationAutoRefresh();
}

function updateSimulationAutoRefresh() {
  if (!isSimulationsViewActive() || !hasRunningSimulations()) {
    stopSimulationAutoRefresh();
    return;
  }
  if (simulationRefreshTimer) return;
  simulationRefreshTimer = setInterval(async () => {
    try { await refreshSimulationState(); } catch (error) { console.warn('Simulation auto-refresh:', error); }
  }, SIMULATION_REFRESH_MS);
}

function highlightSelectedRun(runId) {
  document.querySelectorAll('.simulation-run-item').forEach((item) => {
    item.classList.toggle('active', Number(item.dataset.runId) === Number(runId));
  });
  updateSimulationActionButtons();
}

function renderSimulationSummary(run, metrics) {
  const summary = document.getElementById('simulation-summary');
  if (!summary) return;
  const progress = run.progress || {};
  const progressPct = Number(progress.progress_pct || 0);
  const progressLabel = progress.total_days ? `${progress.processed_days || 0} / ${progress.total_days} Tage` : '–';
  const currentStepLabel = progress.latest_snapshot_date || progress.current_date || '–';
  summary.innerHTML = `
    <div class="stats-bar">
      <div class="stat-card"><div class="stat-label">Run</div><div class="stat-value">${escapeHtml(run.name || `Run #${run.id}`)}</div><div class="stat-sub">ID ${run.id} · ${escapeHtml(run.status)}</div></div>
      <div class="stat-card"><div class="stat-label">Fortschritt</div><div class="stat-value">${progressPct.toFixed(1)}%</div><div class="stat-sub">${escapeHtml(progressLabel)}</div></div>
      <div class="stat-card"><div class="stat-label">Aktueller Stand</div><div class="stat-value">${escapeHtml(currentStepLabel)}</div><div class="stat-sub">${run.latest_snapshot ? `Equity: ${fmtEUR(run.latest_snapshot.equity_eur)}` : 'Noch kein Snapshot'}</div></div>
      <div class="stat-card"><div class="stat-label">Return</div><div class="stat-value ${(metrics.total_return_pct || 0) >= 0 ? 'positive' : 'negative'}">${fmtPct(metrics.total_return_pct || 0)}</div><div class="stat-sub">Final: ${fmtEUR(metrics.final_equity_eur)}</div></div>
      <div class="stat-card"><div class="stat-label">Benchmark</div><div class="stat-value ${(metrics.benchmark_return_pct || 0) >= 0 ? 'positive' : 'negative'}">${fmtPct(metrics.benchmark_return_pct || 0)}</div><div class="stat-sub">Outperformance: ${metrics.outperformance_pct == null ? '-' : fmtPct(metrics.outperformance_pct)}</div></div>
      <div class="stat-card"><div class="stat-label">Trades</div><div class="stat-value">${metrics.total_trades || 0}</div><div class="stat-sub">Win Rate: ${fmtPct(metrics.win_rate || 0)}</div></div>
    </div>
  `;
}

function resetSimulationChart() {
  if (simulationChart) simulationChart.remove();
  simulationChart = null;
  simulationEquitySeries = null;
  simulationBenchmarkSeries = null;
}

function renderSimulationChart(equityRows, benchmarkRows) {
  const container = document.getElementById('simulation-chart');
  if (!container) return;
  if (!equityRows.length) {
    resetSimulationChart();
    container.innerHTML = '<div class="empty-state" style="padding:30px;color:var(--text-muted)">Noch keine Simulationsdaten für den Chart</div>';
    return;
  }
  if (container.querySelector('.empty-state')) container.innerHTML = '';
  const width = container.clientWidth || 600;
  const height = container.clientHeight || 280;
  if (!simulationChart) {
    simulationChart = LightweightCharts.createChart(container, {
      layout: { background: { type: 'solid', color: '#161b22' }, textColor: '#8b949e' },
      grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
      rightPriceScale: { borderColor: '#30363d' },
      timeScale: { borderColor: '#30363d', timeVisible: false },
      width, height,
    });
    simulationEquitySeries = simulationChart.addAreaSeries({ lineColor: '#3fb950', topColor: 'rgba(63,185,80,.30)', bottomColor: 'rgba(63,185,80,0)', lineWidth: 2 });
    simulationBenchmarkSeries = simulationChart.addLineSeries({ color: '#58a6ff', lineWidth: 2, lineStyle: 2 });
  } else {
    simulationChart.resize(width, height);
  }
  simulationEquitySeries.setData((equityRows || []).map((row) => ({ time: row.sim_date, value: Number(row.equity_eur) })));
  simulationBenchmarkSeries.setData((benchmarkRows || []).map((row) => ({ time: row.sim_date, value: Number(row.value_eur) })));
  simulationChart.timeScale().fitContent();
}

function renderSimulationEmpty() {
  const summary = document.getElementById('simulation-summary');
  const chart = document.getElementById('simulation-chart');
  resetSimulationChart();
  if (summary) summary.innerHTML = '<div class="empty-state" style="padding:30px;color:var(--text-muted)">Erstelle den ersten Replay-Run, um Metriken zu sehen.</div>';
  if (chart) chart.innerHTML = '<div class="empty-state" style="padding:30px;color:var(--text-muted)">Noch keine Simulationsdaten vorhanden.</div>';
  updateSimulationActionButtons();
}

function renderSimulationError(message) {
  const summary = document.getElementById('simulation-summary');
  if (summary) summary.innerHTML = `<div class="empty-state" style="padding:30px;color:var(--red-bright)">Fehler beim Laden der Simulationen: ${escapeHtml(message)}</div>`;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}
