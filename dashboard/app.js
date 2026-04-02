/* ═══════════════════════════════════════════════════════════════════════════
   POLYHUNT — Dashboard Application Logic
   Glass Morphism Edition — Spanish UI
   ═══════════════════════════════════════════════════════════════════════════ */

/* ─── State ─────────────────────────────────────────────────────────────────── */
let lastData = null;
let currentSection = 'overview';
let settingsKeys = [];
let modalState = null;
let modalTriggerEl = null;
let logFilters = {
  level: 'ALL',
  source: 'ALL',
  limit: '100',
  q: '',
};

function getModalEls() {
  return {
    root: document.getElementById('app-modal'),
    title: document.getElementById('app-modal-title'),
    body: document.getElementById('app-modal-body'),
    error: document.getElementById('app-modal-error'),
    confirm: document.getElementById('app-modal-confirm'),
    cancel: document.getElementById('app-modal-cancel'),
    close: document.getElementById('app-modal-close'),
  };
}

function setModalError(message = '') {
  const { error } = getModalEls();
  if (!error) return;
  if (!message) {
    error.textContent = '';
    error.classList.add('hidden');
    return;
  }
  error.textContent = message;
  error.classList.remove('hidden');
}

function setModalBusy(isBusy) {
  const { confirm, cancel, close } = getModalEls();
  if (!confirm || !cancel || !close) return;
  confirm.disabled = isBusy;
  cancel.disabled = isBusy;
  close.disabled = isBusy;
}

function closeModal() {
  const { root, body } = getModalEls();
  if (!root || !body) return;
  if (modalState && modalState.busy) return;
  root.classList.add('hidden');
  body.innerHTML = '';
  setModalError('');
  modalState = null;
  if (modalTriggerEl && typeof modalTriggerEl.focus === 'function') {
    modalTriggerEl.focus();
  }
}

async function confirmModalAction() {
  if (!modalState || modalState.busy) return;
  const { onConfirm, collectData } = modalState;
  if (!onConfirm) {
    closeModal();
    return;
  }
  try {
    modalState.busy = true;
    setModalBusy(true);
    setModalError('');
    const payload = collectData ? collectData() : undefined;
    await onConfirm(payload);
    if (modalState) {
      modalState.busy = false;
      setModalBusy(false);
    }
    closeModal();
  } catch (e) {
    setModalError(e?.message || 'Ha ocurrido un error.');
  } finally {
    if (modalState) {
      modalState.busy = false;
      setModalBusy(false);
    }
  }
}

function openModal({ title, bodyHTML, confirmText = 'Aceptar', cancelText = 'Cancelar', onConfirm, collectData, wide = false }) {
  const { root, title: titleEl, body, confirm, cancel, close } = getModalEls();
  if (!root || !titleEl || !body || !confirm || !cancel || !close) return;

  modalTriggerEl = document.activeElement;
  modalState = { onConfirm, collectData, busy: false };
  titleEl.textContent = title || 'Confirmar acción';
  body.innerHTML = bodyHTML || '';
  confirm.textContent = confirmText;
  cancel.textContent = cancelText;
  setModalError('');
  setModalBusy(false);
  root.classList.remove('hidden');

  const firstInput = body.querySelector('input, textarea, select, button');
  if (firstInput && typeof firstInput.focus === 'function') {
    firstInput.focus();
  } else {
    confirm.focus();
  }
}

function initModal() {
  const { root, confirm, cancel, close } = getModalEls();
  if (!root || !confirm || !cancel || !close) return;

  confirm.addEventListener('click', confirmModalAction);
  cancel.addEventListener('click', closeModal);
  close.addEventListener('click', closeModal);

  root.addEventListener('click', (e) => {
    const target = e.target;
    if (target && target.getAttribute && target.getAttribute('data-close') === 'overlay') {
      closeModal();
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modalState) {
      closeModal();
    }
    if (e.key === 'Enter' && modalState) {
      const tag = (document.activeElement?.tagName || '').toLowerCase();
      if (tag !== 'textarea') {
        e.preventDefault();
        confirmModalAction();
      }
    }
  });
}

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
      <div class="empty-state-desc">Los análisis de Cerebras y Groq se almacenan aquí para calibración futura.</div>
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
    if (currentSection === 'settings') {
      await refreshKeys();
    }

    document.getElementById('last-update').textContent =
      'Actualizado ' + new Date().toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });

  } catch (err) {
    console.error('[PolyHunt] Error de actualización:', err);
    document.getElementById('last-update').textContent = 'Error de conexión';
  }
}

async function refreshKeys() {
  try {
    const r = await fetch('/api/settings/keys');
    if (!r.ok) return;
    const d = await r.json();
    settingsKeys = d.keys || [];
    renderKeysByService('cerebras');
    renderKeysByService('groq');
  } catch (e) {
    console.error('[PolyHunt] Error cargando keys:', e);
  }
}

function renderKeysByService(service) {
  const container = document.getElementById(`keys-${service}`);
  if (!container) return;
  const items = settingsKeys.filter(k => k.service === service);
  if (items.length === 0) {
    container.innerHTML = `<div class="empty-state" style="min-height:120px;padding:16px;">
      <div class="empty-state-title">Sin keys</div>
      <div class="empty-state-desc">Añade una key para habilitar ${service}.</div>
    </div>`;
    return;
  }

  container.innerHTML = items.map(k => {
    let statusCls = 'disabled';
    let statusText = 'Deshabilitada';
    if (k.is_enabled && k.in_cooldown) {
      statusCls = 'cooldown';
      const mins = cooldownMinutes(k.cooldown_until);
      statusText = `Cooldown - ${mins}min`;
    } else if (k.is_enabled) {
      statusCls = 'active';
      statusText = 'Activa';
    }
    return `<div class="key-row">
      <div class="key-main">
        <div class="key-label">${k.label || '(sin label)'}</div>
        <div class="key-mask">${k.masked || ('****' + (k.last_4 || '????'))}</div>
      </div>
      <div class="key-metrics">calls:${k.calls_today || 0} · tok:${k.tokens_today || 0}</div>
      <span class="key-badge ${statusCls}">${statusText}</span>
      <div class="key-actions">
        <button class="toggle-btn" onclick="toggleKey(${k.id}, ${k.is_enabled ? 'false' : 'true'})">${k.is_enabled ? 'Desactivar' : 'Activar'}</button>
        <button class="delete-btn" onclick="deleteKey(${k.id})">Eliminar</button>
      </div>
    </div>`;
  }).join('');
}

function cooldownMinutes(cooldownUntil) {
  if (!cooldownUntil) return 0;
  const dt = new Date(cooldownUntil);
  const ms = dt.getTime() - Date.now();
  return Math.max(0, Math.ceil(ms / 60000));
}

async function openAddKey(service) {
  openModal({
    title: `Añadir API key (${service})`,
    confirmText: 'Guardar key',
    cancelText: 'Cancelar',
    bodyHTML: `
      <div class="modal-form-group">
        <label class="modal-form-label" for="modal-key-label">Label</label>
        <input id="modal-key-label" class="modal-input" type="text" maxlength="120" value="${service}-key" />
      </div>
      <div class="modal-form-group">
        <label class="modal-form-label" for="modal-key-value">API Key</label>
        <input id="modal-key-value" class="modal-input" type="password" autocomplete="off" />
        <div class="modal-hint">Se almacenará de forma segura y en la interfaz solo se muestra el final (****1234).</div>
      </div>
    `,
    collectData: () => {
      const label = (document.getElementById('modal-key-label')?.value || '').trim();
      const keyValue = (document.getElementById('modal-key-value')?.value || '').trim();
      if (!keyValue) throw new Error('La API key es obligatoria.');
      return { label, keyValue };
    },
    onConfirm: async ({ label, keyValue }) => {
      const r = await fetch('/api/settings/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service, label, key_value: keyValue })
      });
      const d = await r.json();
      if (!r.ok || !d.ok) throw new Error(d.error || 'No se pudo añadir la key.');
      await refreshKeys();
    }
  });
}

async function deleteKey(id) {
  openModal({
    title: 'Eliminar key',
    confirmText: 'Eliminar',
    cancelText: 'Cancelar',
    bodyHTML: `<div>Esta acción quitará la key del proveedor y no se podrá deshacer.</div>`,
    onConfirm: async () => {
      const r = await fetch(`/api/settings/keys/${id}`, { method: 'DELETE' });
      const d = await r.json();
      if (!r.ok || !d.ok) throw new Error(d.error || 'No se pudo eliminar.');
      await refreshKeys();
    }
  });
}

async function toggleKey(id, isEnabled) {
  try {
    const r = await fetch(`/api/settings/keys/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_enabled: isEnabled })
    });
    const d = await r.json();
    if (!r.ok || !d.ok) {
      openModal({
        title: 'No se pudo actualizar',
        confirmText: 'Entendido',
        cancelText: 'Cerrar',
        bodyHTML: `<div>${d.error || 'No se pudo actualizar el estado de la key.'}</div>`,
        onConfirm: async () => {}
      });
      return;
    }
    await refreshKeys();
  } catch (e) {
    openModal({
      title: 'Error de conexión',
      confirmText: 'Entendido',
      cancelText: 'Cerrar',
      bodyHTML: '<div>Error de conexión al actualizar key.</div>',
      onConfirm: async () => {}
    });
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
    openModal({
      title: 'Error de conexión',
      confirmText: 'Entendido',
      cancelText: 'Cerrar',
      bodyHTML: '<div>No se pudo verificar el estado del bot.</div>',
      onConfirm: async () => {}
    });
    return;
  }

  if (status.status !== 'paused') {
    openModal({
      title: 'Bot en ejecución',
      confirmText: 'Entendido',
      cancelText: 'Cerrar',
      bodyHTML: '<div>El bot debe estar completamente <span class="modal-emphasis">PAUSADO</span> antes de reiniciar la base de datos.</div>',
      onConfirm: async () => {}
    });
    return;
  }

  openModal({
    title: 'Reiniciar base de datos',
    confirmText: 'Reiniciar BD',
    cancelText: 'Cancelar',
    bodyHTML: `
      <div>Se borrarán <span class="modal-emphasis">todos los datos</span> de paper trading (trades, posiciones, análisis, noticias y snapshots).</div>
      <div class="modal-form-group">
        <label class="modal-form-label" for="modal-reset-balance">Capital inicial (USD)</label>
        <input id="modal-reset-balance" class="modal-input" type="text" inputmode="decimal" autocomplete="off" value="10000" />
        <div class="modal-hint">Mínimo permitido: $100</div>
      </div>
    `,
    collectData: () => {
      const raw = document.getElementById('modal-reset-balance')?.value;
      const amount = parseFloat(raw);
      if (isNaN(amount) || amount < 100) {
        throw new Error('Cantidad inválida. El mínimo es $100.');
      }
      if (!isFinite(amount)) {
        throw new Error('Cantidad inválida.');
      }
      return { amount };
    },
    onConfirm: async ({ amount }) => {
      const res = await fetch('/api/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ balance: amount })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Reinicio fallido.');
      updateBotStatusUI('paused');
      await refresh();
    }
  });
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
      if (out) out.innerHTML = '<div class="log-message">Logs limpiados.</div>';
    }
  } catch (e) {
    console.error('[PolyHunt] Error al limpiar logs:', e);
  }
}

function formatLogLine(line) {
  const safe = String(line || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  const tsMatch = safe.match(/^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})/);
  const levelMatch = safe.match(/\[(INFO|WARNING|ERROR)\]/);
  const sourceMatch = safe.match(/\[(SCAN|LLM|QUEUE|TRADE|RISK|KEYS|API|ERR)\]/);

  const time = tsMatch ? tsMatch[1] : '--:--:--';
  const level = levelMatch ? levelMatch[1] : 'INFO';
  const source = sourceMatch ? sourceMatch[1] : '';
  const levelClass = level.toLowerCase();

  return `<div class="log-line">
    <span class="log-time">${time}</span>
    <span class="log-level ${levelClass}">${level}</span>
    <span class="log-message">${source ? `<span class="log-source">[${source}]</span> ` : ''}${safe}</span>
  </div>`;
}

function buildLogQuery() {
  const params = new URLSearchParams();
  params.set('limit', logFilters.limit || '100');
  params.set('level', logFilters.level || 'ALL');
  params.set('source', logFilters.source || 'ALL');
  if (logFilters.q) params.set('q', logFilters.q);
  return params.toString();
}

function initLogFilters() {
  const levelEl = document.getElementById('log-level-filter');
  const sourceEl = document.getElementById('log-source-filter');
  const limitEl = document.getElementById('log-limit-filter');
  const searchEl = document.getElementById('log-search-filter');
  const autoEl = document.getElementById('log-autoscroll-toggle');

  if (!levelEl || !sourceEl || !limitEl || !searchEl || !autoEl) return;

  levelEl.addEventListener('change', () => {
    logFilters.level = levelEl.value;
    refreshLogs();
  });
  sourceEl.addEventListener('change', () => {
    logFilters.source = sourceEl.value;
    refreshLogs();
  });
  limitEl.addEventListener('change', () => {
    logFilters.limit = limitEl.value;
    refreshLogs();
  });
  let searchDebounce = null;
  searchEl.addEventListener('input', () => {
    if (searchDebounce) clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => {
      logFilters.q = searchEl.value.trim();
      refreshLogs();
    }, 250);
  });
  autoEl.addEventListener('change', () => {
    _consolePinned = autoEl.checked;
  });
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
    const d = await fetch('/api/logs?' + buildLogQuery()).then(r => r.json());
    const out = document.getElementById('console-output');
    if (!out) return;
    const lines = d.lines || [];
    if (!lines.length) {
      out.innerHTML = '<div class="log-message">Sin logs para los filtros actuales...</div>';
      return;
    }
    out.innerHTML = lines.map(formatLogLine).join('');
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
  initModal();
  initLogFilters();
  refresh();
  refreshStatus();
  refreshLogs();
  refreshKeys();

  setInterval(refresh, 60_000);
  setInterval(refreshStatus, 5_000);
  setInterval(refreshLogs, 5_000);
  setInterval(refreshKeys, 10_000);

  const consoleOut = document.getElementById('console-output');
  if (consoleOut) {
    consoleOut.addEventListener('scroll', function () {
      _consolePinned = this.scrollTop + this.clientHeight >= this.scrollHeight - 20;
    });
  }
});
