"""
Cross-Market Correlation para PolyHunt.

Detecta mercados relacionados usando:
  1. Similitud semántica (embeddings + pgvector)
  2. Correlación de precios históricos

Ejemplo: Si "Trump wins election" sube, "Trump policy X" probablemente también.

Requiere:
  - pgvector extension en Supabase
  - sentence-transformers para embeddings (all-MiniLM-L6-v2, 384 dims)
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
import re

from core.db import get_db, db_retry

logger = logging.getLogger(__name__)

# Configuración
EMBEDDING_DIMENSION = 384       # all-MiniLM-L6-v2
SIMILARITY_THRESHOLD = 0.7      # Cosine similarity mínima para considerar relacionados
PRICE_CORRELATION_DAYS = 14     # Días de historial para correlación de precios
MIN_CORRELATION = 0.5           # Correlación mínima para considerar relacionados

# Cache de embedder (carga lazy)
_embedder = None


def _get_embedder():
    """Carga el modelo de embeddings de forma lazy."""
    global _embedder
    if _embedder is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embedder = SentenceTransformer('all-MiniLM-L6-v2')
            logger.info("[Correlator] Modelo de embeddings cargado")
        except ImportError:
            logger.warning("[Correlator] sentence-transformers no instalado")
            return None
        except Exception as e:
            logger.warning(f"[Correlator] Error cargando embedder: {e}")
            return None
    return _embedder


def compute_embedding(text: str) -> Optional[list[float]]:
    """
    Computa embedding para un texto.
    
    Args:
        text: Texto a embeber
    
    Returns:
        Lista de floats (384 dims) o None si falla
    """
    embedder = _get_embedder()
    if embedder is None:
        return None
    
    try:
        # Limpiar texto
        clean_text = re.sub(r'\s+', ' ', text).strip()[:500]
        if not clean_text:
            return None
        
        embedding = embedder.encode(clean_text, convert_to_numpy=True)
        return embedding.tolist()
    except Exception as e:
        logger.debug(f"[Correlator] Error computando embedding: {e}")
        return None


def update_market_embedding(market_id: str, question: str, description: str = "") -> bool:
    """
    Actualiza el embedding de un mercado en Supabase.
    
    Args:
        market_id: ID del mercado
        question: Pregunta del mercado
        description: Descripción adicional
    
    Returns:
        True si se actualizó exitosamente
    """
    text = f"{question} {description or ''}"
    embedding = compute_embedding(text)
    
    if embedding is None:
        return False
    
    db = get_db()
    try:
        # pgvector espera el embedding como string con formato [x,y,z,...]
        embedding_str = f"[{','.join(str(x) for x in embedding)}]"
        
        db_retry(lambda: db.table("markets").update({
            "description_embedding": embedding_str,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", market_id).execute())
        
        return True
    except Exception as e:
        logger.warning(f"[Correlator] Error guardando embedding: {e}")
        return False


def find_similar_markets(
    market_id: str,
    limit: int = 5,
    min_similarity: float = SIMILARITY_THRESHOLD
) -> list[dict]:
    """
    Encuentra mercados similares usando pgvector.
    
    Args:
        market_id: ID del mercado de referencia
        limit: Máximo de resultados
        min_similarity: Similitud mínima (0-1)
    
    Returns:
        Lista de dicts con id, question, similarity
    """
    db = get_db()
    
    try:
        # Primero obtener el embedding del mercado de referencia
        result = db.table("markets")\
            .select("description_embedding")\
            .eq("id", market_id)\
            .limit(1)\
            .execute()
        
        if not result.data or not result.data[0].get("description_embedding"):
            return []
        
        ref_embedding = result.data[0]["description_embedding"]
        
        # Usar RPC para búsqueda de similitud (requiere función en Supabase)
        # Si no existe la función, fallback a query manual
        try:
            similar = db.rpc("find_similar_markets", {
                "query_embedding": ref_embedding,
                "match_threshold": min_similarity,
                "match_count": limit + 1,  # +1 porque incluirá el propio mercado
            }).execute()
            
            # Filtrar el mercado de referencia
            return [
                m for m in (similar.data or [])
                if m.get("id") != market_id
            ][:limit]
            
        except Exception:
            # Fallback: query manual (menos eficiente)
            logger.debug("[Correlator] RPC no disponible, usando query manual")
            
            # Obtener todos los mercados con embeddings
            all_markets = db.table("markets")\
                .select("id, question, description_embedding")\
                .not_.is_("description_embedding", "null")\
                .neq("id", market_id)\
                .execute()
            
            if not all_markets.data:
                return []
            
            # Calcular similitudes manualmente
            import numpy as np
            
            ref_vec = np.array(_parse_embedding(ref_embedding))
            ref_norm = np.linalg.norm(ref_vec)
            
            similarities = []
            for m in all_markets.data:
                try:
                    m_vec = np.array(_parse_embedding(m["description_embedding"]))
                    m_norm = np.linalg.norm(m_vec)
                    
                    if ref_norm > 0 and m_norm > 0:
                        similarity = float(np.dot(ref_vec, m_vec) / (ref_norm * m_norm))
                        
                        if similarity >= min_similarity:
                            similarities.append({
                                "id": m["id"],
                                "question": m["question"],
                                "similarity": similarity,
                            })
                except Exception:
                    continue
            
            # Ordenar por similitud descendente
            similarities.sort(key=lambda x: x["similarity"], reverse=True)
            return similarities[:limit]
        
    except Exception as e:
        logger.warning(f"[Correlator] Error buscando mercados similares: {e}")
        return []


def _parse_embedding(embedding_str: str) -> list[float]:
    """Parsea string de embedding a lista de floats."""
    if isinstance(embedding_str, list):
        return embedding_str
    
    # Formato pgvector: [x,y,z,...]
    clean = embedding_str.strip().strip("[]")
    return [float(x) for x in clean.split(",") if x.strip()]


def compute_price_correlation(market_id_a: str, market_id_b: str, days: int = PRICE_CORRELATION_DAYS) -> Optional[float]:
    """
    Calcula correlación de precios entre dos mercados.
    
    Usa los price_snapshots de los últimos N días.
    
    Returns:
        Correlación (-1 a 1) o None si no hay suficientes datos
    """
    db = get_db()
    
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        
        # Obtener snapshots de ambos mercados
        snapshots_a = db.table("price_snapshots")\
            .select("price, timestamp")\
            .eq("market_id", market_id_a)\
            .gte("timestamp", cutoff)\
            .order("timestamp")\
            .execute()
        
        snapshots_b = db.table("price_snapshots")\
            .select("price, timestamp")\
            .eq("market_id", market_id_b)\
            .gte("timestamp", cutoff)\
            .order("timestamp")\
            .execute()
        
        if not snapshots_a.data or not snapshots_b.data:
            return None
        
        # Alinear por timestamp (aproximado por hora)
        def to_hour_key(ts_str):
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d-%H")
        
        prices_a = {to_hour_key(s["timestamp"]): float(s["price"]) for s in snapshots_a.data}
        prices_b = {to_hour_key(s["timestamp"]): float(s["price"]) for s in snapshots_b.data}
        
        # Encontrar timestamps comunes
        common_keys = set(prices_a.keys()) & set(prices_b.keys())
        
        if len(common_keys) < 10:  # Mínimo 10 puntos para correlación significativa
            return None
        
        # Calcular correlación de Pearson
        import numpy as np
        
        values_a = [prices_a[k] for k in sorted(common_keys)]
        values_b = [prices_b[k] for k in sorted(common_keys)]
        
        correlation = np.corrcoef(values_a, values_b)[0, 1]
        
        return float(correlation) if not np.isnan(correlation) else None
        
    except Exception as e:
        logger.debug(f"[Correlator] Error calculando correlación de precios: {e}")
        return None


def save_correlation_snapshot(
    market_id_a: str,
    market_id_b: str,
    price_correlation: Optional[float] = None,
    semantic_similarity: Optional[float] = None
) -> bool:
    """
    Guarda snapshot de correlación entre dos mercados.
    """
    db = get_db()
    
    try:
        # Ordenar IDs para consistencia
        if market_id_a > market_id_b:
            market_id_a, market_id_b = market_id_b, market_id_a
        
        db_retry(lambda: db.table("correlation_snapshots").insert({
            "market_id_a": market_id_a,
            "market_id_b": market_id_b,
            "price_correlation": price_correlation,
            "semantic_similarity": semantic_similarity,
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
        }).execute())
        
        return True
    except Exception as e:
        logger.debug(f"[Correlator] Error guardando correlación: {e}")
        return False


def get_correlated_markets(market_id: str, min_correlation: float = MIN_CORRELATION) -> list[dict]:
    """
    Obtiene mercados correlacionados (por precio o semántica).
    
    Combina similitud semántica y correlación de precios.
    
    Returns:
        Lista de mercados correlacionados con scores
    """
    results = []
    
    # Similitud semántica
    similar = find_similar_markets(market_id, limit=10, min_similarity=min_correlation)
    
    for m in similar:
        # Añadir correlación de precios si está disponible
        price_corr = compute_price_correlation(market_id, m["id"])
        
        results.append({
            "id": m["id"],
            "question": m["question"],
            "semantic_similarity": m["similarity"],
            "price_correlation": price_corr,
            "combined_score": _combine_scores(m["similarity"], price_corr),
        })
    
    # Ordenar por score combinado
    results.sort(key=lambda x: x["combined_score"], reverse=True)
    
    return results


def _combine_scores(semantic: float, price: Optional[float]) -> float:
    """Combina scores de similitud semántica y correlación de precios."""
    if price is None:
        return semantic
    
    # Peso: 60% semántica, 40% precio
    return 0.6 * semantic + 0.4 * abs(price)


def update_all_embeddings(markets: list[dict], batch_size: int = 20) -> int:
    """
    Actualiza embeddings para una lista de mercados.
    
    Args:
        markets: Lista de mercados
        batch_size: Tamaño del batch (para no sobrecargar)
    
    Returns:
        Número de embeddings actualizados
    """
    updated = 0
    
    for market in markets[:batch_size]:
        market_id = market.get("id")
        question = market.get("question", "")
        description = market.get("description", "")
        
        if not market_id or not question:
            continue
        
        if update_market_embedding(market_id, question, description):
            updated += 1
    
    logger.info(f"[Correlator] Actualizados {updated}/{len(markets[:batch_size])} embeddings")
    return updated


def find_market_clusters(markets: list[dict], min_cluster_size: int = 3) -> list[dict]:
    """
    Agrupa mercados en clusters basados en similitud semántica.
    
    Usa clustering jerárquico simple.
    
    Returns:
        Lista de clusters con market_ids y keywords
    """
    if len(markets) < min_cluster_size:
        return []
    
    try:
        import numpy as np
        from collections import defaultdict
        
        # Obtener embeddings
        db = get_db()
        result = db.table("markets")\
            .select("id, question, description_embedding")\
            .not_.is_("description_embedding", "null")\
            .execute()
        
        if not result.data or len(result.data) < min_cluster_size:
            return []
        
        # Calcular matriz de similitud
        market_ids = []
        questions = []
        embeddings = []
        
        for m in result.data:
            emb = _parse_embedding(m["description_embedding"])
            if emb:
                market_ids.append(m["id"])
                questions.append(m["question"])
                embeddings.append(emb)
        
        if len(embeddings) < min_cluster_size:
            return []
        
        emb_matrix = np.array(embeddings)
        
        # Normalizar
        norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
        emb_matrix = emb_matrix / np.maximum(norms, 1e-8)
        
        # Similitud coseno
        sim_matrix = np.dot(emb_matrix, emb_matrix.T)
        
        # Clustering simple: greedy agrupación
        clusters = []
        used = set()
        
        for i in range(len(market_ids)):
            if i in used:
                continue
            
            cluster_indices = [i]
            used.add(i)
            
            for j in range(i + 1, len(market_ids)):
                if j in used:
                    continue
                
                # Verificar similitud con todos los miembros del cluster
                similarities = [sim_matrix[i][j] for i in cluster_indices]
                avg_sim = sum(similarities) / len(similarities)
                
                if avg_sim >= SIMILARITY_THRESHOLD:
                    cluster_indices.append(j)
                    used.add(j)
            
            if len(cluster_indices) >= min_cluster_size:
                cluster_market_ids = [market_ids[idx] for idx in cluster_indices]
                cluster_questions = [questions[idx] for idx in cluster_indices]
                
                # Extraer keywords comunes
                all_words = []
                for q in cluster_questions:
                    words = re.findall(r'\b[A-Za-z]{4,}\b', q)
                    all_words.extend([w.lower() for w in words])
                
                from collections import Counter
                word_counts = Counter(all_words)
                keywords = [w for w, c in word_counts.most_common(5) if c >= 2]
                
                clusters.append({
                    "market_ids": cluster_market_ids,
                    "size": len(cluster_market_ids),
                    "keywords": keywords,
                    "cluster_name": " ".join(keywords[:3]).title() if keywords else "Cluster",
                })
        
        logger.info(f"[Correlator] Encontrados {len(clusters)} clusters de mercados")
        return clusters
        
    except Exception as e:
        logger.warning(f"[Correlator] Error encontrando clusters: {e}")
        return []


def get_correlation_signal(market_id: str) -> Optional[dict]:
    """
    Obtiene señal de correlación para un mercado.
    
    Si mercados correlacionados se mueven en una dirección,
    puede indicar hacia dónde se moverá este mercado.
    
    Returns:
        Dict con signal ("bullish"/"bearish") y confidence, o None
    """
    correlated = get_correlated_markets(market_id)
    
    if not correlated:
        return None
    
    db = get_db()
    bullish_signals = 0
    bearish_signals = 0
    total_weight = 0
    
    for m in correlated[:5]:  # Top 5 correlacionados
        try:
            # Obtener cambio de precio reciente
            result = db.table("markets")\
                .select("last_price, last_price_change_pct")\
                .eq("id", m["id"])\
                .limit(1)\
                .execute()
            
            if result.data:
                change = result.data[0].get("last_price_change_pct", 0)
                weight = m["combined_score"]
                total_weight += weight
                
                if change > 0.02:  # +2%
                    bullish_signals += weight
                elif change < -0.02:  # -2%
                    bearish_signals += weight
                    
        except Exception:
            continue
    
    if total_weight == 0:
        return None
    
    bullish_ratio = bullish_signals / total_weight
    bearish_ratio = bearish_signals / total_weight
    
    if bullish_ratio > 0.6:
        return {"signal": "bullish", "confidence": bullish_ratio}
    elif bearish_ratio > 0.6:
        return {"signal": "bearish", "confidence": bearish_ratio}
    
    return None
