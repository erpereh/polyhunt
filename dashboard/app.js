/* ─── Estado global ─────────────────────────────────────────────────────────── */
let lastData   = null;
let currentSection = 'overview';

/* ─── Helpers de formato ────────────────────────────────────────────────────── */
const fmt = {
  usd: n => {
    const v = parseFloat(n);
    if (isNaN(v)) return '$—';
    return '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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
        day: '2-digit', month: '2-digit', year: '2-digit',
        hour: '2-digit', minute: '2-digit',
      });
    } catch { return iso.slice(0, 16); }
  },

  dateShort: iso => {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleDateString('es-ES', {
        day: '2-digit', month: '2-digit',
        hour: '2-digit', minute: '2-digit',
      });
    } catch { return iso.slice(0, 16); }
  },
};

/* ─── Navegación ────────────────────────────────────────────────────────────── */
function showSection(name) {
  // Ocultar todas las secciones
  document.querySelectorAll('section[id^="section-"]').forEach(s => s.classList.add('hidden'));
  // Mostrar la seleccionada
  const el = document.getElementById(`section-${name}`);
  if (el) el.classList.remove('hidden');

  // Actualizar nav activo
  document.querySelectorAll('.nav-item').forEach(item => {
    item.classList.toggle('active', item.dataset.section === name);
  });

  // Actualizar título
  const titles = {
    overview:  'Overview',
    positions: 'Posiciones abiertas',
    trades:    'Historial de trades',
    markets:   'Mercados rastreados',
    analyses:  'Análisis LLM',
    news:      'Noticias procesadas',
  };
  document.getElementById('page-title').textContent = titles[name] || name;
  currentSection = name;
}

/* ─── Actualizar número con fade ────────────────────────────────────────────── */
function setWithFade(el, value) {
  if (!el) return;
  if (el.textContent !== String(value)) {
    el.textContent = value;
    el.classList.remove('fade-update');
    void el.offsetWidth; // reflow para reiniciar animación
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

  // Total P&L con delta
  const pnlEl = document.getElementById('kpi-total-pnl');
  if (pnlEl) {
    pnlEl.textContent = fmt.pnl(totalPnl);
    pnlEl.className = 'kpi-value ' + fmt.pnlClass(totalPnl);
  }
  const pnlDeltaEl = document.getElementById('kpi-pnl-delta');
  if (pnlDeltaEl) {
    const pct = balance > 0 ? ((balance - 10000) / 10000 * 100).toFixed(1) : '0.0';
    pnlDeltaEl.textContent = `${pct >= 0 ? '+' : ''}${pct}% desde el inicio`;
    pnlDeltaEl.className = 'kpi-delta ' + (totalPnl >= 0 ? 'up' : 'down');
  }

  // Unrealized P&L
  const unrEl = document.getElementById('kpi-unrealized');
  if (unrEl) {
    unrEl.textContent = fmt.pnl(unrealized);
    unrEl.className = 'kpi-value ' + fmt.pnlClass(unrealized);
  }
  const openEl = document.getElementById('kpi-open-count');
  if (openEl) {
    openEl.textContent = `${data.open_count || 0} posiciones abiertas`;
  }

  // Win Rate
  setWithFade(document.getElementById('kpi-winrate'), `${winRate.toFixed(1)}%`);
  const closedTrades = (data.trades || []).filter(t => t.status === 'closed');
  const tradeCountEl = document.getElementById('kpi-trade-count');
  if (tradeCountEl) {
    tradeCountEl.textContent = `${closedTrades.length} trades cerrados`;
  }
}

/* ─── Render Posiciones ─────────────────────────────────────────────────────── */
function renderPositions(positions) {
  const count = positions ? positions.length : 0;

  // Actualizar contadores en todos los contextos
  ['positions-count', 'overview-pos-count'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = count;
  });

  const tableHTML = count === 0 ? null : `
    <table class="data-table">
      <thead>
        <tr>
          <th>Mercado</th>
          <th>Dir.</th>
          <th class="num">Entrada</th>
          <th class="num">Precio actual</th>
          <th class="num">Tamaño</th>
          <th class="num">No realizado</th>
          <th>Abierto</th>
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
      <div class="empty-state-title">Sin trades registrados</div>
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
          <th class="num">P&amp;L</th>
          <th>Estado</th>
          <th>Abierto</th>
        </tr>
      </thead>
      <tbody>
        ${trades.map(t => {
          const question = t.markets?.question || t.market_id || '—';
          const pnl = t.pnl != null ? parseFloat(t.pnl) : null;
          return `<tr>
            <td class="mono" style="color:var(--text-secondary)">#${t.id}</td>
            <td><div class="market-question" title="${question}">${question}</div></td>
            <td><span class="direction-badge ${(t.direction || '').toLowerCase()}">${t.direction || '—'}</span></td>
            <td class="num">${fmt.pct(t.entry_price)}</td>
            <td class="num">${t.exit_price != null ? fmt.pct(t.exit_price) : '—'}</td>
            <td class="num">${fmt.usd(t.size_usd)}</td>
            <td class="num">${pnl != null ? `<span class="${fmt.pnlClass(pnl)}">${fmt.pnl(pnl)}</span>` : '—'}</td>
            <td><span class="status-badge ${t.status || 'open'}">${t.status || 'open'}</span></td>
            <td class="mono">${fmt.dateShort(t.opened_at)}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;
}

/* ─── Render Mercados ───────────────────────────────────────────────────────── */
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
      <div class="empty-state-desc">El escáner detectará mercados políticos de Polymarket en el próximo ciclo.</div>
    </div>`;
    return;
  }

  // Crear mapa de último análisis por mercado para mostrar gap
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
          <th>Cierre</th>
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
            <td>${gap != null ? fmt.gapBadge(gap) : '<span style="color:var(--text-muted)">—</span>'}</td>
            <td class="mono">${fmt.dateShort(m.end_date)}</td>
            <td class="mono">${fmt.dateShort(m.updated_at)}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;
}

/* ─── Render Análisis LLM ───────────────────────────────────────────────────── */
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
      <div class="empty-state-desc">Los análisis de Groq y Gemini se guardan aquí para calibración posterior.</div>
    </div>`;
    return;
  }

  body.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>Modelo</th>
          <th>Mercado</th>
          <th class="num">Precio mkt</th>
          <th class="num">Prob. LLM</th>
          <th>Gap</th>
          <th>Confianza</th>
          <th class="num">Edge</th>
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
            <td><span class="mono" title="${mid}" style="color:var(--text-secondary)">${shortId}</span></td>
            <td class="num">${a.market_price_at_analysis != null ? fmt.pct(a.market_price_at_analysis) : '—'}</td>
            <td class="num">${a.probability_yes != null ? fmt.pct(a.probability_yes) : '—'}</td>
            <td>${a.gap != null ? fmt.gapBadge(a.gap) : '—'}</td>
            <td>${a.confidence ? fmt.confidence(a.confidence) : '—'}</td>
            <td class="num" style="color:${a.edge_detected ? 'var(--green)' : 'var(--text-secondary)'}">
              ${a.edge_detected ? '✓' : '—'}
            </td>
            <td><div class="reasoning-text" title="${a.reasoning || ''}">${a.reasoning || '—'}</div></td>
            <td class="mono">${fmt.dateShort(a.timestamp)}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;
}

/* ─── Render Noticias ───────────────────────────────────────────────────────── */
function renderNews(news) {
  const count = news ? news.length : 0;
  ['news-count', 'overview-news-count'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = count;
  });

  const emptyHTML = `<div class="empty-state">
    <div class="empty-state-icon">◌</div>
    <div class="empty-state-title">Sin noticias procesadas</div>
    <div class="empty-state-desc">El monitor RSS procesará noticias con score de relevancia ≥ 0.3.</div>
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

/* ─── Fetch y refresco principal ────────────────────────────────────────────── */
async function refresh() {
  try {
    const res  = await fetch('/api/dashboard');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    if (data.error) {
      console.error('[PolyHunt] API error:', data.error);
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
      'Actualizado ' + new Date().toLocaleTimeString('es-ES');

    // Indicar que el bot está activo
    const dot = document.getElementById('status-dot');
    if (dot) { dot.className = 'status-dot active'; }
    const statusText = document.getElementById('bot-status-text');
    if (statusText) { statusText.textContent = 'Bot activo'; }

  } catch (err) {
    console.error('[PolyHunt] Error al recargar:', err);
    const dot = document.getElementById('status-dot');
    if (dot) { dot.className = 'status-dot paused'; }
    const statusText = document.getElementById('bot-status-text');
    if (statusText) { statusText.textContent = 'Sin conexión'; }
    document.getElementById('last-update').textContent = 'Error de conexión';
  }
}

/* ─── Inicialización ────────────────────────────────────────────────────────── */
refresh();
setInterval(refresh, 60_000); // refresco cada 60 segundos
