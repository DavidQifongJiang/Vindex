"""Canonical search identities and immutable, engine-independent contracts.

These contracts do not grant authorization or persist index readiness. Callers
must obtain ownership/current revision/publication state from Postgres.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
from uuid import UUID, uuid5


PASSAGE_NAMESPACE = UUID("1a3feaf4-27c1-54d7-b16c-c053c7c465c0")
PROJECTION_NAMESPACE = uuid5(PASSAGE_NAMESPACE, "physical-projection-v1")
IDENTITY_FIELDS = ("video_id", "revision_id", "passage_id", "ordinal")


@dataclass(frozen=True)
class LibrarySearchQuery:
    query: str
    mode: str = "semantic"
    scope: str = "my_library"
    limit: int = 5

    def __post_init__(self):
        if not isinstance(self.query, str) or not 1 <= len(self.query.strip()) <= 500:
            raise ValueError("Search query must contain 1 to 500 characters")
        object.__setattr__(self, "query", self.query.strip())
        if self.mode not in {"semantic", "lexical", "hybrid"}:
            raise ValueError("Unknown search mode")
        if self.scope != "my_library":
            raise ValueError("Only My library is supported")
        if type(self.limit) is not int or not 1 <= self.limit <= 20:
            raise ValueError("Search result limit must be between 1 and 20")


def canonical_uuid(value):
    if not isinstance(value, (str, UUID)):
        raise ValueError("Identity must be a UUID string or UUID")
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Identity must be a valid UUID") from exc


def nonnegative_integer(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def passage_id(video_id, revision_id, ordinal):
    identity = [canonical_uuid(video_id), canonical_uuid(revision_id),
                nonnegative_integer(ordinal, "ordinal")]
    return str(uuid5(PASSAGE_NAMESPACE, json.dumps(identity, separators=(",", ":"))))


def physical_point_id(projection_id, logical_passage_id):
    """UUID accepted by both Qdrant point IDs and Elasticsearch document IDs."""
    identity = [canonical_uuid(projection_id), canonical_uuid(logical_passage_id)]
    return str(uuid5(PROJECTION_NAMESPACE, json.dumps(identity, separators=(",", ":"))))


def seconds_to_ms(value):
    # Reject strings/bools: legacy artifacts must contain JSON numbers, not values
    # that happen to coerce to numbers. Decimal(str(...)) fixes rounding semantics.
    if type(value) not in (int, float):
        raise ValueError("Timestamp must be a numeric number of seconds")
    try:
        seconds = Decimal(str(value))
        if not seconds.is_finite() or seconds < 0:
            raise ValueError("Timestamp must be finite and nonnegative")
        result = int((seconds * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, OverflowError) as exc:
        raise ValueError("Timestamp is outside the supported range") from exc
    if result > 2**63 - 1:
        raise ValueError("Timestamp exceeds signed 64-bit milliseconds")
    return result


@dataclass(frozen=True)
class Passage:
    video_id: str
    revision_id: str
    ordinal: int
    start_ms: int
    end_ms: int
    text: str

    def __post_init__(self):
        object.__setattr__(self, "video_id", canonical_uuid(self.video_id))
        object.__setattr__(self, "revision_id", canonical_uuid(self.revision_id))
        nonnegative_integer(self.ordinal, "ordinal")
        for name in ("start_ms", "end_ms"):
            value = nonnegative_integer(getattr(self, name), name)
            if value > 2**63 - 1:
                raise ValueError("Timestamp exceeds signed 64-bit milliseconds")
        if self.end_ms < self.start_ms:
            raise ValueError("Passage ends before it starts")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("Passage text must be nonempty")

    @property
    def passage_id(self):
        return passage_id(self.video_id, self.revision_id, self.ordinal)

    def as_hit(self):
        return {"video_id": self.video_id, "revision_id": self.revision_id,
                "passage_id": self.passage_id, "ordinal": self.ordinal,
                "start": self.start_ms / 1000, "end": self.end_ms / 1000, "text": self.text}

    @classmethod
    def from_hit(cls, hit):
        if not all(field in hit for field in IDENTITY_FIELDS):
            raise ValueError("Canonical search hit is missing identity fields")
        passage = cls(hit["video_id"], hit["revision_id"], hit["ordinal"],
                      seconds_to_ms(hit["start"]), seconds_to_ms(hit["end"]), hit["text"])
        if hit["start"] != passage.start_ms / 1000 or hit["end"] != passage.end_ms / 1000:
            raise ValueError("Canonical hits must use exact millisecond timestamps")
        if canonical_uuid(hit["passage_id"]) != passage.passage_id:
            raise ValueError("Passage ID does not match its video, revision and ordinal")
        return passage


@dataclass(frozen=True)
class TranscriptSnapshot:
    video_id: str
    revision_id: str
    manifest_sha256: str
    passages: tuple[Passage, ...]

    def __post_init__(self):
        object.__setattr__(self, "video_id", canonical_uuid(self.video_id))
        object.__setattr__(self, "revision_id", canonical_uuid(self.revision_id))
        if (not isinstance(self.manifest_sha256, str) or len(self.manifest_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.manifest_sha256)):
            raise ValueError("Manifest checksum must be a lowercase SHA-256 hex digest")
        object.__setattr__(self, "passages", tuple(self.passages))
        for index, passage in enumerate(self.passages):
            if not isinstance(passage, Passage) or (passage.video_id, passage.revision_id, passage.ordinal) != (
                    self.video_id, self.revision_id, index):
                raise ValueError("Snapshot passages must belong to this revision in ordinal order")

    @classmethod
    def from_manifest(cls, video_id, revision_id, manifest_bytes, *, expected_sha256=None):
        if not isinstance(manifest_bytes, bytes):
            raise ValueError("Manifest must be exact source bytes")
        checksum = sha256(manifest_bytes).hexdigest()
        if expected_sha256 is not None and checksum != expected_sha256:
            raise ValueError("Accepted manifest checksum mismatch")
        def reject_constant(_):
            raise ValueError("Manifest contains a non-finite JSON number")
        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Manifest contains a duplicate JSON key")
                result[key] = value
            return result
        segments = json.loads(manifest_bytes, parse_constant=reject_constant, object_pairs_hook=unique_keys)
        if not isinstance(segments, list):
            raise ValueError("Manifest must be an ordered list of passages")
        passages = []
        for ordinal, segment in enumerate(segments):
            if not isinstance(segment, dict) or not {"start", "end", "text"} <= segment.keys():
                raise ValueError("Manifest passage is missing required fields")
            # Validate source ordering before rounding, so a reversed sub-ms
            # interval cannot become an apparently valid zero-length passage.
            start, end = seconds_to_ms(segment["start"]), seconds_to_ms(segment["end"])
            if segment["end"] < segment["start"]:
                raise ValueError("Passage ends before it starts")
            passages.append(Passage(video_id, revision_id, ordinal, start, end, segment["text"]))
        return cls(video_id, revision_id, checksum, tuple(passages))


@dataclass(frozen=True)
class Projection:
    video_id: str
    revision_id: str
    projection_id: str
    backend: str
    generation: str
    state: str

    def __post_init__(self):
        for name in ("video_id", "revision_id", "projection_id"):
            object.__setattr__(self, name, canonical_uuid(getattr(self, name)))
        if self.backend not in {"semantic", "lexical"}:
            raise ValueError("Unknown search backend")
        if self.state not in {"pending", "indexing", "retrying", "ready", "failed", "obsolete"}:
            raise ValueError("Unknown projection state")
        if not isinstance(self.generation, str) or not self.generation.strip():
            raise ValueError("Generation must be nonempty")


def eligible_projections(projections, *, current_revisions, backend, generation):
    """Select from an ALREADY AUTHORIZED authoritative video/revision mapping.

    This is not an authorization lookup: the application must obtain the mapping
    from Postgres, fail closed on lookup failure, and revalidate returned hits.
    """
    if backend not in {"semantic", "lexical"}:
        raise ValueError("Unknown search backend")
    scope = {canonical_uuid(video): canonical_uuid(revision) for video, revision in current_revisions.items()}
    selected = {}
    for projection in projections:
        if (projection.backend == backend and projection.generation == generation
                and projection.state == "ready"
                and scope.get(projection.video_id) == projection.revision_id):
            previous = selected.get(projection.video_id)
            if previous is not None and previous != projection:
                raise ValueError("Multiple accepted projections for one video/backend/generation")
            selected[projection.video_id] = projection
    if len({item.projection_id for item in selected.values()}) != len(selected):
        raise ValueError("Projection IDs must be globally unique across videos")
    return tuple(selected[video] for video in sorted(selected))
