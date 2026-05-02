export const API_BASE = '/api';

// Globaler 401-Handler — wird von app.js gesetzt (UI-01)
let _on401 = null;
export function set401Handler(fn) { _on401 = fn; }

async function request(path, options = {}) {
  const response = await fetch(API_BASE + path, {
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    ...options,
  });

  if (response.status === 401) {
    if (_on401) _on401();
    throw new Error('Nicht angemeldet');
  }

  if (!response.ok) {
    let message = `API ${path}: ${response.status}`;
    try {
      const body = await response.json();
      if (body?.error) message = body.error;
    } catch {
      // ignore parse errors
    }
    throw new Error(message);
  }

  return response.json();
}

export const api = {
  get: (path) => request(path),
  post: (path, body = {}) => request(path, {
    method: 'POST',
    body: JSON.stringify(body),
  }),

  // ── Auth (UI-01, UI-02) ──────────────────────────────────────────────────
  login: (username, password) => request('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  }),
  logout: () => request('/auth/logout', { method: 'POST' }),
  me: () => request('/auth/me'),

  // ── Portfolios (UI-03, UI-04) ────────────────────────────────────────────
  getPortfolios: () => request('/portfolios'),
  createPortfolio: (payload) => request('/portfolios', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updatePortfolio: (id, payload) => request(`/portfolios/${id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  }),
  togglePortfolioStatus: (id) => request(`/portfolios/${id}/status`, {
    method: 'PATCH',
  }),
  deletePortfolio: (id) => request(`/portfolios/${id}`, { method: 'DELETE' }),
  activatePortfolio: (id) => request(`/portfolios/${id}/activate`, {
    method: 'POST',
  }),

  // ── Proposals (Approval-Modus) ───────────────────────────────────────────
  getTodayProposal: (portfolioId) => request(`/portfolios/${portfolioId}/proposals/today`),
  getProposals: (portfolioId) => request(`/portfolios/${portfolioId}/proposals`),
  patchOrderApproval: (proposalId, orderId, approved) =>
    request(`/proposals/${proposalId}/orders/${orderId}`, {
      method: 'PATCH',
      body: JSON.stringify({ approved }),
    }),
  executeProposal: (proposalId) => request(`/proposals/${proposalId}/execute`, {
    method: 'POST',
  }),

  // ── Trading ──────────────────────────────────────────────────────────────
  getAccount: () => request('/account'),
  getPositions: () => request('/positions'),
  getWatchlist: () => request('/watchlist'),
  getTrades: (limit = 100) => request(`/trades?limit=${limit}`),
  getEquity: (days = 60) => request(`/equity?days=${days}`),
  getPrices: (symbol, days = 90) => request(`/prices/${symbol}?days=${days}`),
  getAlgoParams: () => request('/algo/params'),
  getStrategies: () => request('/strategies'),
  getUniverses: () => request('/universes'),
  setActiveStrategy: (strategyId) => request('/strategies/active', {
    method: 'POST',
    body: JSON.stringify({ strategy_id: strategyId }),
  }),
  updateStrategy: (strategyId, payload) => request(`/strategies/${strategyId}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  }),
  approveStrategyForLive: (strategyId) => request(`/strategies/${strategyId}/approve-live`, {
    method: 'POST',
  }),
  runTradingCycle: () => request('/trading/run', { method: 'POST' }),
  runOptimization: () => request('/trading/optimize', { method: 'POST' }),

  // ── Simulations ──────────────────────────────────────────────────────────
  getSimulations: () => request('/simulations'),
  deleteAllSimulations: () => request('/simulations', { method: 'DELETE' }),
  createSimulation: (payload) => request('/simulations', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  getSimulation: (runId) => request(`/simulations/${runId}`),
  deleteSimulation: (runId) => request(`/simulations/${runId}`, { method: 'DELETE' }),
  cancelSimulation: (runId) => request(`/simulations/${runId}/cancel`, { method: 'POST' }),
  getSimulationEquity: (runId) => request(`/simulations/${runId}/equity`),
  getSimulationTrades: (runId, limit = 300) => request(`/simulations/${runId}/trades?limit=${limit}`),
  getSimulationPositions: (runId) => request(`/simulations/${runId}/positions`),
  getSimulationDecisions: (runId, limit = 400) => request(`/simulations/${runId}/decisions?limit=${limit}`),
  getSimulationMetrics: (runId) => request(`/simulations/${runId}/metrics`),
  getSimulationBenchmark: (runId) => request(`/simulations/${runId}/benchmark`),

  // ── Admin / Users ────────────────────────────────────────────────────────
  getUsers: () => request('/users'),
  createUser: (payload) => request('/users', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  toggleUserStatus: (userId) => request(`/users/${userId}/status`, { method: 'PATCH' }),
  adminResetPassword: (userId, newPassword) => request(`/users/${userId}/password`, {
    method: 'PUT',
    body: JSON.stringify({ new_password: newPassword }),
  }),
  getStatus: () => request('/status'),

  // ── Scenarios ────────────────────────────────────────────────────────────
  getScenarios: () => request('/scenarios'),
  updateScenario: (scenarioId, payload) => request(`/scenarios/${scenarioId}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  }),
  deleteScenario: (scenarioId) => request(`/scenarios/${scenarioId}`, { method: 'DELETE' }),
  createScenarioBatch: (payload) => request('/scenario-batches', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  getScenarioBatch: (batchId) => request(`/scenario-batches/${batchId}`),
  deleteScenarioBatch: (batchId) => request(`/scenario-batches/${batchId}`, { method: 'DELETE' }),
  runScenarioBatch: (batchId) => request(`/scenario-batches/${batchId}/run`, { method: 'POST' }),
};
