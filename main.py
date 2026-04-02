"""
PolyHunt — bot principal con arquitectura event-driven.

Loop 1 (cada 5 min): escaneo de precios, noticias, P&L, reglas de salida.
Loop 2 (continuo): consume cola LLM y ejecuta análisis en cascada.
"""
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from queue import PriorityQueue, Empty

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

__import__("config")

from core.market_scanner import get_political_markets, get_market_price
from core.news_monitor import fetch_news, save_articles_to_db
from core.llm_analyzer import full_analysis, pop_model_stats
from core.paper_trader import (
    open_paper_trade,
    update_unrealized_pnl,
    check_exit_rules,
    upsert_market,
    save_price_snapshot,
)
from core.db import get_db
from core.state import run_event, stop_requested
from core import key_manager
from server import app, set_bot_status

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.ERROR)

_log_file = os.path.join(os.path.dirname(__file__), "polyhunt.log")
_fh = logging.FileHandler(_log_file, encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logging.getLogger().addHandler(_fh)

logger = logging.getLogger(__name__)

SCAN_SECONDS = 5 * 60
TRADE_SIZE_USD = 500.0
FLASK_PORT = int(os.environ.get("PORT", 5000))
FLASK_HOST = "0.0.0.0"
MARKETS_PER_SCAN = 150

_stop_event = threading.Event()
_llm_queue: PriorityQueue = PriorityQueue()
_queued_market_ids: set[str] = set()
_queue_lock = threading.Lock()
_queue_counter = 0


def _handle_sigint(_sig, _frame):
    print("\n[PolyHunt] Señal de parada recibida — cerrando...")
    _stop_event.set()
    run_event.clear()


def _start_flask() -> None:
    try:
        app.run(host=FLASK_HOST, port=FLASK_PORT, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Error arrancando Flask: {e}")


def _score_market(market: dict, price: float, has_recent_news: bool) -> tuple[int, list[str]]:
    score = 0
    reasons = []

    if 0.02 <= price <= 0.98:
        score += 30
    else:
        reasons.append("precio fuera 2%-98%")

    volume = float(market.get("volume") or 0)
    if 50_000 <= volume <= 250_000:
        score += 25
    else:
        reasons.append("volumen fuera 50K-250K")

    end_date = market.get("end_date")
    if end_date:
        try:
            end_dt = datetime.fromisoformat(str(end_date).replace("Z", "+00:00"))
            days = (end_dt - datetime.now(timezone.utc)).days
            if 7 <= days <= 60:
                score += 25
            else:
                reasons.append("dias fuera 7-60")
        except Exception:
            reasons.append("end_date invalida")
    else:
        reasons.append("sin end_date")

    if has_recent_news:
        score += 20
    else:
        reasons.append("sin noticia 24h")

    return score, reasons


def _enqueue_market(market: dict, market_price: float, force: bool, reason: str) -> bool:
    global _queue_counter
    market_id = market.get("id")
    if not market_id:
        return False

    with _queue_lock:
        if market_id in _queued_market_ids:
            return False
        _queued_market_ids.add(market_id)
        _queue_counter += 1
        priority = 0 if force else 1
        _llm_queue.put((priority, _queue_counter, {
            "market": market,
            "market_price": market_price,
            "force": force,
            "reason": reason,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        }))
    return True


def _scan_loop() -> None:
    scan_num = 0
    db = get_db()

    while not _stop_event.is_set():
        if not run_event.wait(timeout=2.0):
            continue

        scan_num += 1
        t0 = datetime.now(timezone.utc)

        scanned = 0
        price_changed = 0
        queued = 0
        skipped_quant = 0
        new_news_count = 0
        trades_closed = 0

        try:
            markets = get_political_markets(min_volume=50_000, max_volume=250_000, min_days_remaining=7)
            markets = sorted(markets, key=lambda m: m.get("volume", 0), reverse=True)[:MARKETS_PER_SCAN]
            scanned = len(markets)

            # Noticias nuevas
            articles = fetch_news(max_age_hours=6)
            new_news_count = save_articles_to_db(articles, active_markets=markets)

            recent_cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            recent_news_rows = (
                db.table("news_articles")
                .select("related_market_id")
                .gte("processed_at", recent_cutoff)
                .not_.is_("related_market_id", "null")
                .execute()
                .data or []
            )
            markets_with_recent_news = {r["related_market_id"] for r in recent_news_rows}

            for market in markets:
                if _stop_event.is_set() or not run_event.is_set():
                    break

                market_id = market.get("id")
                token_id = market.get("yes_token_id")
                if not market_id:
                    continue

                market_price = get_market_price(token_id) if token_id else None
                if market_price is None:
                    market_price = market.get("last_price")
                if market_price is None:
                    continue

                prev = (
                    db.table("markets")
                    .select("last_price,last_llm_analysis_at")
                    .eq("id", market_id)
                    .limit(1)
                    .execute()
                    .data
                )
                prev_price = None
                last_llm_at = None
                if prev:
                    prev_price = prev[0].get("last_price")
                    last_llm_at = prev[0].get("last_llm_analysis_at")

                # Upsert mercado + snapshot
                market["last_price"] = market_price
                upsert_market(market)
                save_price_snapshot(market_id, market_price, market.get("volume"))

                # Cambio de precio > 5%
                change_pct = 0.0
                force = False
                reason = ""
                should_enqueue = False

                if prev_price not in (None, 0):
                    change_pct = abs((float(market_price) - float(prev_price)) / float(prev_price))
                    if change_pct > 0.05:
                        price_changed += 1
                        force = True
                        reason = "price_move"
                        should_enqueue = True

                if market_id in markets_with_recent_news:
                    force = True
                    reason = "news"
                    should_enqueue = True

                if not last_llm_at:
                    force = True
                    reason = "new_market"
                    should_enqueue = True
                else:
                    try:
                        llm_dt = datetime.fromisoformat(str(last_llm_at).replace("Z", "+00:00"))
                        if datetime.now(timezone.utc) - llm_dt > timedelta(hours=8):
                            reason = "cache_expired"
                            should_enqueue = True
                    except Exception:
                        reason = "cache_expired"
                        should_enqueue = True

                # filtro cuantitativo pre-LLM
                score, _reasons = _score_market(market, float(market_price), market_id in markets_with_recent_news)
                if score < 40:
                    skipped_quant += 1
                    logger.info(
                        f"[SCAN] Quant skip | market={market_id[:16]}... | score={score} | "
                        f"reason={','.join(_reasons[:3])}"
                    )
                    continue

                if should_enqueue and _enqueue_market(market, float(market_price), force, reason or "event"):
                    queued += 1
                    logger.info(
                        f"[QUEUE] Enqueued | market={market_id[:16]}... | reason={reason or 'event'} | "
                        f"force={force} | price={float(market_price):.4f}"
                    )

                db.table("markets").update({
                    "last_price": market_price,
                    "last_price_change_pct": round(change_pct, 4),
                    "last_price_checked_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", market_id).execute()

            # P&L + reglas de salida
            positions = db.table("positions").select("market_id, markets(yes_token_id)").execute().data or []
            for pos in positions:
                token_id = (pos.get("markets") or {}).get("yes_token_id")
                if not token_id:
                    continue
                current_price = get_market_price(token_id)
                if current_price is not None:
                    update_unrealized_pnl(pos["market_id"], current_price)

            trades_closed = check_exit_rules(stop_loss_pct=0.30, take_profit_pct=0.40, time_stop_days=30, time_stop_gain_pct=0.10)

            # key maintenance
            key_manager.check_cooldowns()
            if key_manager.should_reset_daily():
                key_manager.reset_daily_counts()

            model_stats = pop_model_stats()
            cd = key_manager.get_cooldown_counts()
            elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
            logger.info(
                f"[Scan #{scan_num}] {elapsed:.1f}s | Mercados={scanned} · CambioPrecio={price_changed} · "
                f"NoticiasNuevas={new_news_count} · ColaLLM={_llm_queue.qsize()} · QuantSkip={skipped_quant} · "
                f"CerebrasOK/Err={model_stats['cerebras_ok']}/{model_stats['cerebras_err']} · "
                f"GroqOK/Err={model_stats['groq_ok']}/{model_stats['groq_err']} · "
                f"Cooldowns=cerebras:{cd.get('cerebras',0)} groq:{cd.get('groq',0)} · Trades={trades_closed}"
            )

            set_bot_status(
                running=True,
                cycle=scan_num,
                markets_scanned=scanned,
                last_cycle=datetime.now(timezone.utc).isoformat(),
                next_cycle=(datetime.now(timezone.utc) + timedelta(seconds=SCAN_SECONDS)).isoformat(),
            )

        except Exception as e:
            logger.error(f"[scan_loop] Error: {e}", exc_info=True)

        _stop_event.wait(timeout=SCAN_SECONDS)


def _llm_loop() -> None:
    db = get_db()
    while not _stop_event.is_set():
        if not run_event.wait(timeout=2.0):
            continue
        try:
            _priority, _ord, item = _llm_queue.get(timeout=1.0)
        except Empty:
            continue

        market = item["market"]
        market_id = market.get("id")
        market_price = item["market_price"]
        force = item["force"]

        try:
            enqueued_at = item.get("enqueued_at")
            if enqueued_at:
                try:
                    queued_dt = datetime.fromisoformat(str(enqueued_at).replace("Z", "+00:00"))
                    if datetime.now(timezone.utc) - queued_dt > timedelta(minutes=30):
                        logger.info(f"[LLMQueue] Item expirado (>30m), descartando {market_id}")
                        continue
                except Exception:
                    pass

            token_id = market.get("yes_token_id")
            if token_id:
                latest_price = get_market_price(token_id)
                if latest_price is not None:
                    market_price = float(latest_price)

            news = (
                db.table("news_articles")
                .select("*")
                .eq("related_market_id", market_id)
                .order("processed_at", desc=True)
                .limit(5)
                .execute()
                .data or []
            )

            cerebras_result, groq_result, gap, should_trade = full_analysis(
                market=market,
                market_price=market_price,
                news_articles=news,
                force=force,
            )

            db.table("markets").update({
                "last_llm_analysis_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", market_id).execute()

            if should_trade:
                probs = []
                for res in (cerebras_result, groq_result):
                    if res and res.get("probability_yes") is not None:
                        probs.append(float(res["probability_yes"]))
                if probs:
                    prob_yes = sum(probs) / len(probs)
                    direction = "YES" if prob_yes > market_price else "NO"
                    groq_reasoning = (groq_result or {}).get("reasoning", "") or (cerebras_result or {}).get("reasoning", "")
                    gemini_reasoning = ""
                    trade_id, _ = open_paper_trade(
                        market_id=market_id,
                        direction=direction,
                        size_usd=TRADE_SIZE_USD,
                        entry_price=market_price,
                        llm_probability=prob_yes,
                        gap=gap,
                        groq_reasoning=groq_reasoning,
                        gemini_reasoning=gemini_reasoning,
                    )
                    if trade_id:
                        logger.info(
                            f"[TRADE] Opened | id={trade_id} | market={market_id[:16]}... | dir={direction} | "
                            f"prob={prob_yes:.3f} | price={market_price:.3f} | gap={gap:.3f}"
                        )
            else:
                c_prob = (cerebras_result or {}).get("probability_yes")
                g_prob = (groq_result or {}).get("probability_yes")
                logger.info(
                    f"[LLM] No trade | market={market_id[:16]}... | c={c_prob if c_prob is not None else '-'} | "
                    f"g={g_prob if g_prob is not None else '-'} | gap={gap:.3f}"
                )

        except Exception as e:
            logger.error(f"[llm_loop] Error procesando mercado {market_id}: {e}")
        finally:
            with _queue_lock:
                if market_id in _queued_market_ids:
                    _queued_market_ids.remove(market_id)


def main() -> None:
    signal.signal(signal.SIGINT, _handle_sigint)

    key_manager.load_keys()

    flask_thread = threading.Thread(target=_start_flask, daemon=True, name="flask")
    flask_thread.start()
    time.sleep(1)

    scan_thread = threading.Thread(target=_scan_loop, daemon=True, name="scan-loop")
    llm_thread = threading.Thread(target=_llm_loop, daemon=True, name="llm-loop")
    scan_thread.start()
    llm_thread.start()

    print()
    print("=" * 52)
    print("   PolyHunt -- Paper Trading Bot")
    print("   SIMULADO -- sin fondos reales")
    print("=" * 52)
    print(f"   Dashboard -> http://localhost:{FLASK_PORT}")
    print("   Escaneo   -> cada 5 minutos")
    print("   LLM Queue -> continuo (2do loop)")
    print("   Parar     -> Ctrl+C")
    print("=" * 52)
    print()

    if not key_manager.has_keys():
        logger.warning("No hay API keys configuradas")

    logger.info("[PolyHunt] Bot arrancado en estado PAUSADO — actívalo desde el dashboard.")
    set_bot_status(running=False)

    while not _stop_event.is_set():
        if not run_event.wait(timeout=5.0):
            if stop_requested.is_set():
                stop_requested.clear()
                set_bot_status(running=False)
                logger.info("[PolyHunt] Bot pausado como se solicitó")
            continue
        if _stop_event.is_set():
            break
        time.sleep(1)

    set_bot_status(running=False)
    logger.info("[PolyHunt] Bot detenido correctamente.")
    sys.exit(0)


if __name__ == "__main__":
    main()
