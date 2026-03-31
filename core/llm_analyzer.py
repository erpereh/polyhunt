"""
Análisis de mercados de predicción con Groq (LLaMA) y Gemini.

Pipeline dual-modelo:
  1. Groq siempre — análisis rápido
  2. Gemini solo si gap > 10% — análisis profundo
  3. Si ambos discrepan > 20% entre sí → should_trade = False

Guarda SIEMPRE el reasoning en Supabase — es el dato más valioso.
"""
import json
import re
import logging
from datetime import datetime
from typing import Optional

from groq import Groq
import google.generativeai as genai

from config import GROQ_API_KEY, GEMINI_API_KEY

logger = logging.getLogger(__name__)

# Clientes lazy-initialized
_groq_client: Optional[Groq] = None
_gemini_model = None


def _get_groq() -> Groq:
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client


def _get_gemini():
    global _gemini_model
    if _gemini_model is None:
        genai.configure(api_key=GEMINI_API_KEY)
        _gemini_model = genai.GenerativeModel("gemini-1.5-flash")
    return _gemini_model


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


def analyze_groq(question: str, description: str, market_price: float,
                 news_articles: list[dict] = None) -> dict:
    """
    Análisis rápido con Groq (LLaMA-3.3-70B).
    Retorna dict con los campos de análisis, o {} si falla.
    """
    if news_articles is None:
        news_articles = []

    prompt = _build_prompt(question, description, market_price, news_articles)

    try:
        client   = _get_groq()
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.1,
            max_tokens=600,
        )
        content = response.choices[0].message.content
        result  = _parse_json(content)

        if result and result.get("probability_yes") is not None:
            logger.info(
                f"[{datetime.now()}] Groq OK — prob={result['probability_yes']:.2f} "
                f"conf={result.get('confidence','?')} | {question[:50]}"
            )
        else:
            logger.warning(f"[{datetime.now()}] Groq devolvió JSON inválido para: {question[:50]}")

        return result

    except Exception as e:
        logger.error(f"[{datetime.now()}] Error en Groq: {e}")
        return {}


def analyze_gemini(question: str, description: str, market_price: float,
                   news_articles: list[dict] = None) -> dict:
    """
    Análisis profundo con Gemini 1.5 Flash.
    Retorna dict con los campos de análisis, o {} si falla.
    """
    if news_articles is None:
        news_articles = []

    # Gemini recibe sistema + usuario en un solo prompt
    full_prompt = _SYSTEM_PROMPT + "\n\n" + _build_prompt(
        question, description, market_price, news_articles
    )

    try:
        model    = _get_gemini()
        response = model.generate_content(
            full_prompt,
            generation_config=genai.GenerationConfig(
                temperature=0.1,
                max_output_tokens=600,
            ),
        )
        content = response.text
        result  = _parse_json(content)

        if result and result.get("probability_yes") is not None:
            logger.info(
                f"[{datetime.now()}] Gemini OK — prob={result['probability_yes']:.2f} "
                f"conf={result.get('confidence','?')} | {question[:50]}"
            )
        else:
            logger.warning(f"[{datetime.now()}] Gemini devolvió JSON inválido para: {question[:50]}")

        return result

    except Exception as e:
        logger.error(f"[{datetime.now()}] Error en Gemini: {e}")
        return {}


def full_analysis(
    market: dict,
    market_price: float,
    news_articles: list[dict] = None,
) -> tuple[dict, Optional[dict], float, bool]:
    """
    Pipeline de análisis dual-modelo completo.

    Lógica:
      - Groq primero, siempre
      - Gemini solo si gap LLM-mercado > 10%
      - Si ambos modelos discrepan > 20% entre sí → should_trade = False
      - should_trade = True requiere: gap >= 15%, confianza medium/high, resolution_risk != high

    Retorna:
      (groq_result, gemini_result_o_None, gap, should_trade)
    """
    if news_articles is None:
        news_articles = []

    # Importar aquí para evitar circular
    from core.paper_trader import save_llm_analysis

    question    = market.get("question", "")
    description = market.get("description", "") or ""
    market_id   = market.get("id", "")

    # ─── GROQ: screening rápido ───────────────────────────────────────────────
    groq_result = analyze_groq(question, description, market_price, news_articles)

    if not groq_result or groq_result.get("probability_yes") is None:
        logger.warning(f"[{datetime.now()}] Groq falló para {market_id[:16]}… — saltando mercado")
        return {}, None, 0.0, False

    groq_prob = float(groq_result["probability_yes"])
    gap       = abs(groq_prob - market_price)

    # Guardar análisis de Groq siempre
    save_llm_analysis(market_id, "groq/llama-3.3-70b", groq_result, market_price, gap)

    # Si el gap es < 10%, no merece análisis profundo ni trade
    if gap < 0.10:
        logger.info(
            f"[{datetime.now()}] Gap pequeño ({gap:.1%}) — sin Gemini | {question[:50]}"
        )
        return groq_result, None, gap, False

    # ─── GEMINI: análisis profundo ────────────────────────────────────────────
    gemini_result = analyze_gemini(question, description, market_price, news_articles)

    if gemini_result and gemini_result.get("probability_yes") is not None:
        gemini_prob    = float(gemini_result["probability_yes"])
        gemini_gap     = abs(gemini_prob - market_price)
        model_diverge  = abs(groq_prob - gemini_prob)

        # Guardar análisis de Gemini
        save_llm_analysis(
            market_id, "gemini/gemini-1.5-flash",
            gemini_result, market_price, gemini_gap,
        )

        # Si los modelos discrepan demasiado → no operar
        if model_diverge > 0.20:
            logger.warning(
                f"[{datetime.now()}] Modelos divergen {model_diverge:.1%} — no se abre trade | {question[:50]}"
            )
            return groq_result, gemini_result, gap, False

        # Usar promedio ponderado (Gemini tiene más contexto)
        avg_prob = (groq_prob + gemini_prob) / 2
        gap      = abs(avg_prob - market_price)
        groq_result["probability_yes"] = round(avg_prob, 4)

    # ─── Decisión de trading ──────────────────────────────────────────────────
    confidence      = groq_result.get("confidence", "low")
    resolution_risk = groq_result.get("resolution_risk", "high")

    should_trade = (
        gap >= 0.15
        and confidence in ("high", "medium")
        and resolution_risk != "high"
    )

    logger.info(
        f"[{datetime.now()}] Análisis completo — gap={gap:.1%} conf={confidence} "
        f"risk={resolution_risk} should_trade={should_trade} | {question[:50]}"
    )

    return groq_result, gemini_result, gap, should_trade
