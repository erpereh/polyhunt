# Skill: Supabase — PolyHunt

## Propósito
Gestionar la base de datos Supabase del bot de paper trading PolyHunt.
Usar el MCP de Supabase para crear proyectos, ejecutar SQL, verificar datos en tiempo real.

## Cliente singleton (core/db.py)
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
    db = get_db()
    try:
        account = db.table("account").select("balance").eq("id", 1).single().execute()
        balance = float(account.data["balance"])
        size_usd = min(size_usd, balance * 0.05)
        if size_usd <= 0 or size_usd > balance:
            return None, "Insufficient balance"

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

        db.table("account").update({
            "balance": round(balance - size_usd, 2),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", 1).execute()

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
    db = get_db()
    try:
        trade = db.table("paper_trades").select("*").eq("id", trade_id).single().execute().data
        if not trade:
            return None

        direction = trade["direction"]
        size_usd = float(trade["size_usd"])
        entry_price = float(trade["entry_price"])
        market_id = trade["market_id"]

        pnl = (exit_price - entry_price) / entry_price * size_usd if direction == "YES" \
              else (entry_price - exit_price) / entry_price * size_usd

        db.table("paper_trades").update({
            "status": "closed",
            "exit_price": exit_price,
            "pnl": round(pnl, 2),
            "closed_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", trade_id).execute()

        account = db.table("account").select("balance, total_pnl").eq("id", 1).single().execute()
        db.table("account").update({
            "balance": round(float(account.data["balance"]) + size_usd + pnl, 2),
            "total_pnl": round(float(account.data["total_pnl"]) + pnl, 2),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", 1).execute()

        db.table("positions").delete().eq("market_id", market_id).execute()

        logger.info(f"[{datetime.now()}] Trade cerrado #{trade_id} | P&L: ${pnl:.2f}")
        return pnl
    except Exception as e:
        logger.error(f"[{datetime.now()}] Error cerrando trade #{trade_id}: {e}")
        return None

def update_unrealized_pnl(market_id, current_price):
    db = get_db()
    try:
        pos = db.table("positions").select("*").eq("market_id", market_id).single().execute().data
        if not pos:
            return
        size_usd = float(pos["size_usd"])
        entry_price = float(pos["entry_price"])
        unrealized = (current_price - entry_price) / entry_price * size_usd if pos["direction"] == "YES" \
                     else (entry_price - current_price) / entry_price * size_usd
        db.table("positions").update({
            "current_price": current_price,
            "unrealized_pnl": round(unrealized, 2)
        }).eq("market_id", market_id).execute()
    except Exception as e:
        logger.error(f"[{datetime.now()}] Error actualizando P&L unrealized {market_id}: {e}")

def save_news_article(title, summary, source, url, published_at, relevance_score, related_market_id=None):
    db = get_db()
    try:
        db.table("news_articles").upsert({
            "title": title, "summary": summary, "source": source,
            "url": url, "published_at": published_at,
            "relevance_score": relevance_score,
            "related_market_id": related_market_id
        }, on_conflict="url").execute()
    except Exception as e:
        logger.error(f"[{datetime.now()}] Error guardando noticia: {e}")

def get_dashboard_data():
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
            "account": account, "positions": positions, "trades": trades,
            "markets": markets, "news": news, "analyses": analyses,
            "win_rate": round(win_rate, 1), "open_count": len(positions),
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
- Este es un bot de PAPER TRADING — nunca conecta a wallet real ni ejecuta órdenes reales en Polymarket
