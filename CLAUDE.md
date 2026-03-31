### 4. Cliente singleton (core/db.py)
```python
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY
import logging

logger = logging.getLogger(__name__)
_client: Client = None

def get_db() -> Client:
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client
```

## Schema SQL — ejecutar en Supabase > SQL Editor ANTES de arrancar el bot
```sql
-- Cuenta (balance simulado del paper trading)
CREATE TABLE IF NOT EXISTS account (
    id SERIAL PRIMARY KEY,
    balance NUMERIC(12,2) DEFAULT 10000.00,
    total_invested NUMERIC(12,2) DEFAULT 0.00,
    total_pnl NUMERIC(12,2) DEFAULT 0.00,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
INSERT INTO account (balance) VALUES (10000.00);

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

-- Snapshots de precio histórico (para backtesting futuro)
CREATE TABLE IF NOT EXISTS price_snapshots (
    id BIGSERIAL PRIMARY KEY,
    market_id TEXT REFERENCES markets(id) ON DELETE CASCADE,
    price NUMERIC(6,4),
    volume NUMERIC(16,2),
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

-- Análisis de LLMs (guardar todo para calibración posterior)
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

-- Operaciones simuladas (paper trades)
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

-- Posiciones abiertas actualmente
CREATE TABLE IF NOT EXISTS positions (
    market_id TEXT PRIMARY KEY REFERENCES markets(id) ON DELETE CASCADE,
    direction TEXT CHECK (direction IN ('YES', 'NO')),
    size_usd NUMERIC(10,2),
    entry_price NUMERIC(6,4),
    current_price NUMERIC(6,4),
    unrealized_pnl NUMERIC(10,2) DEFAULT 0,
    opened_at TIMESTAMPTZ DEFAULT NOW()
);

-- Noticias procesadas con score de relevancia
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

## Funciones core del paper trader
```python
from datetime import datetime, timezone
from core.db import get_db
import logging

logger = logging.getLogger(__name__)

def upsert_market(market_data):
    """Guarda o actualiza un mercado en Supabase."""
    db = get_db()
    try:
        db.table("markets").upsert({
            "id": market_data["id"],
            "question": market_data["question"],
            "description": market_data.get("description", ""),
            "volume": market_data.get("volume", 0),
            "end_date": market_data.get("end_date"),
            "yes_token_id": market_data.get("yes_token_id"),
            "no_token_id": market_data.get("no_token_id"),
            "last_price": market_data.get("last_price"),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).execute()
    except Exception as e:
        logger.error(f"[{datetime.now()}] Error guardando mercado {market_data['id']}: {e}")

def save_price_snapshot(market_id, price, volume=None):
    """Guarda snapshot de precio para historial."""
    db = get_db()
    try:
        db.table("price_snapshots").insert({
            "market_id": market_id,
            "price": price,
            "volume": volume
        }).execute()
    except Exception as e:
        logger.error(f"[{datetime.now()}] Error guardando snapshot {market_id}: {e}")

def save_llm_analysis(market_id, model, result, market_price, gap):
    """Guarda análisis de LLM para calibración futura."""
    db = get_db()
    try:
        db.table("llm_analyses").insert({
            "market_id": market_id,
            "model": model,
            "probability_yes": result.get("probability_yes"),
            "probability_range": result.get("probability_range"),
            "confidence": result.get("confidence"),
            "resolution_risk": result.get("resolution_risk"),
            "edge_detected": result.get("edge_detected", False),
            "reasoning": result.get("reasoning"),
            "market_price_at_analysis": market_price,
            "gap": gap
        }).execute()
    except Exception as e:
        logger.error(f"[{datetime.now()}] Error guardando análisis LLM: {e}")

def open_paper_trade(market_id, direction, size_usd, entry_price,
                     llm_probability, gap, groq_reasoning, gemini_reasoning=None):
    """
    Abre una posición simulada.
    Aplica Kelly conservador: máximo 5% del balance por posición.
    Retorna: (trade_id, mensaje)
    """
    db = get_db()
    try:
        # Verificar balance disponible
        account = db.table("account").select("balance").eq("id", 1).single().execute()
        balance = float(account.data["balance"])

        # Kelly conservador: máximo 5% por posición
        size_usd = min(size_usd, balance * 0.05)

        if size_usd <= 0 or size_usd > balance:
            return None, "Insufficient balance"

        # Registrar el trade
        trade = db.table("paper_trades").insert({
            "market_id": market_id,
            "direction": direction,
            "size_usd": round(size_usd, 2),
            "entry_price": entry_price,
            "llm_probability": llm_probability,
            "gap_at_entry": gap,
            "groq_reasoning": groq_reasoning,
            "gemini_reasoning": gemini_reasoning
        }).execute()

        trade_id = trade.data[0]["id"]

        # Descontar del balance
        db.table("account").update({
            "balance": round(balance - size_usd, 2),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", 1).execute()

        # Crear posición abierta
        db.table("positions").upsert({
            "market_id": market_id,
            "direction": direction,
            "size_usd": round(size_usd, 2),
            "entry_price": entry_price,
            "current_price": entry_price,
            "unrealized_pnl": 0
        }).execute()

        logger.info(f"[{datetime.now()}] Trade abierto #{trade_id} | {direction} {market_id} @ {entry_price} | ${size_usd:.2f}")
        return trade_id, "OK"

    except Exception as e:
        logger.error(f"[{datetime.now()}] Error abriendo trade: {e}")
        return None, str(e)

def close_paper_trade(trade_id, exit_price):
    """
    Cierra una posición simulada y calcula P&L.
    Retorna: pnl (float) o None si error
    """
    db = get_db()
    try:
        trade_res = db.table("paper_trades").select("*").eq("id", trade_id).single().execute()
        trade = trade_res.data
        if not trade:
            return None

        direction = trade["direction"]
        size_usd = float(trade["size_usd"])
        entry_price = float(trade["entry_price"])
        market_id = trade["market_id"]

        # Calcular P&L
        if direction == "YES":
            pnl = (exit_price - entry_price) / entry_price * size_usd
        else:
            pnl = (entry_price - exit_price) / entry_price * size_usd

        # Cerrar el trade
        db.table("paper_trades").update({
            "status": "closed",
            "exit_price": exit_price,
            "pnl": round(pnl, 2),
            "closed_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", trade_id).execute()

        # Devolver capital + P&L al balance
        account = db.table("account").select("balance, total_pnl").eq("id", 1).single().execute()
        new_balance = float(account.data["balance"]) + size_usd + pnl
        new_pnl = float(account.data["total_pnl"]) + pnl

        db.table("account").update({
            "balance": round(new_balance, 2),
            "total_pnl": round(new_pnl, 2),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", 1).execute()

        # Eliminar posición abierta
        db.table("positions").delete().eq("market_id", market_id).execute()

        logger.info(f"[{datetime.now()}] Trade cerrado #{trade_id} | P&L: ${pnl:.2f}")
        return pnl

    except Exception as e:
        logger.error(f"[{datetime.now()}] Error cerrando trade #{trade_id}: {e}")
        return None

def update_unrealized_pnl(market_id, current_price):
    """Actualiza el P&L no realizado de una posición abierta."""
    db = get_db()
    try:
        pos_res = db.table("positions").select("*").eq("market_id", market_id).single().execute()
        if not pos_res.data:
            return
        pos = pos_res.data
        size_usd = float(pos["size_usd"])
        entry_price = float(pos["entry_price"])

        if pos["direction"] == "YES":
            unrealized = (current_price - entry_price) / entry_price * size_usd
        else:
            unrealized = (entry_price - current_price) / entry_price * size_usd

        db.table("positions").update({
            "current_price": current_price,
            "unrealized_pnl": round(unrealized, 2)
        }).eq("market_id", market_id).execute()

    except Exception as e:
        logger.error(f"[{datetime.now()}] Error actualizando P&L unrealized {market_id}: {e}")

def save_news_article(title, summary, source, url, published_at, relevance_score, related_market_id=None):
    """Guarda noticia procesada. Usa url como clave única para evitar duplicados."""
    db = get_db()
    try:
        db.table("news_articles").upsert({
            "title": title,
            "summary": summary,
            "source": source,
            "url": url,
            "published_at": published_at,
            "relevance_score": relevance_score,
            "related_market_id": related_market_id
        }, on_conflict="url").execute()
    except Exception as e:
        logger.error(f"[{datetime.now()}] Error guardando noticia: {e}")

def get_dashboard_data():
    """Obtiene todos los datos necesarios para el dashboard en una sola llamada."""
    db = get_db()
    try:
        account = db.table("account").select("*").eq("id", 1).single().execute().data
        positions = db.table("positions").select("*, markets(question)").execute().data
        trades = db.table("paper_trades").select("*, markets(question)").order("opened_at", desc=True).limit(50).execute().data
        markets = db.table("markets").select("*").order("updated_at", desc=True).limit(20).execute().data
        news = db.table("news_articles").select("*").order("processed_at", desc=True).limit(10).execute().data
        analyses = db.table("llm_analyses").select("*").order("timestamp", desc=True).limit(30).execute().data

        closed_trades = [t for t in trades if t["status"] == "closed"]
        wins = sum(1 for t in closed_trades if t.get("pnl") and float(t["pnl"]) > 0)
        win_rate = (wins / len(closed_trades) * 100) if closed_trades else 0

        total_unrealized = sum(float(p.get("unrealized_pnl") or 0) for p in positions)

        return {
            "account": account,
            "positions": positions,
            "trades": trades,
            "markets": markets,
            "news": news,
            "analyses": analyses,
            "win_rate": round(win_rate, 1),
            "open_count": len(positions),
            "total_unrealized_pnl": round(total_unrealized, 2)
        }
    except Exception as e:
        logger.error(f"[{datetime.now()}] Error obteniendo datos dashboard: {e}")
        return {}
```

## Reglas críticas
- El .env NUNCA se sube a git — añadir a .gitignore desde el primer momento
- Usar python-dotenv y load_dotenv() al inicio de config.py
- Ejecutar el SQL del schema en Supabase SQL Editor ANTES de arrancar el bot
- TODAS las llamadas a Supabase van en try/except — la red puede fallar
- Nunca más del 5% del balance en una posición — Kelly conservador
- Guardar SIEMPRE el reasoning del LLM — es el dato más valioso para calibrar
- Cerrar posiciones si el precio se mueve >30% en contra del trade
- El plan gratuito de Supabase da 500MB y conexiones simultáneas limitadas — usar el cliente singleton


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARCHIVO 4: skills/dashboard.md
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Skill: Dashboard PolyHunt — Diseño Premium

## Identidad visual
PolyHunt es un bot de trading sofisticado. El dashboard debe transmitir inteligencia,
precisión y control total. Estilo: "Dark Financial Terminal".
Inspiración directa: Bloomberg Terminal + Linear.app.
Oscuro, denso de información, perfectamente legible. Cada píxel tiene un propósito.
El color se usa con precisión quirúrgica — nunca decorativamente.

## Dirección estética: NO es un dashboard oscuro genérico
Es una terminal de trading de alto nivel: extremadamente funcional pero con una
elegancia fría y calculada. Los detalles que lo hacen memorable:
- Tipografía monoespaciada para todos los números
- Líneas sutiles que aparecen solo en hover
- Verde usado solo donde importa de verdad
- Densidad de información alta pero perfectamente jerarquizada
- Grain overlay casi imperceptible que da profundidad

## Tipografía — OBLIGATORIA, nunca cambiar
- Números/datos/código: JetBrains Mono — da sensación de terminal profesional
- Textos/labels/descripciones: DM Sans — limpio, técnico, ligeramente diferente a lo genérico
- NUNCA Inter, Roboto, Arial, system-ui ni ninguna fuente genérica
- Import obligatorio en el <head>:
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;1,9..40,400&display=swap" rel="stylesheet">

## Paleta de colores exacta — CSS variables obligatorias
```css
:root {
  /* Fondos en capas */
  --bg-void: #060810;           /* el más oscuro: fondo del body */
  --bg-base: #0c1018;           /* sidebar y elementos base */
  --bg-surface: #111827;        /* cards, panels, tablas */
  --bg-elevated: #1a2234;       /* hover states, elementos flotantes */

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
  --font-mono: 'JetBrains Mono', monospace;
  --font-sans: 'DM Sans', sans-serif;

  /* Radios — precisión, no redondez excesiva */
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 12px;
}
```

## Layout global
```css
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html { font-size: 14px; }

body {
  background-color: var(--bg-void);
  background-image:
    radial-gradient(ellipse 80% 60% at 15% 40%, rgba(82, 183, 136, 0.028) 0%, transparent 70%),
    radial-gradient(ellipse 60% 50% at 85% 15%, rgba(82, 183, 136, 0.018) 0%, transparent 60%);
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

.main {
  padding: 28px 32px;
  overflow-y: auto;
  max-width: 1400px;
}
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

.logo-symbol {
  font-family: var(--font-mono);
  font-size: 18px;
  color: var(--green);
  line-height: 1;
}

.logo-name {
  font-family: var(--font-mono);
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.01em;
}

.logo-version {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-secondary);
  margin-left: auto;
}

.nav { flex: 1; padding: 4px 0; }

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 20px;
  color: var(--text-secondary);
  text-decoration: none;
  font-size: 13px;
  font-weight: 400;
  border-left: 2px solid transparent;
  transition: all 0.15s ease;
  cursor: pointer;
}

.nav-item:hover {
  color: var(--text-primary);
  background: rgba(255,255,255,0.03);
}

.nav-item.active {
  color: var(--green);
  background: var(--green-dim);
  border-left-color: var(--green);
  font-weight: 500;
}

.nav-icon {
  font-size: 12px;
  opacity: 0.7;
  width: 16px;
  text-align: center;
}

.sidebar-footer {
  padding: 16px 20px;
  border-top: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary);
}

.status-dot {
  width: 7px; height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.status-dot.active {
  background: var(--profit);
  box-shadow: 0 0 0 0 rgba(74, 222, 128, 0.5);
  animation: pulse 2.5s ease infinite;
}

.status-dot.paused { background: var(--text-secondary); }

@keyframes pulse {
  0%   { box-shadow: 0 0 0 0 rgba(74, 222, 128, 0.5); }
  70%  { box-shadow: 0 0 0 7px rgba(74, 222, 128, 0); }
  100% { box-shadow: 0 0 0 0 rgba(74, 222, 128, 0); }
}
```

## Header de página
```css
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--border);
}

.page-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.02em;
}

.last-update {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
}
```

## KPI Cards — el bloque más importante visualmente
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
  transition: border-color 0.2s ease, transform 0.15s ease;
}

/* Línea superior que aparece en hover — detalle premium */
.kpi-card::after {
  content: '';
  position: absolute;
  top: 0; left: 10%; right: 10%;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--green-border), transparent);
  opacity: 0;
  transition: opacity 0.3s ease;
}

.kpi-card:hover { border-color: var(--border-hover); transform: translateY(-1px); }
.kpi-card:hover::after { opacity: 1; }

/* Card destacada — balance principal */
.kpi-card.featured {
  border-color: var(--green-border);
  background: linear-gradient(140deg, var(--bg-surface) 60%, rgba(82,183,136,0.06));
  box-shadow: var(--green-glow), inset 0 1px 0 rgba(82,183,136,0.08);
}
.kpi-card.featured::after { opacity: 0.6; }

.kpi-label {
  font-size: 10.5px;
  font-weight: 500;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--text-secondary);
  margin-bottom: 10px;
}

.kpi-value {
  font-family: var(--font-mono);
  font-size: 1.8rem;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1;
  margin-bottom: 10px;
  letter-spacing: -0.02em;
}

.kpi-delta {
  font-family: var(--font-mono);
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.kpi-delta.up { color: var(--profit); }
.kpi-delta.down { color: var(--loss); }
.kpi-delta.neutral { color: var(--text-secondary); }
```

## Sección con tabla
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

.section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.01em;
}

.section-count {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--bg-elevated);
  padding: 2px 8px;
  border-radius: 20px;
  border: 1px solid var(--border);
}

/* Tablas — el corazón del dashboard */
.data-table { width: 100%; border-collapse: collapse; }

.data-table th {
  text-align: left;
  padding: 10px 20px;
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.09em;
  text-transform: uppercase;
  color: var(--text-secondary);
  background: var(--bg-surface);
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

/* Números alineados a la derecha */
.data-table .num {
  font-family: var(--font-mono);
  font-size: 12px;
  text-align: right;
}

.data-table .mono {
  font-family: var(--font-mono);
  font-size: 12px;
}
```

## Componentes de estado
```css
/* Gap badge (diferencia LLM vs mercado) */
.gap-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 7px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}
.gap-badge.high {
  background: var(--green-dim);
  color: var(--green);
  border: 1px solid var(--green-border);
}
.gap-badge.medium {
  background: rgba(251,191,36,0.08);
  color: var(--warning);
  border: 1px solid rgba(251,191,36,0.25);
}
.gap-badge.low {
  background: transparent;
  color: var(--text-secondary);
  border: 1px solid var(--border);
}

/* Dirección YES/NO */
.direction-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
}
.direction-badge.yes {
  background: rgba(74, 222, 128, 0.1);
  color: var(--profit);
  border: 1px solid rgba(74, 222, 128, 0.2);
}
.direction-badge.no {
  background: rgba(248, 113, 113, 0.1);
  color: var(--loss);
  border: 1px solid rgba(248, 113, 113, 0.2);
}

/* Confianza LLM — 3 segmentos */
.confidence-bar {
  display: inline-flex;
  gap: 3px;
  align-items: center;
  vertical-align: middle;
}
.cb-seg {
  width: 14px; height: 3px;
  border-radius: 2px;
  background: var(--bg-elevated);
}
.cb-seg.on { background: var(--green); }
/* high: 3 on | medium: 2 on | low: 1 on */

/* P&L coloreado */
.pnl-positive { color: var(--profit); font-family: var(--font-mono); font-size: 12px; }
.pnl-negative { color: var(--loss); font-family: var(--font-mono); font-size: 12px; }
.pnl-zero { color: var(--text-secondary); font-family: var(--font-mono); font-size: 12px; }
```

## Estado vacío (empty state) — debe verse bien sin datos
```css
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 24px;
  color: var(--text-secondary);
  text-align: center;
  gap: 8px;
}
.empty-state-icon {
  font-size: 28px;
  opacity: 0.3;
  margin-bottom: 4px;
}
.empty-state-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
}
.empty-state-desc {
  font-size: 12px;
  color: var(--text-muted);
  max-width: 280px;
  line-height: 1.6;
}
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
        data = get_dashboard_data()
        return jsonify(data)
    except Exception as e:
        logger.error(f"Error en /api/dashboard: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
```

## Actualización en tiempo real (app.js)
```javascript
let lastData = null;

async function refresh() {
  try {
    const data = await fetch('/api/dashboard').then(r => r.json());
    if (data.error) { console.error('API error:', data.error); return; }
    lastData = data;
    renderKPIs(data);
    renderPositions(data.positions);
    renderTrades(data.trades);
    renderMarkets(data.markets, data.analyses);
    renderNews(data.news);
    document.getElementById('last-update').textContent =
      'Actualizado ' + new Date().toLocaleTimeString('es-ES');
  } catch (err) {
    console.error('Error refreshing:', err);
  }
}

refresh();
setInterval(refresh, 60000);

// Helpers de formato
const fmt = {
  usd: n => '$' + parseFloat(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}),
  pct: n => (parseFloat(n) * 100).toFixed(1) + '%',
  pnl: n => { const v = parseFloat(n); return (v >= 0 ? '+' : '') + fmt.usd(v); },
  pnlClass: n => parseFloat(n) > 0 ? 'pnl-positive' : parseFloat(n) < 0 ? 'pnl-negative' : 'pnl-zero',
  gapBadge: gap => {
    const g = parseFloat(gap) * 100;
    const cls = g >= 15 ? 'high' : g >= 8 ? 'medium' : 'low';
    return `<span class="gap-badge ${cls}">${g.toFixed(1)}%</span>`;
  },
  confidence: lvl => {
    const n = lvl === 'high' ? 3 : lvl === 'medium' ? 2 : 1;
    return `<span class="confidence-bar">${[1,2,3].map(i => `<span class="cb-seg ${i<=n?'on':''}"></span>`).join('')}</span>`;
  }
};
```

## Reglas absolutas de diseño — sin excepciones
- TODOS los números financieros y porcentajes: font-family var(--font-mono)
- Verde #52b788 solo para: elemento featured, item nav activo, gap alto, borde de card importante
- Sin border-radius mayor de 12px en ningún elemento
- Sombras solo en .kpi-card.featured y modales/tooltips
- Todo texto secundario en var(--text-secondary) — nunca blanco puro para labels
- Las tablas son el corazón del dashboard — más atención que cualquier otro elemento
- El dashboard debe verse impresionante tanto con datos como sin ellos (empty states cuidados)
- Animaciones solo en: status-dot pulse, kpi-card hover transform, line hover opacity

Crea los 4 archivos exactamente con esas rutas y contenido. Sin modificaciones ni confirmaciones.