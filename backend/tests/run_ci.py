"""Run one CI job's tests and fail unless every required test actually ran.

Usage from backend/: python -m tests.run_ci --job backend|kafka|elasticsearch
"""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import unittest

MANIFEST = Path(__file__).with_name("ci_test_owners.json")
JOBS = ("backend", "kafka", "elasticsearch")


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed = []
        self.skip_reasons = {}

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed.append(test.id())

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.skip_reasons[test.id()] = reason


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def owner_of(test_id, owners):
    for entry in owners:
        if test_id == entry["test"] or test_id.startswith(entry["test"] + "."):
            return entry
    return None


def evaluate(job, discovered, passed, skip_reasons, failed, owners):
    """Return every reason this job's run cannot be trusted."""
    problems = []
    if not discovered:
        problems.append("No tests were discovered")
    problems.extend(f"Failed: {test_id}" for test_id in failed)
    for test_id, reason in sorted(skip_reasons.items()):
        entry = owner_of(test_id, owners)
        if entry is None:
            problems.append(f"Unexpected skip with no owning job: {test_id} ({reason})")
        elif entry["job"] == job:
            problems.append(f"Owned by this job but skipped: {test_id} ({reason})")
        elif reason != entry["skip_reason"]:
            problems.append(f"Skip reason changed: {test_id} ({reason!r} != {entry['skip_reason']!r})")
    for entry in owners:
        matching = [test_id for test_id in discovered if owner_of(test_id, [entry])]
        if job == "backend" and not matching:
            problems.append(f"Manifest entry matches no test: {entry['test']}")
        if entry["job"] == job and not set(matching) & set(passed):
            problems.append(f"Owned by this job but did not pass: {entry['test']}")
    return problems


def load_suite(job, owners):
    loader = unittest.TestLoader()
    if job == "backend":
        return loader.discover("tests", pattern="no_such_*.py", top_level_dir=".")
    return loader.loadTestsFromNames([entry["test"] for entry in owners if entry["job"] == job])


def commit():
    if os.getenv("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    return result.stdout.strip() or None


def render_markdown(summary):
    lines = [f"### {summary['job']} tests: {'passed' if not summary['problems'] else 'FAILED'}", "",
             f"Commit `{summary['commit']}`, Python {summary['python']}", "",
             "| Discovered | Passed | Failed | Skipped |", "| --- | --- | --- | --- |",
             f"| {summary['discovered']} | {len(summary['passed'])} | {len(summary['failed'])} | {len(summary['skipped'])} |"]
    if summary["skipped"]:
        lines += ["", "Skipped:", *[f"- `{test_id}`: {reason}" for test_id, reason in sorted(summary["skipped"].items())]]
    if summary["problems"]:
        lines += ["", "Problems:", *[f"- {problem}" for problem in summary["problems"]]]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True, choices=JOBS)
    parser.add_argument("--output", type=Path, help="Where to write the JSON summary")
    args = parser.parse_args()
    owners = json.loads(MANIFEST.read_text(encoding="utf-8"))["owners"]

    suite = load_suite(args.job, owners)
    discovered = [test.id() for test in flatten(suite)]
    result = unittest.TextTestRunner(verbosity=1, resultclass=RecordingResult).run(suite)
    failed = sorted({test.id() for test, _ in result.failures + result.errors}
                    | {test.id() for test in result.unexpectedSuccesses})
    problems = evaluate(args.job, discovered, result.passed, result.skip_reasons, failed, owners)

    summary = {"job": args.job, "commit": commit(), "python": platform.python_version(),
               "services": sorted(name for name in os.environ if name.startswith("VINDEX_TEST_")),
               "discovered": len(discovered), "passed": sorted(result.passed), "failed": failed,
               "skipped": result.skip_reasons, "problems": problems}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(render_markdown(summary))
    for problem in problems:
        print("CI problem:", problem, file=sys.stderr)
    print(f"{args.job}: {len(discovered)} discovered, {len(result.passed)} passed, "
          f"{len(failed)} failed, {len(result.skip_reasons)} skipped")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
