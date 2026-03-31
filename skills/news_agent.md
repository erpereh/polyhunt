# Skill: News Agent — PolyHunt

## Propósito
Monitorear noticias relevantes para los mercados de predicción activos.
Usar feedparser para RSS feeds y LLMs para scoring de relevancia.

## Fuentes de noticias (RSS feeds)
```python
NEWS_FEEDS = [
    # Noticias generales
    "https://feeds.reuters.com/reuters/topNews",
    "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "https://feeds.bbci.co.uk/news/rss.xml",
    # Política USA (muy relevante para Polymarket)
    "https://rss.politico.com/politics-news.xml",
    "https://thehill.com/feed/",
    # Crypto/finanzas
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    # Ciencia/tecnología
    "https://feeds.feedburner.com/TechCrunch",
]
```

## Fetcher con feedparser
```python
import feedparser
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

def fetch_recent_articles(feed_url: str, max_age_hours: int = 6) -> list[dict]:
    """Obtiene artículos de las últimas N horas de un feed RSS."""
    try:
        feed = feedparser.parse(feed_url)
        articles = []
        cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)
        
        for entry in feed.entries:
            # Obtener timestamp de publicación
            published = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                import calendar
                published = calendar.timegm(entry.published_parsed)
            
            if published and published < cutoff:
                continue
            
            articles.append({
                "title": entry.get("title", ""),
                "summary": entry.get("summary", entry.get("description", "")),
                "url": entry.get("link", ""),
                "source": feed.feed.get("title", feed_url),
                "published_at": datetime.fromtimestamp(published, tz=timezone.utc).isoformat() if published else None
            })
        
        return articles
    except Exception as e:
        logger.error(f"Error fetching feed {feed_url}: {e}")
        return []
```

## Scoring de relevancia con Groq
```python
def score_article_relevance(article: dict, active_markets: list[dict]) -> tuple[float, str | None]:
    """
    Puntúa la relevancia de un artículo respecto a los mercados activos.
    Retorna (score 0-1, market_id relacionado o None)
    """
    if not active_markets:
        return 0.0, None
    
    market_questions = "\n".join([
        f"- [{m['id']}] {m['question']}" 
        for m in active_markets[:20]  # Top 20 mercados
    ])
    
    prompt = f"""Artículo: {article['title']}
Resumen: {article['summary'][:500]}

Mercados activos:
{market_questions}

Responde SOLO con JSON:
{{
  "relevance_score": 0.XX,
  "related_market_id": "id_del_mercado_o_null",
  "reason": "breve explicación"
}}"""
    
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=200
        )
        import json, re
        content = response.choices[0].message.content
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            return float(result.get("relevance_score", 0)), result.get("related_market_id")
    except Exception as e:
        logger.error(f"Error scoring article: {e}")
    
    return 0.0, None
```

## Loop del news agent
```python
async def news_loop(interval_minutes: int = 30):
    """Corre en background, actualiza noticias cada N minutos."""
    import asyncio
    from core.paper_trader import save_news_article
    from core.db import get_db
    
    while True:
        try:
            # Obtener mercados activos para el scoring
            db = get_db()
            markets = db.table("markets").select("id, question").eq("status", "active").execute().data
            
            for feed_url in NEWS_FEEDS:
                articles = fetch_recent_articles(feed_url, max_age_hours=2)
                for article in articles:
                    if not article["url"]:
                        continue
                    score, related_market = score_article_relevance(article, markets)
                    if score >= 0.3:  # Solo guardar si es mínimamente relevante
                        save_news_article(
                            title=article["title"],
                            summary=article["summary"][:1000],
                            source=article["source"],
                            url=article["url"],
                            published_at=article["published_at"],
                            relevance_score=score,
                            related_market_id=related_market
                        )
            
            logger.info(f"[{datetime.now()}] News loop completado")
        except Exception as e:
            logger.error(f"Error en news loop: {e}")
        
        await asyncio.sleep(interval_minutes * 60)
```

## Reglas
- Solo guardar artículos con relevance_score >= 0.3
- Usar URL como clave única (upsert) para evitar duplicados
- Max 10 noticias en el dashboard (las más recientes)
- El scoring LLM es opcional — si falla, guardar con score 0
- No bloquear el loop principal de trading con el news agent
