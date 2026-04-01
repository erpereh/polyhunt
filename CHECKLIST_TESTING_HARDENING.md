# Checklist de Testing (post-hardening)

## 1) Arranque base
- Ejecutar `python main.py`.
- Esperado: Flask + hilos arrancan sin crash.
- Si no hay keys: warning de "No hay API keys configuradas".

## 2) API status y bloqueo de start sin keys
- `GET /api/status` debe devolver `status: paused`.
- `POST /api/bot/start` sin keys debe devolver 400.

## 3) CRUD de keys en Ajustes
- Agregar 1 key valida por servicio (`cerebras`, `gemini`, `groq`).
- Verificar que UI/API muestran solo mascara y `last_4`.
- Intentar duplicar key+servicio: debe devolver 409.
- Probar key invalida/corta: debe devolver 400.

## 4) Start/Stop
- Con keys cargadas, `POST /api/bot/start` activa el bot.
- `POST /api/bot/stop` pasa a `stopping` y luego `paused`.

## 5) Scan loop (cada 5 min)
- Esperar un ciclo y revisar `polyhunt.log` o `GET /api/logs`.
- Confirmar metricas de scan, cola LLM, skips cuantitativos y cooldowns.

## 6) Pre-filtro cuantitativo
- Verificar en logs que `QuantSkip` incrementa en mercados < 40 puntos.
- Confirmar que no se encolan mercados bajo umbral.

## 7) LLM queue + refresh de precio
- Forzar evento (new market/noticia/cache expired) y validar enqueue.
- Confirmar procesamiento LLM y update de `last_llm_analysis_at`.
- Verificar descarte de items vencidos en cola (>30 min).

## 8) Regla de consenso (minimo 2 modelos)
- Validar caso con solo Cerebras respondiendo.
- Esperado: `should_trade = False` y no abre posicion.

## 9) Apertura de trade y sizing de riesgo
- Cuando haya señal valida, verificar apertura de trade.
- Confirmar tamaño de posicion <= 5% del balance.

## 10) Exit rules
- Validar cierres por:
  - stop-loss (<= -30%)
  - take-profit (> +40%)
  - time-stop (>30 dias y <10% de ganancia)
- Confirmar impacto en `account.balance` y `account.total_pnl`.

## 11) Cooldown de keys
- Forzar 429 (si posible) y verificar:
  - key entra en cooldown
  - rotacion a otra key
  - liberacion tras ventana de cooldown

## 12) Reset diario (Pacific)
- Verificar que corre sin errores `should_reset_daily/reset_daily_counts`.
- Confirmar que `calls_today/tokens_today` vuelven a cero cuando corresponda.

## 13) Dashboard integridad
- Revisar tabs + Ajustes en desktop y mobile.
- Confirmar que no se expone key completa en UI ni endpoints.

## 14) Smoke final
- Dejar correr el bot 20-30 min.
- Confirmar estado estable y sin excepciones recurrentes en logs.

## Nota de modelos free-tier (actualizado)
- Gemini configurado a `gemini-2.0-flash` para evitar bloqueo de cuota de `gemini-2.5-pro` en free tier.
- Groq validado con `llama-3.3-70b-versatile`.
- Si aparece un error de Groq mencionando `qwen-3-235b`, reiniciar proceso (`python main.py`) para asegurar que no corre código antiguo.
