"""
GDELT News Pipeline para PolyHunt.

Reemplaza/complementa el RSS feed con GDELT DOC 2.0 API.
GDELT ofrece cobertura global, actualización cada 15 minutos y sin límites.

API utilizada:
  - GET https://api.gdeltproject.org/api/v2/doc/doc?query=...&mode=ArtList&format=json

Ventajas sobre RSS:
  - Cobertura global masiva (>100 idiomas)
  - Análisis de tono/sentimiento incluido
  - Sin rate limits
  - Actualización más frecuente
"""
import logging
import re
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import quote_plus

import httpx

from core.db import get_db, db_retry

logger = logging.getLogger(__name__)

# Configuración
GDELT_API_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
DEFAULT_TIMESPAN = "24h"        # Timespan por defecto
MAX_RECORDS = 50                # Máximo artículos por query
MIN_RELEVANCE_SCORE = 0.3      # Score mínimo para guardar

# Keywords base para mercados políticos
POLITICAL_KEYWORDS = [
    "Trump", "Biden", "election", "Congress", "Senate", "White House",
    "Putin", "Zelensky", "Ukraine", "Russia", "NATO", "China", "Taiwan",
    "Iran", "Israel", "Gaza", "war", "sanctions", "tariffs", "trade deal",
    "Federal Reserve", "inflation", "recession", "economy",
]


def _build_gdelt_query(keywords: list[str], timespan: str = DEFAULT_TIMESPAN) -> str:
    """
    Construye la URL de query para GDELT DOC 2.0.
    
    Args:
        keywords: Lista de keywords a buscar
        timespan: Periodo de tiempo (e.g., "24h", "7d")
    
    Returns:
        URL completa de la query
    """
    # Unir keywords con OR
    query_str = " OR ".join(f'"{kw}"' for kw in keywords[:10])  # Max 10 para no exceder límite
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


def fetch_gdelt_news(
    keywords: list[str] = None,
    timespan: str = DEFAULT_TIMESPAN,
    language: str = None
) -> list[dict]:
    """
    Obtiene artículos de GDELT DOC 2.0 API.
    
    Args:
        keywords: Keywords a buscar (default: POLITICAL_KEYWORDS)
        timespan: Periodo de tiempo
        language: Código de idioma (e.g., "English", "Spanish")
    
    Returns:
        Lista de dicts con title, url, source, tone, etc.
    """
    if keywords is None:
        keywords = POLITICAL_KEYWORDS
    
    if not keywords:
        return []
    
    url = _build_gdelt_query(keywords, timespan)
    
    # Añadir filtro de idioma si se especifica
    if language:
        url += f"&sourcelang={language}"
    
    articles = []
    
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
            
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
                        # Formato GDELT: YYYYMMDDTHHMMSSZ
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
                    "summary": "",  # GDELT no provee summary en ArtList
                    "published_at": published_at,
                    "gdelt_id": _generate_gdelt_id(article_url),
                    "tone": float(item.get("tone", 0)),
                    "domain": domain,
                    "language": item.get("language", "English"),
                    "image_url": item.get("socialimage", ""),
                    "source_country": item.get("sourcecountry", ""),
                })
            
            logger.info(f"[GDELT] Obtenidos {len(articles)} artículos para keywords: {keywords[:3]}...")
            
    except httpx.HTTPStatusError as e:
        logger.warning(f"[GDELT] Error HTTP: {e.response.status_code}")
    except Exception as e:
        logger.warning(f"[GDELT] Error obteniendo noticias: {e}")
    
    return articles


def fetch_news_for_markets(markets: list[dict], timespan: str = "6h") -> list[dict]:
    """
    Obtiene noticias relevantes para una lista de mercados.
    
    Extrae keywords de las preguntas de los mercados y busca en GDELT.
    
    Args:
        markets: Lista de mercados con 'question'
        timespan: Periodo de tiempo a buscar
    
    Returns:
        Lista de artículos con related_market_id si se pudo asociar
    """
    if not markets:
        return []
    
    all_articles = []
    seen_urls = set()
    
    # Extraer keywords únicas de todos los mercados
    market_keywords = {}  # keyword -> market_id mapping
    
    for market in markets:
        question = market.get("question", "")
        market_id = market.get("id")
        
        # Extraer palabras significativas (>4 chars, no stopwords)
        words = re.findall(r'\b[A-Za-z]{5,}\b', question)
        stopwords = {'would', 'could', 'should', 'about', 'which', 'there', 'where', 'these', 'their', 'before', 'after'}
        keywords = [w for w in words if w.lower() not in stopwords][:5]
        
        for kw in keywords:
            if kw not in market_keywords:
                market_keywords[kw] = market_id
    
    # Buscar en batches (GDELT tiene límite de query length)
    all_keywords = list(market_keywords.keys())
    batch_size = 8
    
    for i in range(0, len(all_keywords), batch_size):
        batch = all_keywords[i:i+batch_size]
        articles = fetch_gdelt_news(batch, timespan)
        
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
    
    Usa gdelt_id y url para evitar duplicados.
    
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
        relevance_score = min(0.8, 0.4 + abs(tone) / 20)  # Base 0.4, aumenta con tone extremo
        
        if article.get("related_market_id"):
            relevance_score = min(1.0, relevance_score + 0.2)  # Boost si está relacionado
        
        if relevance_score < MIN_RELEVANCE_SCORE:
            continue
        
        try:
            # Intentar insertar (fallará si url ya existe por unique constraint)
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
            # Duplicado o error - continuar
            logger.debug(f"[GDELT] Skip artículo (posible duplicado): {e}")
    
    logger.info(f"[GDELT] {saved}/{len(articles)} artículos guardados")
    return saved


def get_recent_gdelt_news(
    market_id: str = None,
    hours: int = 24,
    limit: int = 10
) -> list[dict]:
    """
    Obtiene noticias GDELT recientes de la base de datos.
    
    Args:
        market_id: Filtrar por mercado (opcional)
        hours: Horas hacia atrás
        limit: Máximo de resultados
    
    Returns:
        Lista de artículos
    """
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
    """
    Calcula el sentimiento promedio de noticias para un mercado.
    
    Usa el campo 'tone' de GDELT (negativo = bearish, positivo = bullish).
    
    Returns:
        Tone promedio o None si no hay datos
    """
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
    }
    
    # Fetch para mercados específicos
    if markets:
        articles = fetch_news_for_markets(markets, timespan="6h")
        stats["articles_fetched"] += len(articles)
        stats["articles_saved"] += save_gdelt_articles(articles)
        stats["markets_covered"] = len(set(a.get("related_market_id") for a in articles if a.get("related_market_id")))
    
    # Fetch general de política
    political_articles = fetch_gdelt_news(POLITICAL_KEYWORDS, timespan="3h")
    stats["articles_fetched"] += len(political_articles)
    stats["articles_saved"] += save_gdelt_articles(political_articles)
    
    logger.info(
        f"[GDELT] Ciclo completado: fetched={stats['articles_fetched']} "
        f"saved={stats['articles_saved']} markets={stats['markets_covered']}"
    )
    
    return stats
