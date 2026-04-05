"""
Whale Tracking para PolyHunt.

Monitorea las posiciones de los principales holders en mercados de Polymarket
para detectar movimientos de "smart money".

API utilizada:
  - GET https://data-api.polymarket.com/holders?market=<conditionId>
  - GET https://data-api.polymarket.com/v1/leaderboard

Señales detectadas:
  - large_buy: Compra grande (>5% cambio en balance)
  - large_sell: Venta grande (>5% cambio en balance)
  - new_position: Nuevo holder top 20
  - exit_position: Holder top 20 sale completamente
"""
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from core.db import get_db, db_retry

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# Configuración
# ═══════════════════════════════════════════════════════════════════════════

POLYMARKET_DATA_API = "https://data-api.polymarket.com"
TOP_HOLDERS_LIMIT = 20          # Top N holders a monitorear
SIGNIFICANT_CHANGE_PCT = 0.05   # 5% cambio = significativo
SNAPSHOT_INTERVAL_HOURS = 4     # Frecuencia de snapshots

# Rate limiting para API de holders
MIN_REQUEST_INTERVAL = 0.5      # Mínimo 500ms entre requests
_last_request_time: float = 0

# Cache de último snapshot por mercado
_last_snapshot_time: dict[str, datetime] = {}

# Cache de holders por mercado (para evitar doble request YES/NO)
_holders_cache: dict[str, tuple[float, list]] = {}  # market_id -> (timestamp, data)
CACHE_TTL_SECONDS = 60  # Cache válido por 1 minuto


# ═══════════════════════════════════════════════════════════════════════════
# Funciones de API
# ═══════════════════════════════════════════════════════════════════════════

def _rate_limit():
    """Aplica rate limiting entre requests."""
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.time()


def get_market_holders(condition_id: str) -> list[dict]:
    """
    Obtiene todos los holders de un mercado (ambos tokens YES y NO).
    
    La API retorna una lista con 2 items:
    [
        {"token": "...", "holders": [...]},  # YES token
        {"token": "...", "holders": [...]}   # NO token
    ]
    
    Args:
        condition_id: El conditionId del mercado (usado como market_id)
    
    Returns:
        Lista raw de la API con holders de ambos tokens
    """
    if not condition_id:
        return []
    
    # Verificar cache
    cached = _holders_cache.get(condition_id)
    if cached:
        cache_time, cache_data = cached
        if time.time() - cache_time < CACHE_TTL_SECONDS:
            return cache_data
    
    _rate_limit()
    
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(
                f"{POLYMARKET_DATA_API}/holders",
                params={"market": condition_id}
            )
            response.raise_for_status()
            data = response.json()
            
            # Guardar en cache
            _holders_cache[condition_id] = (time.time(), data)
            
            return data if isinstance(data, list) else []
            
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.debug(f"[Whale] Market {condition_id[:16]}... no encontrado")
        elif e.response.status_code == 429:
            logger.warning(f"[Whale] Rate limited por API de holders")
        else:
            logger.debug(f"[Whale] Error HTTP {e.response.status_code} obteniendo holders")
        return []
    except Exception as e:
        logger.debug(f"[Whale] Error obteniendo holders para {condition_id[:16]}...: {e}")
        return []


def get_token_holders(
    condition_id: str,
    token_id: str,
    limit: int = TOP_HOLDERS_LIMIT
) -> list[dict]:
    """
    Obtiene los principales holders de un token específico (YES o NO).
    
    Args:
        condition_id: El conditionId del mercado
        token_id: ID del token específico (YES o NO)
        limit: Número máximo de holders a obtener
    
    Returns:
        Lista de dicts con holder_address, holder_name, balance, percentage_of_supply
    """
    if not condition_id or not token_id:
        return []
    
    # Obtener todos los holders del mercado
    market_data = get_market_holders(condition_id)
    if not market_data:
        return []
    
    # Buscar el token específico
    for item in market_data:
        if item.get("token") == token_id:
            raw_holders = item.get("holders", [])
            
            # Mapear campos de la API a nuestro formato
            holders = []
            for h in raw_holders[:limit]:
                try:
                    holders.append({
                        "holder_address": h.get("proxyWallet", ""),
                        "holder_name": h.get("name") or h.get("pseudonym") or "",
                        "balance": float(h.get("amount", 0) or 0),
                        # La API no retorna percentage, lo dejamos en 0
                        "percentage_of_supply": 0.0,
                    })
                except (ValueError, TypeError):
                    continue
            
            return holders
    
    return []


def get_leaderboard(limit: int = 50) -> list[dict]:
    """
    Obtiene el leaderboard global de traders en Polymarket.
    
    Returns:
        Lista de dicts con address, pnl, volume, etc.
    """
    _rate_limit()
    
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(
                f"{POLYMARKET_DATA_API}/v1/leaderboard",
                params={"limit": limit}
            )
            response.raise_for_status()
            data = response.json()
            
            traders = []
            for item in data if isinstance(data, list) else data.get("leaderboard", []):
                traders.append({
                    "address": item.get("proxyWallet") or item.get("address", ""),
                    "name": item.get("userName") or item.get("name", ""),
                    "pnl": float(item.get("pnl", 0) or 0),
                    "volume": float(item.get("vol") or item.get("volume", 0) or 0),
                })
            
            return traders
            
    except Exception as e:
        logger.debug(f"[Whale] Error obteniendo leaderboard: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════
# Funciones de Supabase
# ═══════════════════════════════════════════════════════════════════════════

def save_holder_snapshot(
    market_id: str,
    token_id: str,
    token_type: str,
    holders: list[dict]
) -> int:
    """
    Guarda snapshot de holders en Supabase.
    
    Returns:
        Número de holders guardados
    """
    if not holders:
        return 0
    
    db = get_db()
    saved = 0
    now = datetime.now(timezone.utc).isoformat()
    
    for holder in holders:
        try:
            db_retry(lambda h=holder: db.table("holder_snapshots").insert({
                "market_id": market_id,
                "token_id": token_id,
                "token_type": token_type,
                "holder_address": h["holder_address"],
                "holder_name": h.get("holder_name", ""),
                "balance": h["balance"],
                "percentage_of_supply": h.get("percentage_of_supply", 0),
                "snapshot_at": now,
            }).execute())
            saved += 1
        except Exception as e:
            logger.debug(f"[Whale] Error guardando holder snapshot: {e}")
    
    return saved


def detect_whale_changes(
    market_id: str,
    token_id: str,
    token_type: str,
    current_holders: list[dict]
) -> list[dict]:
    """
    Detecta cambios significativos comparando con el snapshot anterior.
    
    Returns:
        Lista de alertas detectadas
    """
    if not current_holders:
        return []
    
    db = get_db()
    alerts = []
    
    try:
        # Obtener último snapshot
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=SNAPSHOT_INTERVAL_HOURS + 1)).isoformat()
        result = db.table("holder_snapshots")\
            .select("holder_address, holder_name, balance, percentage_of_supply")\
            .eq("market_id", market_id)\
            .eq("token_id", token_id)\
            .gte("snapshot_at", cutoff)\
            .order("snapshot_at", desc=True)\
            .limit(TOP_HOLDERS_LIMIT)\
            .execute()
        
        prev_holders = {h["holder_address"]: h for h in (result.data or [])}
        
        if not prev_holders:
            # Primer snapshot, no hay comparación
            return []
        
        current_map = {h["holder_address"]: h for h in current_holders}
        
        # Detectar cambios
        for addr, curr in current_map.items():
            prev = prev_holders.get(addr)
            holder_name = curr.get("holder_name", "")
            
            if not prev:
                # Nuevo holder en top - solo alertar si tiene balance significativo
                if curr["balance"] > 0:
                    alerts.append({
                        "market_id": market_id,
                        "holder_address": addr,
                        "holder_name": holder_name,
                        "token_type": token_type,
                        "change_type": "new_position",
                        "previous_balance": 0,
                        "new_balance": curr["balance"],
                        "change_pct": 1.0,  # 100% porque es nuevo
                    })
            else:
                # Holder existente - verificar cambio
                prev_bal = float(prev.get("balance", 0))
                curr_bal = float(curr.get("balance", 0))
                
                if prev_bal > 0:
                    change_pct = (curr_bal - prev_bal) / prev_bal
                    
                    if change_pct >= SIGNIFICANT_CHANGE_PCT:
                        alerts.append({
                            "market_id": market_id,
                            "holder_address": addr,
                            "holder_name": holder_name,
                            "token_type": token_type,
                            "change_type": "large_buy",
                            "previous_balance": prev_bal,
                            "new_balance": curr_bal,
                            "change_pct": change_pct,
                        })
                    elif change_pct <= -SIGNIFICANT_CHANGE_PCT:
                        alerts.append({
                            "market_id": market_id,
                            "holder_address": addr,
                            "holder_name": holder_name,
                            "token_type": token_type,
                            "change_type": "large_sell",
                            "previous_balance": prev_bal,
                            "new_balance": curr_bal,
                            "change_pct": change_pct,
                        })
        
        # Detectar salidas (holders previos que ya no están en top)
        for addr, prev in prev_holders.items():
            if addr not in current_map:
                prev_bal = float(prev.get("balance", 0))
                if prev_bal > 0:
                    alerts.append({
                        "market_id": market_id,
                        "holder_address": addr,
                        "holder_name": prev.get("holder_name", ""),
                        "token_type": token_type,
                        "change_type": "exit_position",
                        "previous_balance": prev_bal,
                        "new_balance": 0,
                        "change_pct": -1.0,
                    })
        
    except Exception as e:
        logger.debug(f"[Whale] Error detectando cambios: {e}")
    
    return alerts


def save_whale_alerts(alerts: list[dict]) -> int:
    """
    Guarda alertas de whale en Supabase.
    
    Returns:
        Número de alertas guardadas
    """
    if not alerts:
        return 0
    
    db = get_db()
    saved = 0
    now = datetime.now(timezone.utc).isoformat()
    
    for alert in alerts:
        try:
            db_retry(lambda a=alert: db.table("whale_alerts").insert({
                "market_id": a["market_id"],
                "holder_address": a["holder_address"],
                "holder_name": a.get("holder_name", ""),
                "token_type": a["token_type"],
                "change_type": a["change_type"],
                "previous_balance": a["previous_balance"],
                "new_balance": a["new_balance"],
                "change_pct": a["change_pct"],
                "detected_at": now,
            }).execute())
            saved += 1
            
            name_str = f" ({a['holder_name']})" if a.get("holder_name") else ""
            logger.info(
                f"[Whale] Alert: {a['change_type']} | market={a['market_id'][:16]}... | "
                f"holder={a['holder_address'][:10]}...{name_str} | change={a['change_pct']:.1%}"
            )
        except Exception as e:
            logger.debug(f"[Whale] Error guardando alert: {e}")
    
    return saved


# ═══════════════════════════════════════════════════════════════════════════
# Función principal de scan
# ═══════════════════════════════════════════════════════════════════════════

def scan_market_whales(market: dict) -> dict:
    """
    Escanea un mercado para whale activity.
    
    Args:
        market: Dict con id (conditionId), yes_token_id, no_token_id
    
    Returns:
        Dict con estadísticas del scan
    """
    market_id = market.get("id")  # Este es el conditionId
    if not market_id:
        return {"scanned": False}
    
    # Verificar si ya escaneamos recientemente
    now = datetime.now(timezone.utc)
    last = _last_snapshot_time.get(market_id)
    if last and (now - last).total_seconds() < SNAPSHOT_INTERVAL_HOURS * 3600:
        return {"scanned": False, "reason": "recent_snapshot"}
    
    stats = {
        "scanned": True,
        "market_id": market_id,
        "holders_saved": 0,
        "alerts_detected": 0,
    }
    
    # Escanear token YES
    yes_token_id = market.get("yes_token_id")
    if yes_token_id:
        yes_holders = get_token_holders(market_id, yes_token_id)
        if yes_holders:
            alerts = detect_whale_changes(market_id, yes_token_id, "YES", yes_holders)
            stats["alerts_detected"] += save_whale_alerts(alerts)
            stats["holders_saved"] += save_holder_snapshot(market_id, yes_token_id, "YES", yes_holders)
    
    # Escanear token NO (usa cache, no hace segunda request)
    no_token_id = market.get("no_token_id")
    if no_token_id:
        no_holders = get_token_holders(market_id, no_token_id)
        if no_holders:
            alerts = detect_whale_changes(market_id, no_token_id, "NO", no_holders)
            stats["alerts_detected"] += save_whale_alerts(alerts)
            stats["holders_saved"] += save_holder_snapshot(market_id, no_token_id, "NO", no_holders)
    
    _last_snapshot_time[market_id] = now
    
    if stats["holders_saved"] > 0:
        logger.debug(f"[Whale] Scanned {market_id[:16]}... | holders={stats['holders_saved']} alerts={stats['alerts_detected']}")
    
    return stats


# ═══════════════════════════════════════════════════════════════════════════
# Funciones de consulta
# ═══════════════════════════════════════════════════════════════════════════

def get_whale_activity_for_market(market_id: str, hours: int = 24) -> list[dict]:
    """
    Obtiene alertas de whale recientes para un mercado.
    
    Args:
        market_id: ID del mercado (conditionId)
        hours: Horas hacia atrás a buscar
    
    Returns:
        Lista de alertas
    """
    db = get_db()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        result = db.table("whale_alerts")\
            .select("*")\
            .eq("market_id", market_id)\
            .gte("detected_at", cutoff)\
            .order("detected_at", desc=True)\
            .execute()
        return result.data or []
    except Exception as e:
        logger.debug(f"[Whale] Error obteniendo actividad: {e}")
        return []


def has_whale_activity(market_id: str, hours: int = 24) -> bool:
    """
    Verifica si hay whale activity reciente en un mercado.
    """
    activity = get_whale_activity_for_market(market_id, hours)
    return len(activity) > 0


def get_whale_signal(market_id: str, hours: int = 24) -> Optional[str]:
    """
    Retorna señal de whale si hay actividad significativa.
    
    Returns:
        "bullish" si hay más compras que ventas
        "bearish" si hay más ventas que compras
        None si no hay señal clara
    """
    activity = get_whale_activity_for_market(market_id, hours)
    if not activity:
        return None
    
    buys = sum(1 for a in activity if a["change_type"] in ("large_buy", "new_position"))
    sells = sum(1 for a in activity if a["change_type"] in ("large_sell", "exit_position"))
    
    if buys > sells and buys >= 2:
        return "bullish"
    elif sells > buys and sells >= 2:
        return "bearish"
    
    return None


def clear_holders_cache():
    """Limpia el cache de holders (útil para tests)."""
    global _holders_cache
    _holders_cache = {}
