# PolyHunt — Pipeline LLM Dual (core/llm_analyzer.py)

## Modelos utilizados

| Modelo             | Librería              | Uso                                     |
|--------------------|-----------------------|-----------------------------------------|
| `llama-3.3-70b-versatile` | `groq`         | Screening rápido — SIEMPRE se ejecuta   |
| `gemini-1.5-flash` | `google-generativeai` | Análisis profundo — solo si gap > 10%   |

Ambos clientes son lazy-initialized (singleton global `_groq_client`, `_gemini_model`).

---

## Pipeline `full_analysis(market, market_price, news_articles)` → `(groq_result, gemini_result, gap, should_trade)`

```
1. Groq analiza siempre
   └─ Si falla o no devuelve probability_yes → return ({}, None, 0.0, False)
   └─ Guardar en llm_analyses

2. gap = abs(groq_prob - market_price)
   └─ Si gap < 0.10 → return (groq_result, None, gap, False)  [sin Gemini]

3. Gemini analiza (solo si gap >= 0.10)
   └─ Si Gemini OK:
       model_diverge = abs(groq_prob - gemini_prob)
       └─ Si model_diverge > 0.20 → should_trade = False  [modelos discrepan]
       └─ Si OK: avg_prob = (groq_prob + gemini_prob) / 2
                 gap = abs(avg_prob - market_price)
                 groq_result["probability_yes"] = avg_prob  [mutado in-place]
   └─ Guardar en llm_analyses

4. should_trade = gap >= 0.15 AND confidence in ("high","medium") AND resolution_risk != "high"
```

---

## Formato JSON que devuelve cada modelo

El prompt exige este JSON exacto (sin texto adicional):

```json
{
  "probability_yes": 0.72,
  "probability_range": "0.65-0.80",
  "confidence": "high",
  "resolution_risk": "low",
  "edge_detected": true,
  "reasoning": "Texto conciso de 2-3 oraciones. OBLIGATORIO."
}
```

**Valores posibles:**
- `probability_yes`: float 0.01–0.99
- `confidence`: `"high"` | `"medium"` | `"low"`
- `resolution_risk`: `"high"` | `"medium"` | `"low"`
- `edge_detected`: `true` si prob difiere >10% del precio de mercado

---

## Parser JSON con fallbacks (`_parse_json`)

Tres intentos en orden:
1. `json.loads(content.strip())` — parseo directo
2. `re.search(r'\{[^{}]*\}', content, re.DOTALL)` — buscar bloque JSON
3. Eliminar markdown code blocks (` ```json `) y volver a parsear

Si los 3 fallan → retorna `{}`

---

## Parámetros de llamada

**Groq:**
```python
client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user",   "content": prompt},
    ],
    temperature=0.1,
    max_tokens=600,
)
```

**Gemini:** recibe sistema+usuario en un solo string (no soporta roles separados en GenerativeModel).
```python
model.generate_content(
    full_prompt,
    generation_config=genai.GenerationConfig(temperature=0.1, max_output_tokens=600),
)
```

---

## Prompt de análisis (`_build_prompt`)

```
MERCADO DE PREDICCIÓN
Pregunta: {question}
Descripción: {description[:300]}
Precio actual del mercado (YES token): {market_price:.2%}

NOTICIAS RECIENTES RELEVANTES:
  • [fuente] título: resumen[:200]
  ...  (máximo 5 noticias)

Analiza este mercado y proporciona tu estimación de probabilidad.
```

---

## Thresholds de decisión

| Condición                          | Valor    | Efecto                        |
|------------------------------------|----------|-------------------------------|
| gap mínimo para llamar a Gemini    | 10%      | Si gap < 10% → no Gemini, no trade |
| gap mínimo para abrir trade        | 15%      | should_trade requiere gap ≥ 0.15 |
| divergencia máxima entre modelos   | 20%      | Si > 20% → should_trade = False |
| confidence requerida               | high/medium | low → no trade             |
| resolution_risk bloqueante         | high     | high → no trade              |

---

## Dirección del trade (en main.py)
```python
direction = "YES" if float(prob_yes) > market_price else "NO"
```
Si el LLM cree que la probabilidad real es mayor que el precio → comprar YES.  
Si cree que es menor → comprar NO.

---

## Notas
- `save_llm_analysis()` se llama SIEMPRE aunque no se abra trade — es el dato más valioso
- Gemini usa promedio ponderado igualitario (50/50) con Groq cuando ambos coinciden
- `groq_result["probability_yes"]` se muta al promedio si Gemini confirma — el caller recibe el valor combinado
