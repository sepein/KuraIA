import requests
import time
import json
import os
from typing import Dict, Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configuración global
BASE_URL = "http://localhost:4096"
SESSIONS_FILE = "team_sessions.json"
MAX_WAIT_SECONDS = 60
MAX_ROUNDS_PER_DEBATE = 15
MAX_BUDGET_EUR = 0.50                # Límite de gasto aproximado por sesión
MAX_CONTEXT_CHARS = 12000            # Máximo antes de resumir
GROQ_COST_PER_M_INPUT = 0.59 / 1_000_000
GROQ_COST_PER_M_OUTPUT = 0.79 / 1_000_000

class OpenCodeTeam:
    def __init__(self):
        self.sessions: Dict[str, str] = self.load_sessions()
        self.total_input_chars = 0
        self.total_output_chars = 0
        self.model_default = "groq/llama-3.1-70b-versatile"
        self.models = {
            "Arquitecto": "groq/llama-3.1-70b-versatile",
            "Critico_Dev": "groq/llama-3.1-70b-versatile",
            # Puedes añadir más: "Backend_Dev": "groq/llama-3.1-8b-instant", etc.
        }

    def load_sessions(self) -> Dict:
        if os.path.exists(SESSIONS_FILE):
            with open(SESSIONS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def save_sessions(self):
        with open(SESSIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.sessions, f, indent=2, ensure_ascii=False)

    def create_agent(self, role: str, custom_prompt: Optional[str] = None, workspace_path: Optional[str] = None) -> str:
        if role in self.sessions:
            return self.sessions[role]

        model = self.models.get(role, self.model_default)

        system_prompt = custom_prompt or (
            f"Eres {role} en un equipo pequeño de desarrollo software. "
            "Responde SOLO en tu rol, sé técnico, conciso y argumenta tus decisiones. "
            "Usa español si el mensaje está en español. No salgas de tu rol."
        )

        payload = {
            "model": model,
            "system_prompt": system_prompt,
        }
        if workspace_path:
            payload["workspace_path"] = workspace_path

        resp = requests.post(f"{BASE_URL}/sessions", json=payload)
        resp.raise_for_status()
        session_id = resp.json()["id"]
        self.sessions[role] = session_id
        self.save_sessions()
        print(f"[+] Sesión creada para {role}: {session_id} (model: {model})")
        return session_id

    def send_message(self, session_id: str, content: str) -> Optional[str]:
        # Resumir si contexto muy largo
        if len(content) > MAX_CONTEXT_CHARS:
            content = self.summarize(content) + "\n[Contexto resumido automáticamente]"

        payload = {"content": content, "role": "user"}
        resp = requests.post(f"{BASE_URL}/sessions/{session_id}/messages", json=payload)
        resp.raise_for_status()

        start = time.time()
        while time.time() - start < MAX_WAIT_SECONDS:
            time.sleep(1.5)
            hist_resp = requests.get(f"{BASE_URL}/sessions/{session_id}/messages")
            hist_resp.raise_for_status()
            messages = hist_resp.json()

            for msg in reversed(messages):
                if msg.get("role") == "assistant":
                    response = msg.get("content", "").strip()
                    self.total_input_chars += len(content)
                    self.total_output_chars += len(response)
                    return response
        raise TimeoutError(f"No respuesta del agente en {MAX_WAIT_SECONDS} segundos")

    def summarize(self, text: str) -> str:
        summary_role = "Summarizer"
        if summary_role not in self.sessions:
            self.create_agent(summary_role, "Eres un resumidor profesional. Resume el texto manteniendo puntos clave, decisiones y argumentos importantes.")
        return self.send_message(self.sessions[summary_role], text)

    def parallel_responses(self, roles_list: List[str], prompt: str) -> Dict[str, str]:
        results = {}
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(self.send_message, self.sessions.get(role), prompt): role
                for role in roles_list if role in self.sessions
            }
            for future in as_completed(futures):
                role = futures[future]
                try:
                    results[role] = future.result()
                except Exception as e:
                    results[role] = f"Error: {str(e)}"
        return results

    def print_cost_summary(self):
        input_tokens = self.total_input_chars // 4
        output_tokens = self.total_output_chars // 4
        cost_usd = (input_tokens * GROQ_COST_PER_M_INPUT) + (output_tokens * GROQ_COST_PER_M_OUTPUT)
        cost_eur = cost_usd * 0.92  # conversión aproximada
        print(f"\n[RESUMEN DE COSTE]")
        print(f"  Tokens input/output aprox: {input_tokens:,} / {output_tokens:,}")
        print(f"  Coste estimado Groq 70B: ~${cost_usd:.4f} (~{cost_eur:.2f} €)")
        if cost_eur > MAX_BUDGET_EUR:
            print("  ¡ATENCIÓN! Presupuesto superado.")

    def run_debate(self, initial_task: str, sequence: List[str], parallel_groups: Optional[List[List[str]]] = None):
        print("\n" + "="*60)
        print(f"DEBATE INICIADO – Tarea: {initial_task}")
        print("="*60 + "\n")

        current = initial_task
        round_num = 0

        for role in sequence:
            if round_num >= MAX_ROUNDS_PER_DEBATE:
                print("[!] Límite de rondas alcanzado.")
                break
            if (self.total_input_chars / 4 * GROQ_COST_PER_M_INPUT) > MAX_BUDGET_EUR:
                print("[!] Presupuesto aproximado superado.")
                break

            self.create_agent(role)
            print(f"→ {role} recibe contexto ({len(current)} chars)...")

            try:
                response = self.send_message(self.sessions[role], current)
                print(f"{role}:\n{response[:500]}{'...' if len(response) > 500 else ''}\n")

                # Intervención del jefe
                if role != "Jefe_Sergio":
                    print("-" * 50)
                    action = input("Intervenir como Jefe? (Enter=continuar, f=feedback, p=parar): ").strip().lower()
                    if action in ['p', 'parar', 'stop']:
                        print("Debate detenido por el Jefe.")
                        break
                    elif action in ['f', 'feedback']:
                        fb = input("Tu mensaje/feedback: ").strip()
                        if fb:
                            current += f"\n\n[JEFE SERGIO INTERVIENE]\n{fb}\n[/JEFE SERGIO]\n"
                            print("Feedback añadido al flujo.\n")
                    else:
                        current = f"Respuesta de {role}: {response}\nSiguiente turno."

                # Respuestas paralelas si corresponde
                if parallel_groups and round_num < len(parallel_groups):
                    print("\n--- Paralelo ---")
                    par_prompt = f"Respuesta previa de {role}: {response[:600]}...\nResponde desde tu rol."
                    par_results = self.parallel_responses(parallel_groups[round_num], par_prompt)
                    par_text = []
                    for r, res in par_results.items():
                        print(f"  {r}: {res[:250]}{'...' if len(res) > 250 else ''}")
                        par_text.append(f"{r}: {res}")
                    current += "\n\n[RESPUESTAS PARALELAS]\n" + "\n".join(par_text) + "\n[/RESPUESTAS PARALELAS]\n"

            except Exception as e:
                print(f"Error en {role}: {e}")
                break

            round_num += 1

        self.print_cost_summary()
        print("\n=== DEBATE FINALIZADO ===")

# Configuración del equipo
team = OpenCodeTeam()

roles_sequence = [
    "Arquitecto",
    "Critico_Dev",
    "Backend_Dev",
    "Frontend_Dev",
    "DevOps_Dev",
    "Tester_Dev",
    "Security_Dev",
    "Jefe_Sergio"
]

# Ejemplo de grupos paralelos (opcional – puedes quitar o modificar)
parallel_groups_example = [
    ["Frontend_Dev", "DevOps_Dev", "Security_Dev"],   # después del Arquitecto
    ["Tester_Dev", "Critico_Dev"]                     # después de otro rol, etc.
]

# Crear agentes iniciales
for role in roles_sequence:
    team.create_agent(role)

if __name__ == "__main__":
    tarea = input("Introduce la tarea inicial para el equipo:\n> ") or \
            "Diseña la arquitectura completa para una SaaS de gestión de tareas para pymes vascas (Go backend, React frontend, Postgres)."

    team.run_debate(tarea, roles_sequence, parallel_groups=parallel_groups_example)

    while True:
        extra = input("\n¿Quieres lanzar otro debate o continuar? (Enter para salir): ").strip()
        if not extra:
            break
        team.run_debate(extra, roles_sequence, parallel_groups=parallel_groups_example)