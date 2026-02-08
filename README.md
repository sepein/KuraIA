# OpenCode Team Orchestrator

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![OpenCode](https://img.shields.io/badge/Powered%20by-OpenCode-8A2BE2)](https://opencode.ai)

Simulador de equipo de desarrollo pequeño (8 roles) usando **OpenCode** en modo server + orquestador en Python.  
Ideal para experimentar con multi-agente, debates entre modelos IA, planificación de proyectos y simulación de procesos de empresa sin hardware local potente.

- 6 programadores + 1 arquitecto + 1 jefe de departamento (tú).
- Debates secuenciales y paralelos.
- Intervención humana en tiempo real.
- Control estricto de costes y contexto.
- Persistencia de sesiones y estimación de gasto (€).

## Características principales

- **Modo server de OpenCode** como backend (API HTTP headless).
- Orquestador Python que gestiona 8 agentes con roles fijos.
- **Debates secuenciales** + **respuestas paralelas** (threading).
- Intervención dinámica del jefe (feedback, parar, continuar).
- Resumen automático de contexto largo para ahorrar tokens.
- Estimación y límite de presupuesto (Groq 70B por defecto).
- Persistencia de sesiones en `team_sessions.json`.
- Soporte multi-model por rol (modelo diferente para Arquitecto/Crítico, etc.).
- Logging aproximado de tokens y coste en euros.

## Requisitos

- **OpenCode** instalado y actualizado (2026+)
  ```bash
  curl -fsSL https://opencode.ai/install | bash