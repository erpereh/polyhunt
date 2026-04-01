# PolyHunt — Dashboard Glass Morphism Edition

## Archivos
- `dashboard/index.html` — Layout HTML con navegacion superior
- `dashboard/style.css` — Sistema de diseno Glass Morphism
- `dashboard/app.js` — Logica de refresh y render (UI en espanol)
- `server.py` — Flask server que sirve el dashboard

---

## Identidad Visual

PolyHunt es un bot de paper trading sofisticado. El dashboard transmite inteligencia,
precision y control con una estetica **Glass Morphism** moderna.

**Inspiracion:** Linear.app + Stripe Dashboard
**Estetica:** Fondos oscuros neutros, tarjetas con efecto cristal, micro-animaciones sutiles
**Idioma:** Todo el UI esta en espanol

---

## Paleta de Colores — CSS Variables

```css
:root {
  /* Fondos oscuros neutros (sin purpura intenso) */
  --bg-void: #09090b;
  --bg-gradient-1: #0c0c10;
  --bg-gradient-2: #0f0f14;
  --bg-gradient-3: #0a0c12;

  /* Superficies Glass */
  --glass-bg: rgba(255, 255, 255, 0.035);
  --glass-bg-hover: rgba(255, 255, 255, 0.065);
  --glass-bg-active: rgba(255, 255, 255, 0.09);
  --glass-border: rgba(255, 255, 255, 0.10);
  --glass-border-hover: rgba(255, 255, 255, 0.18);
  --glass-blur: 20px;
  --glass-blur-heavy: 40px;

  /* Acento — Slate Azulado (elegante y suave) */
  --accent: #94a3b8;
  --accent-light: #cbd5e1;
  --accent-dim: rgba(148, 163, 184, 0.12);
  --accent-glow: 0 0 20px rgba(148, 163, 184, 0.15);
  --accent-border: rgba(148, 163, 184, 0.25);

  /* Secundario — Violeta suave */
  --violet: #a78bfa;
  --violet-dim: rgba(167, 139, 250, 0.10);
  --violet-glow: 0 0 20px rgba(167, 139, 250, 0.12);

  /* Colores semanticos */
  --profit: #4ade80;
  --profit-dim: rgba(74, 222, 128, 0.12);
  --profit-glow: 0 0 15px rgba(74, 222, 128, 0.25);
  --loss: #f87171;
  --loss-dim: rgba(248, 113, 113, 0.12);
  --loss-glow: 0 0 15px rgba(248, 113, 113, 0.25);
  --warning: #fbbf24;
  --warning-dim: rgba(251, 191, 36, 0.12);

  /* Jerarquia de texto */
  --text-primary: rgba(255, 255, 255, 0.92);
  --text-secondary: rgba(255, 255, 255, 0.55);
  --text-muted: rgba(255, 255, 255, 0.28);

  /* Tipografia */
  --font-display: 'Outfit', sans-serif;
  --font-mono: 'IBM Plex Mono', monospace;

  /* Transiciones */
  --transition-fast: 0.15s cubic-bezier(0.4, 0, 0.2, 1);
  --transition-smooth: 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  --transition-spring: 0.5s cubic-bezier(0.34, 1.56, 0.64, 1);
}
```

---

## Tipografia — OBLIGATORIA

- **Display/Textos:** Outfit (moderna, geometrica)
- **Numeros/Datos:** IBM Plex Mono (tecnico, legible)
- **NUNCA usar:** Inter, Roboto, Arial, system-ui

Import en `<head>`:
```html
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
```

---

## Layout Global

### Estructura Principal
- **Navegacion superior** (no sidebar) con tabs horizontales
- **Bento Grid** de 12 columnas para contenido
- **Max-width:** 1600px centrado

```css
.app {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.topnav {
  position: sticky;
  top: 0;
  z-index: 100;
  background: var(--glass-bg);
  backdrop-filter: blur(var(--glass-blur-heavy));
  border-bottom: 1px solid var(--glass-border);
}

.bento-grid {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  gap: var(--space-md);
}
```

---

## Efecto Glass Morphism

Todas las tarjetas usan este patron:

```css
.glass-card {
  background: var(--glass-bg);
  backdrop-filter: blur(var(--glass-blur));
  -webkit-backdrop-filter: blur(var(--glass-blur));
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-lg);
}

.glass-card:hover {
  background: var(--glass-bg-hover);
  border-color: var(--glass-border-hover);
}
```

---

## Sistema de Micro-Animaciones

### Animaciones Globales Definidas

```css
/* Fade-slide entrada */
@keyframes fadeSlideIn {
  from { opacity: 0; transform: translateY(12px); }
  to { opacity: 1; transform: translateY(0); }
}

/* Float suave para featured card */
@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-3px); }
}

/* Glow pulse para status dots */
@keyframes glowPulse {
  0%, 100% { box-shadow: 0 0 4px currentColor; opacity: 1; }
  50% { box-shadow: 0 0 12px currentColor, 0 0 20px currentColor; opacity: 0.85; }
}

/* Mesh float para el fondo */
@keyframes meshFloat {
  0%, 100% { transform: translate(0, 0) scale(1); }
  33% { transform: translate(1%, -0.5%) scale(1.01); }
  66% { transform: translate(-0.5%, 1%) scale(0.99); }
}
```

### Transiciones Base (Globales)
```css
button, a, .nav-tab, .glass-card, .kpi-card, .news-item, .data-table tr {
  transition: 
    background-color var(--transition-fast),
    border-color var(--transition-fast),
    transform var(--transition-fast),
    box-shadow var(--transition-smooth);
}
```

### Staggered Entrance
```css
.kpi-card:nth-child(1) { animation-delay: 0.05s; }
.kpi-card:nth-child(2) { animation-delay: 0.1s; }
.kpi-card:nth-child(3) { animation-delay: 0.15s; }
.kpi-card:nth-child(4) { animation-delay: 0.2s; }
```

---

## Componentes Implementados

### `.kpi-card` / `.kpi-card.featured`
Grid 4 columnas. Card featured tiene gradiente accent+violet y animacion float.
Hover: translateY(-3px) + box-shadow intensificado.

### `.nav-tab`
Tabs horizontales en la navegacion superior.
- Activo: fondo `--accent-dim`, texto `--accent-light`
- Hover: translateY(-1px) + fondo hover

### `.status-dot`
- `.running` → verde con glowPulse 2.5s
- `.stopping` → amarillo con glowPulse 1.2s
- `.paused` → gris muted

### `.data-table`
Tablas con hover en filas. Headers 11px uppercase.
Clase `.num` = monoespaciado alineado derecha.

### `.gap-badge`
```css
.high   → slate accent (gap >= 15%)
.medium → warning amarillo (gap >= 8%)
.low    → muted gris (gap < 8%)
```

### `.direction-badge`
- `.yes` → verde profit
- `.no` → rojo loss

### `.confidence-bar`
3 segmentos de 16x4px. Segmentos activos usan `--accent`.

### `.sim-badge`
Badge "SIM" amarillo siempre visible — recuerda que es paper trading.

### `.empty-state`
Estado vacio con icono, titulo y descripcion centrados.

---

## Flask Server (server.py)

```python
app = Flask(__name__, static_folder="dashboard", static_url_path="")

GET /              → app.send_static_file("index.html")
GET /api/dashboard → get_dashboard_data() como JSON
GET /api/status    → bot_status dict
POST /api/bot/start → Iniciar bot
POST /api/bot/stop  → Detener bot
POST /api/reset     → Reiniciar base de datos
GET /api/logs       → Ultimas lineas de log
```

---

## app.js — Logica de Refresh

### Idioma
**Todo el UI dinamico esta en espanol:**
- Fechas: `toLocaleDateString('es-ES', ...)`
- Numeros: `toLocaleString('es-ES', ...)`
- Textos: "posiciones abiertas", "trades cerrados", etc.
- Alertas: Mensajes en espanol

### Ciclo de Actualizacion
```javascript
refresh();                     // llamada inicial
setInterval(refresh, 60_000);  // cada 60 segundos
setInterval(refreshStatus, 5_000);  // estado del bot
setInterval(refreshLogs, 5_000);    // logs de consola
```

### Objeto `fmt` — Helpers de Formato
```javascript
fmt.usd(n)          // → '$1.234,56' (formato espanol)
fmt.pct(n)          // → '72.3%'
fmt.pnl(n)          // → '+$45,00' o '-$12,30'
fmt.pnlClass(n)     // → 'pnl-positive' | 'pnl-negative' | 'pnl-zero'
fmt.gapBadge(gap)   // → '<span class="gap-badge high">18.5%</span>'
fmt.confidence(lvl) // → '<span class="confidence-bar">...</span>'
fmt.date(iso)       // → '01 abr 26, 14:30'
fmt.dateShort(iso)  // → '01 abr, 14:30'
```

### Funciones de Render
- `renderKPIs(data)` — KPI cards con fade en cambios
- `renderPositions(positions)` — tabla o empty state
- `renderTrades(trades)` — historial con status badge
- `renderMarkets(markets, analyses)` — mercados con gap
- `renderAnalyses(analyses)` — analisis LLM con reasoning
- `renderNews(news)` — noticias con score de relevancia

---

## Secciones del Dashboard

| Seccion    | ID HTML             | Contenido                                    |
|------------|---------------------|----------------------------------------------|
| Resumen    | `section-overview`  | KPI grid + posiciones + noticias recientes   |
| Posiciones | `section-positions` | Tabla completa de posiciones abiertas        |
| Historial  | `section-trades`    | Ultimos 50 trades con P&L                    |
| Mercados   | `section-markets`   | Mercados escaneados con ultimo gap LLM       |
| Analisis   | `section-analyses`  | Analisis LLM con reasoning                   |
| Noticias   | `section-news`      | Noticias procesadas con score de relevancia  |

---

## Favicon

SVG inline con simbolo ◈ y gradiente:

```html
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,...">
```

El simbolo usa gradiente de `--accent` (#94a3b8) a `--violet` (#a78bfa).

---

## Responsive Breakpoints

```css
@media (max-width: 1200px)  /* Tablet landscape */
@media (max-width: 900px)   /* Tablet portrait */
@media (max-width: 600px)   /* Mobile */
```

En mobile:
- KPI grid → 2 columnas, luego 1
- Bento grid → 1 columna
- Nav tabs → scroll horizontal
- Tablas → scroll horizontal

---

## Reglas de Diseno — Sin Excepciones

1. **Numeros:** SIEMPRE `font-family: var(--font-mono)`
2. **Acento slate:** Solo para elementos destacados, no decorativamente
3. **Glass morphism:** Todas las tarjetas con blur + borde sutil
4. **Animaciones:** Solo las definidas globalmente, nunca inline
5. **Hover:** Siempre sutil (translateY, border-color, opacity)
6. **Empty states:** Cuidados, con icono + titulo + descripcion
7. **Sombras:** Solo en hover y elementos featured
8. **Border-radius:** Maximo 24px (--radius-xl)
