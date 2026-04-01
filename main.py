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
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone

# Forzar UTF-8 en consola Windows para caracteres especiales
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Validar variables de entorno antes de nada
__import__("config")  # valida variables de entorno al arrancar (sys.exit si falta alguna)

from core.market_scanner import get_political_markets, save_markets_to_db, get_market_price
from core.llm_analyzer   import full_analysis
from core.news_monitor   import fetch_news, save_articles_to_db
from core.paper_trader   import (
    open_paper_trade,
    update_unrealized_pnl,
    check_stop_losses,
)
from core.state import run_event
from server import app, set_bot_status

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# FileHandler: escribe en polyhunt.log para que /api/logs pueda leerlo
_log_file = os.path.join(os.path.dirname(__file__), "polyhunt.log")
_fh = logging.FileHandler(_log_file, encoding="utf-8")
_fh.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
logging.getLogger().addHandler(_fh)

logger = logging.getLogger(__name__)

# ─── Configuración ────────────────────────────────────────────────────────────

CYCLE_SECONDS        = 15 * 60   # 15 minutos entre ciclos
TRADE_SIZE_USD       = 500.0      # tamaño base de cada posición en USD
FLASK_PORT           = int(os.environ.get("PORT", 5000))
FLASK_HOST           = "0.0.0.0"
MARKETS_PER_CYCLE    = 50         # máximo de mercados a analizar por ciclo
PRICE_SKIP_LOW       = 0.02       # skip si precio YES < 2% (casi resuelto NO)
PRICE_SKIP_HIGH      = 0.98       # skip si precio YES > 98% (casi resuelto YES)

# Palabras clave que identifican mercados especulativos sin datos objetivos.
# Conteos de redes sociales, métricas de engagement, etc.
SPECULATION_KEYWORDS = frozenset([
    "tweet", "tweets", "post", "posts", "retweet", "retweets",
    "follower", "followers", "view", "views", "like", "likes",
])

# ─── Control de parada ────────────────────────────────────────────────────────

_stop_event = threading.Event()


def _handle_sigint(_sig, _frame):
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
        news_saved = save_articles_to_db(articles, active_markets=markets)
        logger.info(f"[Ciclo] {len(articles)} artículos obtenidos, {news_saved} guardados")
    except Exception as e:
        logger.error(f"[Ciclo] Error procesando noticias: {e}")

    # ── 3. Seleccionar top-50 por volumen con rotación entre ciclos ──────────────
    # Ordenar por volumen desc y tomar los primeros MARKETS_PER_CYCLE,
    # rotando el punto de inicio para cubrir todos los mercados gradualmente.
    markets_sorted = sorted(markets, key=lambda m: m.get("volume", 0), reverse=True)
    total_markets  = len(markets_sorted)
    rotation_start = ((cycle_num - 1) * MARKETS_PER_CYCLE) % max(total_markets, 1)
    # Construir ventana rotativa circular
    indices = [(rotation_start + i) % total_markets for i in range(min(MARKETS_PER_CYCLE, total_markets))]
    batch   = [markets_sorted[i] for i in indices]

    logger.info(
        f"[Ciclo] Paso 3/5 — Analizando {len(batch)}/{total_markets} mercados "
        f"(rot={rotation_start}, ciclo={cycle_num})…"
    )
    trades_opened = 0

    try:
        from core.news_monitor import get_relevant_news
        news_cache = get_relevant_news(limit=5)
    except Exception:
        news_cache = []

    # Cargar set de market_ids con noticias relacionadas en los últimos 7 días.
    # Si related_news_count = 0 para un mercado → skip (sin contexto suficiente).
    # Si la consulta falla → markets_with_news vacío → no filtrar nada (fail-open).
    try:
        from core.db import get_db as _get_db
        from datetime import timedelta
        _cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        _news_rows = (
            _get_db()
            .table("news_articles")
            .select("related_market_id")
            .gte("processed_at", _cutoff)
            .not_.is_("related_market_id", "null")
            .execute()
            .data or []
        )
        markets_with_news = {row["related_market_id"] for row in _news_rows}
        logger.info(f"[Ciclo] {len(markets_with_news)} mercados con noticias en los últimos 7 días")
    except Exception as e:
        logger.warning(f"[Ciclo] No se pudo cargar markets_with_news — filtro desactivado: {e}")
        markets_with_news = set()

    for market in batch:
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

        # Filtro 1: precios extremos — mercado casi resuelto, no hay edge real
        if market_price < PRICE_SKIP_LOW or market_price > PRICE_SKIP_HIGH:
            logger.info(
                f"[Ciclo] SKIP precio extremo ({market_price:.1%}) | {question[:50]}"
            )
            continue

        # Filtro 2: mercados especulativos sin datos objetivos (métricas de redes sociales)
        question_lower = question.lower()
        if any(kw in question_lower for kw in SPECULATION_KEYWORDS):
            logger.info(
                f"[Ciclo] SKIP keyword especulativo | {question[:50]}"
            )
            continue

        # Anotar si este mercado tiene noticias recientes (afecta umbral de gap post-análisis)
        has_news = bool(markets_with_news) and market_id in markets_with_news

        # Análisis LLM dual
        try:
            groq_result, gemini_result, gap, should_trade = full_analysis(
                market=market,
                market_price=market_price,
                news_articles=news_cache,
            )
        except Exception as e:
            logger.error(f"[Ciclo] Error en full_analysis para {market_id[:16]}…: {e}")
            continue

        if not should_trade:
            continue

        # Filtro 3: confianza baja → no operar aunque el gap sea grande
        # (full_analysis ya lo chequea internamente, esto es defensa en profundidad)
        if groq_result.get("confidence") == "low":
            logger.info(
                f"[Ciclo] SKIP confianza baja | {question[:50]}"
            )
            continue

        # Filtro 4 (two-tier noticias):
        #   - Con noticias recientes → umbral normal gap >= 15% (ya garantizado por full_analysis)
        #   - Sin noticias recientes → exigir gap >= 20% para compensar la incertidumbre
        if not has_news and gap < 0.20:
            logger.info(
                f"[Ciclo] SKIP gap {gap:.1%} insuficiente sin noticias (req. >20%) | {question[:50]}"
            )
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
            market_id        = market_id,
            direction        = direction,
            size_usd         = TRADE_SIZE_USD,
            entry_price      = market_price,
            llm_probability  = float(prob_yes),
            gap              = gap,
            groq_reasoning   = groq_reasoning,
            gemini_reasoning = gemini_reasoning,
        )

        if trade_id:
            trades_opened += 1
            logger.info(
                f"[Ciclo] Trade #{trade_id} abierto | {direction} {question[:40]}… "
                f"gap={gap:.1%} @ {market_price:.2%}"
            )
        else:
            logger.debug(f"[Ciclo] Trade no abierto: {msg}")

        # Pausa para no saturar la API de Groq
        time.sleep(0.5)

    logger.info(f"[Ciclo] {trades_opened} trades abiertos en este ciclo")

    # ── 4. Actualizar P&L no realizado ────────────────────────────────────────
    logger.info("[Ciclo] Paso 4/5 — Actualizando P&L no realizado…")
    try:
        from core.db import get_db
        db = get_db()
        positions = db.table("positions").select("market_id, markets(yes_token_id)").execute().data or []

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
    print("=" * 52)
    print("   PolyHunt -- Paper Trading Bot")
    print("   SIMULADO -- sin fondos reales")
    print("=" * 52)
    print(f"   Dashboard -> http://localhost:{FLASK_PORT}")
    print(f"   Ciclo     -> cada {CYCLE_SECONDS // 60} minutos")
    print(f"   Tamano    -> ${TRADE_SIZE_USD:.0f} por posicion")
    print("   Parar     -> Ctrl+C")
    print("=" * 52)
    print()

    logger.info("[PolyHunt] Bot arrancado en estado PAUSADO — actívalo desde el dashboard.")
    set_bot_status(running=False)

    cycle = 0
    while not _stop_event.is_set():
        # Bloquear mientras el bot está pausado.
        # Timeout de 5s para poder chequear _stop_event periódicamente.
        if not run_event.wait(timeout=5.0):
            continue
        if _stop_event.is_set():
            break

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
