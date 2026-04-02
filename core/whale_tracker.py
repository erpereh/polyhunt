"""
Whale Tracking para PolyHunt.

Monitorea las posiciones de los principales holders en mercados de Polymarket
para detectar movimientos de "smart money".

API utilizada:
  - GET https://data-api.polymarket.com/holders?token_id=XXX
  - GET https://data-api.polymarket.com/v1/leaderboard

Señales detectadas:
  - large_buy: Compra grande (>5% del supply)
  - large_sell: Venta grande (>5% del supply)
  - new_position: Nuevo holder top 10
  - exit_position: Holder top 10 sale completamente
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from core.db import get_db, db_retry

logger = logging.getLogger(__name__)

# Configuración
POLYMARKET_DATA_API = "https://data-api.polymarket.com"
TOP_HOLDERS_LIMIT = 20          # Top N holders a monitorear
SIGNIFICANT_CHANGE_PCT = 0.05   # 5% del supply = cambio significativo
SNAPSHOT_INTERVAL_HOURS = 4     # Frecuencia de snapshots

# Cache de último snapshot por mercado
_last_snapshot_time: dict[str, datetime] = {}


def get_token_holders(token_id: str, limit: int = TOP_HOLDERS_LIMIT) -> list[dict]:
    """
    Obtiene los principales holders de un token.
    
    Args:
        token_id: ID del token (YES o NO)
        limit: Número máximo de holders a obtener
    
    Returns:
        Lista de dicts con holder_address, balance, percentage
    """
    if not token_id:
        return []
    
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(
                f"{POLYMARKET_DATA_API}/holders",
                params={"token_id": token_id, "limit": limit}
            )
            response.raise_for_status()
            data = response.json()
            
            holders = []
            for item in data if isinstance(data, list) else data.get("holders", []):
                holders.append({
                    "holder_address": item.get("address") or item.get("holder_address", ""),
                    "balance": float(item.get("balance", 0)),
                    "percentage_of_supply": float(item.get("percentage", 0) or item.get("percentage_of_supply", 0)),
                })
            
            return holders
            
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.debug(f"[Whale] Token {token_id[:16]}... no encontrado en holders API")
        else:
            logger.warning(f"[Whale] Error HTTP obteniendo holders: {e.response.status_code}")
        return []
    except Exception as e:
        logger.warning(f"[Whale] Error obteniendo holders para {token_id[:16]}...: {e}")
        return []


def get_leaderboard(limit: int = 50) -> list[dict]:
    """
    Obtiene el leaderboard global de traders en Polymarket.
    
    Returns:
        Lista de dicts con address, pnl, volume, etc.
    """
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
                    "address": item.get("address", ""),
                    "pnl": float(item.get("pnl", 0)),
                    "volume": float(item.get("volume", 0)),
                    "trades_count": int(item.get("trades_count", 0) or item.get("num_trades", 0)),
                })
            
            return traders
            
    except Exception as e:
        logger.warning(f"[Whale] Error obteniendo leaderboard: {e}")
        return []


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
                "balance": h["balance"],
                "percentage_of_supply": h["percentage_of_supply"],
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
            .select("holder_address, balance, percentage_of_supply")\
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
            
            if not prev:
                # Nuevo holder en top
                if curr["percentage_of_supply"] >= SIGNIFICANT_CHANGE_PCT:
                    alerts.append({
                        "market_id": market_id,
                        "holder_address": addr,
                        "token_type": token_type,
                        "change_type": "new_position",
                        "previous_balance": 0,
                        "new_balance": curr["balance"],
                        "change_pct": curr["percentage_of_supply"],
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
                            "token_type": token_type,
                            "change_type": "large_sell",
                            "previous_balance": prev_bal,
                            "new_balance": curr_bal,
                            "change_pct": change_pct,
                        })
        
        # Detectar salidas (holders previos que ya no están en top)
        for addr, prev in prev_holders.items():
            if addr not in current_map:
                if prev["percentage_of_supply"] >= SIGNIFICANT_CHANGE_PCT:
                    alerts.append({
                        "market_id": market_id,
                        "holder_address": addr,
                        "token_type": token_type,
                        "change_type": "exit_position",
                        "previous_balance": prev["balance"],
                        "new_balance": 0,
                        "change_pct": -1.0,
                    })
        
    except Exception as e:
        logger.warning(f"[Whale] Error detectando cambios: {e}")
    
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
                "token_type": a["token_type"],
                "change_type": a["change_type"],
                "previous_balance": a["previous_balance"],
                "new_balance": a["new_balance"],
                "change_pct": a["change_pct"],
                "detected_at": now,
            }).execute())
            saved += 1
            
            logger.info(
                f"[Whale] Alert: {a['change_type']} | market={a['market_id'][:16]}... | "
                f"holder={a['holder_address'][:10]}... | change={a['change_pct']:.1%}"
            )
        except Exception as e:
            logger.debug(f"[Whale] Error guardando alert: {e}")
    
    return saved


def scan_market_whales(market: dict) -> dict:
    """
    Escanea un mercado para whale activity.
    
    Args:
        market: Dict con id, yes_token_id, no_token_id
    
    Returns:
        Dict con estadísticas del scan
    """
    market_id = market.get("id")
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
        yes_holders = get_token_holders(yes_token_id)
        if yes_holders:
            alerts = detect_whale_changes(market_id, yes_token_id, "YES", yes_holders)
            stats["alerts_detected"] += save_whale_alerts(alerts)
            stats["holders_saved"] += save_holder_snapshot(market_id, yes_token_id, "YES", yes_holders)
    
    # Escanear token NO
    no_token_id = market.get("no_token_id")
    if no_token_id:
        no_holders = get_token_holders(no_token_id)
        if no_holders:
            alerts = detect_whale_changes(market_id, no_token_id, "NO", no_holders)
            stats["alerts_detected"] += save_whale_alerts(alerts)
            stats["holders_saved"] += save_holder_snapshot(market_id, no_token_id, "NO", no_holders)
    
    _last_snapshot_time[market_id] = now
    return stats


def get_whale_activity_for_market(market_id: str, hours: int = 24) -> list[dict]:
    """
    Obtiene alertas de whale recientes para un mercado.
    
    Args:
        market_id: ID del mercado
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
        logger.warning(f"[Whale] Error obteniendo actividad: {e}")
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
