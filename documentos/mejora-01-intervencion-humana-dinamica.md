# Mejora 01 – Intervención humana dinámica como Jefe Sergio

**Versión:** 1.0  
**Fecha:** Febrero 2026  
**Objetivo de esta mejora:**  
Permitir que el usuario (Jefe Sergio) pueda intervenir en cualquier momento durante el debate, añadiendo feedback, corrigiendo rumbo, aprobando o parando el flujo, sin tener que esperar al final del ciclo completo.

**Por qué es importante:**  
Esto simula el rol real de jefe de departamento: supervisión activa, no solo al final.

**Dónde aplicarla:**  
Dentro del método `run_debate()`, después de recibir la respuesta de cada agente (excepto cuando el agente es el propio Jefe_Sergio).

**Instrucciones para implementar:**

1. Después de imprimir la respuesta del agente actual (`print(f"{role}: {response[:400]}...\n")`), añade un bloque de interacción con el usuario.

2. Usa `input()` para preguntar al usuario qué quiere hacer.

3. Opciones posibles:
   - Enter o vacío → continuar normalmente
   - "f" o "feedback" → pedir texto y añadirlo al `current_content`
   - "p" o "parar" → romper el bucle del debate
   - (opcional) "s" o "saltar" → pasar al siguiente agente sin añadir nada

**Código a insertar / modificar (fragmento dentro del for role in sequence):**

```python
            # ... después de obtener y mostrar response

            if role != "Jefe_Sergio":
                print("\n" + "="*40)
                action = input(f"¿Intervenir como Jefe Sergio? (Enter=continuar, f=feedback, p=parar): ").strip().lower()

                if action in ['p', 'parar', 'stop']:
                    print("Debate interrumpido por el Jefe.")
                    break

                elif action in ['f', 'feedback']:
                    feedback_text = input("Escribe tu feedback o instrucción para el equipo: ").strip()
                    if feedback_text:
                        current += f"\n\n[INTERVENCIÓN DEL JEFE SERGIO]\n{feedback_text}\n[/INTERVENCIÓN]\n"
                        print("Feedback incorporado al flujo.\n")

                # Si no hace nada (enter), simplemente sigue
                else:
                    current = f"Respuesta anterior de {role}: {response}\nContinúa el debate."
            else:
                # Cuando es turno del Jefe, puedes forzar input si quieres más control
                jefe_input = input(f"Como Jefe Sergio, ¿quieres añadir algo antes de pasar al siguiente? (Enter=continuar): ").strip()
                if jefe_input:
                    current += f"\nAporte adicional del Jefe: {jefe_input}"