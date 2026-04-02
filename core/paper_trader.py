"""
Paper Trader — lógica completa de trading simulado para PolyHunt.

PAPER TRADING ONLY — nunca conecta a wallets reales ni ejecuta órdenes
reales en Polymarket. Toda la operativa es simulada en Supabase.

Reglas de riesgo:
  - Kelly conservador: máximo 5% del balance por posición
  - Stop loss automático: cerrar si el precio se mueve >30% en contra
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from core.db import get_db, db_retry

logger = logging.getLogger(__name__)


# ─── Mercados ──────────────────────────────────────────────────────────────────

def upsert_market(market_data: dict) -> None:
    """Guarda o actualiza un mercado en Supabase."""
    db = get_db()
    try:
        db_retry(
            lambda: db.table("markets").upsert({
                "id":          market_data["id"],
                "question":    market_data["question"],
                "description": market_data.get("description", ""),
                "volume":      market_data.get("volume", 0),
                "end_date":    market_data.get("end_date"),
                "yes_token_id": market_data.get("yes_token_id"),
                "no_token_id":  market_data.get("no_token_id"),
                "last_price":   market_data.get("last_price"),
                "updated_at":  datetime.now(timezone.utc).isoformat(),
            }).execute(),
            context=f"upsert_market({market_data.get('id', '?')[:16]})"
        )
    except Exception as e:
        logger.error(f"[{datetime.now()}] Error guardando mercado {market_data.get('id', '?')}: {e}")


# ─── Snapshots de precio ───────────────────────────────────────────────────────

def save_price_snapshot(market_id: str, price: float, volume: float = None) -> None:
    """Guarda snapshot de precio para historial de backtesting."""
    db = get_db()
    try:
        db_retry(
            lambda: db.table("price_snapshots").insert({
                "market_id": market_id,
                "price":     price,
                "volume":    volume,
            }).execute(),
            context=f"save_price_snapshot({market_id[:16]})"
        )
    except Exception as e:
        logger.error(f"[{datetime.now()}] Error guardando snapshot {market_id}: {e}")


# ─── Análisis LLM ──────────────────────────────────────────────────────────────

def save_llm_analysis(market_id: str, model: str, result: dict,
                      market_price: float, gap: float) -> None:
    """Guarda análisis de LLM para calibración futura. Siempre guardar."""
    db = get_db()
    try:
        db_retry(
            lambda: db.table("llm_analyses").insert({
                "market_id":              market_id,
                "model":                  model,
                "probability_yes":        result.get("probability_yes"),
                "probability_range":      result.get("probability_range"),
                "confidence":             result.get("confidence"),
                "resolution_risk":        result.get("resolution_risk"),
                "edge_detected":          result.get("edge_detected", False),
                "reasoning":              result.get("reasoning"),
                "market_price_at_analysis": market_price,
                "gap":                    gap,
            }).execute(),
            context=f"save_llm_analysis({model})"
        )
    except Exception as e:
        logger.error(f"[{datetime.now()}] Error guardando análisis LLM {model}: {e}")


# ─── Trades ────────────────────────────────────────────────────────────────────

def open_paper_trade(
    market_id: str,
    direction: str,
    size_usd: float,
    entry_price: float,
    llm_probability: float,
    gap: float,
    groq_reasoning: str,
    gemini_reasoning: str = None,
) -> tuple[Optional[int], str]:
    """
    Abre una posición simulada.

    Aplica Kelly conservador: máximo 5% del balance por posición.
    No permite abrir dos posiciones en el mismo mercado.

    Retorna: (trade_id, "OK") o (None, mensaje_de_error)
    """
    db = get_db()
    try:
        # Verificar que no existe posición abierta en este mercado
        existing = db.table("positions").select("market_id").eq("market_id", market_id).execute()
        if existing.data:
            return None, "Ya existe una posición abierta en este mercado"

        # Verificar balance disponible
        account = db.table("account").select("balance").eq("id", 1).single().execute()
        balance = float(account.data["balance"])

        # Kelly conservador: máximo 5% del balance por posición
        size_usd = min(size_usd, balance * 0.05)

        if size_usd <= 0:
            return None, "Tamaño de posición inválido"
        if size_usd > balance:
            return None, "Balance insuficiente"

        # Registrar el trade
        trade = db_retry(
            lambda: db.table("paper_trades").insert({
                "market_id":       market_id,
                "direction":       direction,
                "size_usd":        round(size_usd, 2),
                "entry_price":     entry_price,
                "llm_probability": llm_probability,
                "gap_at_entry":    gap,
                "groq_reasoning":  groq_reasoning,
                "gemini_reasoning": gemini_reasoning,
            }).execute(),
            context=f"open_paper_trade.insert({market_id[:16]})"
        )

        trade_id = trade.data[0]["id"]

        # Descontar del balance
        db_retry(
            lambda: db.table("account").update({
                "balance":    round(balance - size_usd, 2),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", 1).execute(),
            context="open_paper_trade.update_account"
        )

        # Crear posición abierta
        db_retry(
            lambda: db.table("positions").upsert({
                "market_id":     market_id,
                "direction":     direction,
                "size_usd":      round(size_usd, 2),
                "entry_price":   entry_price,
                "current_price": entry_price,
                "unrealized_pnl": 0,
            }).execute(),
            context=f"open_paper_trade.upsert_position({market_id[:16]})"
        )

        logger.info(
            f"[{datetime.now()}] Trade abierto #{trade_id} | {direction} {market_id[:16]}… "
            f"@ {entry_price:.2%} | ${size_usd:.2f}"
        )
        return trade_id, "OK"

    except Exception as e:
        logger.error(f"[{datetime.now()}] Error abriendo trade: {e}")
        return None, str(e)


def close_paper_trade(trade_id: int, exit_price: float) -> Optional[float]:
    """
    Cierra una posición simulada y calcula P&L.

    P&L para YES: (exit - entry) / entry * size
    P&L para NO:  (entry - exit) / entry * size

    Retorna el P&L calculado, o None si hubo un error.
    """
    db = get_db()
    try:
        trade_res = db.table("paper_trades").select("*").eq("id", trade_id).single().execute()
        trade = trade_res.data
        if not trade:
            logger.warning(f"[{datetime.now()}] Trade #{trade_id} no encontrado")
            return None

        direction    = trade["direction"]
        size_usd     = float(trade["size_usd"])
        entry_price  = float(trade["entry_price"])
        market_id    = trade["market_id"]

        # Calcular P&L
        if direction == "YES":
            pnl = (exit_price - entry_price) / entry_price * size_usd
        else:
            pnl = (entry_price - exit_price) / entry_price * size_usd

        pnl = round(pnl, 2)

        # Cerrar el trade
        db_retry(
            lambda: db.table("paper_trades").update({
                "status":    "closed",
                "exit_price": exit_price,
                "pnl":       pnl,
                "closed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", trade_id).execute(),
            context=f"close_paper_trade.update_trade({trade_id})"
        )

        # Devolver capital + P&L al balance
        account     = db.table("account").select("balance, total_pnl").eq("id", 1).single().execute()
        new_balance = round(float(account.data["balance"]) + size_usd + pnl, 2)
        new_total   = round(float(account.data["total_pnl"]) + pnl, 2)

        db_retry(
            lambda: db.table("account").update({
                "balance":    new_balance,
                "total_pnl":  new_total,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", 1).execute(),
            context="close_paper_trade.update_account"
        )

        # Eliminar posición abierta
        db_retry(
            lambda: db.table("positions").delete().eq("market_id", market_id).execute(),
            context=f"close_paper_trade.delete_position({market_id[:16]})"
        )

        emoji = "✅" if pnl >= 0 else "🔴"
        logger.info(f"[{datetime.now()}] {emoji} Trade cerrado #{trade_id} | P&L: ${pnl:+.2f}")
        return pnl

    except Exception as e:
        logger.error(f"[{datetime.now()}] Error cerrando trade #{trade_id}: {e}")
        return None


# ─── Posiciones ────────────────────────────────────────────────────────────────

def update_unrealized_pnl(market_id: str, current_price: float) -> None:
    """Actualiza el P&L no realizado de una posición abierta."""
    db = get_db()
    try:
        pos_res = db.table("positions").select("*").eq("market_id", market_id).single().execute()
        if not pos_res.data:
            return

        pos         = pos_res.data
        size_usd    = float(pos["size_usd"])
        entry_price = float(pos["entry_price"])

        if pos["direction"] == "YES":
            unrealized = (current_price - entry_price) / entry_price * size_usd
        else:
            unrealized = (entry_price - current_price) / entry_price * size_usd

        max_unrealized = float(pos.get("max_unrealized_pnl") or 0)
        if unrealized > max_unrealized:
            max_unrealized = unrealized

        opened_at = pos.get("opened_at")
        days_open = 0
        if opened_at:
            try:
                opened_dt = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
                days_open = max(0, (datetime.now(timezone.utc) - opened_dt).days)
            except Exception:
                days_open = int(pos.get("days_open") or 0)

        db_retry(
            lambda: db.table("positions").update({
                "current_price":  current_price,
                "unrealized_pnl": round(unrealized, 2),
                "max_unrealized_pnl": round(max_unrealized, 2),
                "days_open": days_open,
            }).eq("market_id", market_id).execute(),
            context=f"update_unrealized_pnl({market_id[:16]})"
        )

    except Exception as e:
        logger.error(f"[{datetime.now()}] Error actualizando P&L unrealized {market_id}: {e}")


def check_stop_losses(stop_loss_pct: float = 0.30) -> int:
    """
    Revisa todas las posiciones abiertas y cierra las que se han movido
    más del stop_loss_pct en contra del trade.

    Por defecto: cierra si la pérdida supera el 30%.
    Retorna el número de posiciones cerradas por stop loss.
    """
    db      = get_db()
    closed  = 0

    try:
        positions = db.table("positions").select("*").execute().data or []

        for pos in positions:
            entry_price   = float(pos.get("entry_price", 0) or 0)
            current_price = float(pos.get("current_price", 0) or 0)
            direction     = pos.get("direction")
            market_id     = pos.get("market_id")

            if not entry_price or not current_price or not direction or not market_id:
                continue

            # Calcular pérdida relativa
            if direction == "YES":
                loss_pct = (entry_price - current_price) / entry_price  # positivo = pérdida
            else:
                loss_pct = (current_price - entry_price) / entry_price  # positivo = pérdida

            if loss_pct < stop_loss_pct:
                continue

            # Buscar el trade abierto para este mercado
            trade_res = (
                db.table("paper_trades")
                .select("id")
                .eq("market_id", market_id)
                .eq("status", "open")
                .limit(1)
                .execute()
            )

            if not trade_res.data:
                continue

            trade_id = trade_res.data[0]["id"]
            pnl      = close_paper_trade(trade_id, current_price)

            logger.warning(
                f"[{datetime.now()}] 🛑 STOP LOSS {market_id[:16]}… | "
                f"pérdida={loss_pct:.1%} | P&L=${pnl or 0:.2f}"
            )
            closed += 1

    except Exception as e:
        logger.error(f"[{datetime.now()}] Error en check_stop_losses: {e}")

    return closed


def check_exit_rules(
    stop_loss_pct: float = 0.30,
    take_profit_pct: float = 0.40,
    time_stop_days: int = 30,
    time_stop_gain_pct: float = 0.10,
) -> int:
    """
    Reglas de salida extendidas:
      - Stop loss: pérdida >= 30%
      - Take profit: ganancia > 40%
      - Time stop: >30 días abierta con ganancia < 10%
    """
    db = get_db()
    closed = 0

    try:
        positions = db.table("positions").select("*").execute().data or []
        for pos in positions:
            entry_price = float(pos.get("entry_price", 0) or 0)
            current_price = float(pos.get("current_price", 0) or 0)
            direction = pos.get("direction")
            market_id = pos.get("market_id")
            if not entry_price or not current_price or not direction or not market_id:
                continue

            if direction == "YES":
                move_pct = (current_price - entry_price) / entry_price
            else:
                move_pct = (entry_price - current_price) / entry_price

            opened_at = pos.get("opened_at")
            days_open = int(pos.get("days_open") or 0)
            if opened_at:
                try:
                    opened_dt = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
                    days_open = max(0, (datetime.now(timezone.utc) - opened_dt).days)
                except Exception:
                    pass

            trade_res = (
                db.table("paper_trades")
                .select("id")
                .eq("market_id", market_id)
                .eq("status", "open")
                .limit(1)
                .execute()
            )
            if not trade_res.data:
                continue
            trade_id = trade_res.data[0]["id"]

            if move_pct <= -stop_loss_pct:
                pnl = close_paper_trade(trade_id, current_price)
                logger.warning(f"[Exit] Stop-loss {move_pct:.1%} | {market_id[:16]}… | P&L=${pnl or 0:.2f}")
                closed += 1
                continue

            if move_pct > take_profit_pct:
                pnl = close_paper_trade(trade_id, current_price)
                logger.info(f"[Exit] Take-profit +{move_pct:.1%} | {market_id[:16]}… | P&L=${pnl or 0:.2f}")
                closed += 1
                continue

            if days_open > time_stop_days and move_pct < time_stop_gain_pct:
                pnl = close_paper_trade(trade_id, current_price)
                logger.info(f"[Exit] Time-stop 30d +{move_pct:.1%} | {market_id[:16]}… | P&L=${pnl or 0:.2f}")
                closed += 1
                continue

    except Exception as e:
        logger.error(f"[{datetime.now()}] Error en check_exit_rules: {e}")

    return closed


# ─── Noticias ──────────────────────────────────────────────────────────────────

def save_news_article(
    title: str,
    summary: str,
    source: str,
    url: str,
    published_at: Optional[str],
    relevance_score: float,
    related_market_id: Optional[str] = None,
) -> None:
    """Guarda noticia procesada. Usa url como clave única para evitar duplicados."""
    db = get_db()
    try:
        db_retry(
            lambda: db.table("news_articles").upsert({
                "title":             title,
                "summary":           summary,
                "source":            source,
                "url":               url,
                "published_at":      published_at,
                "relevance_score":   relevance_score,
                "related_market_id": related_market_id,
            }, on_conflict="url").execute(),
            context="save_news_article"
        )
    except Exception as e:
        logger.error(f"[{datetime.now()}] Error guardando noticia: {e}")


# ─── Dashboard ─────────────────────────────────────────────────────────────────

def get_dashboard_data() -> dict:
    """
    Obtiene todos los datos necesarios para el dashboard en una sola llamada.
    Calcula métricas derivadas: win_rate, total_unrealized_pnl.
    """
    db = get_db()
    try:
        account   = db.table("account").select("*").eq("id", 1).single().execute().data
        positions = db.table("positions").select("*, markets(question)").execute().data or []
        trades    = (
            db.table("paper_trades")
            .select("*, markets(question)")
            .order("opened_at", desc=True)
            .limit(50)
            .execute()
            .data or []
        )
        markets  = (
            db.table("markets")
            .select("*")
            .order("updated_at", desc=True)
            .limit(20)
            .execute()
            .data or []
        )
        news     = (
            db.table("news_articles")
            .select("*")
            .order("processed_at", desc=True)
            .limit(10)
            .execute()
            .data or []
        )
        analyses = (
            db.table("llm_analyses")
            .select("*")
            .order("timestamp", desc=True)
            .limit(30)
            .execute()
            .data or []
        )

        # Calcular win rate
        closed_trades = [t for t in trades if t.get("status") == "closed"]
        wins          = sum(1 for t in closed_trades if t.get("pnl") and float(t["pnl"]) > 0)
        win_rate      = (wins / len(closed_trades) * 100) if closed_trades else 0

        # P&L unrealized total
        total_unrealized = sum(float(p.get("unrealized_pnl") or 0) for p in positions)

        # Contar actividad total (sin límite de 50) para bloqueo del input de capital
        total_trades_count    = (db.table("paper_trades").select("id", count="exact").execute().count or 0)
        total_positions_count = (db.table("positions").select("market_id", count="exact").execute().count or 0)

        return {
            "account":             account,
            "positions":           positions,
            "trades":              trades,
            "markets":             markets,
            "news":                news,
            "analyses":            analyses,
            "win_rate":            round(win_rate, 1),
            "open_count":          len(positions),
            "total_unrealized_pnl": round(total_unrealized, 2),
            "has_activity":        (total_trades_count + total_positions_count) > 0,
        }
    except Exception as e:
        logger.error(f"[{datetime.now()}] Error obteniendo datos dashboard: {e}")
        return {}
