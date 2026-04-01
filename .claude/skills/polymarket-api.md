# PolyHunt — Polymarket API (core/market_scanner.py)

## Endpoints utilizados

| API       | Base URL                              | Uso                              |
|-----------|---------------------------------------|----------------------------------|
| Gamma API | `https://gamma-api.polymarket.com`    | Listado y metadatos de mercados  |
| CLOB API  | `https://clob.polymarket.com`         | Precios mid y historial          |

## Funciones públicas

### `get_political_markets(min_volume, max_volume, min_days_remaining) → list[dict]`
Escanea Polymarket y retorna mercados políticos activos.

**Parámetros:**
- `min_volume=50_000` — volumen mínimo en USD
- `max_volume=250_000` — volumen máximo en USD (sweet spot para ineficiencias)
- `min_days_remaining=7` — días mínimos hasta cierre

**Endpoint:** `GET /markets` con params `active=true`, `closed=false`, `order=volume24hr`, `limit=100`  
**Paginación:** offset 0→500 en pasos de 100, `time.sleep(1)` entre páginas (rate limit 1 req/s)  
**Para cuando `len(batch) < limit`** → no hay más páginas, break.

**Filtros aplicados en orden:**
1. Volumen dentro de `[min_volume, max_volume]`
2. `endDate` > ahora + `min_days_remaining` días
3. `_is_political()` — keyword match en question+description

**Extracción de token IDs:**
```python
for token in (m.get("tokens") or []):
    outcome = (token.get("outcome") or "").upper()
    if outcome == "YES":
        yes_token_id = token.get("token_id")
    elif outcome == "NO":
        no_token_id = token.get("token_id")
```

**Extracción de precio YES:**
```python
raw_prices = m.get("outcomePrices")  # puede ser string JSON o lista
prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
last_price = float(prices[0])  # prices[0] = YES, prices[1] = NO
```

**Market ID:** usa `conditionId` o `id` como fallback.

**Dict normalizado retornado:**
```python
{
    "id":           str,   # conditionId
    "question":     str,
    "description":  str,   # truncado a 500 chars
    "volume":       float,
    "end_date":     str,   # ISO 8601
    "yes_token_id": str | None,
    "no_token_id":  str | None,
    "last_price":   float | None,  # precio YES token
}
```

---

### `get_market_price(token_id) → Optional[float]`
Precio mid del token YES desde CLOB API.

**Endpoint:** `GET /midpoint?token_id=<token_id>`  
**Retorna:** `float` entre 0 y 1 (probabilidad implícita), o `None` si falla.  
**Timeout:** 10s

---

### `get_price_history(token_id, interval="1d", fidelity=60) → list[dict]`
Historial de precios para backtesting.

**Endpoint:** `GET /prices-history?token_id=...&interval=...&fidelity=...`  
**Retorna:** lista de `{"t": timestamp_ms, "p": price}`

---

### `save_markets_to_db(markets) → int`
Llama `upsert_market()` + `save_price_snapshot()` por cada mercado.  
Retorna número de mercados guardados. Importa desde `core.paper_trader` (evita circular).

---

## Keywords políticos detectados
`president`, `election`, `senate`, `congress`, `governor`, `vote`, `ballot`,
`democrat`, `republican`, `trump`, `biden`, `harris`, `political`, `policy`,
`legislation`, `parliament`, `minister`, `cabinet`, `impeach`, `resign`,
`government`, `nato`, `ukraine`, `russia`, `china`, `war`, `treaty`,
`tariff`, `sanction`, `supreme court`, `administration`, `primary`,
`midterm`, `campaign`, `polling`, `majority`, `speaker`

---

## Notas de implementación
- `httpx.Client(timeout=30.0)` para el escáner, `timeout=10.0` para precios individuales
- Máximo 500 mercados por ciclo (5 páginas × 100)
- Todos los errores HTTP y de red en try/except con log — nunca lanzan excepción al caller
