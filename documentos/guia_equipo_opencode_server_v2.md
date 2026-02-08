# Guía Completa v2.0: Simular Equipo de Programación (8 Roles) usando OpenCode Server + Capa Orquestadora Avanzada

**Versión:** 2.0 – Febrero 2026  
**Autor:** Grok (adaptado para este proyecto)  
**Objetivo:** Crear un sistema avanzado que simule un equipo de 8 personas (6 programadores + 1 arquitecto + 1 jefe) usando **OpenCode** en modo server (API HTTP) y una capa orquestadora en Python.  

Incluye: debates secuenciales y paralelos, intervención humana dinámica, control estricto de costes, persistencia, resumen de contexto y multi-model por rol.

## 1. Objetivo del Sistema
- Un solo servidor OpenCode (`opencode serve`) exponiendo API en http://localhost:4096.
- 8 sesiones independientes (una por rol).
- Orquestador Python que:
  - Maneja debates secuenciales y paralelos.
  - Permite intervención tuya como jefe en cualquier momento.
  - Resume contexto largo para ahorrar tokens.
  - Registra tokens/coste estimado y para si supera presupuesto.
  - Persiste sesiones en JSON para reanudar.
- Modelos remotos (Groq recomendado: Llama 3.1/3.3 70B barato y rápido).
- Coste típico: 0.05–0.30 €/hora con límites estrictos.

## 2. Requisitos Previos
- OpenCode actualizado (instalado vía curl o GitHub).
- API keys configuradas (`opencode` → `/connect` para Groq/Gemini/etc.).
- Python 3.10+ con paquetes: `pip install requests`
- Puerto 4096 libre.
- Workspace recomendado: un repo Git vacío para contexto compartido.
- Opcional: `pip install concurrent.futures` (ya en stdlib, pero confirma).

## 3. Arquitectura
- **Server OpenCode**: Headless, API OpenAPI 3.1 en `/doc` (Swagger).
  - Clave: POST /session (crear), POST /session/{id}/message, GET /session/{id}/messages.
- **Orquestador**: Clase `OpenCodeTeam` con métodos para create, send, parallel, summarize, persistir.
- Flujos: secuencial (ronda por ronda), paralelo (varios agentes responden a la vez).
- Control: max_rounds, max_budget_eur, max_context_chars.

## 4. Paso a Paso de Implementación

### Paso 1: Arrancar el Server (una vez, en background)
```bash
opencode serve --port 4096 --hostname 127.0.0.1
