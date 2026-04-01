"""
Análisis de mercados de predicción con 2 modelos (Cerebras + Groq).

Pipeline:
  1. Cerebras → screener primario
  2. Groq → confirmación final

Un trade solo se abre si:
  - Ambos modelos responden
  - gap_final >= 15%
  - La divergencia entre modelos no supera 20%
  - Ninguno devuelve confidence = low

Caché unificada: 8 horas por modelo.
"""
import json
import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from openai import OpenAI
from groq import Groq

from core import key_manager

logger = logging.getLogger(__name__)

_MODEL_STATS = {
    "cerebras_ok": 0,
    "cerebras_err": 0,
    "groq_ok": 0,
    "groq_err": 0,
}


def _bump_stat(key: str) -> None:
    if key in _MODEL_STATS:
        _MODEL_STATS[key] += 1


def pop_model_stats() -> dict:
    stats = dict(_MODEL_STATS)
    for k in _MODEL_STATS:
        _MODEL_STATS[k] = 0
    return stats

# Max reintentos con diferentes keys ante 429
MAX_RETRIES = 3

# Modelos
CEREBRAS_MODEL = "qwen-3-235b-a22b-instruct-2507"
CEREBRAS_FALLBACK_MODEL = "llama3.1-8b"
GROQ_MODEL = "llama-3.3-70b-versatile"


_SYSTEM_PROMPT = """Eres un analista experto en mercados de predicción (prediction markets).
Tu trabajo es estimar la probabilidad REAL de que un evento ocurra, comparándola con el precio actual del mercado para detectar ineficiencias.

Analiza la pregunta, el contexto y las noticias recientes. Responde ÚNICAMENTE con un objeto JSON válido, sin texto adicional:
{
  "probability_yes": 0.XX,
  "probability_range": "0.XX-0.XX",
  "confidence": "high|medium|low",
  "resolution_risk": "high|medium|low",
  "edge_detected": true|false,
  "reasoning": "explicación concisa de tu análisis en 2-3 oraciones. OBLIGATORIO incluir."
}

Definiciones:
- probability_yes: probabilidad de que el evento ocurra (0.01 a 0.99)
- probability_range: rango de incertidumbre razonable
- confidence: confianza en tu estimación (high=análisis sólido, medium=algo incierto, low=muy incierto)
- resolution_risk: riesgo de resolución ambigua o subjetiva
- edge_detected: true si tu probabilidad difiere >10% del precio de mercado
- reasoning: SIEMPRE incluir — es el dato más valioso para calibración posterior"""


def _parse_json(content: str) -> dict:
    """Extrae y parsea JSON de la respuesta del LLM con múltiples fallbacks."""
    # Intento 1: parsear directamente
    try:
        return json.loads(content.strip())
    except json.JSONDecodeError:
        pass

    # Intento 2: buscar bloque JSON
    match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Intento 3: limpiar markdown code blocks
    clean = re.sub(r'```(?:json)?\n?', '', content).replace('```', '').strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    return {}


def _normalize_result(result: dict) -> Optional[dict]:
    if not isinstance(result, dict):
        return None
    if result.get("probability_yes") is None:
        return None
    try:
        prob = float(result.get("probability_yes"))
    except (TypeError, ValueError):
        return None
    if prob < 0.0 or prob > 1.0:
        return None

    confidence = str(result.get("confidence", "low")).strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"

    resolution_risk = str(result.get("resolution_risk", "medium")).strip().lower()
    if resolution_risk not in {"high", "medium", "low"}:
        resolution_risk = "medium"

    return {
        "probability_yes": prob,
        "probability_range": result.get("probability_range"),
        "confidence": confidence,
        "resolution_risk": resolution_risk,
        "edge_detected": bool(result.get("edge_detected", False)),
        "reasoning": str(result.get("reasoning") or "").strip(),
    }


def _build_prompt(question: str, description: str, market_price: float,
                  news_articles: list[dict]) -> str:
    """Construye el prompt de análisis con contexto de noticias."""
    news_block = ""
    if news_articles:
        items = []
        for a in news_articles[:5]:
            title   = a.get("title", "")
            summary = (a.get("summary") or "")[:200]
            source  = a.get("source", "Fuente desconocida")
            items.append(f"  • [{source}] {title}: {summary}")
        news_block = "\n\nNOTICIAS RECIENTES RELEVANTES:\n" + "\n".join(items)

    return (
        f"MERCADO DE PREDICCIÓN\n"
        f"Pregunta: {question}\n"
        f"Descripción: {(description or '')[:300]}\n"
        f"Precio actual del mercado (YES token): {market_price:.2%}\n"
        f"{news_block}\n\n"
        f"Analiza este mercado y proporciona tu estimación de probabilidad."
    )


def _get_cached_analysis(market_id: str, model: str, max_age_hours: int = 8) -> Optional[dict]:
    """
    Busca un análisis reciente en Supabase para cualquier modelo.
    
    Args:
        market_id: ID del mercado
        model: nombre del modelo (e.g., "cerebras/qwen-3-235b")
        max_age_hours: máxima antigüedad del cache (default 8h)
    
    Returns:
        dict con el análisis si existe cache válido, None si no hay o falló
    """
    from core.db import get_db
    db = get_db()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        result = (
            db.table("llm_analyses")
            .select("probability_yes, probability_range, confidence, resolution_risk, edge_detected, reasoning, timestamp")
            .eq("market_id", market_id)
            .eq("model", model)
            .gte("timestamp", cutoff)
            .order("timestamp", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            row = result.data[0]
            ts_str = row["timestamp"].replace("Z", "+00:00")
            age_min = (
                datetime.now(timezone.utc) - datetime.fromisoformat(ts_str)
            ).total_seconds() / 60
            logger.info(
                f"[LLM] Cache hit ({age_min:.0f}min) — reutilizando {model} | {market_id[:16]}…"
            )
            return {
                "probability_yes":  row.get("probability_yes"),
                "probability_range": row.get("probability_range"),
                "confidence":       row.get("confidence", "low"),
                "resolution_risk":  row.get("resolution_risk", "medium"),
                "edge_detected":    row.get("edge_detected", False),
                "reasoning":        row.get("reasoning", ""),
            }
    except Exception as e:
        logger.debug(f"[LLM] Error consultando cache {model}: {e}")
    return None


def _save_analysis(market_id: str, model: str, result: dict,
                   market_price: float, gap: float) -> None:
    """Guarda análisis de LLM para calibración futura."""
    from core.db import get_db
    db = get_db()
    try:
        db.table("llm_analyses").insert({
            "market_id":              market_id,
            "model":                  model,
            "probability_yes":        result.get("probability_yes"),
            "probability_range":      result.get("probability_range"),
            "confidence":             result.get("confidence"),
            "resolution_risk":        result.get("resolution_risk"),
            "edge_detected":          result.get("edge_detected", False),
            "reasoning":              result.get("reasoning"),
            "market_price_at_analysis": market_price,
            "gap":                    gap,
        }).execute()
    except Exception as e:
        logger.error(f"[LLM] Error guardando análisis {model}: {e}")


def analyze_cerebras(question: str, description: str, market_price: float,
                     news_articles: list[dict] = None) -> Optional[dict]:
    """
    Análisis con Cerebras (Qwen 3 235B) — screener primario.
    
    Usa API OpenAI-compatible.
    Retorna dict con los campos de análisis, o None si falla/sin keys.
    """
    if news_articles is None:
        news_articles = []

    prompt = _build_prompt(question, description, market_price, news_articles)

    for attempt in range(MAX_RETRIES):
        key_data = key_manager.get_next_key("cerebras")
        if not key_data:
            if attempt == 0:
                logger.warning("[LLM] No hay keys disponibles para Cerebras")
            return None

        try:
            client = OpenAI(
                base_url="https://api.cerebras.ai/v1",
                api_key=key_data["key_value"],
            )

            model_used = CEREBRAS_MODEL
            try:
                response = client.chat.completions.create(
                    model=model_used,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user",   "content": prompt},
                    ],
                    temperature=0.1,
                    max_tokens=400,
                )
            except Exception as e:
                err = str(e).lower()
                if "model_not_found" in err or "does not exist" in err:
                    model_used = CEREBRAS_FALLBACK_MODEL
                    response = client.chat.completions.create(
                        model=model_used,
                        messages=[
                            {"role": "system", "content": _SYSTEM_PROMPT},
                            {"role": "user",   "content": prompt},
                        ],
                        temperature=0.1,
                        max_tokens=400,
                    )
                else:
                    raise
            
            content = response.choices[0].message.content
            result = _normalize_result(_parse_json(content))

            if result:
                # Marcar éxito
                tokens_used = response.usage.total_tokens if response.usage else 0
                key_manager.mark_success(key_data["id"], tokens_used)
                
                logger.info(
                    f"[LLM] Cerebras OK ({model_used}) — prob={result['probability_yes']:.2f} "
                    f"conf={result.get('confidence','?')} | {question[:50]}"
                )
                _bump_stat("cerebras_ok")
                return result
            else:
                logger.warning(f"[LLM] Cerebras devolvió JSON inválido para: {question[:50]}")
                _bump_stat("cerebras_err")
                return None

        except Exception as e:
            error_str = str(e).lower()
            if "429" in str(e) or "rate" in error_str or "limit" in error_str:
                key_manager.mark_cooldown(key_data["id"], str(e)[:200])
                logger.warning(f"[LLM] Cerebras 429 — rotando key (intento {attempt + 1}/{MAX_RETRIES})")
                _bump_stat("cerebras_err")
                continue
            else:
                logger.error(f"[LLM] Error en Cerebras: {e}")
                _bump_stat("cerebras_err")
                return None

    return None


def analyze_groq(question: str, description: str, market_price: float,
                 news_articles: list[dict] = None) -> Optional[dict]:
    """
    Análisis de confirmación con Groq (LLaMA-3.3-70B-Versatile).
    Retorna dict con los campos de análisis, o None si falla/sin keys.
    """
    if news_articles is None:
        news_articles = []

    prompt = _build_prompt(question, description, market_price, news_articles)

    for attempt in range(MAX_RETRIES):
        key_data = key_manager.get_next_key("groq")
        if not key_data:
            if attempt == 0:
                logger.warning("[LLM] No hay keys disponibles para Groq")
            return None

        try:
            client = Groq(api_key=key_data["key_value"])
            
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.1,
                max_tokens=400,
            )
            
            content = response.choices[0].message.content
            result = _normalize_result(_parse_json(content))

            if result:
                # Marcar éxito
                tokens_used = response.usage.total_tokens if response.usage else 0
                key_manager.mark_success(key_data["id"], tokens_used)
                
                logger.info(
                    f"[LLM] Groq OK — prob={result['probability_yes']:.2f} "
                    f"conf={result.get('confidence','?')} | {question[:50]}"
                )
                _bump_stat("groq_ok")
                return result
            else:
                logger.warning(f"[LLM] Groq devolvió JSON inválido para: {question[:50]}")
                _bump_stat("groq_err")
                return None

        except Exception as e:
            error_str = str(e).lower()
            if "429" in str(e) or "rate" in error_str or "limit" in error_str:
                key_manager.mark_cooldown(key_data["id"], str(e)[:200])
                logger.warning(f"[LLM] Groq 429 — rotando key (intento {attempt + 1}/{MAX_RETRIES})")
                _bump_stat("groq_err")
                continue
            else:
                logger.error(f"[LLM] Error en Groq: {e}")
                _bump_stat("groq_err")
                return None

    return None


def full_analysis(
    market: dict,
    market_price: float,
    news_articles: list[dict] = None,
    force: bool = False,
) -> tuple[Optional[dict], Optional[dict], float, bool]:
    """
    Pipeline de análisis de 2 modelos (Cerebras + Groq).
    
    Args:
        market: dict con id, question, description
        market_price: precio actual del token YES
        news_articles: lista de noticias recientes relevantes
        force: True para ignorar cache (mercados con evento nuevo)
    
    Flujo:
      1. Cerebras → screener primario
         - Si cache válida y no force → usar cache
         - Si result es None o gap < 10% → no operar
      
      2. Groq → confirmación final
         - Solo si Cerebras detectó gap >= 10% y conf != low
         - Si cache válida y no force → usar cache
         - Si result es None → no operar
    
    Lógica de decisión:
      - gap_final = promedio de los modelos que respondieron
      - should_trade = True solo si:
        * Al menos 2 modelos respondieron
        * gap_final >= 15%
        * Cerebras y Groq no discrepan > 20%
        * Ninguno devolvió confidence = low
    
    Returns:
        (cerebras_result, groq_result, gap_final, should_trade)
    """
    if news_articles is None:
        news_articles = []

    question    = market.get("question", "")
    description = market.get("description", "") or ""
    market_id   = market.get("id", "")

    cerebras_result = None
    groq_result     = None

    # ─── CEREBRAS: screener primario ────────────────────────────────────────────
    cerebras_model = f"cerebras/{CEREBRAS_MODEL}"
    
    if not force:
        cerebras_result = _get_cached_analysis(market_id, cerebras_model, max_age_hours=8)
    
    if cerebras_result is None:
        cerebras_result = analyze_cerebras(question, description, market_price, news_articles)
        
        if cerebras_result and cerebras_result.get("probability_yes") is not None:
            cerebras_prob = float(cerebras_result["probability_yes"])
            cerebras_gap = abs(cerebras_prob - market_price)
            _save_analysis(market_id, cerebras_model, cerebras_result, market_price, cerebras_gap)
    
    if not cerebras_result or cerebras_result.get("probability_yes") is None:
        logger.warning(f"[LLM] Cerebras falló para {market_id[:16]}… — saltando mercado")
        return None, None, 0.0, False

    cerebras_prob = float(cerebras_result["probability_yes"])
    cerebras_gap  = abs(cerebras_prob - market_price)
    cerebras_conf = cerebras_result.get("confidence", "low")

    # Si gap < 10% o confianza baja → no merece confirmación
    if cerebras_gap < 0.10 or cerebras_conf == "low":
        logger.info(
            f"[LLM] Gap pequeño ({cerebras_gap:.1%}) o conf low — sin Groq | {question[:50]}"
        )
        return cerebras_result, None, cerebras_gap, False

    # ─── GROQ: confirmación final ────────────────────────────────────────────
    groq_model = f"groq/{GROQ_MODEL}"
    
    if not force:
        groq_result = _get_cached_analysis(market_id, groq_model, max_age_hours=8)
    
    if groq_result is None:
        groq_result = analyze_groq(question, description, market_price, news_articles)
        
        if groq_result and groq_result.get("probability_yes") is not None:
            groq_prob = float(groq_result["probability_yes"])
            groq_gap = abs(groq_prob - market_price)
            _save_analysis(market_id, groq_model, groq_result, market_price, groq_gap)

    # ─── DECISIÓN FINAL ────────────────────────────────────────────────────────
    if not groq_result or groq_result.get("probability_yes") is None:
        logger.info(f"[LLM] Groq no disponible — no operar | {question[:50]}")
        return cerebras_result, None, cerebras_gap, False

    groq_prob = float(groq_result["probability_yes"])
    groq_conf = groq_result.get("confidence", "low")

    divergence_cg = abs(cerebras_prob - groq_prob)
    if divergence_cg > 0.20:
        logger.warning(
            f"[LLM] Divergencia Cerebras-Groq {divergence_cg:.1%} — no operar | {question[:50]}"
        )
        return cerebras_result, groq_result, 0.0, False

    probs = [cerebras_prob, groq_prob]
    confs = [cerebras_conf, groq_conf]
    
    # Calcular gap final como promedio
    avg_prob  = sum(probs) / len(probs)
    gap_final = abs(avg_prob - market_price)
    
    # Verificar que ningún modelo tenga confianza baja
    has_low_conf = any(c == "low" for c in confs)
    
    # Necesitamos al menos 2 modelos (ya garantizado aquí)
    should_trade = (
        len(probs) >= 2
        and gap_final >= 0.15
        and not has_low_conf
    )

    logger.info(
        f"[LLM] Análisis completo — {len(probs)} modelos | gap={gap_final:.1%} "
        f"should_trade={should_trade} | {question[:50]}"
    )

    return cerebras_result, groq_result, gap_final, should_trade
