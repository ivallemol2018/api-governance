"""
Orquestador de revisión de contratos OpenAPI para GitHub Actions.
Orquesta las dos fases del motor híbrido usando `copilot -p` (CLI de @github/copilot).
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

# ── Fase 1: generación de contexto (script Python) ───────────────────────────

def generar_contexto(contrato: str, rama_base: str, codigos: str) -> tuple[str, bool]:
    """
    Fase 1 del motor híbrido: corre extraer_contexto.py.
    Resuelve reglas calculadas (R-GENM02, R-DATM01, R-DATM04) y genera
    las preguntas de verificación [G1],[P1],[E1]… para Copilot.
    Devuelve (ruta_contexto.md | mensaje_error, ok).
    """
    script = str(_SKILL_BASE / ".github/skills/revisar-contratos-api/scripts/extraer_contexto.py")
    output_path = f".api-review/{Path(contrato).stem}/contexto.md"
    cmd = [sys.executable, script, contrato,
           "--rama", f"origin/{rama_base}",
           "--salida", output_path]
    if codigos:
        cmd += ["--codigos", codigos]

    result = subprocess.run(cmd, capture_output=True, text=True)
    salida = result.stdout.strip()
    if result.returncode != 0 or salida.startswith("Error:"):
        return salida or result.stderr.strip(), False
    return output_path, True


# ── Fase 2: análisis semántico (copilot -p) ───────────────────────────────────

def revisar_con_copilot_cli(contexto_path: str) -> str:
    """
    Fase 2 del motor híbrido: pasa el contexto al CLI de @github/copilot
    usando el flag -p (modo no-interactivo).

    El CLI lee GITHUB_TOKEN del entorno — sin auth login, sin Chat API.
    Sintaxis: copilot -p "prompt"  (o --prompt)

    El prompt incluye las instrucciones del SKILL.md (pasos 2-4) y el
    contexto.md generado en la Fase 1.
    """
    skill   = SKILL_MD.read_text(encoding="utf-8")
    contexto = Path(contexto_path).read_text(encoding="utf-8")

    # Incluimos solo los pasos 2-4 del SKILL.md (el paso 1 ya fue ejecutado)
    prompt = (
        f"Sigue los pasos 2, 3 y 4 de estas instrucciones:\n\n"
        f"{skill}\n\n"
        f"El contexto del contrato ya fue generado (paso 1 completado):\n\n"
        f"{contexto}"
    )

    result = subprocess.run(
        ["copilot", "-p", prompt],          # -p = --prompt, modo no-interactivo
        capture_output=True,
        text=True,
        env={**os.environ},                 # GITHUB_TOKEN ya está en el entorno
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Error en Copilot CLI: {result.stderr.strip()}\n"
            "Verifica que GITHUB_TOKEN esté disponible y tenga licencia Copilot."
        )

    return result.stdout.strip()


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
    print(f"Contratos: {len(contracts)} | {reglas_info}")
    print("Fase 1: extraer_contexto.py | Fase 2: copilot -p | Sin Chat API.")

    resultados = []

    for contrato in contracts:
        print(f"  → {contrato}")

        # Fase 1: script resuelve reglas calculadas y genera preguntas de verificación
        contexto_path, ok = generar_contexto(contrato, base_branch, codigos)
        if not ok:
            resultados.append(
                f"### `{contrato}`\n\n> **NO_EVALUABLE:** {contexto_path}\n"
            )
            continue

        # Fase 2: Copilot CLI (-p) responde verificaciones y combina tabla final
        hallazgos = revisar_con_copilot_cli(contexto_path)
        resultados.append(f"### `{contrato}`\n\n{hallazgos}\n")

    cuerpo = (
        "## Revisión de Contratos API\n\n"
        "> GitHub Copilot CLI (`copilot -p`)\n\n"
        + "\n---\n".join(resultados)
        + "\n\n---\n*Generado por api-governance-ai-skills*"
    )

    publicar_comentario(pr_number, repo, cuerpo)
    print("Comentario publicado en el PR.")


if __name__ == "__main__":
    main()
