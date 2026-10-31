"""Read immutable Git snapshots and bounded local changes; never execute repository code."""
from __future__ import annotations

import difflib
import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


MAX_FILE_BYTES = 250_000
ROOT_FILES = {"AGENTS.md", "README.md", "Dockerfile", "Caddyfile", "docker-compose.yml"}
ROOTS = ("backend/app/", "backend/tests/", "backend/alembic/", "frontend/src/",
         "docs/architecture/", "docs/operations/", "docs/search/", "docs/kafka/",
         "ops/", ".github/", "tools/code_review/")
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".cjs", ".css", ".md", ".yml", ".yaml", ".toml", ".json", ".ini", ".txt"}
DENIED_PARTS = {".git", ".aws", ".codex", ".claude", ".venv", "node_modules", "__pycache__", "storage", "tmp", "dist", "model_cache", ".review-cache"}


class ReviewError(Exception):
    pass


def allowed(path: str) -> bool:
    p = PurePosixPath(path)
    return (not p.is_absolute() and ".." not in p.parts and "\\" not in path
            and not any(x.lower() in DENIED_PARTS for x in p.parts)
            and not p.name.lower().startswith(".env")
            and not any(x in p.name.lower() for x in ("credential", "secret", "package-lock", "requirements.lock"))
            and (path in ROOT_FILES or (path.startswith(ROOTS) and p.suffix in SUFFIXES)))


def git(root: Path, *args: str, data: bytes | None = None) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], input=data, capture_output=True, timeout=60)
    if result.returncode:
        raise ReviewError("Git could not read the requested snapshot: " + result.stderr.decode("utf-8", "replace")[:500])
    return result.stdout


def resolve(root: Path, ref: str) -> str:
    return git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()


@dataclass
class Snapshot:
    revision: str
    files: dict[str, str]
    excluded: dict[str, str] = field(default_factory=dict)


def snapshot(root: Path, revision: str, *, index: bool = False) -> Snapshot:
    entries = []
    for record in git(root, *(('ls-files', '--stage', '-z') if index else ('ls-tree', '-rz', revision))).split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        path = raw_path.decode("utf-8", "strict")
        parts = metadata.decode().split()
        mode, oid = (parts[0], parts[1]) if index else (parts[0], parts[2])
        if index and parts[2] != "0":
            raise ReviewError("Resolve merge conflicts before reviewing.")
        if allowed(path):
            entries.append((path, mode, oid))
    regular = [(p, o) for p, m, o in entries if m in {"100644", "100755"}]
    # Batch reads avoid spawning hundreds of Git processes on Windows.
    output = git(root, "cat-file", "--batch", data="".join(o + "\n" for _, o in regular).encode()) if regular else b""
    files, excluded, offset = {}, {p: "symlink or submodule" for p, m, _ in entries if m not in {"100644", "100755"}}, 0
    for path, _ in regular:
        end = output.index(b"\n", offset)
        size = int(output[offset:end].split()[2])
        content = output[end + 1:end + 1 + size]
        offset = end + size + 2
        if size > MAX_FILE_BYTES or b"\0" in content:
            excluded[path] = "oversize or binary file"
            continue
        try:
            files[path] = content.decode("utf-8")
        except UnicodeDecodeError:
            excluded[path] = "not UTF-8"
    return Snapshot(revision, files, excluded)


def working_snapshot(root: Path) -> Snapshot:
    paths = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0")
    files, excluded = {}, {}
    for name in sorted(set(filter(allowed, paths))):
        path = root / name
        if not path.exists():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            excluded[name] = "symlink or path outside repository"
            continue
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            excluded[name] = "oversize or non-regular file"
            continue
        data = path.read_bytes()
        try:
            if b"\0" in data:
                raise ValueError()
            files[name] = data.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            excluded[name] = "binary or not UTF-8"
    digest = hashlib.sha256(repr(sorted(files.items())).encode()).hexdigest()
    return Snapshot("worktree:" + digest, files, excluded)


@dataclass
class Change:
    path: str
    old_path: str
    diff: str
    old_lines: set[int]
    new_lines: set[int]


def changes(before: Snapshot, after: Snapshot, paths: list[str]) -> list[Change]:
    selected = lambda p: not paths or any(p == x or p.startswith(x.rstrip("/") + "/") for x in paths)
    for p in sorted(set(before.excluded) | set(after.excluded)):
        if selected(p):
            raise ReviewError(f"Cannot fully read {p}: {after.excluded.get(p, before.excluded.get(p))}. Narrow --path to readable files.")
    removed = {p for p in before.files if p not in after.files}
    # Exact moves are paired before diffing. Modified moves remain additions/deletions.
    by_content: dict[str, list[str]] = {}
    for p in sorted(removed):
        by_content.setdefault(before.files[p], []).append(p)
    renames = {}
    for p in sorted(set(after.files) - set(before.files)):
        candidates = by_content.get(after.files[p], [])
        if candidates:
            renames[p] = candidates.pop(0)
    consumed = set(renames.values())
    result = []
    for p in sorted(set(before.files) | set(after.files)):
        if p in consumed or not (selected(p) or selected(renames.get(p, p))):
            continue
        old_path = renames.get(p, p)
        old, new = before.files.get(old_path, ""), after.files.get(p, "")
        if old == new:
            continue
        lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), fromfile="a/" + old_path, tofile="b/" + p, n=3, lineterm=""))
        old_changed, new_changed, a, b = set(), set(), 0, 0
        for line in lines[2:]:
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if match:
                a, b = map(int, match.groups())
            elif line.startswith("-"):
                old_changed.add(a)
                a += 1
            elif line.startswith("+"):
                new_changed.add(b)
                b += 1
            else:
                a += 1
                b += 1
        result.append(Change(p, old_path, "\n".join(lines), old_changed, new_changed))
    return result


def collect(root: Path, base: str, head: str, mode: str, paths: list[str]):
    head_sha = resolve(root, head)
    if mode == "branch":
        base_sha = git(root, "merge-base", resolve(root, base), head_sha).decode().strip()
        before, after = snapshot(root, base_sha), snapshot(root, head_sha)
    else:
        before = snapshot(root, head_sha)
        after = snapshot(root, "index", index=True) if mode == "staged" else working_snapshot(root)
    delta = changes(before, after, paths)
    if len(delta) > 30 or sum(len(c.diff) for c in delta) > 55_000:
        raise ReviewError("Diff exceeds 30 files or 55,000 characters. Split the review using --path; no content was silently truncated.")
    return before, after, delta
