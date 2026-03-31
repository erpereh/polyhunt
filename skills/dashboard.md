# Skill: Dashboard PolyHunt — Diseño Premium

## Identidad visual
PolyHunt es un bot de paper trading sofisticado. El dashboard transmite inteligencia,
precisión y control total. Estilo: Dark Financial Terminal.
Inspiración: Bloomberg Terminal + Linear.app.
Oscuro, denso de información, perfectamente legible. Cada píxel tiene un propósito.

## IMPORTANTE: Badge de simulación
El dashboard SIEMPRE debe mostrar un badge visible que diga "MODO SIMULACIÓN" o "Paper Trading".
Ejemplo de badge:
```html
<div class="sim-badge">⬡ MODO SIMULACIÓN</div>
```
```css
.sim-badge {
  background: rgba(251,191,36,0.1);
  color: var(--warning);
  border: 1px solid rgba(251,191,36,0.3);
  padding: 3px 10px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.05em;
}
```

## Tipografía — OBLIGATORIA
- Números/datos: JetBrains Mono
- Textos/labels: DM Sans
- NUNCA Inter, Roboto, Arial ni system fonts
```html
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;1,9..40,400&display=swap" rel="stylesheet">
```

## Paleta exacta
```css
:root {
  --bg-void: #060810;
  --bg-base: #0c1018;
  --bg-surface: #111827;
  --bg-elevated: #1a2234;
  --green: #52b788;
  --green-bright: #6ee7b7;
  --green-dim: rgba(82, 183, 136, 0.10);
  --green-border: rgba(82, 183, 136, 0.22);
  --green-glow: 0 0 24px rgba(82, 183, 136, 0.12);
  --profit: #4ade80;
  --loss: #f87171;
  --warning: #fbbf24;
  --text-primary: #f1f5f9;
  --text-secondary: #64748b;
  --text-muted: #2d3748;
  --border: rgba(255, 255, 255, 0.055);
  --border-hover: rgba(255, 255, 255, 0.10);
  --border-accent: rgba(82, 183, 136, 0.28);
  --font-mono: 'JetBrains Mono', monospace;
  --font-sans: 'DM Sans', sans-serif;
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 12px;
}
```

## Layout
```css
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { font-size: 14px; }

body {
  background-color: var(--bg-void);
  background-image:
    radial-gradient(ellipse 80% 60% at 15% 40%, rgba(82,183,136,0.028) 0%, transparent 70%),
    radial-gradient(ellipse 60% 50% at 85% 15%, rgba(82,183,136,0.018) 0%, transparent 60%);
  font-family: var(--font-sans);
  color: var(--text-primary);
  min-height: 100vh;
  line-height: 1.5;
}

.app {
  display: grid;
  grid-template-columns: 216px 1fr;
  min-height: 100vh;
}

.main { padding: 28px 32px; overflow-y: auto; max-width: 1400px; }
```

## Sidebar
```css
.sidebar {
  background: var(--bg-base);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  height: 100vh;
  padding: 20px 0;
}

.logo {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 0 20px 24px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 8px;
}

.logo-symbol { font-family: var(--font-mono); font-size: 18px; color: var(--green); }
.logo-name { font-family: var(--font-mono); font-size: 15px; font-weight: 600; }
.logo-version { font-family: var(--font-mono); font-size: 10px; color: var(--text-secondary); margin-left: auto; }

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 20px;
  color: var(--text-secondary);
  text-decoration: none;
  font-size: 13px;
  border-left: 2px solid transparent;
  transition: all 0.15s ease;
  cursor: pointer;
}

.nav-item:hover { color: var(--text-primary); background: rgba(255,255,255,0.03); }
.nav-item.active { color: var(--green); background: var(--green-dim); border-left-color: var(--green); font-weight: 500; }

.sidebar-footer {
  padding: 16px 20px;
  border-top: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: auto;
}

.status-dot { width: 7px; height: 7px; border-radius: 50%; }
.status-dot.active { background: var(--profit); animation: pulse 2.5s ease infinite; }
.status-dot.paused { background: var(--text-secondary); }

@keyframes pulse {
  0%   { box-shadow: 0 0 0 0 rgba(74,222,128,0.5); }
  70%  { box-shadow: 0 0 0 7px rgba(74,222,128,0); }
  100% { box-shadow: 0 0 0 0 rgba(74,222,128,0); }
}
```

## KPI Cards
```css
.kpi-grid {
  display: grid;
  grid-template-columns: 2fr 1fr 1fr 1fr;
  gap: 14px;
  margin-bottom: 28px;
}

.kpi-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 20px 22px;
  position: relative;
  overflow: hidden;
  transition: border-color 0.2s, transform 0.15s;
}

.kpi-card::after {
  content: '';
  position: absolute;
  top: 0; left: 10%; right: 10%;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--green-border), transparent);
  opacity: 0;
  transition: opacity 0.3s;
}

.kpi-card:hover { border-color: var(--border-hover); transform: translateY(-1px); }
.kpi-card:hover::after { opacity: 1; }

.kpi-card.featured {
  border-color: var(--green-border);
  background: linear-gradient(140deg, var(--bg-surface) 60%, rgba(82,183,136,0.06));
  box-shadow: var(--green-glow), inset 0 1px 0 rgba(82,183,136,0.08);
}
.kpi-card.featured::after { opacity: 0.6; }

.kpi-label { font-size: 10.5px; font-weight: 500; letter-spacing: 0.07em; text-transform: uppercase; color: var(--text-secondary); margin-bottom: 10px; }
.kpi-value { font-family: var(--font-mono); font-size: 1.8rem; font-weight: 700; line-height: 1; margin-bottom: 10px; letter-spacing: -0.02em; }
.kpi-delta { font-family: var(--font-mono); font-size: 12px; }
.kpi-delta.up { color: var(--profit); }
.kpi-delta.down { color: var(--loss); }
.kpi-delta.neutral { color: var(--text-secondary); }
```

## Secciones y tablas
```css
.section {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  margin-bottom: 20px;
  overflow: hidden;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}

.section-title { font-size: 13px; font-weight: 600; letter-spacing: -0.01em; }
.section-count {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--bg-elevated);
  padding: 2px 8px;
  border-radius: 20px;
  border: 1px solid var(--border);
}

.data-table { width: 100%; border-collapse: collapse; }
.data-table th {
  text-align: left;
  padding: 10px 20px;
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.09em;
  text-transform: uppercase;
  color: var(--text-secondary);
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.data-table td {
  padding: 13px 20px;
  border-bottom: 1px solid rgba(255,255,255,0.025);
  vertical-align: middle;
  font-size: 13px;
}
.data-table tr:last-child td { border-bottom: none; }
.data-table tr:hover td { background: rgba(255,255,255,0.018); }
.data-table .num { font-family: var(--font-mono); font-size: 12px; text-align: right; }
.data-table .mono { font-family: var(--font-mono); font-size: 12px; }
```

## Componentes
```css
.gap-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 7px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
}
.gap-badge.high { background: var(--green-dim); color: var(--green); border: 1px solid var(--green-border); }
.gap-badge.medium { background: rgba(251,191,36,0.08); color: var(--warning); border: 1px solid rgba(251,191,36,0.25); }
.gap-badge.low { background: transparent; color: var(--text-secondary); border: 1px solid var(--border); }

.direction-badge { display: inline-block; padding: 2px 8px; border-radius: var(--radius-sm); font-family: var(--font-mono); font-size: 11px; font-weight: 600; }
.direction-badge.yes { background: rgba(74,222,128,0.1); color: var(--profit); border: 1px solid rgba(74,222,128,0.2); }
.direction-badge.no { background: rgba(248,113,113,0.1); color: var(--loss); border: 1px solid rgba(248,113,113,0.2); }

.confidence-bar { display: inline-flex; gap: 3px; align-items: center; }
.cb-seg { width: 14px; height: 3px; border-radius: 2px; background: var(--bg-elevated); }
.cb-seg.on { background: var(--green); }
/* high: 3 on | medium: 2 on | low: 1 on */

.pnl-positive { color: var(--profit); font-family: var(--font-mono); font-size: 12px; }
.pnl-negative { color: var(--loss); font-family: var(--font-mono); font-size: 12px; }
.pnl-zero { color: var(--text-secondary); font-family: var(--font-mono); font-size: 12px; }

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 48px 24px;
  color: var(--text-secondary);
  text-align: center;
  gap: 8px;
}
.empty-state-icon { font-size: 28px; opacity: 0.3; }
.empty-state-title { font-size: 14px; font-weight: 500; }
.empty-state-desc { font-size: 12px; color: var(--text-muted); max-width: 280px; line-height: 1.6; }
```

## Backend Flask
```python
from flask import Flask, jsonify
from core.paper_trader import get_dashboard_data
import logging

app = Flask(__name__, static_folder='dashboard', static_url_path='')
logger = logging.getLogger(__name__)

@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/api/dashboard')
def dashboard():
    try:
        return jsonify(get_dashboard_data())
    except Exception as e:
        logger.error(f"Error en /api/dashboard: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
```

## Actualización en tiempo real (app.js)
```javascript
async function refresh() {
  try {
    const data = await fetch('/api/dashboard').then(r => r.json());
    if (data.error) { console.error('API error:', data.error); return; }
    renderKPIs(data);
    renderPositions(data.positions);
    renderTrades(data.trades);
    renderMarkets(data.markets, data.analyses);
    renderNews(data.news);
    document.getElementById('last-update').textContent =
      'Actualizado ' + new Date().toLocaleTimeString('es-ES');
  } catch(e) { console.error(e); }
}
refresh();
setInterval(refresh, 60000);

const fmt = {
  usd: n => '$' + parseFloat(n).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2}),
  pct: n => (parseFloat(n)*100).toFixed(1)+'%',
  pnl: n => { const v=parseFloat(n); return (v>=0?'+':'')+fmt.usd(v); },
  pnlClass: n => parseFloat(n)>0?'pnl-positive':parseFloat(n)<0?'pnl-negative':'pnl-zero',
  gapBadge: gap => {
    const g=parseFloat(gap)*100;
    const c=g>=15?'high':g>=8?'medium':'low';
    return `<span class="gap-badge ${c}">${g.toFixed(1)}%</span>`;
  },
  confidence: lvl => {
    const n=lvl==='high'?3:lvl==='medium'?2:1;
    return `<span class="confidence-bar">${[1,2,3].map(i=>`<span class="cb-seg ${i<=n?'on':''}"></span>`).join('')}</span>`;
  }
};
```

## Reglas absolutas de diseño — sin excepciones
- TODOS los números financieros y porcentajes: font-family var(--font-mono)
- Verde #52b788 solo para: elemento featured, item nav activo, gap alto, borde card importante
- Sin border-radius mayor de 12px en ningún elemento
- Sombras solo en .kpi-card.featured y modales/tooltips
- Todo texto secundario en var(--text-secondary) — nunca blanco puro para labels
- Las tablas son el corazón del dashboard — más atención que cualquier otro elemento
- El dashboard debe verse impresionante tanto con datos como sin ellos (empty states cuidados)
- Animaciones solo en: status-dot pulse, kpi-card hover transform, line hover opacity
- SIEMPRE mostrar el badge "MODO SIMULACIÓN" / "Paper Trading" visible
