"""
Orquestador de revisión de contratos OpenAPI para GitHub Actions.
Invoca el skill via `copilot run` (CLI de @github/copilot).
Sin llamadas HTTP a la Copilot Chat API — el CLI gestiona todo.
Solo stdlib Python.
"""
import json
import os
import subprocess
import urllib.request
from pathlib import Path

# ── Configuración ─────────────────────────────────────────────────────────────

# SKILL_BASE se inyecta desde el workflow; apunta al checkout de api-governance-ai-skills
_SKILL_BASE = Path(os.environ.get("SKILL_BASE", "_skill"))
SKILL_MD    = _SKILL_BASE / ".github/skills/revisar-contratos-api/SKILL.md"
MARKER      = "<!-- api-governance-review -->"


# ── Ejecución del skill via Copilot CLI ───────────────────────────────────────

def ejecutar_skill(contrato: str, rama_base: str, codigos: str) -> tuple[str, bool]:
    """
    Invoca el SKILL.md completo a través del CLI de @github/copilot.

    El CLI ejecuta internamente los pasos 1-4 definidos en SKILL.md:
      · Paso 1: corre extraer_contexto.py (reglas calculadas + preguntas verificación)
      · Paso 2: lee contexto.md generado
      · Paso 3: responde SÍ/NO a las verificaciones [G1],[P1],[E1]…
      · Paso 4: produce la tabla final de hallazgos en stdout

    No se hace ninguna llamada HTTP a api.githubcopilot.com desde este script.

    Devuelve (hallazgos_markdown | mensaje_error, ok).
    """
    # Argumento del skill según SKILL.md:
    # "[ruta del contrato] [--rama RAMA] [--codigos CODIGOS]"
    skill_arg = f'"{contrato}" "--rama" "origin/{rama_base}"'
    if codigos:
        skill_arg += f' "--codigos" "{codigos}"'

    result = subprocess.run(
        ["copilot", "run", str(SKILL_MD), skill_arg],
        capture_output=True,
        text=True,
        env={**os.environ},
    )

    salida = result.stdout.strip()

    if result.returncode != 0 or salida.startswith("NO_EVALUABLE"):
        return salida or result.stderr.strip(), False

    return salida, True


# ── Comentario en PR ──────────────────────────────────────────────────────────

def publicar_comentario(pr_number: str, repo: str, cuerpo: str) -> None:
    """Crea o actualiza el comentario de revisión en el PR."""
    token = os.environ["GITHUB_TOKEN"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    base_url = f"https://api.github.com/repos/{repo}"

    req = urllib.request.Request(
        f"{base_url}/issues/{pr_number}/comments", headers=headers
    )
    with urllib.request.urlopen(req) as resp:
        comments = json.loads(resp.read())

    existing = next(
        (c for c in comments if MARKER in c.get("body", "")), None
    )

    payload = json.dumps({"body": f"{MARKER}\n{cuerpo}"}).encode()

    if existing:
        url = f"{base_url}/issues/comments/{existing['id']}"
        req = urllib.request.Request(url, data=payload, headers=headers, method="PATCH")
    else:
        url = f"{base_url}/issues/{pr_number}/comments"
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")

    with urllib.request.urlopen(req):
        pass


# ── Punto de entrada ──────────────────────────────────────────────────────────

def main() -> None:
    contracts = [
        c for c in os.environ.get("CONTRACTS", "").splitlines() if c.strip()
    ]
    base_branch = os.environ["BASE_BRANCH"]
    pr_number   = os.environ["PR_NUMBER"]
    repo        = os.environ["REPO"]
    codigos     = os.environ.get("CODIGOS", "").strip()
    model_info  = os.environ.get("COPILOT_MODEL", "default")

    reglas_info = f"reglas: {codigos}" if codigos else "todas las reglas activas"
    print(f"Skill: {SKILL_MD} | Modelo: {model_info} | Contratos: {len(contracts)} | {reglas_info}")
    print("El CLI de Copilot ejecuta el skill completo (pasos 1-4). Sin llamadas a Chat API.")

    resultados = []

    for contrato in contracts:
        print(f"  → {contrato}")

        # El CLI ejecuta internamente extraer_contexto.py + análisis híbrido
        hallazgos, ok = ejecutar_skill(contrato, base_branch, codigos)

        if not ok:
            resultados.append(
                f"### `{contrato}`\n\n> **NO_EVALUABLE:** {hallazgos}\n"
            )
            continue

        resultados.append(f"### `{contrato}`\n\n{hallazgos}\n")

    cuerpo = (
        f"## Revisión de Contratos API\n\n"
        f"> Modelo: `{MODEL}` (GitHub Copilot CLI)\n\n"
        + "\n---\n".join(resultados)
        + "\n\n---\n*Generado por api-governance-ai-skills*"
    )

    publicar_comentario(pr_number, repo, cuerpo)
    print("Comentario publicado en el PR.")


if __name__ == "__main__":
    main()
