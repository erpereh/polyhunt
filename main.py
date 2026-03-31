"""
PolyHunt — Bot principal de paper trading en Polymarket.

PAPER TRADING ONLY — nunca conecta a wallets reales ni ejecuta órdenes reales.
Toda la operativa es simulada en Supabase.

Ciclo principal (cada 15 minutos):
  1. Escanear mercados políticos de Polymarket
  2. Guardar mercados y snapshots en Supabase
  3. Procesar feeds RSS → guardar noticias relevantes
  4. Analizar cada mercado con LLM dual (Groq + Gemini)
  5. Abrir posición si should_trade=True
  6. Actualizar P&L no realizado de posiciones abiertas
  7. Revisar stop losses (cerrar si pérdida > 30%)

Arranque:
  - Valida configuración (config.py hace sys.exit si falta alguna variable)
  - Inicia Flask en hilo daemon (puerto 5000)
  - Muestra URL del dashboard
  - Inicia loop principal
  - Ctrl+C para parada limpia
"""
import logging
import signal
import sys
import threading
import time
from datetime import datetime, timezone

# Validar variables de entorno antes de nada
import config  # noqa: F401 — valida y hace sys.exit si falta alguna variable

from core.market_scanner import get_political_markets, save_markets_to_db, get_market_price
from core.llm_analyzer   import full_analysis
from core.news_monitor   import fetch_news, save_articles_to_db
from core.paper_trader   import (
    open_paper_trade,
    update_unrealized_pnl,
    check_stop_losses,
)
from server import app, set_bot_status

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Configuración ────────────────────────────────────────────────────────────

CYCLE_SECONDS   = 15 * 60   # 15 minutos entre ciclos
TRADE_SIZE_USD  = 500.0      # tamaño base de cada posición en USD
FLASK_PORT      = 5000
FLASK_HOST      = "0.0.0.0"

# ─── Control de parada ────────────────────────────────────────────────────────

_stop_event = threading.Event()


def _handle_sigint(sig, frame):
    """Maneja Ctrl+C para parada limpia."""
    print("\n[PolyHunt] Señal de parada recibida — cerrando…")
    _stop_event.set()


# ─── Flask en hilo daemon ─────────────────────────────────────────────────────

def _start_flask() -> None:
    """Inicia el servidor Flask en un hilo daemon (no bloquea el loop principal)."""
    import os
    # Desactivar log de werkzeug en producción para no ensuciar la terminal
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    os.environ.setdefault("FLASK_ENV", "production")

    try:
        app.run(host=FLASK_HOST, port=FLASK_PORT, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Error arrancando Flask: {e}")


# ─── Ciclo principal ──────────────────────────────────────────────────────────

def run_cycle(cycle_num: int) -> None:
    """
    Ejecuta un ciclo completo de trading:
      1. Escanear mercados
      2. Procesar noticias
      3. Analizar con LLM y abrir trades
      4. Actualizar P&L no realizado
      5. Revisar stop losses
    """
    cycle_start = datetime.now(timezone.utc)
    logger.info(f"{'─'*60}")
    logger.info(f"[Ciclo #{cycle_num}] Iniciando — {cycle_start.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    # ── 1. Escanear mercados ───────────────────────────────────────────────────
    logger.info("[Ciclo] Paso 1/5 — Escaneando mercados políticos…")
    try:
        markets = get_political_markets(
            min_volume=50_000,
            max_volume=250_000,
            min_days_remaining=7,
        )
        saved_count = save_markets_to_db(markets) if markets else 0
        logger.info(f"[Ciclo] {len(markets)} mercados encontrados, {saved_count} guardados")
    except Exception as e:
        logger.error(f"[Ciclo] Error escaneando mercados: {e}")
        markets = []

    set_bot_status(markets_scanned=len(markets))

    # ── 2. Procesar noticias ──────────────────────────────────────────────────
    logger.info("[Ciclo] Paso 2/5 — Procesando noticias RSS…")
    try:
        articles = fetch_news(max_age_hours=6)
        news_saved = save_articles_to_db(articles, active_markets=markets[:15])
        logger.info(f"[Ciclo] {len(articles)} artículos obtenidos, {news_saved} guardados")
    except Exception as e:
        logger.error(f"[Ciclo] Error procesando noticias: {e}")

    # ── 3. Analizar mercados con LLM y abrir trades ───────────────────────────
    logger.info(f"[Ciclo] Paso 3/5 — Analizando {len(markets)} mercados con LLM…")
    trades_opened = 0

    for market in markets:
        if _stop_event.is_set():
            break

        market_id    = market.get("id")
        yes_token_id = market.get("yes_token_id")
        question     = market.get("question", "")

        # Obtener precio actualizado desde CLOB API
        market_price = None
        if yes_token_id:
            market_price = get_market_price(yes_token_id)

        # Fallback al precio almacenado en el escaneo
        if market_price is None:
            market_price = market.get("last_price")

        if market_price is None:
            logger.debug(f"[Ciclo] Sin precio para {market_id[:16]}… — saltando")
            continue

        # Obtener noticias relevantes del market almacenadas en Supabase
        try:
            from core.news_monitor import get_relevant_news
            news_for_market = get_relevant_news(question, limit=5)
        except Exception:
            news_for_market = []

        # Análisis LLM dual
        try:
            groq_result, gemini_result, gap, should_trade = full_analysis(
                market=market,
                market_price=market_price,
                news_articles=news_for_market,
            )
        except Exception as e:
            logger.error(f"[Ciclo] Error en full_analysis para {market_id[:16]}…: {e}")
            continue

        if not should_trade:
            continue

        # Determinar dirección del trade
        prob_yes = groq_result.get("probability_yes")
        if prob_yes is None:
            continue

        direction = "YES" if float(prob_yes) > market_price else "NO"

        # Abrir posición
        groq_reasoning   = groq_result.get("reasoning", "")
        gemini_reasoning = gemini_result.get("reasoning", "") if gemini_result else None

        trade_id, msg = open_paper_trade(
            market_id       = market_id,
            direction       = direction,
            size_usd        = TRADE_SIZE_USD,
            entry_price     = market_price,
            llm_probability = float(prob_yes),
            gap             = gap,
            groq_reasoning  = groq_reasoning,
            gemini_reasoning= gemini_reasoning,
        )

        if trade_id:
            trades_opened += 1
            logger.info(
                f"[Ciclo] ✅ Trade #{trade_id} abierto | {direction} {question[:40]}… "
                f"gap={gap:.1%} @ {market_price:.2%}"
            )
        else:
            logger.debug(f"[Ciclo] Trade no abierto: {msg}")

        # Pequeña pausa para no saturar la API de Groq
        time.sleep(0.5)

    logger.info(f"[Ciclo] {trades_opened} trades abiertos en este ciclo")

    # ── 4. Actualizar P&L no realizado ────────────────────────────────────────
    logger.info("[Ciclo] Paso 4/5 — Actualizando P&L no realizado…")
    try:
        from core.db import get_db
        db = get_db()
        positions = db.table("positions").select("market_id, yes_token_id, markets(yes_token_id)").execute().data or []

        for pos in positions:
            if _stop_event.is_set():
                break
            mid = pos.get("market_id")
            # Intentar obtener yes_token_id desde el join con markets
            market_info = pos.get("markets") or {}
            token_id = market_info.get("yes_token_id")
            if not token_id:
                # Buscar en la lista de mercados escaneados en este ciclo
                mkt = next((m for m in markets if m.get("id") == mid), None)
                token_id = mkt.get("yes_token_id") if mkt else None

            if not token_id:
                continue

            current_price = get_market_price(token_id)
            if current_price is not None:
                update_unrealized_pnl(mid, current_price)

    except Exception as e:
        logger.error(f"[Ciclo] Error actualizando P&L unrealized: {e}")

    # ── 5. Stop losses ────────────────────────────────────────────────────────
    logger.info("[Ciclo] Paso 5/5 — Revisando stop losses…")
    try:
        stopped = check_stop_losses(stop_loss_pct=0.30)
        if stopped:
            logger.warning(f"[Ciclo] {stopped} posiciones cerradas por stop loss")
    except Exception as e:
        logger.error(f"[Ciclo] Error en stop losses: {e}")

    # ── Actualizar estado del bot ─────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    set_bot_status(
        running    = True,
        cycle      = cycle_num,
        last_cycle = now.isoformat(),
        next_cycle = datetime.fromtimestamp(
            now.timestamp() + CYCLE_SECONDS, tz=timezone.utc
        ).isoformat(),
        trades_open = trades_opened,
    )

    elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
    logger.info(f"[Ciclo #{cycle_num}] Completado en {elapsed:.1f}s")


# ─── Punto de entrada ─────────────────────────────────────────────────────────

def main() -> None:
    signal.signal(signal.SIGINT, _handle_sigint)

    # Iniciar Flask en hilo daemon
    flask_thread = threading.Thread(target=_start_flask, daemon=True, name="flask")
    flask_thread.start()
    time.sleep(1)  # esperar que Flask arranque

    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║          ◈  PolyHunt — Paper Trading Bot         ║")
    print("║                SIMULADO — sin fondos reales      ║")
    print("╠══════════════════════════════════════════════════╣")
    print(f"║  Dashboard → http://localhost:{FLASK_PORT}                 ║")
    print(f"║  Ciclo     → cada {CYCLE_SECONDS // 60} minutos                      ║")
    print(f"║  Tamaño    → ${TRADE_SIZE_USD:.0f} por posición                ║")
    print("║  Parar     → Ctrl+C                              ║")
    print("╚══════════════════════════════════════════════════╝")
    print()

    set_bot_status(running=True)

    cycle = 0
    while not _stop_event.is_set():
        cycle += 1
        try:
            run_cycle(cycle)
        except Exception as e:
            logger.error(f"[main] Error inesperado en ciclo #{cycle}: {e}", exc_info=True)

        # Esperar hasta el próximo ciclo o hasta que se reciba señal de parada
        _stop_event.wait(timeout=CYCLE_SECONDS)

    set_bot_status(running=False)
    logger.info("[PolyHunt] Bot detenido correctamente.")
    sys.exit(0)


if __name__ == "__main__":
    main()
