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
from openai import OpenAI
from groq import Groq

from core.db import get_db
from core.paper_trader import get_dashboard_data
from core.state import run_event, stop_requested
from core import key_manager

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
    # Determinar estado real basado en los eventos
    if run_event.is_set():
        current_status = "running"
    elif stop_requested.is_set():
        current_status = "stopping"
    else:
        current_status = "paused"
    
    return jsonify({
        **bot_status,
        "status": current_status,
        "running": run_event.is_set(),  # mantener para compatibilidad
    })


# ─── Control del bot ──────────────────────────────────────────────────────────

@app.route("/api/bot/start", methods=["POST"])
def bot_start():
    """Activa el bot. Empieza a ejecutar ciclos en el próximo tick."""
    if not key_manager.has_keys():
        return jsonify({
            "ok": False,
            "error": "No hay API keys. Añádelas desde Ajustes antes de activar el bot.",
        }), 400
    run_event.set()
    bot_status["running"] = True
    logger.info("[API] Bot activado desde el dashboard")
    return jsonify({"ok": True, "status": "running"})


@app.route("/api/bot/stop", methods=["POST"])
def bot_stop():
    """Pausa el bot. El ciclo en curso termina limpiamente; no se inicia el siguiente."""
    run_event.clear()
    stop_requested.set()
    # No actualizar bot_status["running"] aquí — main.py lo hará cuando termine el ciclo
    logger.info("[API] Stop solicitado desde el dashboard — esperando fin de ciclo")
    return jsonify({"ok": True, "status": "stopping"})


# ─── Reset y configuración ────────────────────────────────────────────────────

@app.route("/api/reset", methods=["POST"])
def reset_db():
    """
    Borra TODOS los datos de Supabase y reinicia el balance.
    Requiere que el bot esté PAUSADO — bloquea si está activo o parándose.
    Body JSON opcional: {"balance": 10000}
    """
    # Triple protección: no permitir reset si está corriendo O parándose
    if run_event.is_set() or stop_requested.is_set():
        return jsonify({
            "error": "El bot está activo o parándose. Espera a que el estado sea PAUSADO antes de resetear."
        }), 400

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
        level_filter = (request.args.get("level") or "ALL").strip().upper()
        source_filter = (request.args.get("source") or "ALL").strip().upper()
        search_query = (request.args.get("q") or "").strip().lower()
        try:
            limit = int(request.args.get("limit", 100))
        except (TypeError, ValueError):
            limit = 100
        limit = max(10, min(limit, 500))

        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        # Si el proceso está escribiendo el log en paralelo, la última línea puede
        # quedar incompleta (sin salto de línea final). La descartamos para evitar
        # mostrar una línea "cortada" en el dashboard.
        if lines and not lines[-1].endswith("\n"):
            lines = lines[:-1]

        filtered = []
        for raw in lines:
            line = raw.rstrip()
            upper = line.upper()

            if level_filter in ("INFO", "WARNING", "ERROR") and f"[{level_filter}]" not in upper:
                continue
            if source_filter != "ALL" and f"[{source_filter}]" not in upper:
                continue
            if search_query and search_query not in line.lower():
                continue

            filtered.append(line)

        return jsonify({"lines": filtered[-limit:]})
    except FileNotFoundError:
        return jsonify({"lines": []})
    except Exception as e:
        return jsonify({"lines": [f"Error leyendo logs: {e}"]}), 500


@app.route("/api/logs/clear", methods=["POST"])
def clear_logs():
    """Borra el contenido del archivo polyhunt.log."""
    log_path = os.path.join(os.path.dirname(__file__), "polyhunt.log")
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("")
        logger.info("[API] Logs borrados desde el dashboard")
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"[API] Error borrando logs: {e}")
        return jsonify({"error": str(e)}), 500


def _mask_key(value: str) -> str:
    tail = (value or "")[-4:]
    return f"****{tail}" if tail else "****"


def _validate_key(service: str, key_value: str) -> tuple[bool, str]:
    key_value = (key_value or "").strip()
    if not key_value:
        return False, "Key vacia"
    if len(key_value) < 20:
        return False, "Key demasiado corta"
    try:
        if service == "cerebras":
            client = OpenAI(base_url="https://api.cerebras.ai/v1", api_key=key_value)
            client.chat.completions.create(
                model="qwen-3-235b-a22b-instruct-2507",
                messages=[{"role": "user", "content": "Responde ok"}],
                max_tokens=5,
                temperature=0,
                timeout=15,
            )
            return True, "ok"
        if service == "groq":
            client = Groq(api_key=key_value)
            client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": "Responde ok"}],
                max_tokens=5,
                temperature=0,
                timeout=15,
            )
            return True, "ok"
        return False, "Servicio inválido"
    except Exception as e:
        return False, str(e)


@app.route("/api/settings/keys", methods=["GET"])
def get_settings_keys():
    try:
        db = get_db()
        rows = (
            db.table("api_keys")
            .select("id, service, label, is_enabled, in_cooldown, cooldown_until, calls_today, tokens_today, key_value, created_at")
            .order("created_at", desc=False)
            .execute()
            .data
            or []
        )
        safe = []
        for r in rows:
            service = r.get("service")
            if service not in ("cerebras", "groq"):
                continue
            safe.append({
                "id": r.get("id"),
                "service": service,
                "label": r.get("label"),
                "is_enabled": r.get("is_enabled", True),
                "in_cooldown": r.get("in_cooldown", False),
                "cooldown_until": r.get("cooldown_until"),
                "calls_today": r.get("calls_today", 0),
                "tokens_today": r.get("tokens_today", 0),
                "last_4": (r.get("key_value") or "")[-4:] or "????",
                "masked": _mask_key(r.get("key_value") or ""),
            })
        return jsonify({"keys": safe})
    except Exception as e:
        logger.error(f"Error en GET /api/settings/keys: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings/keys", methods=["POST"])
def create_settings_key():
    data = request.get_json(silent=True) or {}
    service = (data.get("service") or "").strip().lower()
    key_value = (data.get("key_value") or "").strip()
    label = (data.get("label") or "").strip()

    if service not in ("cerebras", "groq"):
        return jsonify({"ok": False, "error": "service inválido"}), 400
    if not key_value:
        return jsonify({"ok": False, "error": "key_value es obligatorio"}), 400

    if len(key_value) > 1024:
        return jsonify({"ok": False, "error": "key_value demasiado larga"}), 400

    if len(label) > 120:
        return jsonify({"ok": False, "error": "label demasiado larga (max 120)"}), 400

    try:
        db = get_db()
        existing = (
            db.table("api_keys")
            .select("id")
            .eq("service", service)
            .eq("key_value", key_value)
            .limit(1)
            .execute()
            .data
            or []
        )
        if existing:
            return jsonify({"ok": False, "error": "Esa key ya existe para ese servicio"}), 409
    except Exception:
        pass

    ok, message = _validate_key(service, key_value)
    if not ok:
        return jsonify({"ok": False, "error": f"Validación fallida: {message}"}), 400

    try:
        created = (
            db.table("api_keys")
            .insert({
                "service": service,
                "key_value": key_value,
                "label": label or None,
                "is_enabled": True,
            })
            .execute()
            .data
        )
        row = created[0]
        key_manager.reload_keys()
        return jsonify({
            "ok": True,
            "id": row.get("id"),
            "label": row.get("label"),
            "service": row.get("service"),
            "last_4": key_value[-4:],
        })
    except Exception as e:
        logger.error(f"Error en POST /api/settings/keys: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/settings/keys/<int:key_id>", methods=["DELETE"])
def delete_settings_key(key_id: int):
    try:
        db = get_db()
        db.table("api_keys").delete().eq("id", key_id).execute()
        key_manager.reload_keys()
        return jsonify({"ok": True, "id": key_id})
    except Exception as e:
        logger.error(f"Error en DELETE /api/settings/keys/{key_id}: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/settings/keys/<int:key_id>", methods=["PATCH"])
def patch_settings_key(key_id: int):
    data = request.get_json(silent=True) or {}
    if "is_enabled" not in data:
        return jsonify({"ok": False, "error": "is_enabled es obligatorio"}), 400
    is_enabled = bool(data.get("is_enabled"))
    try:
        db = get_db()
        db.table("api_keys").update({"is_enabled": is_enabled}).eq("id", key_id).execute()
        key_manager.reload_keys()
        return jsonify({"ok": True, "id": key_id, "is_enabled": is_enabled})
    except Exception as e:
        logger.error(f"Error en PATCH /api/settings/keys/{key_id}: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/settings/keys/status", methods=["GET"])
def get_settings_keys_status():
    try:
        return jsonify({"status": key_manager.get_keys_status()})
    except Exception as e:
        logger.error(f"Error en GET /api/settings/keys/status: {e}")
        return jsonify({"error": str(e)}), 500
