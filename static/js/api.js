export const API_BASE = '/api';

async function request(path, options = {}) {
  const response = await fetch(API_BASE + path, {
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    ...options,
  });

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

  getAccount: () => request('/account'),
  getPositions: () => request('/positions'),
  getWatchlist: () => request('/watchlist'),
  getTrades: (limit = 100) => request(`/trades?limit=${limit}`),
  getEquity: (days = 60) => request(`/equity?days=${days}`),
  getPrices: (symbol, days = 90) => request(`/prices/${symbol}?days=${days}`),
  getAlgoParams: () => request('/algo/params'),
  getStrategies: () => request('/strategies'),
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

  getSimulations: () => request('/simulations'),
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
};
