# PolyHunt — Dashboard (dashboard/ + server.py)

## Archivos
- `dashboard/index.html` — layout HTML completo
- `dashboard/style.css` — sistema de diseño Dark Financial Terminal
- `dashboard/app.js` — lógica de refresh y render
- `server.py` — Flask server que sirve el dashboard

---

## Paleta de colores exacta (CSS variables)

```css
/* Fondos en capas */
--bg-void: #060810;        /* body background */
--bg-base: #0c1018;        /* sidebar */
--bg-surface: #111827;     /* cards, tablas */
--bg-elevated: #1a2234;    /* hover, badges */

/* Verde acento — usar con precisión, no decorativamente */
--green: #52b788;
--green-bright: #6ee7b7;
--green-dim: rgba(82, 183, 136, 0.10);
--green-border: rgba(82, 183, 136, 0.22);
--green-glow: 0 0 24px rgba(82, 183, 136, 0.12);

/* Semáforo P&L */
--profit: #4ade80;
--loss: #f87171;
--warning: #fbbf24;

/* Texto jerárquico */
--text-primary: #f1f5f9;
--text-secondary: #64748b;
--text-muted: #2d3748;

/* Bordes casi invisibles */
--border: rgba(255, 255, 255, 0.055);
--border-hover: rgba(255, 255, 255, 0.10);
--border-accent: rgba(82, 183, 136, 0.28);

/* Tipografías */
--font-mono: 'JetBrains Mono', monospace;  /* TODOS los números */
--font-sans: 'DM Sans', sans-serif;        /* textos y labels */

/* Radios — nunca > 12px */
--radius-sm: 6px;
--radius-md: 10px;
--radius-lg: 12px;
```

**Regla tipográfica absoluta:** NUNCA Inter, Roboto, Arial ni system-ui.  
Import en `<head>`: `https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=DM+Sans:...`

---

## Componentes implementados

### `.kpi-card` / `.kpi-card.featured`
Grid 4 columnas (`2fr 1fr 1fr 1fr`). Card destacada (balance) tiene `--green-border` y `--green-glow`.  
Hover: `translateY(-1px)` + línea superior verde que aparece (`::after`).

### `.data-table`
Todas las tablas comparten el mismo estilo. `th` con letra 10px uppercase. `td` con 13px.  
Clase `.num` = monoespaciado alineado a la derecha. Hover row con `rgba(255,255,255,0.018)`.

### `.gap-badge`
```css
.high   → verde  (gap >= 15%)
.medium → amarillo (gap >= 8%)
.low    → gris   (gap < 8%)
```

### `.direction-badge`
`.yes` → verde (#4ade80). `.no` → rojo (#f87171).

### `.confidence-bar`
3 segmentos de 14×3px. `high`=3 on, `medium`=2 on, `low`=1 on.  
Segmento on: `--green`. Segmento off: `--bg-elevated`.

### `.pnl-positive / .pnl-negative / .pnl-zero`
Siempre con `font-family: var(--font-mono)`.

### `.sim-badge`
Badge "SIM" en el footer del sidebar. Amarillo (`--warning`). SIEMPRE visible — recuerda que es paper trading.

### `.status-dot.active`
Punto verde con animación `pulse` keyframes (0→7px glow→0).

### `.news-item` / `.news-score`
Lista de noticias con score de relevancia. Score `.high` (>=70%) en verde, `.mid` (>=40%) en amarillo, `.low` en gris.

---

## Flask server (server.py)

```python
app = Flask(__name__, static_folder="dashboard", static_url_path="")

GET /              → app.send_static_file("index.html")
GET /api/dashboard → get_dashboard_data() como JSON
GET /api/status    → bot_status dict (ciclo actual, last_cycle, next_cycle, etc.)
```

**`bot_status` dict** — actualizable desde main.py vía `set_bot_status(**kwargs)`:
```python
{
    "running":    bool,
    "cycle":      int,
    "last_cycle": str,   # ISO 8601
    "next_cycle": str,   # ISO 8601
    "markets_scanned": int,
    "trades_open": int,
}
```

---

## app.js — lógica de refresh

### Ciclo de actualización
```javascript
refresh();                   // llamada inicial inmediata
setInterval(refresh, 60_000); // luego cada 60 segundos
```

`refresh()` hace `fetch('/api/dashboard')`, llama a los 5 render functions y actualiza `#last-update`.  
Si falla → `status-dot` pasa a `.paused` y muestra "Sin conexión".

### Objeto `fmt` — helpers de formato
```javascript
fmt.usd(n)          // → '$1,234.56'
fmt.pct(n)          // → '72.3%'  (n es 0-1)
fmt.pnl(n)          // → '+$45.00' o '-$12.30'
fmt.pnlClass(n)     // → 'pnl-positive' | 'pnl-negative' | 'pnl-zero'
fmt.gapBadge(gap)   // → '<span class="gap-badge high">18.5%</span>'
fmt.confidence(lvl) // → '<span class="confidence-bar">...</span>'
fmt.date(iso)       // → '01/04/26, 14:30'
fmt.dateShort(iso)  // → '01/04, 14:30'
```

### Funciones de render
- `renderKPIs(data)` — KPI cards con fade en cambios de valor
- `renderPositions(positions)` — tabla o empty state (se renderiza en #positions-body y #overview-positions-body)
- `renderTrades(trades)` — tabla con status badge open/closed
- `renderMarkets(markets, analyses)` — tabla con último gap del analysisMap
- `renderAnalyses(analyses)` — tabla con modelo, gap badge, confidence bar, reasoning truncado
- `renderNews(news)` — lista con score badge (se renderiza en #news-body y #overview-news-body)

### Navegación `showSection(name)`
Oculta todas las secciones (`id="section-*"`) y muestra la seleccionada.  
Actualiza `.nav-item.active` y `#page-title`.

### Fade en números
`setWithFade(el, value)` — solo anima si el valor cambió. Usa `void el.offsetWidth` para reiniciar la animación CSS.

---

## Secciones del dashboard

| Sección   | ID HTML           | Contenido                                           |
|-----------|-------------------|-----------------------------------------------------|
| Overview  | `section-overview` | KPI grid + posiciones abiertas + noticias recientes |
| Posiciones | `section-positions` | Tabla completa de posiciones abiertas              |
| Historial | `section-trades`  | Últimos 50 trades con P&L                          |
| Mercados  | `section-markets` | Mercados escaneados con último gap LLM             |
| Análisis  | `section-analyses` | Análisis LLM con reasoning                        |
| Noticias  | `section-news`    | Noticias procesadas con score de relevancia        |

Todas las secciones tienen empty state (`class="empty-state"`) con icono, título y descripción.
