# PolyHunt - Estado actual + roadmap

Este documento refleja:
- Lo que YA esta implementado en la app (codigo actual del repo).
- Lo que esta planificado para despues (contenido de `FUTURO_PROMT.txt`).

Importante:
- PAPER TRADING ONLY. Nunca se ejecutan ordenes reales ni se usan wallets reales.
- El dashboard es la interfaz principal de control del bot.

## 1) Estado actual (IMPLEMENTADO)

### Stack actual
- Backend/API: Flask
- Trading engine: Python
- DB: Supabase
- LLMs: Groq (`llama-3.3-70b-versatile`) + Gemini (`gemini-2.0-flash`)
- Frontend dashboard: HTML/CSS/JS en `dashboard/`

### Archivos clave
- `main.py`: loop principal del bot
- `server.py`: servidor Flask + API + estaticos
- `config.py`: carga y validacion de env vars
- `core/state.py`: estado compartido (`run_event`, `stop_requested`)
- `core/db.py`: cliente singleton de Supabase
- `core/market_scanner.py`: mercados y precios Polymarket
- `core/news_monitor.py`: RSS + scoring de relevancia
- `core/llm_analyzer.py`: pipeline dual de analisis LLM
- `core/paper_trader.py`: logica de paper trading y dashboard data
- `dashboard/index.html`, `dashboard/style.css`, `dashboard/app.js`: UI actual

### Arranque local
1. Instalar dependencias:
```bash
pip install -r requirements.txt
```
2. Configurar `.env` (actualmente obligatorio):
   - `SUPABASE_URL`
   - `SUPABASE_KEY`
   - `GROQ_API_KEY`
   - `GEMINI_API_KEY`
3. Ejecutar:
```bash
python main.py
```
4. Abrir dashboard en:
   - `http://localhost:5000` (o `PORT` si Railway lo define)

### Configuracion actual (`config.py`)
- Se usa `python-dotenv` con `load_dotenv()`.
- Si falta cualquier variable requerida, el proceso termina con `sys.exit(1)`.
- Fuentes de keys/documentacion de obtencion ya estan definidas en `_SOURCES`.

### Estado del bot y control (`core/state.py`)
- `run_event`:
  - `set()` -> bot en ejecucion
  - `clear()` -> bot pausado
- `stop_requested`:
  - `set()` -> stop solicitado, termina ciclo actual y luego pausa
  - `clear()` -> sin stop pendiente

### Ciclo actual (`main.py`)
- Intervalo fijo: cada 15 minutos (`CYCLE_SECONDS = 15 * 60`).
- Flujo por ciclo:
  1. Escanear mercados politicos
  2. Guardar mercados/snapshots
  3. Procesar noticias RSS
  4. Analizar mercados con LLM dual
  5. Abrir paper trades si aplica
  6. Actualizar P&L no realizado
  7. Ejecutar stop losses

#### Reglas/filtros actuales importantes
- Escaneo politicos con filtros:
  - volumen: `50k` a `250k`
  - al menos `7` dias hasta resolucion
- Maximo mercados analizados por ciclo: `50` (con rotacion entre ciclos).
- Skip por precio extremo:
  - YES `< 0.02` o `> 0.98`
- Skip por keywords especulativas (tweet/followers/views/etc.).
- Logica de noticias:
  - si NO hay noticias recientes relacionadas, se exige `gap >= 20%` para abrir trade
  - con noticias recientes, basta el umbral base de `full_analysis` (`>= 15%`)

### Scanner de mercados (`core/market_scanner.py`)
- Fuente eventos: Gamma API `/events`
- Precio actual: CLOB API `/midpoint`
- Tag IDs politicos activos:
  - `2` Politics
  - `100265` Geopolitics
  - `126` Trump
  - `96` Ukraine
  - `95` Russia

### Noticias (`core/news_monitor.py`)
- Consume multiples RSS politicos (EN/ES).
- Prefiltro por overlap de keywords con mercados activos.
- Scoring LLM de relevancia con Groq.
- Guarda con upsert por `url` para evitar duplicados.
- `related_market_id` se usa para conectar noticia con mercado.

### Pipeline LLM actual (`core/llm_analyzer.py`)
- Groq siempre (con cache de 4h en Supabase, modelo `groq/llama-3.3-70b-versatile`).
- Gemini solo si `gap > 10%` (`gemini/gemini-2.0-flash`).
- Si Groq y Gemini divergen mas de `20%` -> no trade.
- Regla base `should_trade`:
  - `gap >= 15%`
  - `confidence` en `high|medium`
  - `resolution_risk != high`
- Se guarda SIEMPRE reasoning cuando hay respuesta valida.

### Paper trader actual (`core/paper_trader.py`)
- Maximo 1 posicion abierta por mercado (`positions.market_id` PK).
- Sizing: Kelly conservador, max `5%` del balance por posicion.
- Cierre por stop loss: perdida relativa `>= 30%`.
- `get_dashboard_data()` retorna:
  - `account`, `positions`, `trades`, `markets`, `news`, `analyses`
  - `win_rate`, `open_count`, `total_unrealized_pnl`, `has_activity`

### API Flask actual (`server.py`)
- `GET /` -> `dashboard/index.html`
- `GET /<path:filename>` -> assets dashboard
- `GET /api/dashboard`
- `GET /api/status`
- `POST /api/bot/start`
- `POST /api/bot/stop`
- `POST /api/reset`
- `GET /api/logs`
- `POST /api/logs/clear`

Notas:
- `/api/status` calcula `status` real (`running|stopping|paused`) desde eventos.
- `/api/reset` solo permite reset cuando bot esta pausado.

### Dashboard actual (visual y UX)
- Estilo: Glass Morphism premium.
- Idioma: espanol.
- Navegacion superior con tabs:
  - Panel, Posiciones, Trades, Mercados, Analisis, Noticias.
- Controles superiores:
  - Start, Stop, Reset DB, Toggle tema.
- Fuentes actuales:
  - `Outfit` (display)
  - `IBM Plex Mono` (numeros/datos)
- Tema:
  - modo dark + modo light con variables CSS y `data-theme`.

## 2) Supabase schema recomendado para el estado actual

Ejecutar en Supabase SQL Editor antes de arrancar en entorno limpio.

```sql
-- Cuenta (paper balance)
CREATE TABLE IF NOT EXISTS account (
    id SERIAL PRIMARY KEY,
    balance NUMERIC(12,2) DEFAULT 10000.00,
    initial_balance NUMERIC(12,2) DEFAULT 10000.00,
    total_invested NUMERIC(12,2) DEFAULT 0.00,
    total_pnl NUMERIC(12,2) DEFAULT 0.00,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Asegurar fila base id=1
INSERT INTO account (id, balance, initial_balance, total_invested, total_pnl)
VALUES (1, 10000.00, 10000.00, 0.00, 0.00)
ON CONFLICT (id) DO NOTHING;

-- Mercados rastreados
CREATE TABLE IF NOT EXISTS markets (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    description TEXT,
    volume NUMERIC(16,2),
    end_date TIMESTAMPTZ,
    yes_token_id TEXT,
    no_token_id TEXT,
    last_price NUMERIC(6,4),
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Snapshots de precio historico
CREATE TABLE IF NOT EXISTS price_snapshots (
    id BIGSERIAL PRIMARY KEY,
    market_id TEXT REFERENCES markets(id) ON DELETE CASCADE,
    price NUMERIC(6,4),
    volume NUMERIC(16,2),
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

-- Analisis de LLMs
CREATE TABLE IF NOT EXISTS llm_analyses (
    id BIGSERIAL PRIMARY KEY,
    market_id TEXT REFERENCES markets(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    probability_yes NUMERIC(6,4),
    probability_range TEXT,
    confidence TEXT,
    resolution_risk TEXT,
    edge_detected BOOLEAN DEFAULT FALSE,
    reasoning TEXT,
    market_price_at_analysis NUMERIC(6,4),
    gap NUMERIC(6,4),
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

-- Operaciones simuladas
CREATE TABLE IF NOT EXISTS paper_trades (
    id BIGSERIAL PRIMARY KEY,
    market_id TEXT REFERENCES markets(id) ON DELETE CASCADE,
    direction TEXT CHECK (direction IN ('YES', 'NO')),
    size_usd NUMERIC(10,2) NOT NULL,
    entry_price NUMERIC(6,4) NOT NULL,
    llm_probability NUMERIC(6,4),
    gap_at_entry NUMERIC(6,4),
    groq_reasoning TEXT,
    gemini_reasoning TEXT,
    status TEXT DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    exit_price NUMERIC(6,4),
    pnl NUMERIC(10,2),
    opened_at TIMESTAMPTZ DEFAULT NOW(),
    closed_at TIMESTAMPTZ
);

-- Posiciones abiertas
CREATE TABLE IF NOT EXISTS positions (
    market_id TEXT PRIMARY KEY REFERENCES markets(id) ON DELETE CASCADE,
    direction TEXT CHECK (direction IN ('YES', 'NO')),
    size_usd NUMERIC(10,2),
    entry_price NUMERIC(6,4),
    current_price NUMERIC(6,4),
    unrealized_pnl NUMERIC(10,2) DEFAULT 0,
    opened_at TIMESTAMPTZ DEFAULT NOW()
);

-- Noticias procesadas
CREATE TABLE IF NOT EXISTS news_articles (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT,
    source TEXT,
    url TEXT UNIQUE,
    published_at TIMESTAMPTZ,
    relevance_score NUMERIC(4,3) DEFAULT 0,
    related_market_id TEXT,
    processed_at TIMESTAMPTZ DEFAULT NOW()
);
```

## 3) Reglas criticas actuales

- El `.env` nunca se sube a git.
- Usar `load_dotenv()` al inicio de config.
- Todas las llamadas a Supabase van con `try/except`.
- Nunca abrir posicion mayor al `5%` del balance.
- Guardar siempre reasoning de LLM para calibracion posterior.
- Cerrar posiciones si se mueven `>30%` en contra.
- Usar cliente singleton de Supabase para evitar exceso de conexiones.

---

## 4) Roadmap futuro (PENDIENTE / NO IMPLEMENTADO)

Esta seccion resume lo solicitado en `FUTURO_PROMT.txt`.
No esta implementado aun.

### Contexto objetivo
- Railway tiene filesystem efimero: no escribir `.env` en runtime.
- `.env` y Railway quedarian solo con:
  - `SUPABASE_URL`
  - `SUPABASE_KEY`
- API keys de providers (Cerebras/Gemini/Groq) se moverian a Supabase (`api_keys`).

### Cambio de modelos objetivo
- Gemini 2.0 Flash -> `gemini-2.5-pro`.
- Arquitectura cascada de 3 modelos:
  1. Cerebras (Qwen 3 235B) screener
  2. Gemini 2.5 Pro analisis profundo
  3. Groq llama-3.3-70b confirmacion
- Trade solo si hay consenso y sin divergencia extrema.

### Loop objetivo
- Loop scan precios cada 5 min (sin LLM).
- Reanalisis LLM solo por eventos:
  - precio se mueve >5%
  - noticia nueva relevante
  - mercado nuevo
  - cache LLM expirada (8h)
- Ampliar scan a 150 mercados.

### SQL futuro solicitado (copiado de FUTURO_PROMT.txt)

```sql
-- Tabla de API keys de todos los proveedores
CREATE TABLE IF NOT EXISTS api_keys (
    id BIGSERIAL PRIMARY KEY,
    service TEXT NOT NULL 
        CHECK (service IN ('cerebras', 'gemini', 'groq')),
    key_value TEXT NOT NULL,
    label TEXT,
    is_enabled BOOLEAN DEFAULT TRUE,
    in_cooldown BOOLEAN DEFAULT FALSE,
    cooldown_until TIMESTAMPTZ,
    last_error TEXT,
    last_used_at TIMESTAMPTZ,
    calls_today INTEGER DEFAULT 0,
    tokens_today INTEGER DEFAULT 0,
    calls_reset_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Añadir columnas para reglas de salida en positions
ALTER TABLE positions 
ADD COLUMN IF NOT EXISTS max_unrealized_pnl 
    NUMERIC(10,2) DEFAULT 0;
ALTER TABLE positions
ADD COLUMN IF NOT EXISTS days_open INTEGER DEFAULT 0;

-- Añadir campo de último precio conocido y
-- timestamp del último análisis LLM en markets
ALTER TABLE markets
ADD COLUMN IF NOT EXISTS last_price_change_pct 
    NUMERIC(6,4) DEFAULT 0;
ALTER TABLE markets
ADD COLUMN IF NOT EXISTS last_llm_analysis_at 
    TIMESTAMPTZ;
ALTER TABLE markets
ADD COLUMN IF NOT EXISTS last_price_checked_at 
    TIMESTAMPTZ;
```

### Cambios futuros esperados por archivo
- `core/key_manager.py` (nuevo): rotacion/cooldown/reload/reset diario de keys.
- `config.py`: dejar solo Supabase vars.
- `core/llm_analyzer.py`: reescritura para 3 providers y cache unificada 8h.
- `main.py`: separar loop de scan y loop de cola LLM.
- `core/paper_trader.py`: take-profit + time-stop + tracking de max_unrealized/days_open.
- `server.py`: endpoints de settings para CRUD de keys.
- `dashboard/*`: nueva seccion Ajustes para gestionar keys.

### Nota visual para roadmap
Aunque en notas futuras aparece "dark terminal", la app actual ya usa Glass Morphism.
Los cambios de UX/UI futuros deben mantener coherencia con el estilo visual actual,
salvo que se decida explicitamente una migracion de diseno completa.
