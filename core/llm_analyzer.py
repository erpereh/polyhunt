"""
Análisis de mercados de predicción con 2 modelos (Cerebras + Groq).

Pipeline:
  1. Cerebras → screener primario (con multi-sample variance)
  2. Groq → confirmación final
  
Fase 1 - Calibración:
  - Multi-Sample Variance: 3 llamadas con temperature=0.7, medir dispersión
  - Consistency Check: comparar reasoning keywords entre modelos
  - Platt Scaling: calibrar probabilidades con datos históricos

Un trade solo se abre si:
  - Ambos modelos responden
  - gap_final >= 15%
  - La divergencia entre modelos no supera 20%
  - Ninguno devuelve confidence = low
  - Variance < 15% (nuevo)
  - Reasoning overlap >= 30% (nuevo)

Caché unificada: 8 horas por modelo.
"""
import json
import re
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional
from collections import Counter

import numpy as np
from scipy.optimize import minimize

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


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════════════════════

MAX_RETRIES = 3

# Modelos
CEREBRAS_MODEL = "qwen-3-235b-a22b-instruct-2507"
CEREBRAS_FALLBACK_MODEL = "llama3.1-8b"
GROQ_MODEL = "llama-3.3-70b-versatile"

# Calibración Fase 1
VARIANCE_THRESHOLD = 0.15          # Max desviación estándar permitida
VARIANCE_SAMPLES = 3               # Número de muestras para variance
VARIANCE_TEMPERATURE = 0.7         # Temperature para permitir variación
REASONING_OVERLAP_THRESHOLD = 0.30 # Min overlap de keywords entre modelos
PLATT_MIN_SAMPLES = 50             # Mínimo de datos para entrenar calibrador


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


# ═══════════════════════════════════════════════════════════════════════════════
# PLATT SCALING
# ═══════════════════════════════════════════════════════════════════════════════

class PlattScaler:
    """
    Calibrador de probabilidades usando Platt Scaling.
    
    Ajusta probabilidades raw de LLMs para que estén mejor calibradas
    usando regresión logística entrenada con datos históricos.
    """
    def __init__(self):
        self.a = 1.0  # Parámetro de escala (default = sin cambio)
        self.b = 0.0  # Parámetro de sesgo
        self.is_fitted = False
        self.n_samples = 0
    
    def fit(self, predictions: list[float], outcomes: list[int]) -> bool:
        """
        Entrena el calibrador con datos históricos.
        
        Args:
            predictions: probabilidades predichas (0-1)
            outcomes: resultados reales (0 o 1)
        
        Returns:
            True si entrenó exitosamente, False si no hay suficientes datos
        """
        if len(predictions) < PLATT_MIN_SAMPLES:
            logger.debug(f"[Platt] Insuficientes datos: {len(predictions)}/{PLATT_MIN_SAMPLES}")
            return False
        
        predictions = np.array(predictions)
        outcomes = np.array(outcomes)
        
        # Evitar log(0) y log(1)
        predictions = np.clip(predictions, 1e-7, 1 - 1e-7)
        
        # Log-odds (logit)
        log_odds = np.log(predictions / (1 - predictions))
        
        # Optimizar parámetros minimizando negative log-likelihood
        def neg_log_likelihood(params):
            a, b = params
            calibrated = 1 / (1 + np.exp(-(a * log_odds + b)))
            calibrated = np.clip(calibrated, 1e-7, 1 - 1e-7)
            return -np.sum(
                outcomes * np.log(calibrated) + 
                (1 - outcomes) * np.log(1 - calibrated)
            )
        
        try:
            result = minimize(
                neg_log_likelihood, 
                [1.0, 0.0], 
                method='L-BFGS-B',
                bounds=[(0.1, 10.0), (-5.0, 5.0)]  # Limitar parámetros razonables
            )
            self.a, self.b = result.x
            self.is_fitted = True
            self.n_samples = len(predictions)
            logger.info(f"[Platt] Calibrador entrenado: a={self.a:.3f}, b={self.b:.3f} (n={self.n_samples})")
            return True
        except Exception as e:
            logger.warning(f"[Platt] Error entrenando: {e}")
            return False
    
    def calibrate(self, prob: float) -> float:
        """
        Aplica calibración a una probabilidad.
        
        Si no está entrenado, retorna la probabilidad sin cambios.
        """
        if not self.is_fitted:
            return prob
        
        prob = np.clip(prob, 1e-7, 1 - 1e-7)
        log_odds = np.log(prob / (1 - prob))
        calibrated = 1 / (1 + np.exp(-(self.a * log_odds + self.b)))
        return float(calibrated)


# Cache global de calibradores por modelo
_platt_scalers: dict[str, PlattScaler] = {}
_platt_last_load: dict[str, datetime] = {}
PLATT_CACHE_HOURS = 4  # Recargar calibrador cada 4h


def _load_platt_scaler(model: str) -> PlattScaler:
    """
    Carga o reutiliza calibrador Platt para un modelo.
    
    Entrena con datos de calibration_data donde actual_outcome no es NULL.
    """
    now = datetime.now(timezone.utc)
    
    # Usar cache si existe y no ha expirado
    if model in _platt_scalers and model in _platt_last_load:
        age = (now - _platt_last_load[model]).total_seconds() / 3600
        if age < PLATT_CACHE_HOURS:
            return _platt_scalers[model]
    
    scaler = PlattScaler()
    
    try:
        from core.db import get_db
        db = get_db()
        
        result = db.table('calibration_data')\
            .select('predicted_prob, actual_outcome')\
            .eq('model', model)\
            .not_.is_('actual_outcome', 'null')\
            .execute()
        
        if result.data and len(result.data) >= PLATT_MIN_SAMPLES:
            predictions = [float(r['predicted_prob']) for r in result.data]
            outcomes = [int(float(r['actual_outcome'])) for r in result.data]
            scaler.fit(predictions, outcomes)
    except Exception as e:
        logger.debug(f"[Platt] Error cargando datos para {model}: {e}")
    
    _platt_scalers[model] = scaler
    _platt_last_load[model] = now
    return scaler


def _save_calibration_point(market_id: str, model: str, predicted_prob: float) -> None:
    """
    Guarda predicción para calibración futura.
    
    El actual_outcome se actualizará cuando el mercado resuelva.
    """
    try:
        from core.db import get_db, db_retry
        db = get_db()
        
        db_retry(lambda: db.table('calibration_data').insert({
            'market_id': market_id,
            'model': model,
            'predicted_prob': predicted_prob,
            'actual_outcome': None  # Se llena después
        }).execute())
    except Exception as e:
        logger.debug(f"[Platt] Error guardando calibration point: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# REASONING ANALYSIS (Consistency Check)
# ═══════════════════════════════════════════════════════════════════════════════

# Stopwords para filtrar del reasoning
_STOPWORDS = {
    'that', 'this', 'with', 'from', 'have', 'been', 'would', 'could',
    'should', 'will', 'about', 'which', 'their', 'there', 'what',
    'when', 'where', 'while', 'being', 'these', 'those', 'very',
    'just', 'also', 'into', 'over', 'such', 'than', 'then', 'them',
    'some', 'other', 'only', 'more', 'most', 'make', 'made', 'market',
    'probability', 'likely', 'unlikely', 'analysis', 'based', 'given',
    'current', 'price', 'event', 'outcome', 'suggest', 'indicates',
    'however', 'therefore', 'because', 'although', 'since', 'while',
    'para', 'como', 'esto', 'esta', 'pero', 'porque', 'aunque', 'mercado'
}


def extract_reasoning_keywords(reasoning: str) -> set[str]:
    """
    Extrae keywords significativas del reasoning.
    
    Ignora stopwords y palabras muy cortas.
    Retorna set de las top 10 palabras más frecuentes.
    """
    if not reasoning:
        return set()
    
    # Tokenizar: solo palabras de 4+ letras
    words = re.findall(r'\b[a-zA-Z]{4,}\b', reasoning.lower())
    
    # Filtrar stopwords
    meaningful = [w for w in words if w not in _STOPWORDS]
    
    if not meaningful:
        return set()
    
    # Top 10 keywords más frecuentes
    counter = Counter(meaningful)
    return set(word for word, _ in counter.most_common(10))


def calculate_reasoning_overlap(reasoning1: str, reasoning2: str) -> float:
    """
    Calcula el overlap de keywords entre dos reasonings.
    
    Usa Jaccard similarity: |intersection| / |union|
    Retorna valor entre 0.0 y 1.0
    """
    kw1 = extract_reasoning_keywords(reasoning1)
    kw2 = extract_reasoning_keywords(reasoning2)
    
    if not kw1 or not kw2:
        # Si alguno no tiene keywords, no podemos comparar
        # Retornamos 1.0 para no bloquear el trade por falta de datos
        return 1.0
    
    intersection = kw1 & kw2
    union = kw1 | kw2
    
    overlap = len(intersection) / len(union) if union else 0.0
    
    logger.debug(f"[Consistency] Keywords overlap: {overlap:.2%} | common: {intersection}")
    return overlap


# ═══════════════════════════════════════════════════════════════════════════════
# PARSING Y UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════════
# CACHE
# ═══════════════════════════════════════════════════════════════════════════════

def _get_cached_analysis(market_id: str, model: str, max_age_hours: int = 8) -> Optional[dict]:
    """
    Busca un análisis reciente en Supabase para cualquier modelo.
    """
    from core.db import get_db
    db = get_db()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        result = (
            db.table("llm_analyses")
            .select("probability_yes, probability_range, confidence, resolution_risk, edge_detected, reasoning, sample_variance, reasoning_overlap, timestamp")
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
                "probability_yes":   row.get("probability_yes"),
                "probability_range": row.get("probability_range"),
                "confidence":        row.get("confidence", "low"),
                "resolution_risk":   row.get("resolution_risk", "medium"),
                "edge_detected":     row.get("edge_detected", False),
                "reasoning":         row.get("reasoning", ""),
                "sample_variance":   row.get("sample_variance"),
                "reasoning_overlap": row.get("reasoning_overlap"),
            }
    except Exception as e:
        logger.debug(f"[LLM] Error consultando cache {model}: {e}")
    return None


def _save_analysis(
    market_id: str, 
    model: str, 
    result: dict,
    market_price: float, 
    gap: float,
    sample_variance: float = None,
    sample_count: int = 1,
    reasoning_keywords: list[str] = None,
    reasoning_overlap: float = None
) -> None:
    """Guarda análisis de LLM con campos de calibración."""
    from core.db import get_db
    db = get_db()
    try:
        data = {
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
            "sample_variance":        sample_variance,
            "sample_count":           sample_count,
        }
        
        if reasoning_keywords:
            data["reasoning_keywords"] = reasoning_keywords
        if reasoning_overlap is not None:
            data["reasoning_overlap"] = reasoning_overlap
            
        db.table("llm_analyses").insert(data).execute()
    except Exception as e:
        logger.error(f"[LLM] Error guardando análisis {model}: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# ANÁLISIS POR MODELO
# ═══════════════════════════════════════════════════════════════════════════════

def _single_cerebras_call(
    client: OpenAI, 
    prompt: str, 
    temperature: float = 0.1
) -> Optional[dict]:
    """Una sola llamada a Cerebras. Retorna resultado normalizado o None."""
    model_used = CEREBRAS_MODEL
    try:
        try:
            response = client.chat.completions.create(
                model=model_used,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                temperature=temperature,
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
                    temperature=temperature,
                    max_tokens=400,
                )
            else:
                raise
        
        content = response.choices[0].message.content
        result = _normalize_result(_parse_json(content))
        
        if result:
            tokens_used = response.usage.total_tokens if response.usage else 0
            return {"result": result, "tokens": tokens_used, "model": model_used}
        return None
        
    except Exception as e:
        raise e


def analyze_cerebras_multisample(
    question: str, 
    description: str, 
    market_price: float,
    news_articles: list[dict] = None,
    samples: int = VARIANCE_SAMPLES
) -> tuple[Optional[dict], float, list[float]]:
    """
    Análisis con Cerebras usando multi-sample para medir variance.
    
    Hace `samples` llamadas con temperature=0.7 y calcula la desviación estándar.
    
    Returns:
        (result_dict, variance, all_probs)
        - result_dict: resultado promedio con todos los campos
        - variance: desviación estándar de las probabilidades
        - all_probs: lista de todas las probabilidades obtenidas
    """
    if news_articles is None:
        news_articles = []

    prompt = _build_prompt(question, description, market_price, news_articles)
    
    all_probs = []
    all_results = []
    total_tokens = 0
    last_key_id = None
    
    for sample_idx in range(samples):
        for attempt in range(MAX_RETRIES):
            key_data = key_manager.get_next_key("cerebras")
            if not key_data:
                if attempt == 0 and sample_idx == 0:
                    logger.warning("[LLM] No hay keys disponibles para Cerebras")
                break
            
            last_key_id = key_data["id"]
            
            try:
                client = OpenAI(
                    base_url="https://api.cerebras.ai/v1",
                    api_key=key_data["key_value"],
                )
                
                call_result = _single_cerebras_call(
                    client, 
                    prompt, 
                    temperature=VARIANCE_TEMPERATURE
                )
                
                if call_result:
                    result = call_result["result"]
                    total_tokens += call_result["tokens"]
                    all_probs.append(float(result["probability_yes"]))
                    all_results.append(result)
                    _bump_stat("cerebras_ok")
                    break  # Éxito, siguiente sample
                else:
                    _bump_stat("cerebras_err")
                    break  # JSON inválido, siguiente sample
                    
            except Exception as e:
                error_str = str(e).lower()
                if "429" in str(e) or "rate" in error_str or "limit" in error_str:
                    key_manager.mark_cooldown(key_data["id"], str(e)[:200])
                    logger.warning(f"[LLM] Cerebras 429 sample {sample_idx+1} — rotando key")
                    _bump_stat("cerebras_err")
                    continue
                else:
                    logger.error(f"[LLM] Error en Cerebras sample {sample_idx+1}: {e}")
                    _bump_stat("cerebras_err")
                    break
    
    # Marcar éxito de la última key usada
    if last_key_id and total_tokens > 0:
        key_manager.mark_success(last_key_id, total_tokens)
    
    if len(all_probs) < 2:
        # No hay suficientes samples para calcular variance
        if all_results:
            return all_results[0], 0.0, all_probs
        return None, 0.0, []
    
    # Calcular estadísticas
    mean_prob = sum(all_probs) / len(all_probs)
    variance = math.sqrt(sum((p - mean_prob) ** 2 for p in all_probs) / len(all_probs))
    
    # Crear resultado promedio usando el último reasoning (más completo)
    avg_result = {
        "probability_yes": mean_prob,
        "probability_range": all_results[-1].get("probability_range"),
        "confidence": all_results[-1].get("confidence", "low"),
        "resolution_risk": all_results[-1].get("resolution_risk", "medium"),
        "edge_detected": abs(mean_prob - market_price) > 0.10,
        "reasoning": all_results[-1].get("reasoning", ""),
    }
    
    logger.info(
        f"[LLM] Cerebras multi-sample ({len(all_probs)} muestras) — "
        f"mean={mean_prob:.2f} std={variance:.3f} | {question[:40]}"
    )
    
    return avg_result, variance, all_probs


def analyze_cerebras(question: str, description: str, market_price: float,
                     news_articles: list[dict] = None) -> Optional[dict]:
    """
    Análisis simple con Cerebras (single call, temperature=0.1).
    
    Mantenido para compatibilidad y para casos donde no necesitamos variance.
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


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

def full_analysis(
    market: dict,
    market_price: float,
    news_articles: list[dict] = None,
    force: bool = False,
) -> tuple[Optional[dict], Optional[dict], float, bool]:
    """
    Pipeline de análisis de 2 modelos con calibración Fase 1.
    
    Args:
        market: dict con id, question, description
        market_price: precio actual del token YES
        news_articles: lista de noticias recientes relevantes
        force: True para ignorar cache
    
    Flujo:
      1. Cerebras multi-sample → screener con variance
         - Si variance > 15% → skip (modelo muy incierto)
      
      2. Groq → confirmación final
      
      3. Consistency Check → comparar reasoning keywords
         - Si overlap < 30% → skip (modelos no alineados)
      
      4. Platt Scaling → calibrar probabilidad final
         - Si hay suficientes datos históricos
    
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
    cerebras_variance = 0.0
    reasoning_overlap = None

    # ─── CEREBRAS: screener con multi-sample ────────────────────────────────
    cerebras_model = f"cerebras/{CEREBRAS_MODEL}"
    
    if not force:
        cerebras_result = _get_cached_analysis(market_id, cerebras_model, max_age_hours=8)
        if cerebras_result:
            # Recuperar variance del cache si existe
            cerebras_variance = cerebras_result.get("sample_variance") or 0.0
    
    if cerebras_result is None:
        # Hacer análisis multi-sample para medir variance
        cerebras_result, cerebras_variance, all_probs = analyze_cerebras_multisample(
            question, description, market_price, news_articles
        )
        
        if cerebras_result and cerebras_result.get("probability_yes") is not None:
            cerebras_prob = float(cerebras_result["probability_yes"])
            cerebras_gap = abs(cerebras_prob - market_price)
            cerebras_keywords = list(extract_reasoning_keywords(cerebras_result.get("reasoning", "")))
            
            _save_analysis(
                market_id, cerebras_model, cerebras_result, 
                market_price, cerebras_gap,
                sample_variance=cerebras_variance,
                sample_count=len(all_probs),
                reasoning_keywords=cerebras_keywords
            )
            
            # Guardar para calibración futura
            _save_calibration_point(market_id, cerebras_model, cerebras_prob)
    
    if not cerebras_result or cerebras_result.get("probability_yes") is None:
        logger.warning(f"[LLM] Cerebras falló para {market_id[:16]}… — saltando mercado")
        return None, None, 0.0, False

    cerebras_prob = float(cerebras_result["probability_yes"])
    cerebras_gap  = abs(cerebras_prob - market_price)
    cerebras_conf = cerebras_result.get("confidence", "low")

    # ─── CHECK: Variance alta → skip temprano ────────────────────────────────
    if cerebras_variance > VARIANCE_THRESHOLD:
        logger.info(
            f"[LLM] Variance alta ({cerebras_variance:.3f} > {VARIANCE_THRESHOLD}) "
            f"— modelo muy incierto | {question[:50]}"
        )
        return cerebras_result, None, cerebras_gap, False

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
            groq_keywords = list(extract_reasoning_keywords(groq_result.get("reasoning", "")))
            
            # Calcular overlap con Cerebras
            reasoning_overlap = calculate_reasoning_overlap(
                cerebras_result.get("reasoning", ""),
                groq_result.get("reasoning", "")
            )
            
            _save_analysis(
                market_id, groq_model, groq_result, 
                market_price, groq_gap,
                reasoning_keywords=groq_keywords,
                reasoning_overlap=reasoning_overlap
            )
            
            # Guardar para calibración futura
            _save_calibration_point(market_id, groq_model, groq_prob)

    # ─── DECISIÓN FINAL ────────────────────────────────────────────────────────
    if not groq_result or groq_result.get("probability_yes") is None:
        logger.info(f"[LLM] Groq no disponible — no operar | {question[:50]}")
        return cerebras_result, None, cerebras_gap, False

    groq_prob = float(groq_result["probability_yes"])
    groq_conf = groq_result.get("confidence", "low")

    # Check divergencia numérica
    divergence_cg = abs(cerebras_prob - groq_prob)
    if divergence_cg > 0.20:
        logger.warning(
            f"[LLM] Divergencia Cerebras-Groq {divergence_cg:.1%} — no operar | {question[:50]}"
        )
        return cerebras_result, groq_result, 0.0, False

    # ─── CHECK: Reasoning overlap bajo → skip ────────────────────────────────
    if reasoning_overlap is None:
        reasoning_overlap = calculate_reasoning_overlap(
            cerebras_result.get("reasoning", ""),
            groq_result.get("reasoning", "")
        )
    
    if reasoning_overlap < REASONING_OVERLAP_THRESHOLD:
        logger.info(
            f"[LLM] Reasoning overlap bajo ({reasoning_overlap:.1%} < {REASONING_OVERLAP_THRESHOLD:.0%}) "
            f"— modelos no alineados | {question[:50]}"
        )
        return cerebras_result, groq_result, 0.0, False

    # ─── PLATT SCALING: calibrar probabilidad ────────────────────────────────
    probs = [cerebras_prob, groq_prob]
    confs = [cerebras_conf, groq_conf]
    
    # Promedio de las probabilidades
    avg_prob = sum(probs) / len(probs)
    
    # Intentar calibrar con Platt Scaling
    scaler = _load_platt_scaler(cerebras_model)
    if scaler.is_fitted:
        calibrated_prob = scaler.calibrate(avg_prob)
        logger.info(
            f"[LLM] Platt calibration: {avg_prob:.2f} → {calibrated_prob:.2f} "
            f"(n={scaler.n_samples})"
        )
        avg_prob = calibrated_prob
    
    gap_final = abs(avg_prob - market_price)
    
    # Verificar que ningún modelo tenga confianza baja
    has_low_conf = any(c == "low" for c in confs)
    
    should_trade = (
        len(probs) >= 2
        and gap_final >= 0.15
        and not has_low_conf
        and cerebras_variance <= VARIANCE_THRESHOLD
        and reasoning_overlap >= REASONING_OVERLAP_THRESHOLD
    )

    logger.info(
        f"[LLM] Análisis completo — gap={gap_final:.1%} var={cerebras_variance:.3f} "
        f"overlap={reasoning_overlap:.1%} should_trade={should_trade} | {question[:50]}"
    )

    return cerebras_result, groq_result, gap_final, should_trade
