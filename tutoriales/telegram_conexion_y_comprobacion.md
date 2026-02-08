# Tutorial Rapido: Conexion Telegram + API + Verificacion

Esta guia te permite comprobar en pocos minutos que Telegram controla debates del orquestador.

## 1. Requisitos

- Python y dependencias instaladas:

```bash
pip install -r requirements.txt
```

- Bot de Telegram creado en `@BotFather` con token valido.
- OpenCode server levantado en `http://localhost:4096`.

## 2. Arrancar servicios

Abre 3 terminales en la raiz del proyecto.

### Terminal 1: OpenCode server

```bash
opencode serve --port 4096 --hostname 127.0.0.1
```

### Terminal 2: API de orquestacion

```bash
uvicorn api_server:app --host 0.0.0.0 --port 8000
```

### Terminal 3: Adapter de Telegram (PowerShell)

```powershell
$env:TELEGRAM_BOT_TOKEN = "TU_TOKEN_AQUI"
$env:ORCHESTRATOR_API_BASE_URL = "http://127.0.0.1:8000"
$env:TELEGRAM_DEFAULT_PROFILE = "equipo_programacion"
python telegram_adapter.py
```

Si el adapter arranca bien, veras logs tipo:
- `[telegram-adapter] iniciado`
- `[telegram-adapter] API base: http://127.0.0.1:8000`

## 3. Prueba desde Telegram

Abre chat privado con tu bot y manda estos comandos en orden:

1. `/help`
2. `/profiles`
3. `/startdebate Disenar arquitectura para una app de tareas B2B`
4. `/status`
5. `/feedback Prioriza seguridad minima viable y entrega en 2 semanas`
6. `/stop`

Resultado esperado:
- El bot responde a cada comando.
- Tras `/startdebate`, te devuelve un `debate_id` y queda enlazado al chat.
- `/status` muestra estado, rondas y coste.
- `/feedback` encola intervencion real para agentes.
- `/stop` solicita parada del debate.

## 4. Verificaciones rapidas por API

Puedes validar estado desde terminal:

```bash
curl http://127.0.0.1:8000/debates
```

Si tienes `debate_id`:

```bash
curl http://127.0.0.1:8000/debates/DEBATE_ID
curl "http://127.0.0.1:8000/debates/DEBATE_ID/events?limit=20"
```

## 5. Problemas comunes

### El bot no responde

- Revisa token en `TELEGRAM_BOT_TOKEN`.
- Verifica que `telegram_adapter.py` sigue corriendo.
- Si el bot tenia webhook previo, elimina webhook:

```bash
https://api.telegram.org/bot<TU_TOKEN>/deleteWebhook
```

### Error de conexion API

- Confirma que `uvicorn` esta activo en puerto `8000`.
- Confirma `ORCHESTRATOR_API_BASE_URL` correcto.

### El debate no avanza

- Confirma OpenCode server activo en `127.0.0.1:4096`.
- Revisa logs de `api_server.py` y `team_orchestrator_v2.py`.

## 6. Uso en grupo de Telegram (opcional)

- Anade el bot al grupo.
- Si quieres que procese texto normal (no solo comandos), en BotFather usa `/setprivacy` y desactiva privacy mode.
- Para seguridad, limita usuarios con:

```powershell
$env:TELEGRAM_ALLOWED_USER_IDS = "123456789,987654321"
```

## 7. Checklist de OK

- OpenCode server arriba.
- API arriba.
- Adapter arriba.
- `/startdebate` crea debate.
- `/feedback` y `/stop` tienen efecto.
- Eventos visibles en Telegram y en `/debates/{id}/events`.
