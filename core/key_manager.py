"""
Key Manager — gestión centralizada de API keys desde Supabase.

Funcionalidad:
  - Carga keys habilitadas de Supabase al arrancar (agrupadas por servicio)
  - Rotación automática: get_next_key() devuelve la key con menos calls_today
  - Cooldown: si una key recibe 429, se pone en cooldown 30 min
  - Reset diario: a medianoche Pacific Time se resetean los contadores
  - Reload: recargar keys sin reiniciar (cuando el usuario añade/elimina)

Uso:
  from core.key_manager import get_next_key, mark_success, mark_cooldown
  
  key_data = get_next_key("cerebras")
  if key_data:
      # usar key_data["key_value"] para la llamada
      mark_success(key_data["id"], tokens_used=150)
  else:
      # todas las keys en cooldown o no hay keys
      pass
"""
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from core.db import get_db

logger = logging.getLogger(__name__)

# Timezone para reset diario
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")

# Duración del cooldown en minutos
COOLDOWN_MINUTES = 30

# Cache en memoria de las keys agrupadas por servicio
# Estructura: { "cerebras": [key_dict, ...], "gemini": [...], "groq": [...] }
_keys_cache: dict[str, list[dict]] = {
    "cerebras": [],
    "gemini": [],
    "groq": [],
}

# Fecha del último reset diario (Pacific time)
_last_daily_reset: Optional[datetime] = None
_cache_lock = threading.RLock()


def _parse_utc_datetime(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _set_cached_key_fields(key_id: int, fields: dict) -> None:
    for service_keys in _keys_cache.values():
        for key in service_keys:
            if key.get("id") == key_id:
                key.update(fields)
                return


def _get_cached_key_by_id(key_id: int) -> Optional[dict]:
    for service_keys in _keys_cache.values():
        for key in service_keys:
            if key.get("id") == key_id:
                return key
    return None


def load_keys() -> dict[str, int]:
    """
    Carga todas las keys habilitadas desde Supabase a memoria.
    
    Returns:
        dict con conteo por servicio: {"cerebras": 2, "gemini": 3, "groq": 1}
    """
    global _keys_cache
    db = get_db()
    counts = {"cerebras": 0, "gemini": 0, "groq": 0}
    
    try:
        result = db.table("api_keys").select("*").eq("is_enabled", True).execute()
        
        new_cache = {"cerebras": [], "gemini": [], "groq": []}
        
        for row in result.data or []:
            service = row.get("service")
            if service in new_cache:
                new_cache[service].append(row)
                counts[service] += 1

        with _cache_lock:
            _keys_cache = new_cache
        
        total = sum(counts.values())
        if total > 0:
            logger.info(
                f"[KeyManager] Cargadas {total} keys: "
                f"cerebras={counts['cerebras']}, gemini={counts['gemini']}, groq={counts['groq']}"
            )
        else:
            logger.warning(
                "[KeyManager] No hay API keys configuradas. "
                "Añádelas desde Ajustes antes de activar el bot."
            )
        
        return counts
        
    except Exception as e:
        logger.error(f"[KeyManager] Error cargando keys: {e}")
        return counts


def reload_keys() -> dict[str, int]:
    """
    Recarga las keys desde Supabase. 
    Llamar cuando el usuario añade/elimina keys desde el dashboard.
    """
    logger.info("[KeyManager] Recargando keys desde Supabase...")
    return load_keys()


def get_next_key(service: str) -> Optional[dict]:
    """
    Obtiene la siguiente key disponible para un servicio.
    
    Lógica:
      1. Filtrar keys que NO están en cooldown
      2. Ordenar por calls_today (menor primero)
      3. Retornar la primera
    
    Args:
        service: "cerebras", "gemini", o "groq"
    
    Returns:
        dict con la key completa, o None si no hay disponibles
    """
    if service not in _keys_cache:
        logger.warning(f"[KeyManager] Servicio desconocido: {service}")
        return None

    now = datetime.now(timezone.utc)
    available = []
    keys_to_release: list[int] = []

    with _cache_lock:
        keys = list(_keys_cache.get(service, []))

        if not keys:
            return None

        for key in keys:
            if key.get("in_cooldown"):
                cooldown_until = _parse_utc_datetime(key.get("cooldown_until"))
                if cooldown_until and now < cooldown_until:
                    continue
                key["in_cooldown"] = False
                key["cooldown_until"] = None
                if key.get("id") is not None:
                    keys_to_release.append(key["id"])

            available.append(key)
    
    if not available:
        logger.warning(f"[KeyManager] Todas las keys de {service} están en cooldown")
        return None

    if keys_to_release:
        try:
            db = get_db()
            for key_id in keys_to_release:
                db.table("api_keys").update({
                    "in_cooldown": False,
                    "cooldown_until": None,
                }).eq("id", key_id).execute()
        except Exception as e:
            logger.debug(f"[KeyManager] No se pudo sincronizar liberación de cooldown: {e}")

    # Ordenar por calls_today (menor primero)
    available.sort(key=lambda k: k.get("calls_today", 0))
    
    return available[0]


def mark_success(key_id: int, tokens_used: int = 0) -> None:
    """
    Marca una key como usada exitosamente.
    Actualiza last_used_at, incrementa calls_today y tokens_today.
    
    Args:
        key_id: ID de la key en Supabase
        tokens_used: tokens consumidos en la llamada
    """
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    
    try:
        safe_tokens = max(0, int(tokens_used or 0))
        with _cache_lock:
            cached = _get_cached_key_by_id(key_id)
            if cached is not None:
                calls_today = int(cached.get("calls_today") or 0)
                tokens_today = int(cached.get("tokens_today") or 0)
            else:
                row = (
                    db.table("api_keys")
                    .select("calls_today,tokens_today")
                    .eq("id", key_id)
                    .limit(1)
                    .execute()
                    .data
                )
                current = row[0] if row else {}
                calls_today = int(current.get("calls_today") or 0)
                tokens_today = int(current.get("tokens_today") or 0)

            calls_new = calls_today + 1
            tokens_new = tokens_today + safe_tokens

            db.table("api_keys").update({
                "last_used_at": now,
                "calls_today": calls_new,
                "tokens_today": tokens_new,
            }).eq("id", key_id).execute()

            _set_cached_key_fields(
                key_id,
                {
                    "last_used_at": now,
                    "calls_today": calls_new,
                    "tokens_today": tokens_new,
                },
            )
                    
    except Exception as e:
        logger.error(f"[KeyManager] Error actualizando key {key_id}: {e}")


def mark_cooldown(key_id: int, error_msg: str = "") -> None:
    """
    Pone una key en cooldown por 30 minutos.
    Típicamente llamado cuando se recibe un 429 (rate limit).
    
    Args:
        key_id: ID de la key en Supabase
        error_msg: mensaje de error opcional para logging
    """
    db = get_db()
    now = datetime.now(timezone.utc)
    cooldown_until = (now + timedelta(minutes=COOLDOWN_MINUTES)).isoformat()
    
    # Encontrar label de la key para el log
    key_label = f"ID:{key_id}"
    with _cache_lock:
        for service, keys in _keys_cache.items():
            for key in keys:
                if key.get("id") == key_id:
                    key_label = key.get("label", f"****{key.get('key_value', '')[-4:]}")
                    break
    
    try:
        db.table("api_keys").update({
            "in_cooldown": True,
            "cooldown_until": cooldown_until,
            "last_error": error_msg[:200] if error_msg else None,
        }).eq("id", key_id).execute()
        
        with _cache_lock:
            _set_cached_key_fields(
                key_id,
                {
                    "in_cooldown": True,
                    "cooldown_until": cooldown_until,
                    "last_error": error_msg[:200] if error_msg else None,
                },
            )
        
        logger.warning(
            f"[KeyManager] Key {key_label} en cooldown por {COOLDOWN_MINUTES} min. "
            f"Error: {error_msg[:100] if error_msg else 'rate limit'}"
        )
        
    except Exception as e:
        logger.error(f"[KeyManager] Error marcando cooldown para key {key_id}: {e}")


def check_cooldowns() -> int:
    """
    Revisa y libera keys cuyo cooldown ha expirado.
    Llamar periódicamente desde el loop principal.
    
    Returns:
        Número de keys liberadas
    """
    db = get_db()
    now = datetime.now(timezone.utc)
    released = 0
    
    try:
        result = (
            db.table("api_keys")
            .select("id, label, service, cooldown_until")
            .eq("in_cooldown", True)
            .execute()
        )
        
        for row in result.data or []:
            key_id = row["id"]
            
            cooldown_until = _parse_utc_datetime(row.get("cooldown_until"))
            if cooldown_until and now < cooldown_until:
                continue

            db.table("api_keys").update({
                "in_cooldown": False,
                "cooldown_until": None,
            }).eq("id", key_id).execute()

            with _cache_lock:
                _set_cached_key_fields(key_id, {"in_cooldown": False, "cooldown_until": None})

            label = row.get("label", f"ID:{key_id}")
            logger.info(f"[KeyManager] Key {label} ({row['service']}) liberada del cooldown")
            released += 1
        
        return released
        
    except Exception as e:
        logger.error(f"[KeyManager] Error verificando cooldowns: {e}")
        return 0


def reset_daily_counts() -> bool:
    """
    Resetea calls_today y tokens_today a 0 para todas las keys.
    Se debe llamar a medianoche Pacific Time.
    
    Returns:
        True si se hizo el reset, False si ya se hizo hoy
    """
    global _last_daily_reset
    
    now_pacific = datetime.now(PACIFIC_TZ)
    today_pacific = now_pacific.date()
    
    # Verificar si ya se hizo el reset hoy
    if _last_daily_reset and _last_daily_reset.date() == today_pacific:
        return False
    
    db = get_db()
    
    try:
        # Resetear todas las keys
        db.table("api_keys").update({
            "calls_today": 0,
            "tokens_today": 0,
            "calls_reset_at": datetime.now(timezone.utc).isoformat(),
        }).gte("id", 1).execute()
        
        with _cache_lock:
            for service_keys in _keys_cache.values():
                for key in service_keys:
                    key["calls_today"] = 0
                    key["tokens_today"] = 0
        
        _last_daily_reset = now_pacific
        logger.info("[KeyManager] Reset diario de contadores completado")
        return True
        
    except Exception as e:
        logger.error(f"[KeyManager] Error en reset diario: {e}")
        return False


def should_reset_daily() -> bool:
    """
    Verifica si es hora de hacer el reset diario (medianoche Pacific).
    """
    global _last_daily_reset
    
    now_pacific = datetime.now(PACIFIC_TZ)
    today_pacific = now_pacific.date()
    
    # Si nunca se ha hecho reset o el último fue ayer/antes
    if _last_daily_reset is None or _last_daily_reset.date() < today_pacific:
        return True
    
    return False


def get_keys_status() -> dict:
    """
    Obtiene el estado actual de todas las keys en memoria.
    Para el endpoint /api/settings/keys/status.
    
    Returns:
        dict con estado por servicio
    """
    status = {}
    
    with _cache_lock:
        for service, keys in _keys_cache.items():
            status[service] = []
            for key in keys:
                status[service].append({
                    "id": key.get("id"),
                    "label": key.get("label"),
                    "last_4": key.get("key_value", "")[-4:] if key.get("key_value") else "????",
                    "calls_today": key.get("calls_today", 0),
                    "tokens_today": key.get("tokens_today", 0),
                    "in_cooldown": key.get("in_cooldown", False),
                    "cooldown_until": key.get("cooldown_until"),
                    "is_enabled": key.get("is_enabled", True),
                })
    
    return status


def has_keys() -> bool:
    """
    Verifica si hay al menos una key cargada para cualquier servicio.
    """
    with _cache_lock:
        return any(len(keys) > 0 for keys in _keys_cache.values())


def get_cooldown_counts() -> dict[str, int]:
    """
    Obtiene el conteo de keys en cooldown por servicio.
    Para el log de observabilidad.
    """
    counts = {}
    now = datetime.now(timezone.utc)
    
    with _cache_lock:
        for service, keys in _keys_cache.items():
            in_cooldown = 0
            for key in keys:
                if key.get("in_cooldown"):
                    cooldown_until = _parse_utc_datetime(key.get("cooldown_until"))
                    if cooldown_until and now < cooldown_until:
                        in_cooldown += 1
            counts[service] = in_cooldown
    
    return counts
