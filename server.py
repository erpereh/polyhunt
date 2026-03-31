"""
Servidor Flask para el dashboard de PolyHunt.

Rutas:
  GET /               → sirve dashboard/index.html
  GET /api/dashboard  → JSON con todos los datos del dashboard
  GET /api/status     → estado del bot (bot_status compartido con main.py)
"""
import logging
from flask import Flask, jsonify

from core.paper_trader import get_dashboard_data

logger = logging.getLogger(__name__)

# Estado del bot — se actualiza desde main.py vía set_bot_status()
bot_status: dict = {
    "running":    False,
    "cycle":      0,
    "last_cycle": None,
    "next_cycle": None,
    "markets_scanned": 0,
    "trades_open": 0,
}

app = Flask(__name__, static_folder="dashboard", static_url_path="")


def set_bot_status(**kwargs) -> None:
    """Actualiza campos del bot_status desde main.py."""
    bot_status.update(kwargs)


@app.route("/")
def index():
    """Sirve el dashboard HTML."""
    return app.send_static_file("index.html")


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
