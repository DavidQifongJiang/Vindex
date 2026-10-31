"""Opt-in live smoke check: python -m tools.code_review.smoke --live.

Uses two signed-in Codex calls on a synthetic deletion-only authorization bug.
Never executes application code, writes to GitHub, or accesses VIndex services.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path

from .context import ContextIndex
from .provider import run_review
from .repository import Snapshot, changes
from .review import PRACTICES, combine_findings, make_bundle, prompt, validate_findings, write_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True, help="Authorize two calls using signed-in Codex usage")
    parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    path = "backend/app/api/fixture_private_video.py"
    before = Snapshot("synthetic-base", {path: (
        "def get_private_video(db, video_id, user_id):\n"
        "    video = db.get_video(video_id)\n"
        "    if video is None:\n"
        "        raise LookupError('not found')\n"
        "    if video.owner_user_id != user_id:\n"
        "        raise PermissionError('forbidden')\n"
        "    return {'title': video.title, 'transcript': video.transcript}\n")})
    after = Snapshot("synthetic-head", {path: before.files[path].replace(
        "    if video.owner_user_id != user_id:\n        raise PermissionError('forbidden')\n", "")})
    delta = changes(before, after, [])
    context = ContextIndex(root / ".review-cache").retrieve(after, [path], "Private video access must be restricted to its authenticated owner.")
    bundle = make_bundle(before, after, delta, context,
                         "Preserve owner-only access to private video titles/transcripts. A different authenticated user must be denied. No upstream authorization is performed.")
    report = {"status": "incomplete", "base": before.revision, "head": after.revision,
              "created_at": datetime.now(timezone.utc).isoformat(), "findings": [], "questions": [],
              "limitations": ["Synthetic smoke fixture, NOT a defect reported against VIndex. Not a quality benchmark."],
              "six_practices": PRACTICES, "usage": {}}
    output = root / ".review-cache/reports/smoke-authorization"
    try:
        for area in ("security", "patterns"):
            print("Synthetic smoke: " + area, flush=True)
            answer, usage = run_review(prompt(area, bundle))
            combine_findings(report["findings"], validate_findings(answer, before, after, delta), area)
            report["questions"].extend(answer["questions"])
            report["limitations"].extend(answer["limitations"])
            report["usage"][area] = usage
        if not any(f["severity"] == "P1" and f["rule_id"] == "VIX-AUTH" for f in report["findings"]):
            raise RuntimeError("The smoke check did not detect the seeded authorization regression.")
        report["status"] = "completed"
        print("Detected the seeded authorization regression with valid deletion-line evidence.")
    except Exception as exc:
        report["limitations"].append(str(exc))
        raise
    finally:
        write_report(output, report)
        print(output / "review.md")


if __name__ == "__main__":
    main()
