/* ═══════════════════════════════════════════════════════════════════════════
   POLYHUNT — Dashboard Application Logic
   Glass Morphism Edition — Spanish UI
   ═══════════════════════════════════════════════════════════════════════════ */

/* ─── State ─────────────────────────────────────────────────────────────────── */
let lastData = null;
let currentSection = 'overview';

/* ─── Format Helpers ────────────────────────────────────────────────────────── */
const fmt = {
  usd: n => {
    const v = parseFloat(n);
    if (isNaN(v)) return '$—';
    return '$' + v.toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  },

  pct: n => {
    const v = parseFloat(n);
    if (isNaN(v)) return '—%';
    return (v * 100).toFixed(1) + '%';
  },

  pnl: n => {
    const v = parseFloat(n);
    if (isNaN(v)) return '$—';
    return (v >= 0 ? '+' : '') + fmt.usd(v);
  },

  pnlClass: n => {
    const v = parseFloat(n);
    if (isNaN(v) || v === 0) return 'pnl-zero';
    return v > 0 ? 'pnl-positive' : 'pnl-negative';
  },

  gapBadge: gap => {
    const g = parseFloat(gap) * 100;
    if (isNaN(g)) return '<span class="gap-badge low">—</span>';
    const cls = g >= 15 ? 'high' : g >= 8 ? 'medium' : 'low';
    return `<span class="gap-badge ${cls}">${g.toFixed(1)}%</span>`;
  },

  confidence: lvl => {
    const n = lvl === 'high' ? 3 : lvl === 'medium' ? 2 : 1;
    const segs = [1, 2, 3].map(i => `<span class="cb-seg ${i <= n ? 'on' : ''}"></span>`).join('');
    return `<span class="confidence-bar">${segs}</span>`;
  },

  date: iso => {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleDateString('es-ES', {
        day: '2-digit', month: 'short', year: '2-digit',
        hour: '2-digit', minute: '2-digit',
      });
    } catch { return iso.slice(0, 16); }
  },

  dateShort: iso => {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleDateString('es-ES', {
        day: '2-digit', month: 'short',
        hour: '2-digit', minute: '2-digit',
      });
    } catch { return iso.slice(0, 16); }
  },
};

/* ─── Navigation ────────────────────────────────────────────────────────────── */
function showSection(name) {
  // Hide all sections
  document.querySelectorAll('section[id^="section-"]').forEach(s => s.classList.add('hidden'));
  
  // Show selected
  const el = document.getElementById(`section-${name}`);
  if (el) {
    el.classList.remove('hidden');
    // Add fade animation
    el.querySelectorAll('.glass-card, .kpi-card').forEach((card, i) => {
      card.style.animationDelay = `${i * 0.05}s`;
    });
  }

  // Update nav tabs
  document.querySelectorAll('.nav-tab').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.section === name);
  });

  currentSection = name;
}

/* ─── Value Update with Animation ───────────────────────────────────────────── */
function setWithFade(el, value) {
  if (!el) return;
  if (el.textContent !== String(value)) {
    el.textContent = value;
    el.classList.remove('fade-update');
    void el.offsetWidth;
    el.classList.add('fade-update');
  }
}

/* ─── Render KPIs ───────────────────────────────────────────────────────────── */
function renderKPIs(data) {
  const account = data.account || {};
  const balance = parseFloat(account.balance || 0);
  const totalPnl = parseFloat(account.total_pnl || 0);
  const unrealized = parseFloat(data.total_unrealized_pnl || 0);
  const winRate = parseFloat(data.win_rate || 0);

  // Balance
  setWithFade(document.getElementById('kpi-balance'), fmt.usd(balance));

  // Total P&L
  const pnlEl = document.getElementById('kpi-total-pnl');
  if (pnlEl) {
    pnlEl.textContent = fmt.pnl(totalPnl);
  }
  
  const pnlDeltaEl = document.getElementById('kpi-pnl-delta');
  if (pnlDeltaEl) {
    const initialBalance = parseFloat(account.initial_balance || 10000);
    const pct = initialBalance > 0 ? ((balance - initialBalance) / initialBalance * 100).toFixed(1) : '0.0';
    pnlDeltaEl.textContent = `${pct >= 0 ? '+' : ''}${pct}% desde inicio`;
    pnlDeltaEl.className = 'kpi-delta ' + (totalPnl >= 0 ? 'up' : 'down');
  }

  // Unrealized P&L
  const unrEl = document.getElementById('kpi-unrealized');
  if (unrEl) {
    unrEl.textContent = fmt.pnl(unrealized);
  }
  
  const openEl = document.getElementById('kpi-open-count');
  if (openEl) {
    const count = data.open_count || 0;
    openEl.textContent = `${count} ${count === 1 ? 'posición abierta' : 'posiciones abiertas'}`;
  }

  // Win Rate
  setWithFade(document.getElementById('kpi-winrate'), `${winRate.toFixed(1)}%`);
  
  const closedTrades = (data.trades || []).filter(t => t.status === 'closed');
  const tradeCountEl = document.getElementById('kpi-trade-count');
  if (tradeCountEl) {
    tradeCountEl.textContent = `${closedTrades.length} ${closedTrades.length === 1 ? 'trade cerrado' : 'trades cerrados'}`;
  }
}

/* ─── Render Positions ──────────────────────────────────────────────────────── */
function renderPositions(positions) {
  const count = positions ? positions.length : 0;

  ['positions-count', 'overview-pos-count'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = count;
  });

  const tableHTML = count === 0 ? null : `
    <table class="data-table">
      <thead>
        <tr>
          <th>Mercado</th>
          <th>Dirección</th>
          <th class="num">Entrada</th>
          <th class="num">Actual</th>
          <th class="num">Tamaño</th>
          <th class="num">No Realizado</th>
          <th>Apertura</th>
        </tr>
      </thead>
      <tbody>
        ${positions.map(p => {
          const question = p.markets?.question || p.market_id || '—';
          const unr = parseFloat(p.unrealized_pnl || 0);
          return `<tr>
            <td><div class="market-question" title="${question}">${question}</div></td>
            <td><span class="direction-badge ${(p.direction || '').toLowerCase()}">${p.direction || '—'}</span></td>
            <td class="num">${fmt.pct(p.entry_price)}</td>
            <td class="num">${fmt.pct(p.current_price)}</td>
            <td class="num">${fmt.usd(p.size_usd)}</td>
            <td class="num"><span class="${fmt.pnlClass(unr)}">${fmt.pnl(unr)}</span></td>
            <td class="mono">${fmt.dateShort(p.opened_at)}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;

  const emptyHTML = `<div class="empty-state">
    <div class="empty-state-icon">◎</div>
    <div class="empty-state-title">Sin posiciones abiertas</div>
    <div class="empty-state-desc">El bot abrirá posiciones cuando detecte oportunidades con gap ≥ 15%.</div>
  </div>`;

  ['positions-body', 'overview-positions-body'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = count === 0 ? emptyHTML : tableHTML;
  });
}

/* ─── Render Trades ─────────────────────────────────────────────────────────── */
function renderTrades(trades) {
  const count = trades ? trades.length : 0;
  const el = document.getElementById('trades-count');
  if (el) el.textContent = count;

  const body = document.getElementById('trades-body');
  if (!body) return;

  if (count === 0) {
    body.innerHTML = `<div class="empty-state">
      <div class="empty-state-icon">⟳</div>
      <div class="empty-state-title">Sin operaciones registradas</div>
      <div class="empty-state-desc">El historial completo de operaciones aparecerá aquí.</div>
    </div>`;
    return;
  }

  body.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>#</th>
          <th>Mercado</th>
          <th>Dir.</th>
          <th class="num">Entrada</th>
          <th class="num">Salida</th>
          <th class="num">Tamaño</th>
          <th class="num">P&L</th>
          <th>Estado</th>
          <th>Fecha</th>
        </tr>
      </thead>
      <tbody>
        ${trades.map(t => {
          const question = t.markets?.question || t.market_id || '—';
          const pnl = t.pnl != null ? parseFloat(t.pnl) : null;
          const statusText = t.status === 'closed' ? 'cerrado' : 'abierto';
          return `<tr>
            <td class="mono">#${t.id}</td>
            <td><div class="market-question" title="${question}">${question}</div></td>
            <td><span class="direction-badge ${(t.direction || '').toLowerCase()}">${t.direction || '—'}</span></td>
            <td class="num">${fmt.pct(t.entry_price)}</td>
            <td class="num">${t.exit_price != null ? fmt.pct(t.exit_price) : '—'}</td>
            <td class="num">${fmt.usd(t.size_usd)}</td>
            <td class="num">${pnl != null ? `<span class="${fmt.pnlClass(pnl)}">${fmt.pnl(pnl)}</span>` : '—'}</td>
            <td><span class="status-badge ${t.status || 'open'}">${statusText}</span></td>
            <td class="mono">${fmt.dateShort(t.opened_at)}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;
}

/* ─── Render Markets ────────────────────────────────────────────────────────── */
function renderMarkets(markets, analyses) {
  const count = markets ? markets.length : 0;
  const el = document.getElementById('markets-count');
  if (el) el.textContent = count;

  const body = document.getElementById('markets-body');
  if (!body) return;

  if (count === 0) {
    body.innerHTML = `<div class="empty-state">
      <div class="empty-state-icon">◫</div>
      <div class="empty-state-title">Sin mercados escaneados</div>
      <div class="empty-state-desc">El escáner detectará mercados políticos en el próximo ciclo.</div>
    </div>`;
    return;
  }

  const analysisMap = {};
  if (analyses) {
    analyses.forEach(a => {
      if (!analysisMap[a.market_id]) analysisMap[a.market_id] = a;
    });
  }

  body.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>Mercado</th>
          <th class="num">Precio YES</th>
          <th class="num">Volumen</th>
          <th>Gap LLM</th>
          <th>Cierra</th>
          <th>Actualizado</th>
        </tr>
      </thead>
      <tbody>
        ${markets.map(m => {
          const lastAnalysis = analysisMap[m.id];
          const gap = lastAnalysis ? lastAnalysis.gap : null;
          return `<tr>
            <td><div class="market-question" title="${m.question}">${m.question}</div></td>
            <td class="num">${m.last_price != null ? fmt.pct(m.last_price) : '—'}</td>
            <td class="num">$${m.volume ? (parseFloat(m.volume) / 1000).toFixed(0) + 'K' : '—'}</td>
            <td>${gap != null ? fmt.gapBadge(gap) : '<span class="gap-badge low">—</span>'}</td>
            <td class="mono">${fmt.dateShort(m.end_date)}</td>
            <td class="mono">${fmt.dateShort(m.updated_at)}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;
}

/* ─── Render Analyses ───────────────────────────────────────────────────────── */
function renderAnalyses(analyses) {
  const count = analyses ? analyses.length : 0;
  const el = document.getElementById('analyses-count');
  if (el) el.textContent = count;

  const body = document.getElementById('analyses-body');
  if (!body) return;

  if (count === 0) {
    body.innerHTML = `<div class="empty-state">
      <div class="empty-state-icon">◉</div>
      <div class="empty-state-title">Sin análisis registrados</div>
      <div class="empty-state-desc">Los análisis de Groq y Gemini se almacenan aquí para calibración futura.</div>
    </div>`;
    return;
  }

  body.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>Modelo</th>
          <th>Mercado</th>
          <th class="num">Precio Mercado</th>
          <th class="num">Prob LLM</th>
          <th>Gap</th>
          <th>Confianza</th>
          <th>Edge</th>
          <th>Razonamiento</th>
          <th>Fecha</th>
        </tr>
      </thead>
      <tbody>
        ${analyses.map(a => {
          const mid = a.market_id || '';
          const shortId = mid.length > 12 ? mid.slice(0, 12) + '…' : mid;
          return `<tr>
            <td><span class="model-badge">${(a.model || '—').split('/')[1] || a.model}</span></td>
            <td><span class="mono truncate" title="${mid}">${shortId}</span></td>
            <td class="num">${a.market_price_at_analysis != null ? fmt.pct(a.market_price_at_analysis) : '—'}</td>
            <td class="num">${a.probability_yes != null ? fmt.pct(a.probability_yes) : '—'}</td>
            <td>${a.gap != null ? fmt.gapBadge(a.gap) : '—'}</td>
            <td>${a.confidence ? fmt.confidence(a.confidence) : '—'}</td>
            <td style="color: ${a.edge_detected ? 'var(--accent)' : 'var(--text-muted)'}">
              ${a.edge_detected ? '✓' : '—'}
            </td>
            <td><div class="truncate" title="${a.reasoning || ''}">${a.reasoning || '—'}</div></td>
            <td class="mono">${fmt.dateShort(a.timestamp)}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;
}

/* ─── Render News ───────────────────────────────────────────────────────────── */
function renderNews(news) {
  const count = news ? news.length : 0;
  
  ['news-count', 'overview-news-count'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = count;
  });

  const emptyHTML = `<div class="empty-state">
    <div class="empty-state-icon">◌</div>
    <div class="empty-state-title">Sin noticias procesadas</div>
    <div class="empty-state-desc">El monitor RSS procesa noticias con relevancia ≥ 0.3.</div>
  </div>`;

  const newsHTML = count === 0 ? emptyHTML : `
    <div class="news-list">
      ${news.map(n => {
        const score = parseFloat(n.relevance_score || 0);
        const scoreCls = score >= 0.7 ? 'high' : score >= 0.4 ? 'mid' : 'low';
        const scoreLbl = (score * 100).toFixed(0);
        return `<div class="news-item">
          <span class="news-score ${scoreCls}">${scoreLbl}%</span>
          <div class="news-content">
            <div class="news-title">${n.title || '—'}</div>
            <div class="news-meta">
              <span>${n.source || '—'}</span>
              <span>${fmt.dateShort(n.published_at || n.processed_at)}</span>
            </div>
          </div>
        </div>`;
      }).join('')}
    </div>`;

  ['news-body', 'overview-news-body'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = newsHTML;
  });
}

/* ─── Main Refresh ──────────────────────────────────────────────────────────── */
async function refresh() {
  try {
    const res = await fetch('/api/dashboard');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    if (data.error) {
      console.error('[PolyHunt] Error de API:', data.error);
      return;
    }

    lastData = data;

    renderKPIs(data);
    renderPositions(data.positions || []);
    renderTrades(data.trades || []);
    renderMarkets(data.markets || [], data.analyses || []);
    renderAnalyses(data.analyses || []);
    renderNews(data.news || []);

    document.getElementById('last-update').textContent =
      'Actualizado ' + new Date().toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });

  } catch (err) {
    console.error('[PolyHunt] Error de actualización:', err);
    document.getElementById('last-update').textContent = 'Error de conexión';
  }
}

/* ─── Bot Controls ──────────────────────────────────────────────────────────── */
async function botStart() {
  const r = await fetch('/api/bot/start', { method: 'POST' });
  if (r.ok) updateBotStatusUI('running');
}

async function botStop() {
  const r = await fetch('/api/bot/stop', { method: 'POST' });
  if (r.ok) updateBotStatusUI('stopping');
}

async function resetDB() {
  let status;
  try {
    status = await fetch('/api/status').then(r => r.json());
  } catch (e) {
    alert('Error al verificar estado del bot.');
    return;
  }

  if (status.status !== 'paused') {
    alert('El bot debe estar completamente pausado antes de reiniciar.\nEspera a que el estado muestre PAUSADO.');
    return;
  }

  const raw = prompt('Introduce el capital inicial:\n\nMínimo: $100', '10000');
  if (raw === null) return;

  const amount = parseFloat(raw);
  if (isNaN(amount) || amount < 100) {
    alert('Cantidad inválida. Mínimo $100.');
    return;
  }

  if (!confirm(`¿Reiniciar TODOS los datos?\nCapital inicial: $${amount.toLocaleString('es-ES')}`)) return;

  try {
    const res = await fetch('/api/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ balance: amount })
    });
    const data = await res.json();

    if (!res.ok) {
      alert('Error: ' + (data.error || 'Reinicio fallido.'));
      return;
    }

    updateBotStatusUI('paused');
    await refresh();
  } catch (e) {
    alert('Error de conexión durante el reinicio.');
  }
}

function updateBotStatusUI(status) {
  const dot = document.getElementById('status-dot');
  const text = document.getElementById('status-text');
  const btnStart = document.getElementById('btn-start');
  const btnStop = document.getElementById('btn-stop');
  const btnReset = document.getElementById('btn-reset');

  const statusLabels = {
    running: 'ACTIVO',
    stopping: 'DETENIENDO',
    paused: 'PAUSADO'
  };

  if (dot) {
    dot.className = 'status-dot ' + status;
  }

  if (text) {
    text.className = 'status-text ' + status;
    text.textContent = statusLabels[status] || status.toUpperCase();
  }

  if (status === 'running') {
    if (btnStart) btnStart.disabled = true;
    if (btnStop) btnStop.disabled = false;
    if (btnReset) btnReset.style.display = 'none';
  } else if (status === 'stopping') {
    if (btnStart) btnStart.disabled = true;
    if (btnStop) btnStop.disabled = true;
    if (btnReset) btnReset.style.display = 'none';
  } else {
    if (btnStart) btnStart.disabled = false;
    if (btnStop) btnStop.disabled = true;
    if (btnReset) btnReset.style.display = '';
  }
}

/* ─── Console ───────────────────────────────────────────────────────────────── */
function toggleConsole() {
  const body = document.getElementById('console-body');
  const toggle = document.getElementById('console-toggle');
  if (!body || !toggle) return;
  const collapsed = body.classList.toggle('collapsed');
  toggle.classList.toggle('collapsed', collapsed);
  toggle.textContent = collapsed ? '▶' : '▼';
}

async function clearLogs() {
  try {
    const res = await fetch('/api/logs/clear', { method: 'POST' });
    if (res.ok) {
      const out = document.getElementById('console-output');
      if (out) out.textContent = 'Logs limpiados.';
    }
  } catch (e) {
    console.error('[PolyHunt] Error al limpiar logs:', e);
  }
}

/* ─── Status Polling ────────────────────────────────────────────────────────── */
async function refreshStatus() {
  try {
    const d = await fetch('/api/status').then(r => r.json());
    updateBotStatusUI(d.status || (d.running ? 'running' : 'paused'));
  } catch (_) {}
}

/* ─── Log Polling ───────────────────────────────────────────────────────────── */
let _consolePinned = true;

async function refreshLogs() {
  try {
    const d = await fetch('/api/logs').then(r => r.json());
    const out = document.getElementById('console-output');
    if (!out) return;
    out.textContent = (d.lines || []).join('\n') || 'Sin logs todavía...';
    if (_consolePinned) out.scrollTop = out.scrollHeight;
  } catch (_) {}
}

/* ─── Theme Toggle ──────────────────────────────────────────────────────────── */
function getPreferredTheme() {
  const stored = localStorage.getItem('polyhunt-theme');
  if (stored) return stored;
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

function updateThemeIcon(theme) {
  const icon = document.getElementById('theme-icon');
  if (icon) {
    icon.textContent = theme === 'light' ? '☾' : '☀';
  }
}

function setTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('polyhunt-theme', theme);
  updateThemeIcon(theme);
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  const next = current === 'light' ? 'dark' : 'light';
  setTheme(next);
}

function initTheme() {
  const theme = getPreferredTheme();
  setTheme(theme);
}

/* ─── Initialize ────────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  refresh();
  refreshStatus();
  refreshLogs();

  setInterval(refresh, 60_000);
  setInterval(refreshStatus, 5_000);
  setInterval(refreshLogs, 5_000);

  const consoleOut = document.getElementById('console-output');
  if (consoleOut) {
    consoleOut.addEventListener('scroll', function () {
      _consolePinned = this.scrollTop + this.clientHeight >= this.scrollHeight - 20;
    });
  }
});
