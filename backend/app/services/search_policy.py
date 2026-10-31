"""Pure response policy; coverage and engine execution are separate facts."""
from dataclasses import dataclass


BACKENDS = ("semantic", "lexical")
REQUESTED = {"semantic": ("semantic",), "lexical": ("lexical",), "hybrid": BACKENDS}


@dataclass(frozen=True)
class SearchOutcome:
    http_status: int
    requested_mode: str
    effective_mode: str
    degraded: bool
    coverage_complete: bool
    total_videos: int
    semantic_eligible: int
    lexical_eligible: int
    either_eligible: int
    semantic_status: str
    lexical_status: str
    notices: tuple[str, ...]
    processing_videos: int = 0
    indexing_failed_videos: int = 0

    def as_dict(self):
        return {"requested_mode": self.requested_mode, "effective_mode": self.effective_mode,
                "degraded": self.degraded,
                "coverage": {"complete": self.coverage_complete, "total_videos": self.total_videos,
                    "processing_videos": self.processing_videos, "indexing_failed_videos": self.indexing_failed_videos,
                    "eligible_videos": {"semantic": self.semantic_eligible, "lexical": self.lexical_eligible,
                                        "either": self.either_eligible}},
                "branches": {"semantic": {"status": self.semantic_status}, "lexical": {"status": self.lexical_status}},
                "notices": list(self.notices)}


def search_outcome(mode, *, video_ids, semantic_ids=(), lexical_ids=(), statuses=None,
                   processing_ids=(), indexing_failed_ids=()):
    """Derive status after branch execution over authorized current projections.

    An `ok` status includes a successful search with zero matches. An unavailable
    authoritative scope must raise in the caller, never be represented as empty.
    """
    if mode not in REQUESTED:
        raise ValueError("Unknown search mode")
    scope = frozenset(video_ids)
    eligible = {"semantic": frozenset(semantic_ids), "lexical": frozenset(lexical_ids)}
    if any(not ids <= scope for ids in eligible.values()):
        raise ValueError("Eligible videos must belong to the authorized scope")
    processing, indexing_failed = frozenset(processing_ids), frozenset(indexing_failed_ids)
    if not (processing | indexing_failed) <= scope or processing & indexing_failed:
        raise ValueError("Invalid processing/failure coverage")
    if processing & (eligible["semantic"] | eligible["lexical"]):
        raise ValueError("A processing video cannot have eligible current search output")
    statuses = dict(statuses or {})
    if set(statuses) - set(BACKENDS):
        raise ValueError("Unknown backend status")
    requested = REQUESTED[mode]
    resolved = {}
    for backend in BACKENDS:
        actual = statuses.get(backend)
        if backend not in requested:
            expected = "not_requested"
        elif not eligible[backend]:
            expected = "not_ready"
        else:
            if actual not in {"ok", "failed", "timeout"}:
                raise ValueError("Every eligible requested branch needs an execution status")
            expected = actual
        if actual is not None and actual != expected:
            raise ValueError("Branch execution status contradicts readiness or requested mode")
        resolved[backend] = expected
    successful = [backend for backend in requested if resolved[backend] == "ok"]
    failed = [backend for backend in requested if resolved[backend] in {"failed", "timeout"}]
    complete = all(eligible[backend] == scope for backend in requested)
    fully_eligible = set(scope)
    for backend in requested:
        fully_eligible.intersection_update(eligible[backend])
    if indexing_failed & fully_eligible:
        raise ValueError("Indexing failures must concern missing requested projections")
    status = 200 if successful or not scope else (503 if failed else 409)
    effective = "hybrid" if len(successful) == 2 else (successful[0] if successful else "none")
    notices = [f"{backend}_unavailable" for backend in failed]
    if not complete:
        notices.append("library_indexing_incomplete")
    if processing:
        notices.append("video_processing")
    if indexing_failed:
        notices.append("indexing_failed")
    return SearchOutcome(status, mode, effective, bool(failed), complete, len(scope),
        len(eligible["semantic"]), len(eligible["lexical"]), len(eligible["semantic"] | eligible["lexical"]),
        resolved["semantic"], resolved["lexical"], tuple(notices), len(processing), len(indexing_failed))
