"""
Escáner de mercados políticos de Polymarket.
Usa la Gamma API para listado y el CLOB API para precios mid.
Rate limit: máximo 1 request/segundo.
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

# Keywords para detectar mercados políticos
_POLITICAL_KEYWORDS = [
    "president", "election", "senate", "congress", "governor", "vote",
    "ballot", "democrat", "republican", "trump", "biden", "harris",
    "political", "policy", "legislation", "parliament", "minister",
    "cabinet", "impeach", "resign", "government", "nato", "ukraine",
    "russia", "china", "war", "treaty", "tariff", "sanction",
    "supreme court", "administration", "primary", "primary election",
    "midterm", "campaign", "polling", "majority", "speaker",
]


def _is_political(question: str, description: str = "") -> bool:
    """Devuelve True si el mercado parece político por sus keywords."""
    text = (question + " " + (description or "")).lower()
    return any(kw in text for kw in _POLITICAL_KEYWORDS)


def get_political_markets(
    min_volume: float = 50_000,
    max_volume: float = 250_000,
    min_days_remaining: int = 7,
) -> list[dict]:
    """
    Obtiene mercados políticos activos de Polymarket con volumen en el rango indicado.

    Parámetros:
        min_volume: volumen mínimo en USD (default $50K)
        max_volume: volumen máximo en USD (default $250K)
        min_days_remaining: días mínimos hasta el cierre del mercado

    Retorna lista de dicts normalizados para guardar en Supabase.
    Rate limit: 1 request/segundo máximo.
    """
    results = []
    cutoff = datetime.now(timezone.utc) + timedelta(days=min_days_remaining)

    try:
        with httpx.Client(timeout=30.0) as client:
            offset = 0
            limit  = 100

            while offset < 500:  # máximo 500 mercados por ciclo
                logger.info(f"[{datetime.now()}] Escaneando mercados — offset={offset}")

                resp = client.get(f"{GAMMA_API}/markets", params={
                    "active":    "true",
                    "closed":    "false",
                    "limit":     limit,
                    "offset":    offset,
                    "order":     "volume24hr",
                    "ascending": "false",
                })
                resp.raise_for_status()
                batch = resp.json()

                if not batch:
                    break

                for m in batch:
                    # Filtrar por volumen
                    volume = float(m.get("volume", 0) or 0)
                    if volume < min_volume or volume > max_volume:
                        continue

                    # Filtrar por fecha de cierre
                    end_str = m.get("endDate") or m.get("end_date_iso") or ""
                    if not end_str:
                        continue
                    try:
                        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                        if end_dt <= cutoff:
                            continue
                    except (ValueError, TypeError):
                        continue

                    # Filtrar por tema político
                    question    = m.get("question", "")
                    description = m.get("description", "") or ""
                    if not _is_political(question, description):
                        continue

                    # Extraer IDs de tokens YES / NO
                    yes_token_id = None
                    no_token_id  = None
                    for token in (m.get("tokens") or []):
                        outcome = (token.get("outcome") or "").upper()
                        if outcome == "YES":
                            yes_token_id = token.get("token_id")
                        elif outcome == "NO":
                            no_token_id = token.get("token_id")

                    # Precio YES desde outcomePrices (incluido en la respuesta)
                    last_price = None
                    raw_prices = m.get("outcomePrices")
                    if raw_prices:
                        try:
                            prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
                            if prices and len(prices) > 0:
                                last_price = float(prices[0])
                        except (ValueError, TypeError):
                            pass

                    market_id = m.get("conditionId") or m.get("id")
                    if not market_id:
                        continue

                    results.append({
                        "id":           market_id,
                        "question":     question,
                        "description":  description[:500],
                        "volume":       volume,
                        "end_date":     end_str,
                        "yes_token_id": yes_token_id,
                        "no_token_id":  no_token_id,
                        "last_price":   last_price,
                    })

                offset += limit
                time.sleep(1)  # rate limiting: 1 req/s

                if len(batch) < limit:
                    break  # no hay más páginas

    except httpx.HTTPStatusError as e:
        logger.error(f"[{datetime.now()}] Error HTTP al escanear mercados: {e.response.status_code} {e}")
    except httpx.RequestError as e:
        logger.error(f"[{datetime.now()}] Error de conexión al escanear mercados: {e}")
    except Exception as e:
        logger.error(f"[{datetime.now()}] Error inesperado en market_scanner: {e}", exc_info=True)

    logger.info(f"[{datetime.now()}] {len(results)} mercados políticos encontrados (${min_volume/1000:.0f}K–${max_volume/1000:.0f}K)")
    return results


def get_market_price(token_id: str) -> Optional[float]:
    """
    Obtiene el precio mid de un token YES vía CLOB API.
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
        logger.warning(f"[{datetime.now()}] Error HTTP obteniendo precio mid {token_id[:16]}...: {e.response.status_code}")
    except Exception as e:
        logger.warning(f"[{datetime.now()}] Error obteniendo precio {token_id[:16]}...: {e}")
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
        logger.warning(f"[{datetime.now()}] Error obteniendo historial {token_id[:16]}...: {e}")
    return []


def save_markets_to_db(markets: list[dict]) -> int:
    """
    Guarda o actualiza los mercados en Supabase y registra snapshots de precio.
    Retorna el número de mercados guardados correctamente.
    """
    # Importar aquí para evitar importación circular
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
                f"[{datetime.now()}] Mercado guardado: {market['id'][:16]}… "
                f"${market.get('volume', 0):,.0f} | {market['question'][:60]}"
            )
        except Exception as e:
            logger.error(f"[{datetime.now()}] Error guardando mercado {market.get('id', '?')[:16]}…: {e}")

    logger.info(f"[{datetime.now()}] {saved}/{len(markets)} mercados guardados en Supabase")
    return saved
