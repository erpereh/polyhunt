# Skill: Paper Trader — PolyHunt

## Propósito
PolyHunt es un bot de PAPER TRADING para mercados de predicción (Polymarket).
NO ejecuta órdenes reales. Simula trades para calibrar modelos LLM y estrategias de pricing.

## Arquitectura general
```
main.py (loop principal)
├── core/
│   ├── db.py          — cliente Supabase singleton
│   ├── paper_trader.py — lógica de trades simulados
│   └── config.py      — variables de entorno
├── agents/
│   ├── polymarket.py  — scraping de mercados via API
│   ├── groq_agent.py  — análisis con Groq (LLaMA)
│   └── gemini_agent.py — análisis con Gemini
├── dashboard/
│   ├── index.html     — UI del dashboard
│   ├── app.js         — lógica frontend
│   └── style.css      — estilos
└── dashboard_server.py — Flask server
```

## Flujo del bot
1. Obtener mercados activos de Polymarket API (volumen > $10k, ends > 7 días)
2. Para cada mercado sin posición abierta:
   a. Obtener precio actual YES token
   b. Analizar con Groq (probabilidad estimada)
   c. Analizar con Gemini (segunda opinión)
   d. Si hay gap >= 15% entre LLM y mercado → abrir paper trade
3. Para posiciones abiertas:
   a. Actualizar precio actual
   b. Si precio se mueve >30% en contra → cerrar posición (stop loss)
   c. Si mercado se acerca a resolución → cerrar posición

## Polymarket API
```python
import httpx

POLYMARKET_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

async def get_active_markets(min_volume=10000, min_days_remaining=7):
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{GAMMA_API}/markets", params={
            "active": True,
            "closed": False,
            "limit": 100,
            "order": "volume",
            "ascending": False
        })
        markets = resp.json()
        # Filtrar por volumen y fecha
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) + timedelta(days=min_days_remaining)
        return [
            m for m in markets
            if float(m.get("volume", 0)) >= min_volume
            and m.get("end_date_iso")
            and datetime.fromisoformat(m["end_date_iso"].replace("Z", "+00:00")) > cutoff
        ]

async def get_token_price(token_id: str) -> float:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{CLOB_API}/price", params={"token_id": token_id, "side": "buy"})
        return float(resp.json().get("price", 0.5))
```

## Groq Agent (probabilidad LLM)
```python
from groq import Groq
import json

client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = """Eres un analista de mercados de predicción. 
Analiza la pregunta y responde SOLO con JSON válido:
{
  "probability_yes": 0.XX,
  "probability_range": "0.XX-0.XX",
  "confidence": "high|medium|low",
  "resolution_risk": "high|medium|low",
  "edge_detected": true|false,
  "reasoning": "explicación breve"
}"""

def analyze_market(question: str, description: str = "") -> dict:
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Pregunta: {question}\n\nDescripción: {description}"}
            ],
            temperature=0.1,
            max_tokens=500
        )
        content = response.choices[0].message.content
        # Extraer JSON del response
        import re
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception as e:
        logger.error(f"Error en Groq: {e}")
    return {"probability_yes": 0.5, "confidence": "low", "edge_detected": False}
```

## Lógica de decisión de trading
```python
def should_open_trade(market_price: float, llm_probability: float, 
                      confidence: str, resolution_risk: str) -> tuple[bool, str, float]:
    """
    Retorna (abrir, dirección, tamaño_usd)
    """
    gap = abs(llm_probability - market_price)
    
    # No operar si hay riesgo de resolución alto
    if resolution_risk == "high":
        return False, "", 0
    
    # No operar si confianza baja
    if confidence == "low":
        return False, "", 0
    
    # Gap mínimo 15% para operar
    if gap < 0.15:
        return False, "", 0
    
    # Tamaño base: $50 por trade
    base_size = 50.0
    
    # Multiplicador por confianza
    size_multiplier = {"high": 2.0, "medium": 1.0}.get(confidence, 0.5)
    size_usd = base_size * size_multiplier
    
    if llm_probability > market_price:
        return True, "YES", size_usd
    else:
        return True, "NO", size_usd
```

## config.py
```python
from dotenv import load_dotenv
import os

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Validar que las claves estén presentes
for key in ["SUPABASE_URL", "SUPABASE_KEY", "GROQ_API_KEY"]:
    if not os.getenv(key):
        raise ValueError(f"Falta variable de entorno: {key}")
```

## .env (NUNCA subir a git)
```
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=eyJ...
GROQ_API_KEY=gsk_...
GEMINI_API_KEY=AIza...
```

## requirements.txt
```
supabase>=2.0.0
python-dotenv>=1.0.0
groq>=0.4.0
google-generativeai>=0.4.0
httpx>=0.27.0
flask>=3.0.0
feedparser>=6.0.0
```

## Reglas críticas
- El .env NUNCA se sube a git — .gitignore desde el primer momento
- TODAS las llamadas a APIs van en try/except
- Nunca más del 5% del balance en una posición (Kelly conservador)
- Guardar SIEMPRE el reasoning del LLM — es el dato más valioso
- Cerrar posiciones si precio se mueve >30% en contra (stop loss)
- Este es PAPER TRADING — cero conexiones a wallets ni órdenes reales
- Usar asyncio para las llamadas a la API de Polymarket
- Loop principal: cada 15-30 minutos para no saturar las APIs
