"""ChatGPT-authenticated Codex subprocess; no API-key or cloud-embedding path."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .repository import ReviewError


EVIDENCE = {"type": "object", "additionalProperties": False,
            "properties": {"path": {"type": "string"}, "side": {"enum": ["base", "head"]},
                           "line": {"type": "integer"}, "quote": {"type": "string"}},
            "required": ["path", "side", "line", "quote"]}
FINDING = {"type": "object", "additionalProperties": False,
           "properties": {"title": {"type": "string"}, "severity": {"enum": ["P1", "P2", "P3"]},
                          "rule_id": {"type": "string"}, "path": {"type": "string"},
                          "side": {"enum": ["base", "head"]}, "line": {"type": "integer"},
                          "scenario": {"type": "string"}, "impact": {"type": "string"},
                          "suggestion": {"type": "string"}, "test": {"type": "string"},
                          "evidence": {"type": "array", "items": EVIDENCE}},
           "required": ["title", "severity", "rule_id", "path", "side", "line", "scenario", "impact", "suggestion", "test", "evidence"]}
SCHEMA = {"type": "object", "additionalProperties": False,
          "properties": {"findings": {"type": "array", "items": FINDING},
                         "questions": {"type": "array", "items": {"type": "string"}},
                         "limitations": {"type": "array", "items": {"type": "string"}}},
          "required": ["findings", "questions", "limitations"]}


def clean_environment() -> dict[str, str]:
    # Keep OS/auth-discovery essentials, not inherited API credentials/endpoints,
    # CODEX_* overrides, cloud credentials, or task-specific secrets.
    allowed = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "HOME", "USERPROFILE",
               "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "CODEX_HOME", "LANG"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def executable() -> str:
    result = shutil.which("codex.exe" if os.name == "nt" else "codex")
    if not result:
        raise ReviewError("Codex CLI is missing from PATH. Install it and run 'codex login' with ChatGPT.")
    return result


def check_auth() -> str:
    result = subprocess.run([executable(), "login", "status"], env=clean_environment(), capture_output=True, timeout=30)
    status = (result.stdout + result.stderr).decode("utf-8", "replace")
    if result.returncode or "Logged in using ChatGPT" not in status:
        raise ReviewError("ChatGPT sign-in is required. Run 'codex login'; API-key authentication is not accepted.")
    return "ChatGPT (account usage/credit settings apply)"


def command(exe: str, directory: Path, model: str | None) -> list[str]:
    args = [exe, "-a", "never", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
            "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never", "--json",
            "--cd", str(directory), "--output-schema", str(directory / "schema.json"),
            "--output-last-message", str(directory / "answer.json")]
    config = {"forced_login_method": "chatgpt", "model_provider": "openai", "web_search": "disabled",
              "project_doc_max_bytes": 0, "history.persistence": "none", "mcp_servers": {}}
    for key, value in config.items():
        args.extend(["-c", key + "=" + ("{}" if isinstance(value, dict) else json.dumps(value))])
    for feature in ("shell_tool", "apps", "browser_use", "browser_use_external", "computer_use", "multi_agent",
                    "image_generation", "view_image", "skill_search", "workspace_dependencies", "code_mode_host",
                    "plugins", "hooks", "skill_mcp_dependency_install"):
        args.extend(["--disable", feature])
    args.extend(["--enable", "skip_host_skill_discovery"])
    if model:
        args.extend(["--model", model])
    return args + ["-"]


def run_review(prompt: str, *, model: str | None = None, timeout: int = 180) -> tuple[dict, dict]:
    import jsonschema

    check_auth()
    # An empty directory outside the repo avoids repo config, hooks and AGENTS loading.
    with tempfile.TemporaryDirectory(prefix="vindex-review-") as temp:
        directory = Path(temp)
        (directory / "schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
        process = subprocess.Popen(command(executable(), directory, model), env=clean_environment(),
                                   cwd=directory, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, stderr = process.communicate(prompt.encode(), timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
            else:
                process.kill()
            process.communicate()
            raise ReviewError(f"Codex exceeded {timeout}s; review incomplete. No fallback was attempted.")
        if process.returncode:
            # Keep diagnostics local and bounded; do not persist complete model streams.
            detail = stderr.decode("utf-8", "replace")[-1000:]
            raise ReviewError("Codex stopped; review incomplete. No fallback was attempted. " + detail)
        usage = {}
        for line in stdout.decode("utf-8", "replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "turn.completed":
                usage = event.get("usage", {})
            if event.get("type") in {"error", "turn.failed"}:
                raise ReviewError("Codex reported a failed turn; review incomplete.")
        try:
            answer = json.loads((directory / "answer.json").read_text(encoding="utf-8"))
            jsonschema.validate(answer, SCHEMA)
        except (OSError, ValueError, jsonschema.ValidationError) as exc:
            raise ReviewError("Codex returned missing or invalid structured findings; review incomplete.") from exc
        return answer, usage
