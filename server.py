"""
Servidor Flask para el dashboard de PolyHunt.

Rutas:
  GET  /                   → sirve dashboard/index.html
  GET  /api/dashboard      → JSON con todos los datos del dashboard
  GET  /api/status         → estado del bot
  POST /api/bot/start      → activa el bot (inicia ciclos)
  POST /api/bot/stop       → pausa el bot (termina ciclo actual antes de parar)
  POST /api/reset          → borra todos los datos y reinicia el balance
  POST /api/config/balance → actualiza el capital inicial (solo si BD vacía)
  GET  /api/logs           → últimas 100 líneas del log del bot
"""
import logging
import os

from flask import Flask, jsonify, request, send_from_directory

from core.db import get_db
from core.paper_trader import get_dashboard_data
from core.state import run_event

logger = logging.getLogger(__name__)

# Estado del bot — se actualiza desde main.py vía set_bot_status()
bot_status: dict = {
    "running":         False,
    "cycle":           0,
    "last_cycle":      None,
    "next_cycle":      None,
    "markets_scanned": 0,
    "trades_open":     0,
}

app = Flask(__name__)


def set_bot_status(**kwargs) -> None:
    """Actualiza campos del bot_status desde main.py."""
    bot_status.update(kwargs)


# ─── Rutas estáticas ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Sirve el dashboard HTML."""
    return send_from_directory("dashboard", "index.html")


@app.route("/<path:filename>")
def serve_static(filename):
    """Sirve archivos estáticos del dashboard (JS, CSS, etc.)."""
    return send_from_directory("dashboard", filename)


# ─── API de datos ─────────────────────────────────────────────────────────────

@app.route("/api/dashboard")
def dashboard():
    """Retorna todos los datos del dashboard en un solo JSON."""
    try:
        data = get_dashboard_data()
        return jsonify(data)
    except Exception as e:
        logger.error(f"Error en /api/dashboard: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/status")
def status():
    """Estado actual del bot (ciclos, última ejecución, etc.)."""
    return jsonify(bot_status)


# ─── Control del bot ──────────────────────────────────────────────────────────

@app.route("/api/bot/start", methods=["POST"])
def bot_start():
    """Activa el bot. Empieza a ejecutar ciclos en el próximo tick."""
    run_event.set()
    bot_status["running"] = True
    logger.info("[API] Bot activado desde el dashboard")
    return jsonify({"ok": True, "status": "running"})


@app.route("/api/bot/stop", methods=["POST"])
def bot_stop():
    """Pausa el bot. El ciclo en curso termina limpiamente; no se inicia el siguiente."""
    run_event.clear()
    bot_status["running"] = False
    logger.info("[API] Bot pausado desde el dashboard")
    return jsonify({"ok": True, "status": "paused"})


# ─── Reset y configuración ────────────────────────────────────────────────────

@app.route("/api/reset", methods=["POST"])
def reset_db():
    """
    Borra TODOS los datos de Supabase y reinicia el balance.
    Requiere que el bot esté PAUSADO — bloquea si está activo.
    Body JSON opcional: {"balance": 10000}
    """
    if bot_status.get("running", False):
        return jsonify({"error": "El bot está activo. Pulsa STOP y espera a que termine el ciclo antes de resetear."}), 400

    data            = request.get_json(silent=True) or {}
    initial_balance = float(data.get("balance", 10000.0))
    if not (100 <= initial_balance <= 1_000_000):
        return jsonify({"error": "Balance debe estar entre $100 y $1,000,000"}), 400

    try:
        db = get_db()
        # Truncar en orden correcto respetando FK constraints
        db.table("positions").delete().neq("market_id", "__reset__").execute()
        db.table("paper_trades").delete().gte("id", 1).execute()
        db.table("llm_analyses").delete().gte("id", 1).execute()
        db.table("price_snapshots").delete().gte("id", 1).execute()
        db.table("news_articles").delete().gte("id", 1).execute()
        db.table("markets").delete().neq("id", "__reset__").execute()
        db.table("account").delete().eq("id", 1).execute()
        db.table("account").insert({
            "id": 1,
            "balance": initial_balance,
            "initial_balance": initial_balance,
            "total_invested": 0.0,
            "total_pnl": 0.0,
        }).execute()
        logger.info(f"[Reset] BD reseteada — balance inicial ${initial_balance:,.2f}")
        return jsonify({"ok": True, "balance": initial_balance, "status": "paused"})
    except Exception as e:
        logger.error(f"[Reset] Error reseteando BD: {e}")
        return jsonify({"error": str(e)}), 500



# ─── Logs ─────────────────────────────────────────────────────────────────────

@app.route("/api/logs")
def get_logs():
    """Retorna las últimas 100 líneas del archivo polyhunt.log."""
    log_path = os.path.join(os.path.dirname(__file__), "polyhunt.log")
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return jsonify({"lines": [l.rstrip() for l in lines[-100:]]})
    except FileNotFoundError:
        return jsonify({"lines": []})
    except Exception as e:
        return jsonify({"lines": [f"Error leyendo logs: {e}"]}), 500
