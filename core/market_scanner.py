"""
Escáner de mercados políticos de Polymarket.
Usa la Gamma API /events endpoint con tag_ids políticos confirmados.
Rate limit: máximo 1 request/segundo.

Tag IDs políticos verificados:
  2       → Politics (elecciones, presidentes, legislación)
  100265  → Geopolitics (conflictos, tratados, relaciones internacionales)
  126     → Trump
  96      → Ukraine
  95      → Russia
"""
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

# Tag IDs confirmados que contienen mercados políticos reales
_POLITICAL_TAG_IDS = ["2", "100265", "126", "96", "95"]


def get_political_markets(
    min_volume: float = 50_000,
    max_volume: float = 250_000,
    min_days_remaining: int = 7,
) -> list[dict]:
    """
    Obtiene mercados políticos activos de Polymarket usando el endpoint /events.

    Consulta los tag IDs políticos confirmados (Politics, Geopolitics, Trump,
    Ukraine, Russia), extrae los mercados individuales de cada evento y filtra
    por volumen y fecha de cierre. Deduplica por conditionId.

    Parámetros:
        min_volume: volumen mínimo en USD (default $50K)
        max_volume: volumen máximo en USD (default $250K)
        min_days_remaining: días mínimos hasta el cierre del mercado

    Retorna lista de dicts normalizados para guardar en Supabase.
    """
    results       = []
    seen_ids      = set()
    seen_event_ids = set()
    cutoff        = datetime.now(timezone.utc) + timedelta(days=min_days_remaining)

    try:
        with httpx.Client(timeout=30.0) as client:
            for tag_id in _POLITICAL_TAG_IDS:
                offset = 0
                while offset < 500:
                    logger.info(f"[{datetime.now()}] Escaneando tag_id={tag_id} — offset={offset}")

                    resp = client.get(f"{GAMMA_API}/events", params={
                        "tag_id":    tag_id,
                        "active":    "true",
                        "closed":    "false",
                        "limit":     100,
                        "offset":    offset,
                        "order":     "volume24hr",
                        "ascending": "false",
                    })
                    resp.raise_for_status()
                    events = resp.json()

                    if not isinstance(events, list) or not events:
                        break

                    for ev in events:
                        ev_id = ev.get("id")
                        if ev_id in seen_event_ids:
                            continue
                        seen_event_ids.add(ev_id)

                        for m in (ev.get("markets") or []):
                            market_id = m.get("conditionId") or m.get("id")
                            if not market_id or market_id in seen_ids:
                                continue

                            # Filtrar por volumen
                            volume = float(m.get("volume", 0) or 0)
                            if volume < min_volume or volume > max_volume:
                                continue

                            # Filtrar por fecha de cierre
                            end_str = m.get("endDate", "")
                            if not end_str:
                                continue
                            try:
                                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                                if end_dt <= cutoff:
                                    continue
                            except (ValueError, TypeError):
                                continue

                            # Extraer token IDs YES/NO desde clobTokenIds
                            clob_raw = m.get("clobTokenIds")
                            try:
                                tokens = json.loads(clob_raw) if isinstance(clob_raw, str) else (clob_raw or [])
                            except (ValueError, TypeError):
                                tokens = []

                            yes_token_id = tokens[0] if len(tokens) > 0 else None
                            no_token_id  = tokens[1] if len(tokens) > 1 else None

                            # Precio YES desde outcomePrices
                            last_price = None
                            raw_prices = m.get("outcomePrices")
                            try:
                                prices = json.loads(raw_prices) if isinstance(raw_prices, str) else (raw_prices or [])
                                if prices:
                                    last_price = float(prices[0])
                            except (ValueError, TypeError):
                                pass

                            seen_ids.add(market_id)
                            results.append({
                                "id":           market_id,
                                "question":     m.get("question", ""),
                                "description":  (m.get("description") or "")[:500],
                                "volume":       volume,
                                "end_date":     end_str,
                                "yes_token_id": yes_token_id,
                                "no_token_id":  no_token_id,
                                "last_price":   last_price,
                            })

                    offset += 100
                    if len(events) < 100:
                        break  # no hay más páginas para este tag

                    time.sleep(1)  # rate limiting: 1 req/s

                time.sleep(0.5)  # pausa entre tags

    except httpx.HTTPStatusError as e:
        logger.error(f"[{datetime.now()}] Error HTTP al escanear mercados: {e.response.status_code} {e}")
    except httpx.RequestError as e:
        logger.error(f"[{datetime.now()}] Error de conexión al escanear mercados: {e}")
    except Exception as e:
        logger.error(f"[{datetime.now()}] Error inesperado en market_scanner: {e}", exc_info=True)

    logger.info(
        f"[{datetime.now()}] {len(results)} mercados políticos encontrados "
        f"(${min_volume/1000:.0f}K–${max_volume/1000:.0f}K)"
    )
    return results


def get_market_price(token_id: str) -> Optional[float]:
    """
    Obtiene el precio mid de un token YES via CLOB API.
    Retorna None si falla o el token no es válido.
    """
    if not token_id:
        return None
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{CLOB_API}/midpoint", params={"token_id": token_id})
            resp.raise_for_status()
            data = resp.json()
            mid = data.get("mid")
            if mid is not None:
                return float(mid)
    except httpx.HTTPStatusError as e:
        logger.warning(
            f"[{datetime.now()}] Error HTTP obteniendo precio mid "
            f"{str(token_id)[:16]}...: {e.response.status_code}"
        )
    except Exception as e:
        logger.warning(f"[{datetime.now()}] Error obteniendo precio {str(token_id)[:16]}...: {e}")
    return None


def get_price_history(token_id: str, interval: str = "1d", fidelity: int = 60) -> list[dict]:
    """
    Obtiene el historial de precios de un token para backtesting.
    Retorna lista de {"t": timestamp_ms, "p": price}.
    """
    if not token_id:
        return []
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(f"{CLOB_API}/prices-history", params={
                "token_id": token_id,
                "interval": interval,
                "fidelity": fidelity,
            })
            resp.raise_for_status()
            return resp.json().get("history", [])
    except Exception as e:
        logger.warning(f"[{datetime.now()}] Error obteniendo historial {str(token_id)[:16]}...: {e}")
    return []


def save_markets_to_db(markets: list[dict]) -> int:
    """
    Guarda o actualiza los mercados en Supabase y registra snapshots de precio.
    Retorna el número de mercados guardados correctamente.
    """
    from core.paper_trader import upsert_market, save_price_snapshot

    saved = 0
    for market in markets:
        if not market.get("id"):
            continue
        try:
            upsert_market(market)

            if market.get("last_price") is not None:
                save_price_snapshot(
                    market_id=market["id"],
                    price=market["last_price"],
                    volume=market.get("volume"),
                )

            saved += 1
            logger.debug(
                f"[{datetime.now()}] Mercado guardado: {market['id'][:16]}... "
                f"${market.get('volume', 0):,.0f} | {market['question'][:60]}"
            )
        except Exception as e:
            logger.error(
                f"[{datetime.now()}] Error guardando mercado {market.get('id', '?')[:16]}...: {e}"
            )

    logger.info(f"[{datetime.now()}] {saved}/{len(markets)} mercados guardados en Supabase")
    return saved
