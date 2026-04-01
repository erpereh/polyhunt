# PolyHunt — Paper Trading (core/paper_trader.py)

## PAPER TRADING ONLY
Nunca conecta a wallets reales. Toda la operativa es simulada en Supabase.
Reglas de riesgo: Kelly conservador 5% máximo por posición, stop loss 30%.

---

## Schema completo de Supabase

```sql
-- Cuenta (balance simulado)
account (
    id          SERIAL PRIMARY KEY,
    balance     NUMERIC(12,2) DEFAULT 10000.00,  -- arranca con $10,000
    total_invested NUMERIC(12,2) DEFAULT 0.00,
    total_pnl   NUMERIC(12,2) DEFAULT 0.00,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
)

-- Mercados rastreados
markets (
    id           TEXT PRIMARY KEY,              -- conditionId de Polymarket
    question     TEXT NOT NULL,
    description  TEXT,
    volume       NUMERIC(16,2),
    end_date     TIMESTAMPTZ,
    yes_token_id TEXT,
    no_token_id  TEXT,
    last_price   NUMERIC(6,4),                  -- precio YES token (0–1)
    status       TEXT DEFAULT 'active',
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
)

-- Snapshots de precio histórico (backtesting)
price_snapshots (
    id         BIGSERIAL PRIMARY KEY,
    market_id  TEXT REFERENCES markets(id),
    price      NUMERIC(6,4),
    volume     NUMERIC(16,2),
    timestamp  TIMESTAMPTZ DEFAULT NOW()
)

-- Análisis LLM (guardar siempre para calibración)
llm_analyses (
    id                      BIGSERIAL PRIMARY KEY,
    market_id               TEXT REFERENCES markets(id),
    model                   TEXT NOT NULL,       -- "groq/llama-3.3-70b" o "gemini/gemini-1.5-flash"
    probability_yes         NUMERIC(6,4),
    probability_range       TEXT,
    confidence              TEXT,
    resolution_risk         TEXT,
    edge_detected           BOOLEAN DEFAULT FALSE,
    reasoning               TEXT,
    market_price_at_analysis NUMERIC(6,4),
    gap                     NUMERIC(6,4),
    timestamp               TIMESTAMPTZ DEFAULT NOW()
)

-- Operaciones simuladas
paper_trades (
    id               BIGSERIAL PRIMARY KEY,
    market_id        TEXT REFERENCES markets(id),
    direction        TEXT CHECK (direction IN ('YES','NO')),
    size_usd         NUMERIC(10,2) NOT NULL,
    entry_price      NUMERIC(6,4) NOT NULL,
    llm_probability  NUMERIC(6,4),
    gap_at_entry     NUMERIC(6,4),
    groq_reasoning   TEXT,
    gemini_reasoning TEXT,
    status           TEXT DEFAULT 'open' CHECK (status IN ('open','closed')),
    exit_price       NUMERIC(6,4),
    pnl              NUMERIC(10,2),
    opened_at        TIMESTAMPTZ DEFAULT NOW(),
    closed_at        TIMESTAMPTZ
)

-- Posiciones abiertas actualmente (una por mercado máximo)
positions (
    market_id     TEXT PRIMARY KEY REFERENCES markets(id),
    direction     TEXT CHECK (direction IN ('YES','NO')),
    size_usd      NUMERIC(10,2),
    entry_price   NUMERIC(6,4),
    current_price NUMERIC(6,4),
    unrealized_pnl NUMERIC(10,2) DEFAULT 0,
    opened_at     TIMESTAMPTZ DEFAULT NOW()
)

-- Noticias procesadas
news_articles (
    id                BIGSERIAL PRIMARY KEY,
    title             TEXT NOT NULL,
    summary           TEXT,
    source            TEXT,
    url               TEXT UNIQUE,               -- clave de deduplicación
    published_at      TIMESTAMPTZ,
    relevance_score   NUMERIC(4,3) DEFAULT 0,
    related_market_id TEXT,
    processed_at      TIMESTAMPTZ DEFAULT NOW()
)
```

---

## Funciones públicas

### `upsert_market(market_data: dict) → None`
Guarda o actualiza un mercado. Usa `upsert` por `id` (conditionId).

### `save_price_snapshot(market_id, price, volume=None) → None`
Insert en `price_snapshots`. Siempre insert, nunca upsert.

### `save_llm_analysis(market_id, model, result, market_price, gap) → None`
Insert en `llm_analyses`. Llamar SIEMPRE aunque no haya trade — es el dato más valioso.

---

### `open_paper_trade(...) → tuple[Optional[int], str]`
```python
open_paper_trade(
    market_id:        str,
    direction:        str,          # "YES" o "NO"
    size_usd:         float,        # tamaño solicitado (puede reducirse por Kelly)
    entry_price:      float,        # precio actual del mercado
    llm_probability:  float,
    gap:              float,
    groq_reasoning:   str,
    gemini_reasoning: str = None,
) → (trade_id: int, "OK") | (None, mensaje_error)
```

**Lógica interna:**
1. Verificar que NO existe posición abierta en `positions` para ese `market_id` — una posición por mercado máximo
2. Obtener balance de `account` (id=1)
3. `size_usd = min(size_usd, balance * 0.05)` — Kelly conservador: máximo 5%
4. Validar `size_usd > 0` y `size_usd <= balance`
5. Insert en `paper_trades`
6. Descontar `size_usd` del balance
7. Upsert en `positions`

---

### `close_paper_trade(trade_id, exit_price) → Optional[float]`
Retorna el P&L calculado o `None` si error.

**Fórmulas P&L:**
```python
if direction == "YES":
    pnl = (exit_price - entry_price) / entry_price * size_usd
else:
    pnl = (entry_price - exit_price) / entry_price * size_usd
```

**Lógica interna:**
1. Fetch trade completo desde `paper_trades`
2. Calcular P&L
3. Update trade: `status="closed"`, `exit_price`, `pnl`, `closed_at`
4. Devolver `size_usd + pnl` al balance, actualizar `total_pnl`
5. Delete de `positions` donde `market_id = trade.market_id`

---

### `update_unrealized_pnl(market_id, current_price) → None`
Actualiza `positions.current_price` y `positions.unrealized_pnl`.  
Misma fórmula que close pero sin cerrar la posición.

---

### `check_stop_losses(stop_loss_pct=0.30) → int`
Revisa todas las posiciones y cierra las que superan el umbral de pérdida.

**Cálculo de pérdida relativa:**
```python
if direction == "YES":
    loss_pct = (entry_price - current_price) / entry_price  # positivo = pérdida
else:
    loss_pct = (current_price - entry_price) / entry_price  # positivo = pérdida
```

Si `loss_pct >= stop_loss_pct` → busca el trade `status="open"` del mercado y llama `close_paper_trade()`.  
Retorna número de posiciones cerradas.

---

### `save_news_article(...) → None`
Upsert en `news_articles` con `on_conflict="url"` — no crea duplicados.

---

### `get_dashboard_data() → dict`
Una sola función que obtiene todo para el dashboard:
```python
{
    "account":              dict,
    "positions":            list,  # join con markets(question)
    "trades":               list,  # últimos 50, join con markets(question)
    "markets":              list,  # últimos 20 por updated_at
    "news":                 list,  # últimas 10 por processed_at
    "analyses":             list,  # últimas 30 por timestamp
    "win_rate":             float, # % de trades cerrados con pnl > 0
    "open_count":           int,
    "total_unrealized_pnl": float,
}
```

---

## Invariantes críticos
- Una sola fila en `account` (id=1) — siempre `.eq("id", 1).single()`
- Una sola posición por `market_id` — `positions.market_id` es PRIMARY KEY
- Todos los montos redondeados a 2 decimales con `round(x, 2)`
- Toda llamada a Supabase en try/except — la red puede fallar
