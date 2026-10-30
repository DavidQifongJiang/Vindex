"""Authoring checks for the tutorial; runs only a disposable local practice app."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parent
STARTER = ROOT / "starter"


def run(args, cwd=STARTER, env=None):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=30)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    report = {"local_python": sys.version.split()[0], "checks": {},
              "docker_build_and_container_run": "not run: Docker Desktop engine is stopped",
              "github_actions_runs": "not run: learner creates and pushes the practice repository",
              "ec2_deployment": "not run: optional learner exercise"}
    for file in STARTER.rglob("*.py"):
        ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
    report["checks"]["python_syntax"] = "passed"
    tests = run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    if tests.returncode:
        raise RuntimeError(tests.stdout + tests.stderr)
    assert "Ran 10 tests" in tests.stderr
    report["checks"]["baseline_tests"] = "10 passed"

    # Verify the learner's intended red test in a throwaway copy, never in starter.
    with tempfile.TemporaryDirectory(prefix="cicd-tutorial-check-") as temp:
        copied = Path(temp) / "practice"
        shutil.copytree(STARTER, copied, ignore=shutil.ignore_patterns("__pycache__"))
        app_file = copied / "app.py"
        app_file.write_text(app_file.read_text(encoding="utf-8").replace(
            "return math.ceil(seconds / chunk_seconds)",
            "return math.floor(seconds / chunk_seconds)"), encoding="utf-8")
        broken = run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=copied)
        assert broken.returncode != 0 and "FAIL: test_partial_last_chunk" in broken.stderr
    report["checks"]["intentional_rounding_regression"] = "expected failure confirmed in disposable copy"

    with tempfile.TemporaryDirectory(prefix="cicd-copy-check-") as temp:
        destination = Path(temp) / "fresh-practice"
        copied = run([sys.executable, str(ROOT / "create_practice.py"), str(destination)])
        assert copied.returncode == 0 and (destination / "app.py").exists()
        duplicate = run([sys.executable, str(ROOT / "create_practice.py"), str(destination)])
        assert duplicate.returncode != 0 and "already exists" in duplicate.stderr
        assert not (destination / ".git").exists()
    inside = run([sys.executable, str(ROOT / "create_practice.py"), str(ROOT / "forbidden-copy")])
    assert inside.returncode != 0 and "outside Vindex" in inside.stderr
    report["checks"]["practice_copy"] = "fresh copy succeeds; existing and nested-repository destinations refused"

    app = load_module("tutorial_app", STARTER / "app.py")
    smoke = load_module("tutorial_smoke", STARTER / "scripts" / "smoke.py")
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with patch.dict(os.environ, {"RELEASE_SHA": "validation", "DEMO_FAIL_READY": "0"}):
            assert smoke.check(base, "validation")["release"] == "validation"
            try:
                smoke.check(base, "wrong-release")
            except ValueError:
                pass
            else:
                raise AssertionError("Wrong release was accepted")
        with patch.dict(os.environ, {"DEMO_FAIL_READY": "1"}):
            failed = run([sys.executable, "scripts/smoke.py", base, "--timeout", "0"])
            assert failed.returncode != 0 and "SMOKE FAILED" in failed.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    report["checks"]["http_smoke"] = "success, wrong release rejection, and HTTP 503 rejection passed"

    workflows = {}
    for file in (STARTER / "solutions").glob("*.yml"):
        workflow = yaml.load(file.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        assert "on" in workflow and "jobs" in workflow
        jobs = workflow["jobs"]
        for name, job in jobs.items():
            needs = job.get("needs", [])
            if isinstance(needs, str):
                needs = [needs]
            assert all(parent in jobs for parent in needs), name
        workflows[file.name] = list(jobs)
    report["checks"]["workflow_yaml_and_dependencies"] = workflows
    bash = Path("D:/software/Git/bin/bash.exe")
    if bash.exists():
        checked = 0
        with tempfile.TemporaryDirectory(prefix="cicd-shell-check-") as temp:
            for file in (STARTER / "solutions").glob("*.yml"):
                workflow = yaml.load(file.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
                for job in workflow["jobs"].values():
                    for step in job["steps"]:
                        if "run" in step:
                            script = Path(temp) / f"step-{checked}.sh"
                            script.write_text(step["run"], encoding="utf-8", newline="\n")
                            result = run([str(bash), "-n", str(script)])
                            assert result.returncode == 0, result.stderr
                            checked += 1
        report["checks"]["workflow_bash_syntax"] = f"{checked} command blocks passed"
    env = {**os.environ, "LAB_IMAGE": "cicd-lab:validation", "LAB_PORT": "18080"}
    compose = run(["docker", "compose", "-f", "compose.yaml", "-p", "cicd-lab-validation", "config", "--quiet"], env=env)
    if compose.returncode:
        raise RuntimeError(compose.stderr)
    report["checks"]["compose_configuration"] = "passed without starting services"
    report["checks"]["docker_runtime"] = "unverified"
    (ROOT / "validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
