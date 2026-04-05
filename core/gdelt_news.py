"""
GDELT News Pipeline para PolyHunt.

Reemplaza/complementa el RSS feed con GDELT DOC 2.0 API.
GDELT ofrece cobertura global, actualización cada 15 minutos.

API utilizada:
  - GET https://api.gdeltproject.org/api/v2/doc/doc?query=...&mode=ArtList&format=json

Features:
  - Rate limiting global (1 req/segundo)
  - Backoff exponencial en 429 errors
  - Cache de queries en Supabase (TTL 1 hora)
"""
import logging
import re
import hashlib
import time
import random
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import quote_plus

import httpx

from core.db import get_db, db_retry

logger = logging.getLogger(__name__)

# ─── Configuración ────────────────────────────────────────────────────────────
GDELT_API_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
DEFAULT_TIMESPAN = "24h"
MAX_RECORDS = 50
MIN_RELEVANCE_SCORE = 0.3

# Rate limiting
GDELT_MIN_REQUEST_INTERVAL = 1.5  # segundos mínimos entre requests
GDELT_MAX_RETRIES = 4             # intentos totales (1 inicial + 3 retries)
GDELT_BACKOFF_BASE = 2.0          # segundos base para backoff
GDELT_BACKOFF_FACTOR = 3.0        # multiplicador exponencial
GDELT_BACKOFF_JITTER = 0.2        # ±20% jitter

# Cache
GDELT_CACHE_TTL_HOURS = 1         # TTL del cache en horas

# Estado global
_last_gdelt_request: float = 0.0

# Keywords base para mercados políticos
POLITICAL_KEYWORDS = [
    "Trump", "Biden", "election", "Congress", "Senate", "White House",
    "Putin", "Zelensky", "Ukraine", "Russia", "NATO", "China", "Taiwan",
    "Iran", "Israel", "Gaza", "war", "sanctions", "tariffs", "trade deal",
    "Federal Reserve", "inflation", "recession", "economy",
]


# ─── Rate Limiting & Retry ────────────────────────────────────────────────────

def _rate_limit_wait() -> None:
    """Espera el tiempo necesario para respetar el rate limit."""
    global _last_gdelt_request
    now = time.monotonic()
    elapsed = now - _last_gdelt_request
    
    if elapsed < GDELT_MIN_REQUEST_INTERVAL:
        wait_time = GDELT_MIN_REQUEST_INTERVAL - elapsed
        time.sleep(wait_time)
    
    _last_gdelt_request = time.monotonic()


def _calculate_backoff(attempt: int) -> float:
    """Calcula el tiempo de espera con backoff exponencial y jitter."""
    base_delay = GDELT_BACKOFF_BASE * (GDELT_BACKOFF_FACTOR ** attempt)
    jitter = base_delay * GDELT_BACKOFF_JITTER * (2 * random.random() - 1)
    return base_delay + jitter


def _fetch_with_retry(url: str, timeout: float = 30.0) -> Optional[dict]:
    """
    Fetch URL con retry y backoff exponencial.
    
    Returns:
        Dict con response JSON o None si falla después de todos los intentos.
    """
    last_error = None
    
    for attempt in range(GDELT_MAX_RETRIES):
        try:
            _rate_limit_wait()
            
            with httpx.Client(timeout=timeout) as client:
                response = client.get(url)
                
                # Si es 429 (rate limited), hacer backoff
                if response.status_code == 429:
                    if attempt < GDELT_MAX_RETRIES - 1:
                        backoff = _calculate_backoff(attempt)
                        logger.info(f"[GDELT] Rate limited (429), backoff {backoff:.1f}s (intento {attempt + 1}/{GDELT_MAX_RETRIES})")
                        time.sleep(backoff)
                        continue
                    else:
                        logger.warning(f"[GDELT] Rate limited después de {GDELT_MAX_RETRIES} intentos")
                        return None
                
                response.raise_for_status()
                
                if not response.text.strip():
                    return {"articles": []}
                
                return response.json()
                
        except httpx.HTTPStatusError as e:
            last_error = e
            if e.response.status_code >= 500:
                # Server error, reintentar
                if attempt < GDELT_MAX_RETRIES - 1:
                    backoff = _calculate_backoff(attempt)
                    logger.info(f"[GDELT] Server error {e.response.status_code}, backoff {backoff:.1f}s")
                    time.sleep(backoff)
                    continue
            else:
                # Client error (4xx excepto 429), no reintentar
                logger.warning(f"[GDELT] HTTP error: {e.response.status_code}")
                return None
                
        except httpx.TimeoutException:
            last_error = "timeout"
            if attempt < GDELT_MAX_RETRIES - 1:
                backoff = _calculate_backoff(attempt)
                logger.info(f"[GDELT] Timeout, backoff {backoff:.1f}s")
                time.sleep(backoff)
                continue
                
        except Exception as e:
            last_error = e
            logger.warning(f"[GDELT] Error inesperado: {e}")
            return None
    
    logger.warning(f"[GDELT] Falló después de {GDELT_MAX_RETRIES} intentos: {last_error}")
    return None


# ─── Cache ────────────────────────────────────────────────────────────────────

def _hash_query(url: str) -> str:
    """Genera hash único para una query URL."""
    return hashlib.sha256(url.encode()).hexdigest()[:32]


def _get_cached_query(query_hash: str) -> Optional[list[dict]]:
    """
    Busca query en cache.
    
    Returns:
        Lista de artículos si hay cache válido, None si no existe o expiró.
    """
    db = get_db()
    try:
        result = db.table("gdelt_cache")\
            .select("articles, expires_at")\
            .eq("query_hash", query_hash)\
            .limit(1)\
            .execute()
        
        if not result.data:
            return None
        
        row = result.data[0]
        expires_at = row.get("expires_at")
        
        if expires_at:
            try:
                exp_dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
                if exp_dt < datetime.now(timezone.utc):
                    # Cache expirado
                    return None
            except Exception:
                pass
        
        articles = row.get("articles")
        if isinstance(articles, list):
            return articles
        
        return None
        
    except Exception as e:
        logger.debug(f"[GDELT] Error leyendo cache: {e}")
        return None


def _cache_query_result(query_hash: str, url: str, articles: list) -> None:
    """Guarda resultado de query en cache."""
    db = get_db()
    try:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=GDELT_CACHE_TTL_HOURS)
        
        db_retry(lambda: db.table("gdelt_cache").upsert({
            "query_hash": query_hash,
            "query_url": url[:500],
            "articles": articles,
            "fetched_at": now.isoformat(),
            "expires_at": expires.isoformat(),
        }, on_conflict="query_hash").execute())
        
    except Exception as e:
        logger.debug(f"[GDELT] Error guardando cache: {e}")


def _cleanup_expired_cache() -> int:
    """Limpia entradas de cache expiradas. Retorna cantidad eliminada."""
    db = get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        result = db.table("gdelt_cache")\
            .delete()\
            .lt("expires_at", now)\
            .execute()
        
        deleted = len(result.data) if result.data else 0
        if deleted > 0:
            logger.debug(f"[GDELT] Cache cleanup: {deleted} entradas expiradas eliminadas")
        return deleted
        
    except Exception as e:
        logger.debug(f"[GDELT] Error limpiando cache: {e}")
        return 0


# ─── Query Building ───────────────────────────────────────────────────────────

def _build_gdelt_query(keywords: list[str], timespan: str = DEFAULT_TIMESPAN) -> str:
    """
    Construye la URL de query para GDELT DOC 2.0.
    """
    query_str = " OR ".join(f'"{kw}"' for kw in keywords[:10])
    encoded_query = quote_plus(query_str)
    
    return (
        f"{GDELT_API_BASE}"
        f"?query={encoded_query}"
        f"&mode=ArtList"
        f"&format=json"
        f"&timespan={timespan}"
        f"&maxrecords={MAX_RECORDS}"
        f"&sort=DateDesc"
    )


def _generate_gdelt_id(url: str) -> str:
    """Genera un ID único para artículos GDELT basado en URL."""
    return hashlib.md5(url.encode()).hexdigest()[:16]


# ─── Fetch Functions ──────────────────────────────────────────────────────────

def fetch_gdelt_news(
    keywords: list[str] = None,
    timespan: str = DEFAULT_TIMESPAN,
    language: str = None,
    use_cache: bool = True
) -> list[dict]:
    """
    Obtiene artículos de GDELT DOC 2.0 API.
    
    Args:
        keywords: Keywords a buscar (default: POLITICAL_KEYWORDS)
        timespan: Periodo de tiempo
        language: Código de idioma (e.g., "English", "Spanish")
        use_cache: Si True, usa cache de Supabase
    
    Returns:
        Lista de dicts con title, url, source, tone, etc.
    """
    if keywords is None:
        keywords = POLITICAL_KEYWORDS
    
    if not keywords:
        return []
    
    url = _build_gdelt_query(keywords, timespan)
    
    if language:
        url += f"&sourcelang={language}"
    
    # Revisar cache primero
    if use_cache:
        query_hash = _hash_query(url)
        cached = _get_cached_query(query_hash)
        if cached is not None:
            logger.debug(f"[GDELT] Cache hit para keywords: {keywords[:3]}...")
            return cached
    
    # Fetch con retry
    data = _fetch_with_retry(url)
    
    if data is None:
        return []
    
    articles = []
    items = data.get("articles", [])
    
    for item in items:
        article_url = item.get("url", "").strip()
        if not article_url:
            continue
        
        # Parsear fecha
        seendate = item.get("seendate", "")
        published_at = None
        if seendate:
            try:
                dt = datetime.strptime(seendate, "%Y%m%dT%H%M%SZ")
                published_at = dt.replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                pass
        
        # Extraer dominio como source
        domain = item.get("domain", "")
        if not domain:
            match = re.search(r'https?://([^/]+)', article_url)
            domain = match.group(1) if match else "unknown"
        
        articles.append({
            "title": item.get("title", "").strip(),
            "url": article_url,
            "source": domain,
            "summary": "",
            "published_at": published_at,
            "gdelt_id": _generate_gdelt_id(article_url),
            "tone": float(item.get("tone", 0)),
            "domain": domain,
            "language": item.get("language", "English"),
            "image_url": item.get("socialimage", ""),
            "source_country": item.get("sourcecountry", ""),
        })
    
    # Guardar en cache
    if use_cache and articles:
        _cache_query_result(query_hash, url, articles)
    
    logger.info(f"[GDELT] Obtenidos {len(articles)} artículos para keywords: {keywords[:3]}...")
    
    return articles


def fetch_news_for_markets(markets: list[dict], timespan: str = "6h") -> list[dict]:
    """
    Obtiene noticias relevantes para una lista de mercados.
    
    Extrae keywords de las preguntas de los mercados y busca en GDELT.
    """
    if not markets:
        return []
    
    all_articles = []
    seen_urls = set()
    
    # Extraer keywords únicas de todos los mercados
    market_keywords = {}
    
    for market in markets:
        question = market.get("question", "")
        market_id = market.get("id")
        
        words = re.findall(r'\b[A-Za-z]{5,}\b', question)
        stopwords = {'would', 'could', 'should', 'about', 'which', 'there', 'where', 'these', 'their', 'before', 'after'}
        keywords = [w for w in words if w.lower() not in stopwords][:5]
        
        for kw in keywords:
            if kw not in market_keywords:
                market_keywords[kw] = market_id
    
    # Buscar en batches
    all_keywords = list(market_keywords.keys())
    batch_size = 8
    max_batches = 5
    
    for i in range(0, min(len(all_keywords), batch_size * max_batches), batch_size):
        batch = all_keywords[i:i+batch_size]
        
        # El rate limiter se encarga de la pausa entre requests
        articles = fetch_gdelt_news(batch, timespan, use_cache=True)
        
        for article in articles:
            url = article.get("url")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            
            # Intentar asociar con un mercado
            title_lower = article.get("title", "").lower()
            for kw, market_id in market_keywords.items():
                if kw.lower() in title_lower:
                    article["related_market_id"] = market_id
                    break
            
            all_articles.append(article)
    
    logger.info(f"[GDELT] Total {len(all_articles)} artículos únicos para {len(markets)} mercados")
    return all_articles


def save_gdelt_articles(articles: list[dict]) -> int:
    """
    Guarda artículos de GDELT en Supabase.
    
    Returns:
        Número de artículos guardados (nuevos)
    """
    if not articles:
        return 0
    
    db = get_db()
    saved = 0
    
    for article in articles:
        url = article.get("url", "").strip()
        if not url:
            continue
        
        title = article.get("title", "").strip()
        if not title:
            continue
        
        # Calcular relevance_score basado en tone y keywords
        tone = article.get("tone", 0)
        relevance_score = min(0.8, 0.4 + abs(tone) / 20)
        
        if article.get("related_market_id"):
            relevance_score = min(1.0, relevance_score + 0.2)
        
        if relevance_score < MIN_RELEVANCE_SCORE:
            continue
        
        try:
            result = db_retry(lambda a=article, rs=relevance_score: db.table("news_articles").upsert({
                "title": a.get("title", "")[:500],
                "summary": a.get("summary", "")[:1000],
                "source": a.get("source", "")[:100],
                "url": a.get("url"),
                "published_at": a.get("published_at"),
                "relevance_score": rs,
                "related_market_id": a.get("related_market_id"),
                "gdelt_id": a.get("gdelt_id"),
                "tone": a.get("tone"),
                "domain": a.get("domain", "")[:100],
                "language": a.get("language", "en")[:10],
                "image_url": a.get("image_url", "")[:500],
                "source_country": a.get("source_country", "")[:50],
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="url").execute())
            
            saved += 1
            
        except Exception as e:
            logger.debug(f"[GDELT] Skip artículo (posible duplicado): {e}")
    
    logger.info(f"[GDELT] {saved}/{len(articles)} artículos guardados")
    return saved


def get_recent_gdelt_news(
    market_id: str = None,
    hours: int = 24,
    limit: int = 10
) -> list[dict]:
    """Obtiene noticias GDELT recientes de la base de datos."""
    db = get_db()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        
        query = db.table("news_articles")\
            .select("*")\
            .not_.is_("gdelt_id", "null")\
            .gte("processed_at", cutoff)\
            .order("processed_at", desc=True)\
            .limit(limit)
        
        if market_id:
            query = query.eq("related_market_id", market_id)
        
        result = query.execute()
        return result.data or []
        
    except Exception as e:
        logger.warning(f"[GDELT] Error obteniendo noticias: {e}")
        return []


def get_news_sentiment_for_market(market_id: str, hours: int = 24) -> Optional[float]:
    """Calcula el sentimiento promedio de noticias para un mercado."""
    news = get_recent_gdelt_news(market_id=market_id, hours=hours, limit=20)
    
    if not news:
        return None
    
    tones = [n.get("tone", 0) for n in news if n.get("tone") is not None]
    
    if not tones:
        return None
    
    return sum(tones) / len(tones)


def process_gdelt_cycle(markets: list[dict]) -> dict:
    """
    Ejecuta un ciclo completo de procesamiento GDELT.
    
    Args:
        markets: Lista de mercados activos
    
    Returns:
        Dict con estadísticas del ciclo
    """
    stats = {
        "articles_fetched": 0,
        "articles_saved": 0,
        "markets_covered": 0,
        "cache_cleanup": 0,
    }
    
    # Limpiar cache expirado (una vez por ciclo)
    stats["cache_cleanup"] = _cleanup_expired_cache()
    
    # Fetch para mercados específicos
    if markets:
        articles = fetch_news_for_markets(markets, timespan="6h")
        stats["articles_fetched"] += len(articles)
        stats["articles_saved"] += save_gdelt_articles(articles)
        stats["markets_covered"] = len(set(a.get("related_market_id") for a in articles if a.get("related_market_id")))
    
    # Fetch general de política
    political_articles = fetch_gdelt_news(POLITICAL_KEYWORDS, timespan="3h", use_cache=True)
    stats["articles_fetched"] += len(political_articles)
    stats["articles_saved"] += save_gdelt_articles(political_articles)
    
    logger.info(
        f"[GDELT] Ciclo completado: fetched={stats['articles_fetched']} "
        f"saved={stats['articles_saved']} markets={stats['markets_covered']}"
    )
    
    return stats
