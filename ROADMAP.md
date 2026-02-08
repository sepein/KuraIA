# Roadmap - OpenCode Team Orchestrator

**Proyecto:** Simulador de equipo de programación pequeño (8 roles) usando OpenCode server + orquestador en Python  
**Versión actual:** 2.0 (febrero 2026)  
**Estado actual:** MVP funcional con todas las mejoras principales implementadas (debates secuenciales/paralelos, intervención humana, control de costes, persistencia, resumen de contexto, multi-model).  
**Objetivo general:** Crear una herramienta open source, ligera y de bajo coste para simular procesos de desarrollo en un equipo pequeño, con énfasis en debates entre agentes IA y supervisión humana.

## Visión a medio plazo (3–6 meses)

Convertir el script actual en una herramienta más usable, extensible y mantenible, manteniendo el espíritu de "todo local + APIs remotas baratas".

### Fase 1 – Usabilidad y Experiencia del Usuario (Próximos 1–2 meses)

- [ ] **CLI interactiva con Typer o Click**  
  Comandos principales:
  - `orchestrator start "Diseña arquitectura SaaS X"`  
  - `orchestrator intervene "Añade JWT + refresh tokens"`  
  - `orchestrator status` (muestra sesiones, coste acumulado, último debate)  
  - `orchestrator export last --format md` (exporta debate a Markdown)

- [ ] **Exportación automática de debates**  
  - Generar Markdown o HTML bonito al finalizar cada debate  
  - Incluir: tarea inicial, historial completo, intervenciones del jefe, coste estimado  
  - Opcional: exportar diffs/código generado a archivos en el workspace

- [ ] **Logging mejorado y dashboard simple**  
  - Archivo `debates.log.json` con cada sesión  
  - Comando `orchestrator history` para ver resumen de debates pasados

### Fase 2 – Mejoras de Colaboración y Autonomía (2–4 meses)

- [ ] **Integración con Git real**  
  - Usar tools de OpenCode (si expuestos en API) para commit/push automático  
  - Cada agente trabaja en su branch → merge manual o automático por jefe  
  - Comando `orchestrator git-sync`

- [ ] **Debates más inteligentes**  
  - Modo "round-robin automático" con votación simple (mayoría para decisiones)  
  - Agente "Facilitador" que modera y resume cuando el debate se alarga

- [ ] **Soporte para WebSocket / streaming**  
  - Reemplazar polling por streaming real si OpenCode lo añade en futuras versiones  
  - Mejora UX: ver respuestas aparecer en tiempo real

- [ ] **Multi-proyecto / workspaces**  
  - Seleccionar workspace por debate  
  - Soporte para varios equipos simultáneos (ej. equipo frontend vs backend)

### Fase 3 – Escalabilidad y Comunidad (4–6 meses+)

- [ ] **Dockerización**  
  - Dockerfile para correr server + orquestador en un contenedor  
  - Volumen para workspaces y sesiones persistentes

- [ ] **Publicación como paquete PyPI**  
  - `pip install opencode-team-orchestrator`  
  - Configuración vía archivo YAML/JSON externo

- [ ] **Integración con otros backends**  
  - Fallback a CrewAI / AutoGen / Microsoft Agent Framework si OpenCode falla  
  - Adaptador LLM custom que use OpenCode API como proveedor

- [ ] **Tests y CI/CD**  
  - Tests unitarios para orquestador  
  - GitHub Actions para linting y pruebas básicas

- [ ] **Documentación avanzada**  
  - Wiki o docs en repo con ejemplos reales (SaaS, API, web app)  
  - Capturas de pantalla / GIFs de debates en acción

## Prioridades inmediatas (próximas 2–4 semanas)

1. Implementar CLI con Typer (mejora usabilidad drástica)  
2. Añadir exportación automática de debates a Markdown  
3. Mejorar prompts por rol (más detallados y consistentes)  
4. Añadir opción de "modo silencioso" (sin inputs, para batch)  
5. Probar con un proyecto real pequeño (ej. API REST + frontend simple) y documentar resultados

## Métricas de éxito

- Coste por debate completo < 0.50 €  
- Tiempo de setup < 5 minutos  
- Debates útiles sin intervención constante del jefe  
- Código generado ejecutable en al menos 70% de casos (con revisión humana)  
- Repositorio público con >50 stars en 6 meses (opcional, si se publica)

## Notas finales

- Mantener el proyecto ligero: no más de 1–2 archivos principales + configs  
- Priorizar privacidad y bajo coste (Groq + Gemini Flash como fallback)  
- Documentar todo: README, CHANGELOG, este Roadmap y el informe completo  
- Iterar con feedback real: ejecutar debates y ajustar prompts/roles según resultados

¡Este roadmap es vivo! Actualizarlo cada vez que se complete una tarea o surja una nueva idea.  
Última actualización: 08 de febrero de 2026