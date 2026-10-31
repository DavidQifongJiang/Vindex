"""Audit Python and npm dependencies. Run from the repository root; needs pip-audit and npm.

Exit codes: 0 clean or advisory-only, 1 blocking findings, 2 an audit tool failed.
A tool failure always fails the job: it means nothing was checked, not that nothing was found.
"""
import argparse
from datetime import date
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

EXCEPTIONS = Path(__file__).with_name("audit_exceptions.json")
PYTHON_MANIFESTS = (
    ("backend/requirements.txt", ["--no-deps", "--disable-pip"]),
    ("backend/requirements-analytics.txt", ["--no-deps", "--disable-pip"]),
    ("tools/code_review/requirements.lock", ["--require-hashes", "--disable-pip"]),
)


class AuditToolError(RuntimeError):
    pass


def parse_json(result, tool):
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AuditToolError(f"{tool} produced no report: {result.stderr[-500:]}") from error


def python_findings(manifest, flags):
    result = subprocess.run([sys.executable, "-m", "pip_audit", "-r", manifest, "--format", "json", *flags],
                            capture_output=True, text=True, timeout=600)
    report = parse_json(result, f"pip-audit {manifest}")
    if result.returncode not in (0, 1) or not report.get("dependencies"):
        raise AuditToolError(f"pip-audit {manifest} failed: {result.stderr[-500:]}")
    return [{"source": manifest, "package": package["name"], "version": package["version"], "id": vulnerability["id"],
             "aliases": vulnerability.get("aliases", []), "fix": vulnerability.get("fix_versions", [])}
            for package in report["dependencies"] for vulnerability in package.get("vulns", [])]


def npm_findings():
    result = subprocess.run([shutil.which("npm") or "npm", "audit", "--json"], cwd="frontend",
                            capture_output=True, text=True, encoding="utf-8", timeout=300)
    report = parse_json(result, "npm audit")
    if report.get("error") or "metadata" not in report:
        raise AuditToolError(f"npm audit failed: {report.get('error') or result.stderr[-500:]}")
    findings = []
    for name, package in report.get("vulnerabilities", {}).items():
        for advisory in package.get("via", []):
            # String entries point at another vulnerable package, which is listed itself.
            if isinstance(advisory, dict):
                advisory_id = str(advisory.get("url", "")).rsplit("/", 1)[-1] or str(advisory.get("source"))
                findings.append({"source": "frontend/package-lock.json", "package": name, "version": package.get("range"),
                                 "id": advisory_id, "aliases": [], "fix": []})
    return findings


def load_exceptions(path, today):
    """Return the unexpired exceptions by advisory ID."""
    if not path.exists():
        return {}
    active = {}
    for entry in json.loads(path.read_text(encoding="utf-8")):
        if not entry.get("reason"):
            raise AuditToolError(f"Audit exception {entry['id']} has no reason")
        if date.fromisoformat(entry["expires"]) >= today:
            active[entry["id"]] = entry
    return active


def unique(findings):
    return list({(item["source"], item["package"], item["id"]): item for item in findings}.values())


def split_accepted(findings, exceptions):
    accepted, open_findings = [], []
    for finding in findings:
        (accepted if {finding["id"], *finding["aliases"]} & exceptions.keys() else open_findings).append(finding)
    return accepted, open_findings


def describe(finding):
    return (f"{finding['source']}: {finding['package']} {finding['version']} {finding['id']}"
            + (f" (fixed in {', '.join(finding['fix'])})" if finding["fix"] else ""))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--block-all", action="store_true", help="Fail on any unaccepted finding")
    parser.add_argument("--block-on", action="append", default=[], metavar="MANIFEST",
                        help="Fail on unaccepted findings in this manifest; others only warn")
    args = parser.parse_args()
    try:
        findings = [finding for manifest, flags in PYTHON_MANIFESTS for finding in python_findings(manifest, flags)]
        findings += npm_findings()
        exceptions = load_exceptions(EXCEPTIONS, date.today())
    except (AuditToolError, subprocess.TimeoutExpired) as error:
        print(f"::error::Dependency audit did not complete: {error}")
        return 2

    accepted, open_findings = split_accepted(unique(findings), exceptions)
    blocking = [item for item in open_findings if args.block_all or item["source"] in args.block_on]
    for finding in open_findings:
        print(f"::{'error' if finding in blocking else 'warning'}::{describe(finding)}")
    for finding in accepted:
        print(f"Accepted until exception expiry: {describe(finding)}")
    totals = f"{len(open_findings)} open, {len(blocking)} blocking, {len(accepted)} accepted"
    print(f"Dependency audit: {totals}")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(f"### Dependency audit\n\n{totals}\n\n" + "".join(f"- {describe(item)}\n" for item in open_findings))
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
