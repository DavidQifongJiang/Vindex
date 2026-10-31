"""Run from the repository root: python -m tools.code_review --help."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import provider
from .repository import ReviewError, collect, git
from .review import PRACTICES, combine_findings, feedback, make_bundle, prompt, propose_rule, validate_findings, write_report


def arguments():
    parser = argparse.ArgumentParser(description="Local VIndex pre-PR review. No API-key fallback or GitHub publishing.")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check dependencies and ChatGPT sign-in without model calls")
    for name in ("prepare", "review"):
        run = commands.add_parser(name, help="Build review context only" if name == "prepare" else "Run two independent advisory reviewers")
        run.add_argument("--mode", choices=["branch", "staged", "worktree"], default="branch")
        run.add_argument("--base", default="origin/main")
        run.add_argument("--head", default="HEAD")
        run.add_argument("--path", action="append", default=[], help="Repeat to review a bounded subset of paths")
        task = run.add_mutually_exclusive_group()
        task.add_argument("--task", default="", help="Problem, intended behavior, and acceptance criteria")
        task.add_argument("--task-file", type=Path)
        run.add_argument("--model", help="Optional model supported by your signed-in Codex plan")
        run.add_argument("--timeout", type=int, default=180, help="Maximum seconds per reviewer (1–600)")
        run.add_argument("--refresh", action="store_true", help="Explicitly rerun an identical completed review")
    fb = commands.add_parser("feedback", help="Record developer judgment about a finding")
    fb.add_argument("--report", required=True, type=Path)
    fb.add_argument("--finding", required=True)
    fb.add_argument("--verdict", required=True, choices=["accepted", "false-positive", "intentional", "needs-discussion"])
    fb.add_argument("--reason", required=True)
    proposal = commands.add_parser("propose-rule", help="Draft a reusable rule from feedback; never activates it automatically")
    proposal.add_argument("--feedback", required=True, type=Path)
    proposal.add_argument("--finding", required=True)
    proposal.add_argument("--rule", required=True)
    return parser.parse_args()


def main():
    args = arguments()
    if args.command == "feedback":
        print(feedback(args.report.resolve(), args.finding, args.verdict, args.reason))
        return 0
    if args.command == "propose-rule":
        print(propose_rule(args.feedback.resolve(), args.finding, args.rule))
        return 0
    root = Path(git(args.repo.resolve(), "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if args.command == "doctor":
        import importlib.metadata
        versions = {x: importlib.metadata.version(x) for x in ("chromadb", "onnxruntime", "tree-sitter", "tree-sitter-typescript", "jsonschema")}
        print(json.dumps({"python": sys.version.split()[0], "dependencies": versions, "auth": provider.check_auth(),
                          "api_fallback": False, "embeddings": "local ONNX all-MiniLM-L6-v2", "six_practices": PRACTICES}, indent=2))
        return 0
    task = args.task_file.read_text(encoding="utf-8") if args.task_file else args.task
    if len(task) > 12_000 or not 1 <= args.timeout <= 600:
        raise ReviewError("Task context must be at most 12,000 characters; timeout must be 1–600 seconds.")
    if args.command == "review" and not task.strip():
        raise ReviewError("Supply --task or --task-file with intended behavior and acceptance criteria (course practice 3).")
    cache = root / ".review-cache"
    if cache.is_symlink() or not cache.resolve().is_relative_to(root):
        raise ReviewError("Reviewer cache must remain inside this repository.")
    before, after, delta = collect(root, args.base, args.head, args.mode, args.path)
    report = {"status": "prepared", "created_at": datetime.now(timezone.utc).isoformat(), "base": before.revision,
              "head": after.revision, "findings": [], "questions": [], "limitations": [], "usage": {},
              "six_practices": PRACTICES, "model": args.model or "Codex default (user config ignored)", "api_fallback": False,
              "scope": [c.path for c in delta]}
    if args.path:
        report["limitations"].append("Scoped review only: " + ", ".join(args.path))
    report["limitations"].append("Tests/builds were not run by the reviewer. Retrieved context is selective; this is not an exhaustive audit.")
    report["limitations"].append("Only allowlisted source/documentation is reviewed. Environment files, lockfiles, binaries and generated data are excluded; pure file moves require separate path/import review.")
    context = []
    if delta:
        from .context import ContextIndex
        print("Building/querying local Chroma context (the embedding model downloads once if absent)...", flush=True)
        query = task + "\n" + "\n".join(c.path + "\n" + c.diff[:1500] for c in delta)
        context = ContextIndex(cache).retrieve(after, [c.path for c in delta], query)
    bundle = make_bundle(before, after, delta, context, task)
    identity = {"format_version": 2, "bundle": bundle, "model": args.model, "prompts": [prompt(area, {}) for area in ("security", "patterns")]}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
    output = cache / "reports" / digest
    output.mkdir(parents=True, exist_ok=True)
    existing = output / "review.json"
    if existing.exists() and not args.refresh:
        prior = json.loads(existing.read_text(encoding="utf-8"))
        if prior.get("status") in {"completed", "no_changes"}:
            print("Reusing completed review: " + str(output / "review.md"))
            return 0
    (output / "context.json").write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
    if not delta:
        report["status"] = "no_changes"
        report["limitations"].append("No changed reviewable content in the selected scope. Exact unchanged file moves are omitted.")
    elif args.command == "review":
        try:
            for area in ("security", "patterns"):
                print("Running independent " + area + " review with ChatGPT-authenticated Codex...", flush=True)
                answer, usage = provider.run_review(prompt(area, bundle), model=args.model, timeout=args.timeout)
                validated = validate_findings(answer, before, after, delta)
                combine_findings(report["findings"], validated, area)
                report["questions"].extend(answer["questions"])
                report["limitations"].extend(answer["limitations"])
                report["usage"][area] = usage
            report["status"] = "completed"
            report["findings"].sort(key=lambda f: (f["severity"], f["path"], f["line"]))
        except Exception as exc:
            # A child-process/OS/parser failure must preserve earlier findings as
            # incomplete too, not leave an older prepared/completed report behind.
            report["status"] = "incomplete"
            report["limitations"].append(type(exc).__name__ + ": " + str(exc))
    else:
        report["limitations"].append("Context preparation only; no model review was requested.")
    write_report(output, report)
    print(f"{report['status']}: {output / 'review.md'}")
    return 2 if report["status"] == "incomplete" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ReviewError, OSError, ValueError) as exc:
        print("Review stopped: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
