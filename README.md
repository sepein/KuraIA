# OpenCode Team Orchestrator

Simulador de equipo de desarrollo (8 roles) sobre OpenCode Server, orquestado en Python.

## Estado actual

El proyecto incluye:
- Debate secuencial por roles.
- Sub-respuestas en paralelo con `ThreadPoolExecutor`.
- Intervencion humana del jefe durante el flujo.
- Resumen automatico de contexto largo.
- Persistencia de sesiones por rol en `team_sessions.json`.
- Estimacion de tokens/coste con limite de presupuesto.
- Revalidacion de sesiones guardadas para evitar IDs obsoletos.

## Roles por defecto

- Arquitecto
- Critico_Dev
- Backend_Dev
- Frontend_Dev
- DevOps_Dev
- Tester_Dev
- Security_Dev
- Moderador

## Requisitos

- Python 3.10+
- OpenCode Server activo en local (por defecto `http://localhost:4096`)
- Dependencia Python:

```bash
pip install -r requirements.txt
```

## Ejecucion

```bash
python team_orchestrator_v2.py
```

Flujo interactivo:
1. Introduces una tarea inicial.
2. Cada rol responde en secuencia.
3. Tras cada respuesta (salvo `Moderador`) puedes:
- `Enter`: continuar
- `f`: inyectar feedback como jefe
- `p`: detener el debate
4. Se imprime resumen final de coste.

## API (FastAPI)

Servidor API generico para lanzar debates con definicion externa de roles/modelos:

```bash
uvicorn api_server:app --host 0.0.0.0 --port 8000
```

Documentacion interactiva:
- Swagger UI: `http://127.0.0.1:8000/swagger`
- ReDoc: `http://127.0.0.1:8000/redoc`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

Endpoints principales:
- `POST /debates`
- `GET /debates/{debate_id}`
- `GET /debates/{debate_id}/events`
- `GET /debates/{debate_id}/output-events`
- `POST /debates/{debate_id}/interventions`
- `GET /debates`
- `GET /debates/{debate_id}/memory`
- `GET /debates/{debate_id}/export`
- `GET /memory/export`
- `POST /memory/import`

Nota API general:
- `roles` es obligatorio en `POST /debates`.
- El numero de participantes es dinamico (no fijo).

Ejemplo de creacion:

```json
{
  "task": "Debatir propuesta de arquitectura para SaaS B2B",
  "discussion_profile": "equipo_programacion",
  "global_instructions": "Debatir con foco en coste/tiempo de implementacion y sin over-engineering.",
  "global_rules": [
    "No inventar datos",
    "Declarar supuestos"
  ],
  "minutes_mode": "agent",
  "roles": [
    {
      "name": "Arquitecto",
      "model": "groq/llama-3.1-70b-versatile",
      "prompt": "Eres Arquitecto Senior. Evalua simplicidad, coste y escalabilidad."
    },
    {
      "name": "Critico_Dev",
      "model": "groq/llama-3.1-70b-versatile",
      "prompt": "Eres critico tecnico. Detecta riesgos y mitigaciones."
    }
  ],
  "sequence": ["Arquitecto", "Critico_Dev"],
  "parallel_groups": []
}
```


## Memoria persistente (actas + logs)

La API ahora guarda memoria propia en SQLite para no depender solo del JSONL en disco:
- Metadatos del debate (estado, tarea, perfil, roles, secuencia, coste, error).
- Eventos del debate para consulta historica.
- Acta final resumida (`final_minutes`) al cerrar la ejecucion.
- Fuente de acta (`final_minutes_source`): `agent`, `programmatic` o `programmatic_fallback`.
- Eventos de salida accionables (`output_events`) extraidos por reglas.

Modo de generacion de acta (en `POST /debates`):
- `minutes_mode: "auto"` (default): intenta por agente y, si falla, usa programatico.
- `minutes_mode: "agent"`: fuerza generacion por agente (con fallback registrado).
- `minutes_mode: "programmatic"`: resumen determinista sin llamada extra a LLM.

Exportacion e importacion:
- `GET /debates/{debate_id}/export?include_events=true`: exporta snapshot JSON de una mesa.
- `GET /memory/export?limit=50&include_events=false&include_output_events=false`: exporta varias memorias.
- `POST /memory/import`: importa snapshot previamente exportado.

### Output events por reglas (`#tarea`)

Si una intervencion incluye una linea con `#tarea`, la API genera `output_events` estructurados.
Por defecto se procesan eventos de `Moderador` y `Secretario_Actas`.

Formato recomendado:

```text
#tarea crear title="Preparar backlog sprint 1" owner=Backend_Dev priority=alta
#tarea modificar id=TASK-42 state=in_progress
#tarea borrar id=TASK-15
```

Tambien admite JSON:

```text
#tarea crear {"title":"Preparar backlog sprint 1","owner":"Backend_Dev","priority":"alta"}
```

Consulta:
- `GET /debates/{debate_id}/output-events`

Ejemplo import:

```json
{
  "snapshot": {
    "schema_version": "1.0",
    "debate": { "debate_id": "debate-123", "status": "completed" },
    "events": []
  },
  "overwrite": false
}
```



## CLI (Typer)

Tambien puedes usar la CLI:

```bash
python orchestrator_cli.py --help
```

Comandos principales:

```bash
python orchestrator_cli.py start "Disena arquitectura SaaS X"
python orchestrator_cli.py start "Implementa API Y" --no-interactive
python orchestrator_cli.py status
python orchestrator_cli.py history --limit 20
python orchestrator_cli.py intervene "Anade JWT + refresh tokens"
python orchestrator_cli.py intervene --stop --debate-id debate-123456789-1
python orchestrator_cli.py export last --format md
```

## Dashboard (Streamlit)

Dashboard visual para observar debates, detalle por ronda y encolar intervenciones:

```bash
streamlit run dashboard.py
```

Incluye:
- Auto refresh configurable desde sidebar.
- Graficos de coste, duracion y rondas por debate (ultimos 30 debates).
- Filtro por rango de fechas para historial y metricas.
- Coste estimado por ronda en la vista de detalle del debate.

## Telegram Adapter

Refleja cada debate en una sala de Telegram y permite intervenir desde movil:

```bash
python telegram_adapter.py
```

Variables clave:
- `TELEGRAM_BOT_TOKEN` (obligatoria)
- `ORCHESTRATOR_API_BASE_URL` (default: `http://127.0.0.1:8000`)
- `TELEGRAM_DEFAULT_PROFILE` (default: `equipo_programacion`)
- `TELEGRAM_DEFAULT_SEQUENCE` (csv opcional)
- `TELEGRAM_DEFAULT_ROLES` (csv opcional)
- `TELEGRAM_ALLOWED_USER_IDS` (csv opcional)

Comandos en sala:
- `/startdebate <tarea>`
- `/bind <debate_id>`
- `/status [debate_id]`
- `/feedback <mensaje>`
- `/stop`
- `/profiles`
- `/debates`

## Variables de entorno

Puedes ajustar comportamiento sin tocar codigo:

- `OPENCODE_BASE_URL` (default: `http://localhost:4096`)
- `OPENCODE_SESSIONS_FILE` (default: `team_sessions.json`)
- `MAX_WAIT_SECONDS` (default: `60`)
- `POLL_INTERVAL_SECONDS` (default: `1.5`)
- `MAX_ROUNDS_PER_DEBATE` (default: `15`)
- `MAX_BUDGET_EUR` (default: `0.50`)
- `MAX_CONTEXT_CHARS` (default: `12000`)
- `REQUEST_TIMEOUT_SECONDS` (default: `20`)
- `EUR_PER_USD` (default: `0.92`)
- `DEBATE_LOG_FILE` (default: `debate_events.jsonl`)
- `ENABLE_EVENT_LOGGING` (default: `true`)
- `MAX_LOG_TEXT_CHARS` (default: `4000`)
- `INTERVENTIONS_FILE` (default: `interventions_queue.jsonl`)
- `ROLE_PROMPTS_FILE` (default: `roles.yaml`)
- `CHIEF_ROLE_NAME` (default: `Moderador`)
- `API_MEMORY_DB_FILE` (default: `api_memory.db`)
- `MINUTES_ROLE_NAME` (default: `Secretario_Actas`)
- `OUTPUT_EVENTS_ENABLED` (default: `true`)
- `OUTPUT_EVENTS_ALLOWED_ROLES` (default: `Moderador,Secretario_Actas`)

Ejemplo en PowerShell:

```powershell
$env:OPENCODE_BASE_URL = "http://localhost:4096"
$env:MAX_BUDGET_EUR = "1.20"
python team_orchestrator_v2.py
```

## Modelo por rol

El script usa `groq/llama-3.1-70b-versatile` por defecto.
Puedes personalizar `self.models` en `team_orchestrator_v2.py` para asignar modelos distintos por rol.

## Prompts por rol (roles.yaml)

Si existe `ROLE_PROMPTS_FILE` (por defecto `roles.yaml`), el orquestador intenta cargar prompts por rol:
- Lee `default_model` (si existe) para modelo por defecto global.
- Lee `default_response_format`.
- Lee `roles.<rol>.model` para asignar modelo por rol.
- Renderiza `{default_response_format}` dentro de cada `roles.<rol>.prompt`.
- Lee `profiles.<nombre>` para tipologias de mesa reutilizables (instrucciones y reglas globales).
- Si no puede cargar YAML o falta `PyYAML`, usa prompts por defecto sin romper ejecucion.

## Notas tecnicas

- El polling de respuestas compara contra el tamano del historial previo para evitar capturar mensajes antiguos.
- El contador de input/output es thread-safe para llamadas paralelas.
- El resumidor evita recursividad accidental de auto-resumen.
- Las sesiones persistidas se validan antes de reutilizarse.
- Se generan eventos estructurados JSONL por debate/ronda en `DEBATE_LOG_FILE`.

## Auditoria JSONL

Con `ENABLE_EVENT_LOGGING=true`, se escribe un evento por linea en formato JSON:
- `debate_started`
- `round_started`
- `round_response`
- `chief_action`
- `parallel_started`
- `parallel_completed`
- `round_error`
- `debate_stopped`
- `debate_finished`

Los campos de texto largos se recortan segun `MAX_LOG_TEXT_CHARS` para evitar archivos gigantes.

## Tests

Ejecuta pruebas unitarias:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

## Flujo Git local (sin proteccion en GitHub)

Configura hook local para bloquear push si rompe compilacion/tests:

```bash
git config core.hooksPath .githooks
```

Scripts de apoyo:
- `scripts/sync-develop.ps1`
- `scripts/new-feature.ps1 "nombre-cambio"`

Guia completa:
- `documentos/flujo_git_local.md`

Cobertura actual:
- parseo de variables de entorno (`_env_int`, `_env_float`, `_env_bool`)
- lectura de `AppConfig` desde entorno en tiempo de instanciacion
- normalizacion de roles paralelos
- truncado de texto para logging
- calculo de coste
- escritura de evento JSONL

## Problemas comunes

- `ConnectionError` o timeout:
  - Verifica que OpenCode Server esta levantado y accesible.
  - Revisa `OPENCODE_BASE_URL`.
- Sesion guardada invalida:
  - El sistema la recrea automaticamente.
- Coste demasiado alto:
  - Baja `MAX_BUDGET_EUR`, `MAX_ROUNDS_PER_DEBATE` o `MAX_CONTEXT_CHARS`.

## Estructura

- `team_orchestrator_v2.py`: orquestador principal.
- `orchestrator_cli.py`: interfaz CLI con Typer.
- `dashboard.py`: dashboard Streamlit para observabilidad e intervenciones.
- `telegram_adapter.py`: puente entre API y salas de Telegram.
- `api_server.py`: API FastAPI para orquestacion externa.
- `requirements.txt`: dependencias del proyecto.
- `README.md`: guia de uso.
- `documentos/mejora-01-intervencion-humana-dinamica.md`: detalle de mejora 01.
- `documentos/mejora-02-debate-paralelo-con-threading.md`: detalle de mejora 02.





