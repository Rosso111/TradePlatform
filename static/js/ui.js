export function fmtEUR(v) {
  if (v == null) return '-';
  return new Intl.NumberFormat('de-AT', {
    style: 'currency', currency: 'EUR', minimumFractionDigits: 2,
  }).format(v);
}

export function fmtNum(v, digits = 2) {
  if (v == null) return '-';
  return new Intl.NumberFormat('de-AT', {
    minimumFractionDigits: digits,
    maximumFractionDigits: 4,
  }).format(v);
}

export function fmtPct(v) {
  if (v == null) return '-';
  return `${v >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`;
}

export function fmtDateTime(iso) {
  if (!iso) return '-';
  return new Date(iso).toLocaleString('de-AT', { dateStyle: 'short', timeStyle: 'short' });
}

export function fmtTime(iso) {
  if (!iso) return '-';
  return new Date(iso).toLocaleTimeString('de-AT', { hour: '2-digit', minute: '2-digit' });
}

export function setEl(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

export function setStatusDot(online) {
  const dot = document.querySelector('.status-dot');
  if (dot) dot.className = 'status-dot' + (online ? '' : ' loading');
}

export function hideLoading() {
  const overlay = document.getElementById('loading-overlay');
  if (overlay) {
    overlay.style.opacity = '0';
    overlay.style.transition = 'opacity .5s';
    setTimeout(() => overlay.remove(), 500);
  }
}

export function showDataLoadingBanner(msg) {
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

export function hideDataLoadingBanner() {
  const banner = document.getElementById('data-loading-banner');
  if (banner) banner.remove();
}

export function showToast(msg, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = msg;
  container.prepend(toast);
  setTimeout(() => toast.remove(), 5000);
}

export function addLogEntry(msg) {
  const log = document.getElementById('action-log');
  if (!log) return;
  const cls = msg.includes('KAUF') ? 'log-buy' : msg.includes('VERKAUF') ? 'log-sell' : 'log-info';
  const entry = document.createElement('div');
  entry.className = 'log-entry';
  entry.innerHTML = `<span class="log-time">${new Date().toLocaleTimeString('de-AT')}</span><span class="${cls}">${msg}</span>`;
  log.prepend(entry);
  while (log.children.length > 50) log.removeChild(log.lastChild);
}
