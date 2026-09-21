const CATEGORY_LABEL = {
  connector: 'Connecteur',
  infra: 'Infrastructure',
  other: 'Autre',
};

function statusClass(status) {
  return status === 'ok' ? 'ok' : status === 'fail' ? 'fail' : 'unknown';
}

function statusText(status) {
  return status === 'ok' ? 'OK' : status === 'fail' ? 'ÉCHEC' : 'Inconnu';
}

function euros(v) {
  return new Intl.NumberFormat('fr-FR', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(v || 0);
}

function relativeTime(iso) {
  if (!iso) return '—';
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "à l'instant";
  if (mins < 60) return `il y a ${mins} min`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `il y a ${hours} h`;
  return `il y a ${Math.round(hours / 24)} j`;
}

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

function renderStatusGrid(el, items, { nameKey = 'name', statusKey = 'status', subKey = null } = {}) {
  if (!items.length) {
    el.innerHTML = '<div class="empty-state">Aucune donnée reçue pour le moment</div>';
    return;
  }
  el.innerHTML = items.map((item) => `
    <div class="status-card">
      <span class="status-dot ${statusClass(item[statusKey])}"></span>
      <div>
        <div class="label">${item[nameKey]}</div>
        <div class="meta">
          <span class="status-text ${statusClass(item[statusKey])}">${statusText(item[statusKey])}</span>
          · ${subKey ? item[subKey] : relativeTime(item.last_seen)}
        </div>
      </div>
    </div>
  `).join('');
}

function renderEventList(el, events, nameField) {
  if (!events.length) {
    el.innerHTML = '<div class="empty-state">Aucun incident sur la période</div>';
    return;
  }
  el.innerHTML = '<ul class="event-list">' + events.map((ev) => `
    <li>
      <span class="event-time">${fmtTime(ev.occurred_at)}</span>
      <span class="event-badge ${ev.event_type === 'fail' ? 'fail' : 'recovered'}">
        ${ev.event_type === 'fail' ? 'ÉCHEC' : 'RÉTABLI'}
      </span>
      <span>
        <div class="event-service">${ev[nameField]}</div>
        ${ev.detail ? `<div class="event-detail">${ev.detail}</div>` : ''}
      </span>
    </li>
  `).join('') + '</ul>';
}

async function loadServices() {
  const services = await fetchJson('/api/dashboard/services');
  const connectors = services.filter((s) => s.category === 'connector');
  const infra = services.filter((s) => s.category !== 'connector');
  renderStatusGrid(document.getElementById('connectors-grid'), connectors);
  const infraGrid = document.getElementById('infra-services-grid');
  if (infra.length) {
    infraGrid.closest('section').style.display = '';
    renderStatusGrid(infraGrid, infra);
  } else {
    infraGrid.closest('section').style.display = 'none';
  }
}

async function loadVpn() {
  const nodes = await fetchJson('/api/dashboard/vpn');
  renderStatusGrid(document.getElementById('vpn-grid'), nodes);
}

async function loadEvents(range) {
  const [serviceEvents, vpnEvents] = await Promise.all([
    fetchJson(`/api/dashboard/events?range=${range}`),
    fetchJson(`/api/dashboard/vpn/events?range=${range}`),
  ]);
  const merged = [
    ...serviceEvents.map((e) => ({ ...e, display_name: e.service_name })),
    ...vpnEvents.map((e) => ({ ...e, display_name: `VPN — ${e.node_name}` })),
  ].sort((a, b) => new Date(b.occurred_at) - new Date(a.occurred_at));
  renderEventList(document.getElementById('events-list'), merged, 'display_name');
}

async function loadQnap(range) {
  const rows = await fetchJson(`/api/dashboard/qnap?range=${range}`);
  const latest = rows[rows.length - 1];

  document.getElementById('qnap-summary').innerHTML = latest ? `
    <div class="kpi"><div class="kpi-label">CPU</div><div class="kpi-value">${latest.cpu_pct ?? '—'}%</div></div>
    <div class="kpi"><div class="kpi-label">RAM</div><div class="kpi-value">${latest.ram_pct ?? '—'}%</div></div>
    <div class="kpi"><div class="kpi-label">Disque</div><div class="kpi-value">${latest.disk_usage_pct ?? '—'}%</div></div>
    <div class="kpi"><div class="kpi-label">RAID</div><div class="kpi-value" style="font-size:15px">${latest.raid_status ?? '—'}</div></div>
    <div class="kpi"><div class="kpi-label">Sauvegarde</div><div class="kpi-value" style="font-size:15px">${latest.backup_status ?? '—'}</div></div>
  ` : '<div class="empty-state">Aucune métrique QNAP reçue pour le moment</div>';

  renderLineChart(document.getElementById('qnap-cpu-chart'),
    rows.map((r) => ({ x: r.recorded_at, y: r.cpu_pct })), { yFormat: (v) => `${Math.round(v)}%` });
  renderLineChart(document.getElementById('qnap-ram-chart'),
    rows.map((r) => ({ x: r.recorded_at, y: r.ram_pct })), { yFormat: (v) => `${Math.round(v)}%` });
  renderLineChart(document.getElementById('qnap-disk-chart'),
    rows.map((r) => ({ x: r.recorded_at, y: r.disk_usage_pct })), { yFormat: (v) => `${Math.round(v)}%` });
}

async function loadTokens(range) {
  const summary = await fetchJson(`/api/dashboard/tokens/summary?range=${range}`);
  const totalTokens = (summary.totals.tokens_input || 0) + (summary.totals.tokens_output || 0);

  document.getElementById('tokens-kpis').innerHTML = `
    <div class="kpi"><div class="kpi-label">Coût total</div><div class="kpi-value">${euros(summary.totals.cost_usd)}</div></div>
    <div class="kpi"><div class="kpi-label">Tokens entrée</div><div class="kpi-value">${(summary.totals.tokens_input || 0).toLocaleString('fr-FR')}</div></div>
    <div class="kpi"><div class="kpi-label">Tokens sortie</div><div class="kpi-value">${(summary.totals.tokens_output || 0).toLocaleString('fr-FR')}</div></div>
    <div class="kpi"><div class="kpi-label">Total tokens</div><div class="kpi-value">${totalTokens.toLocaleString('fr-FR')}</div></div>
  `;

  renderLineChart(document.getElementById('tokens-daily-chart'),
    summary.daily.map((d) => ({ x: d.day, y: d.cost_usd })),
    { yFormat: (v) => euros(v), xFormat: fmtDay });

  renderBarChart(document.getElementById('tokens-model-chart'),
    summary.byModel.map((m) => ({ label: m.model, value: m.cost_usd })),
    { yFormat: (v) => euros(v) });

  const bySourceTable = document.getElementById('tokens-source-table');
  if (!summary.bySource.length) {
    bySourceTable.innerHTML = '<div class="empty-state">Aucune donnée</div>';
  } else {
    bySourceTable.innerHTML = `
      <table class="data-table">
        <thead><tr><th>Source</th><th class="num">Tokens entrée</th><th class="num">Tokens sortie</th><th class="num">Coût</th></tr></thead>
        <tbody>
          ${summary.bySource.map((s) => `
            <tr>
              <td>${s.source}</td>
              <td class="num">${s.tokens_input.toLocaleString('fr-FR')}</td>
              <td class="num">${s.tokens_output.toLocaleString('fr-FR')}</td>
              <td class="num">${euros(s.cost_usd)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>`;
  }
}

async function refreshAll() {
  const range = document.getElementById('range-select').value;
  document.getElementById('last-refresh').textContent = `Actualisé ${new Date().toLocaleTimeString('fr-FR')}`;
  await Promise.all([
    loadServices(),
    loadVpn(),
    loadEvents(range),
    loadQnap(range),
    loadTokens(range),
  ]).catch((err) => console.error('Erreur de chargement du dashboard', err));
}

function initTheme() {
  const stored = localStorage.getItem('fta-theme');
  if (stored) document.documentElement.setAttribute('data-theme', stored);
  document.getElementById('theme-toggle').addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme')
      || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('fta-theme', next); } catch (e) { /* stockage indisponible */ }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  document.getElementById('range-select').addEventListener('change', refreshAll);
  refreshAll();
  setInterval(refreshAll, 60000);
});
