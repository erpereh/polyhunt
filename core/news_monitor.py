"""
Monitor de noticias RSS para PolyHunt.
Obtiene artículos de feeds políticos, puntúa su relevancia con Groq
y los guarda en Supabase sin duplicados (url como clave única).
"""
import calendar
import json
import re
import logging
from datetime import datetime, timezone
from typing import Optional

import feedparser
from groq import Groq

from core import key_manager

logger = logging.getLogger(__name__)

# Feeds RSS ordenados por relevancia para mercados políticos globales de Polymarket
NEWS_FEEDS = [
    # Política USA
    "https://feeds.reuters.com/Reuters/PoliticsNews",
    "https://rss.politico.com/politics-news.xml",
    "https://thehill.com/feed/",
    # Internacional inglés
    "http://feeds.bbci.co.uk/news/politics/rss.xml",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    # Internacional español
    "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada",
    "https://www.bbc.co.uk/mundo/index.xml",
    "https://rss.dw.com/rdf/rss-es-all",
    # Google News dirigido
    "https://news.google.com/rss/search?q=US+election+prediction&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=US+politics+2026&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Iran+Trump+war&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=elecciones+2026&hl=es&gl=ES&ceid=ES:es",
]


def fetch_news(max_age_hours: int = 6) -> list[dict]:
    """
    Obtiene artículos recientes de todos los feeds configurados.
    Filtra artículos más viejos que max_age_hours.
    Retorna lista de dicts con title, summary, url, source, published_at.
    """
    all_articles: list[dict] = []
    cutoff_ts = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)

    for feed_url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            source_name = feed.feed.get("title", "") or feed_url.split("/")[2]

            for entry in feed.entries:
                # Determinar timestamp de publicación
                published_ts  = None
                published_iso = None

                for attr in ("published_parsed", "updated_parsed"):
                    parsed = getattr(entry, attr, None)
                    if parsed:
                        published_ts  = calendar.timegm(parsed)
                        published_iso = datetime.fromtimestamp(
                            published_ts, tz=timezone.utc
                        ).isoformat()
                        break

                # Descartar artículos demasiado viejos (si tienen fecha)
                if published_ts and published_ts < cutoff_ts:
                    continue

                url = entry.get("link", "").strip()
                if not url:
                    continue

                # Limpiar HTML del resumen
                summary_raw   = entry.get("summary") or entry.get("description") or ""
                summary_clean = re.sub(r"<[^>]+>", " ", summary_raw)
                summary_clean = re.sub(r"\s+", " ", summary_clean).strip()[:800]

                all_articles.append({
                    "title":        entry.get("title", "").strip(),
                    "summary":      summary_clean,
                    "url":          url,
                    "source":       source_name,
                    "published_at": published_iso,
                })

            logger.debug(f"[{datetime.now()}] Feed OK: {source_name} ({len(feed.entries)} entradas)")

        except Exception as e:
            logger.warning(f"[{datetime.now()}] Error procesando feed {feed_url}: {e}")

    logger.info(f"[{datetime.now()}] {len(all_articles)} artículos obtenidos de {len(NEWS_FEEDS)} feeds")
    return all_articles


def score_relevance(article: dict, market_question: str) -> float:
    """
    Puntúa la relevancia de un artículo respecto a un mercado de predicción.
    Usa Groq (modelo ligero) para el scoring.
    Retorna float 0.0-1.0; si falla, retorna 0.0.
    """
    title   = (article.get("title") or "").strip()
    summary = (article.get("summary") or "")[:300]

    if not title:
        return 0.0

    prompt = (
        f"¿Qué tan relevante es este artículo para el siguiente mercado de predicción?\n\n"
        f"MERCADO: {market_question}\n\n"
        f"ARTÍCULO: {title}\n{summary}\n\n"
        f"Responde SOLO con JSON válido:\n"
        f'{{ "relevance_score": 0.0, "reason": "breve razón" }}\n'
        f"Donde relevance_score está entre 0.0 (nada relevante) y 1.0 (directamente relevante)."
    )

    key_data = None
    try:
        key_data = key_manager.get_next_key("groq")
        if not key_data:
            return 0.0

        client   = Groq(api_key=key_data["key_value"])
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=120,
        )
        tokens_used = response.usage.total_tokens if response.usage else 0
        key_manager.mark_success(key_data["id"], tokens_used)
        content = response.choices[0].message.content
        match   = re.search(r'\{[^{}]*\}', content, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return float(data.get("relevance_score", 0.0))
    except Exception as e:
        if key_data and "429" in str(e):
            try:
                key_manager.mark_cooldown(key_data["id"], str(e)[:200])
            except Exception:
                pass
        logger.debug(f"[{datetime.now()}] Error scoring artículo: {e}")

    return 0.0


def get_relevant_news(limit: int = 5) -> list[dict]:
    """
    Obtiene las noticias más relevantes almacenadas en Supabase
    (score >= 0.3, ordenadas por score desc).
    """
    from core.db import get_db
    db = get_db()
    try:
        result = (
            db.table("news_articles")
            .select("*")
            .gte("relevance_score", 0.3)
            .order("relevance_score", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"[{datetime.now()}] Error obteniendo noticias relevantes: {e}")
        return []


def save_articles_to_db(articles: list[dict], active_markets: list[dict] = None) -> int:
    """
    Guarda artículos en Supabase con scoring de relevancia.
    Usa url como clave única — no crea duplicados.

    Proceso:
      1. Keyword overlap rápido para pre-filtrar
      2. LLM scoring solo si hay keyword match
      3. Guardar si score >= 0.3

    Retorna el número de artículos guardados.
    """
    from core.paper_trader import save_news_article

    if active_markets is None:
        active_markets = []

    saved = 0
    for article in articles:
        url   = article.get("url", "").strip()
        title = article.get("title", "").strip()
        if not url or not title:
            continue

        try:
            relevance_score   = 0.0
            related_market_id = None

            if active_markets:
                title_lower = title.lower()

                # Paso 1: keyword overlap rápido (todos los mercados activos)
                for market in active_markets:
                    q_words   = set(w for w in market.get("question", "").lower().split() if len(w) > 4)
                    t_words   = set(title_lower.split())
                    overlap   = len(q_words & t_words)

                    if overlap >= 2:
                        relevance_score   = 0.55
                        related_market_id = market.get("id")
                        break

                # Paso 2: refinar con LLM si hay match inicial
                if relevance_score >= 0.5 and related_market_id:
                    best_market = next(
                        (m for m in active_markets if m.get("id") == related_market_id),
                        active_markets[0],
                    )
                    llm_score = score_relevance(article, best_market.get("question", ""))
                    if llm_score > relevance_score:
                        relevance_score = llm_score

            # Guardar si supera el umbral mínimo (o si no hay mercados activos, guardar todo)
            if relevance_score >= 0.3 or not active_markets:
                save_news_article(
                    title=title,
                    summary=(article.get("summary") or "")[:1000],
                    source=article.get("source", ""),
                    url=url,
                    published_at=article.get("published_at"),
                    relevance_score=relevance_score,
                    related_market_id=related_market_id,
                )
                saved += 1

        except Exception as e:
            logger.error(f"[{datetime.now()}] Error guardando artículo '{title[:50]}': {e}")

    logger.info(f"[{datetime.now()}] {saved}/{len(articles)} artículos guardados en Supabase")
    return saved
