"""Syntax-aware chunks and an explicit local ONNX/Chroma retrieval backend."""
from __future__ import annotations

import ast
import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

from .repository import Snapshot, ReviewError


@dataclass
class Chunk:
    path: str
    start: int
    end: int
    symbol: str
    content: str

    @property
    def id(self):
        return hashlib.sha256(repr(asdict(self)).encode()).hexdigest()


def chunks_for_file(path: str, content: str) -> list[Chunk]:
    lines = content.splitlines()
    boundaries = {0: "module"}
    if path.endswith(".py"):
        try:
            for node in ast.walk(ast.parse(content)):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    start = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
                    boundaries[start] = node.name
        except SyntaxError:
            boundaries[0] = "syntax-error; line chunks"
    elif path.endswith((".ts", ".tsx")):
        try:
            import tree_sitter_typescript as ts
            from tree_sitter import Language, Parser
            parser = Parser(Language(ts.language_tsx() if path.endswith(".tsx") else ts.language_typescript()))
            tree = parser.parse(content.encode())
            stack = [tree.root_node]
            while stack:
                node = stack.pop()
                if node.type in {"function_declaration", "class_declaration", "interface_declaration", "type_alias_declaration", "lexical_declaration"}:
                    name = node.child_by_field_name("name")
                    boundaries[node.start_point.row] = name.text.decode() if name else node.type
                stack.extend(reversed(node.children))
        except ImportError as exc:
            raise ReviewError("Install the reviewer's requirements.lock to parse TypeScript.") from exc
    elif path.endswith(".md"):
        boundaries.update({i: line for i, line in enumerate(lines) if line.startswith("#")})
    result = []
    starts = sorted(boundaries)
    for index, begin in enumerate(starts):
        stop = starts[index + 1] if index + 1 < len(starts) else len(lines)
        start, size = begin, 0
        for i in range(begin, stop):
            if i > start and (size + len(lines[i]) > 800 or i - start >= 35):
                result.append(Chunk(path, start + 1, i, boundaries[begin], "\n".join(lines[start:i])))
                start, size = i, 0
            size += len(lines[i]) + 1
        if start < stop and "\n".join(lines[start:stop]).strip():
            result.append(Chunk(path, start + 1, stop, boundaries[begin], "\n".join(lines[start:stop])))
    return result


class ContextIndex:
    def __init__(self, cache: Path):
        try:
            import chromadb
            from chromadb.config import Settings
            from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
        except ImportError as exc:
            raise ReviewError("Install the reviewer's requirements.lock before building the local index.") from exc
        # All inference is local. No cloud embedding function or API credentials.
        ONNXMiniLM_L6_V2.DOWNLOAD_PATH = cache / "models" / "all-MiniLM-L6-v2"
        self.embed = ONNXMiniLM_L6_V2(preferred_providers=["CPUExecutionProvider"])
        self.client = chromadb.PersistentClient(path=str(cache / "chroma"), settings=Settings(anonymized_telemetry=False))
        self.collection = self.client.get_or_create_collection("vindex-code-minilm-v1", embedding_function=self.embed)

    def retrieve(self, snapshot: Snapshot, changed_paths: list[str], query: str, limit: int = 15) -> list[dict]:
        # IDs include content and location. Filtering by this snapshot's IDs prevents
        # stale chunks from another branch or deleted files entering the result.
        chunks = [c for p, text in sorted(snapshot.files.items()) for c in chunks_for_file(p, text)]
        if not chunks:
            return []
        ids = [c.id for c in chunks]
        present = set()
        for start in range(0, len(ids), 300):
            present.update(self.collection.get(ids=ids[start:start + 300], include=[])["ids"])
        missing = [c for c in chunks if c.id not in present]
        print(f"Local index: {len(chunks)} current chunks; {len(missing)} need embeddings.", flush=True)
        for start in range(0, len(missing), 64):
            batch = missing[start:start + 64]
            self.collection.add(ids=[c.id for c in batch],
                                documents=[f"{c.path} {c.symbol}\n{c.content}" for c in batch],
                                metadatas=[{"path": c.path, "start": c.start, "end": c.end} for c in batch])
        response = self.collection.query(query_texts=[query[:5000]], ids=ids, n_results=min(limit, len(ids)), include=["distances"])
        by_id = {c.id: c for c in chunks}
        selected = [by_id[i] for i in response["ids"][0]]
        # Direct Python imports complement semantic retrieval. Do not select tests
        # merely because they contain common words from a natural-language task.
        imports = set()
        for path in changed_paths:
            if not path.endswith(".py"):
                continue
            try:
                tree = ast.parse(snapshot.files.get(path, ""))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.replace(".", "/") + ".py")
                    imports.update(node.module.replace(".", "/") + "/" + alias.name + ".py" for alias in node.names)
                elif isinstance(node, ast.Import):
                    imports.update(alias.name.replace(".", "/") + ".py" for alias in node.names)
        related = [c for c in chunks if c.path not in changed_paths
                   and (c.path in imports or c.path.removeprefix("backend/") in imports)]
        for c in related[:3]:
            if c not in selected:
                selected.append(c)
        return [dict(asdict(c), revision=snapshot.revision) for c in selected]
