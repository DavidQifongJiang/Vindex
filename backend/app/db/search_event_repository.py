"""Durable event acceptance and idempotent projections; no Kafka dependency."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from hashlib import sha256
import json
import os
import re
from uuid import uuid4

from sqlalchemy import create_engine, select, update, delete, or_, text
from sqlalchemy.orm import sessionmaker

from app.db.models import Video, SearchDeletion
from app.db.search_event_models import (SearchEventContext as Context, SearchEventOutbox as Outbox,
    SearchEventBudget as Budget, SearchEventReceipt as Receipt, SearchEventProjection as Projection,
    SearchEventQuarantine as Quarantine, SearchEventCheckpoint as Checkpoint, SearchEventConsent as Consent)
from app.services.search_events import SearchEvent


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@lru_cache(maxsize=1)
def sessions():
    from app.db.session import get_engine
    url = os.getenv("SEARCH_EVENTS_DATABASE_URL") or get_engine().url
    schema = os.getenv("SEARCH_EVENTS_SCHEMA", "public")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
        raise ValueError("invalid analytics schema")
    engine = create_engine(url, pool_size=2, max_overflow=0, pool_timeout=.01,
        connect_args={"connect_timeout": 2, "options": f"-c search_path={schema} -c statement_timeout=1000 -c lock_timeout=100"})
    return sessionmaker(engine, expire_on_commit=False)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def budget(db):
    return db.execute(select(Budget).where(Budget.name == "outbox").with_for_update()).scalar_one()


def add_outbox(db, event, fingerprint=None):
    data = encoded(event)
    if len(data) > 16384:
        raise ValueError("event_too_large")
    db.add(Outbox(event_id=event["event_id"], search_id=event["search_id"], payload=event,
        fingerprint=fingerprint or sha256(data).hexdigest(), size=len(data), created_at=utcnow()))
    db.flush()
    # Reserve bytes last: the shared counter lock is held only until commit.
    reserved = db.execute(update(Budget).where(Budget.name == "outbox",
        Budget.pending_bytes + len(data) <= int(os.getenv("SEARCH_EVENTS_OUTBOX_BYTES", "1073741824")))
        .values(pending_bytes=Budget.pending_bytes + len(data)))
    if reserved.rowcount != 1:
        raise ValueError("outbox_full")


def capture_search(owner, query, event, results, share):
    refs = [{k: hit[k] for k in ("video_id", "revision_id", "passage_id")}
            for hit in results]
    with sessions()() as db, db.begin():
        if db.bind.dialect.name == "postgresql":
            data = encoded(event)
            if len(data) > 16384:
                raise ValueError("event_too_large")
            # One round trip, preserving Context -> Outbox -> Budget lock order.
            # Each CTE consumes RETURNING from its predecessor. A failed final
            # reservation MUST raise inside this transaction to undo both inserts.
            reserved = db.scalar(text('''
                WITH context_row AS (
                    INSERT INTO search_event_contexts
                        (search_id, owner_id, query, shared, results, completion, expires_at, interaction_count)
                    VALUES (:search_id, :owner, :query, :shared, CAST(:refs AS json),
                        CAST(:payload AS json), :expires, 0)
                    RETURNING search_id
                ), outbox_row AS (
                    INSERT INTO search_event_outbox
                        (event_id, search_id, payload, fingerprint, size, created_at)
                    SELECT :event_id, search_id, CAST(:payload AS json), :fingerprint, :size, :created
                    FROM context_row RETURNING size
                ), reservation AS (
                    UPDATE search_event_budget AS budget
                    SET pending_bytes = budget.pending_bytes + outbox_row.size
                    FROM outbox_row WHERE budget.name = 'outbox'
                        AND budget.pending_bytes + outbox_row.size <= :capacity
                    RETURNING budget.name
                ) SELECT count(*) FROM reservation
            '''), {"search_id": event["search_id"], "owner": owner, "query": query if share else None,
                "shared": int(share), "refs": encoded(refs).decode(), "payload": data.decode(),
                "expires": utcnow()+timedelta(days=7), "event_id": event["event_id"],
                "fingerprint": sha256(data).hexdigest(), "size": len(data), "created": utcnow(),
                "capacity": int(os.getenv("SEARCH_EVENTS_OUTBOX_BYTES", "1073741824"))})
            if reserved != 1:
                raise ValueError("outbox_full")
            return
        db.add(Context(search_id=event["search_id"], owner_id=owner, query=query if share else None,
            shared=int(share), results=refs, completion=event, expires_at=utcnow()+timedelta(days=7),
            interaction_count=0))
        add_outbox(db, event)


def accept_interaction(owner, search_id, interaction):
    payload = interaction.model_dump()
    fingerprint = sha256(encoded(payload)).hexdigest()
    with sessions()() as db, db.begin():
        context = db.execute(select(Context).where(Context.search_id == search_id).with_for_update()).scalar_one_or_none()
        if context is None or context.owner_id != owner or context.expires_at <= utcnow():
            raise PermissionError("search_context_unavailable")
        existing = db.get(Outbox, interaction.event_id)
        if existing:
            if existing.search_id != search_id or existing.fingerprint != fingerprint:
                raise ValueError("event_id_conflict")
            return
        if context.interaction_count >= 100:
            raise ValueError("interaction_limit")
        event = dict(context.completion, **payload)
        event["occurred_at"] = event["recorded_at"] = datetime.now(timezone.utc).isoformat()
        SearchEvent.model_validate(event)
        context.interaction_count += 1
        add_outbox(db, event, fingerprint)


def withdraw(owner, search_id):
    with sessions()() as db, db.begin():
        context = db.execute(select(Context).where(Context.search_id == search_id).with_for_update()).scalar_one_or_none()
        if context is None or context.owner_id != owner:
            raise PermissionError("search_context_unavailable")
        context.shared, context.query = 0, None


def withdraw_all(owner):
    with sessions()() as db, db.begin():
        if db.bind.dialect.name == "postgresql":
            key = int.from_bytes(sha256(("consent:"+owner).encode()).digest()[:8], "big", signed=True)
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        consent = db.get(Consent, owner)
        if consent:
            consent.withdrawn_before = utcnow()
        else:
            db.add(Consent(owner_id=owner, withdrawn_before=utcnow()))
        db.execute(update(Context).where(Context.owner_id == owner).values(query=None, shared=0))


def claim_batch(limit=100):
    now, token = utcnow(), str(uuid4())
    with sessions()() as db, db.begin():
        rows = db.execute(select(Outbox).where(Outbox.published_at.is_(None),
            Outbox.created_at > now-timedelta(days=7),
            or_(Outbox.lease_until.is_(None), Outbox.lease_until < now))
            .order_by(Outbox.created_at).limit(limit).with_for_update(skip_locked=True)).scalars().all()
        for row in rows:
            row.token, row.lease_until = token, now+timedelta(seconds=30)
        return [(r.event_id, r.search_id, r.payload, token) for r in rows]


def mark_published(event_id, token):
    mark_published_batch([(event_id, token)])


def mark_published_batch(deliveries):
    claims = dict(deliveries)
    if not claims:
        return
    with sessions()() as db, db.begin():
        sizes = db.execute(update(Outbox).where(or_(*[
            (Outbox.event_id == identity) & (Outbox.token == token) for identity, token in claims.items()]),
            Outbox.published_at.is_(None), Outbox.lease_until > utcnow())
            .values(published_at=utcnow()).returning(Outbox.size)).scalars().all()
        db.execute(update(Budget).where(Budget.name == "outbox")
            .values(pending_bytes=Budget.pending_bytes-sum(sizes)))


def apply_event(db, generation, event, batch=None):
    """Caller transaction covers receipt and projection; serialize same search."""
    event = SearchEvent.model_validate(event).model_dump()
    if batch is None and db.bind.dialect.name == "postgresql":
        key = int.from_bytes(sha256((generation+event["search_id"]).encode()).digest()[:8], "big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    seen = event["event_id"] in batch[0] if batch is not None else db.get(Receipt, (generation, event["event_id"]))
    if seen:
        return False
    if batch is not None:
        batch[0].add(event["event_id"])
    db.add(Receipt(generation=generation, event_id=event["event_id"], created_at=utcnow()))
    row = batch[1].get(event["search_id"]) if batch is not None else db.get(Projection, (generation, event["search_id"]))
    state = dict(row.state) if row else {"viewed": False, "opened": False, "feedback": None}
    kind = event["event_type"]
    # Completion is authoritative; interactions carry attribution for out-of-order joins.
    if kind == "search.completed" or "completion" not in state:
        state["completion"] = {k: v for k, v in event.items() if k not in {"rank", "ranks", "feedback"}}
    if kind == "search.completed":
        state["completed"] = True
    elif kind == "search.results_viewed":
        state["viewed"] = True
    elif kind == "search.result_opened":
        state["opened"] = True
    elif kind == "search.feedback_submitted":
        order = (event["recorded_at"], event["event_id"])
        if order > tuple(state.get("feedback_order", ("", ""))):
            state["feedback"], state["feedback_order"] = event["feedback"], list(order)
    state["needs_review"] = state["feedback"] == "not_helpful" or (
        state.get("completed", False) and state["completion"]["status"] == 200 and
        state["completion"]["result_count"] == 0 and state["completion"]["total_videos"] > 0 and
        state["completion"]["coverage_complete"] and not state["completion"]["degraded"])
    if row:
        row.state, row.updated_at = state, utcnow()
    else:
        row = Projection(generation=generation, search_id=event["search_id"], state=state, updated_at=utcnow())
        db.add(row)
        if batch is not None:
            batch[1][event["search_id"]] = row
    return True


def consume_record(generation, topic, partition, offset, raw):
    return consume_batch(generation, [(topic, partition, offset, raw)])[0]


def consume_batch(generation, records):
    """One bounded transaction; caller commits Kafka offsets only after return."""
    if not records or len(records) > 50:
        raise ValueError("batch_size_out_of_bounds")
    parsed, offsets = [], {}
    for topic, partition, offset, raw in records:
        key = (topic, partition)
        if key in offsets and offset <= offsets[key]:
            raise ValueError("unordered_partition_batch")
        offsets[key] = offset
        try:
            if len(raw) > 16384:
                raise ValueError("oversized")
            event = SearchEvent.model_validate_json(raw).model_dump()
        except (ValueError, TypeError):
            event = None
        parsed.append((topic, partition, offset, raw, event))
    with sessions()() as db, db.begin():
        for (topic, partition), offset in sorted(offsets.items()):
            advance_checkpoint(db, generation, topic, partition, offset)
        valid = [item[4] for item in parsed if item[4] is not None]
        identities = {e["event_id"] for e in valid}
        searches = {e["search_id"] for e in valid}
        if searches and db.bind.dialect.name == "postgresql":
            keys = sorted({int.from_bytes(sha256((generation+s).encode()).digest()[:8], "big", signed=True) for s in searches})
            db.execute(text("SELECT pg_advisory_xact_lock(key) FROM unnest(CAST(:keys AS bigint[])) AS locks(key) ORDER BY key"), {"keys": keys})
        receipts = set(db.scalars(select(Receipt.event_id).where(Receipt.generation == generation, Receipt.event_id.in_(identities))))
        projections = {r.search_id: r for r in db.scalars(select(Projection).where(Projection.generation == generation, Projection.search_id.in_(searches)))}
        outcomes = []
        with db.no_autoflush:
            for topic, partition, offset, raw, event in parsed:
                if event is None:
                    key = (generation, topic, partition, offset)
                    if not db.get(Quarantine, key):
                        db.add(Quarantine(generation=generation, topic=topic, partition=partition, offset=offset,
                            digest=sha256(raw if raw is not None else b"<null>").hexdigest(), error="invalid_event", created_at=utcnow()))
                    outcomes.append("quarantined")
                else:
                    outcomes.append("applied" if apply_event(db, generation, event, (receipts, projections)) else "duplicate")
    return outcomes


def advance_checkpoint(db, generation, topic, partition, offset):
    checkpoint = db.execute(select(Checkpoint).where(Checkpoint.generation == generation,
        Checkpoint.topic == topic, Checkpoint.partition == partition).with_for_update()).scalar_one_or_none()
    if checkpoint:
        checkpoint.next_offset = max(checkpoint.next_offset, offset+1)
    else:
        db.add(Checkpoint(generation=generation, topic=topic, partition=partition, next_offset=offset+1))


def starting_offset(generation, topic, partition, committed, low, high):
    with sessions()() as db, db.begin():
        checkpoint = db.get(Checkpoint, (generation, topic, partition))
        if checkpoint:
            if committed < 0 and checkpoint.next_offset == low:
                # No gap: first assignment crashed before commit, or fully processed
                # history ends exactly where retained records begin.
                return low
            if committed < 0 or committed < low or committed > checkpoint.next_offset or checkpoint.next_offset > high:
                raise ValueError("consumer_offset_history_lost")
            return committed
        if committed >= 0:
            raise ValueError("projection_history_missing")
        db.add(Checkpoint(generation=generation, topic=topic, partition=partition, next_offset=low))
        return low


def context_available(db, context):
    if not context.shared or context.expires_at <= utcnow():
        return False
    consent = db.get(Consent, context.owner_id)
    if consent and datetime.fromisoformat(context.completion["occurred_at"]).replace(tzinfo=None) <= consent.withdrawn_before:
        return False
    for ref in context.results:
        video = db.get(Video, ref["video_id"])
        if (not video or video.owner_user_id != context.owner_id or video.status == "deleting"
                or video.current_search_revision_id != ref["revision_id"] or db.get(SearchDeletion, ref["video_id"])):
            return False
    return True


def dashboard(cursor=""):
    with sessions()() as db:
        postgres = db.bind.dialect.name == "postgresql"
        rows = [] if postgres else db.execute(select(Projection).where(Projection.generation == "analytics-v1",
            Projection.updated_at > utcnow()-timedelta(days=7))).scalars().all()
        modes, review = {}, []
        latest_update = None
        if postgres:
            # Aggregate in Postgres, not by materializing seven days of event JSON.
            aggregates = db.execute(text("""
                SELECT (state->'completion'->>'traffic_class') || ':' ||
                       (state->'completion'->>'requested_mode') || ':' ||
                       (state->'completion'->>'configuration') AS key,
                       count(*) AS searches,
                       count(*) FILTER (WHERE (state->'completion'->>'status')::int >= 400) AS errors,
                       count(*) FILTER (WHERE (state->'completion'->>'status')::int = 200 AND (state->'completion'->>'result_count')::int = 0) AS zero_results,
                       count(*) FILTER (WHERE (state->'completion'->>'degraded')::boolean) AS degraded,
                       count(*) FILTER (WHERE (state->>'viewed')::boolean) AS viewed,
                       count(*) FILTER (WHERE (state->>'viewed')::boolean AND (state->>'opened')::boolean) AS opened,
                       count(*) FILTER (WHERE state->>'feedback' = 'helpful') AS helpful,
                       count(*) FILTER (WHERE state->>'feedback' = 'not_helpful') AS not_helpful,
                       percentile_cont(.5) WITHIN GROUP (ORDER BY (state->'completion'->>'elapsed_ms')::float) AS p50_ms,
                       percentile_cont(.95) WITHIN GROUP (ORDER BY (state->'completion'->>'elapsed_ms')::float) AS p95_ms,
                       max(updated_at) AS latest
                FROM search_event_projections
                WHERE generation = 'analytics-v1' AND (state->>'completed')::boolean
                  AND (state->'completion'->>'occurred_at')::timestamptz > :cutoff
                GROUP BY 1
            """), {"cutoff": datetime.now(timezone.utc)-timedelta(days=7)}).mappings()
            for record in aggregates:
                item = dict(record)
                key, latest = item.pop("key"), item.pop("latest")
                modes[key] = item
                if latest and (latest_update is None or latest > latest_update):
                    latest_update = latest
        for row in rows:
            s, e = row.state, row.state["completion"]
            if not s.get("completed") or datetime.fromisoformat(e["occurred_at"]).replace(tzinfo=None) < utcnow()-timedelta(days=7):
                continue
            key = e["traffic_class"]+":"+e["requested_mode"]+":"+e["configuration"]
            bucket = modes.setdefault(key, dict(searches=0, errors=0, zero_results=0, degraded=0,
                viewed=0, opened=0, helpful=0, not_helpful=0, latencies=[]))
            bucket["searches"] += 1
            bucket["errors"] += e["status"] >= 400
            bucket["zero_results"] += e["status"] == 200 and e["result_count"] == 0
            bucket["degraded"] += e["degraded"]
            bucket["viewed"] += s["viewed"]
            bucket["opened"] += s["viewed"] and s["opened"]
            if s["feedback"]:
                bucket[s["feedback"]] += 1
            bucket["latencies"].append(e["elapsed_ms"])
        for bucket in modes.values():
            if "latencies" not in bucket:
                continue
            values = sorted(bucket.pop("latencies"))
            bucket["p50_ms"] = values[(len(values)-1)//2]
            bucket["p95_ms"] = values[min(len(values)-1, int(len(values)*.95))]
        candidates = db.execute(select(Projection).join(Context, Projection.search_id == Context.search_id)
            .where(Projection.generation == "review-v1", Projection.state["needs_review"].as_boolean() == True,
                Context.shared == 1, Context.expires_at > utcnow(), Projection.search_id > cursor)
            .order_by(Projection.search_id).limit(50)).scalars().all()
        for row in candidates:
            context = db.get(Context, row.search_id)
            if row.state.get("needs_review") and context and context_available(db, context):
                review.append(dict(search_id=row.search_id, query=context.query,
                    mode=context.completion["requested_mode"], results=context.results,
                    reason="negative_feedback" if row.state["feedback"] == "not_helpful" else "zero_results"))
        counter = db.get(Budget, "outbox")
        from sqlalchemy import func
        quarantine = db.scalar(select(func.count()).select_from(Quarantine))
        pending = db.scalar(select(func.count()).select_from(Outbox).where(Outbox.published_at.is_(None)))
        return dict(modes=modes, review=review, retention_days=7, pending_events=pending,
                    pending_bytes=counter.pending_bytes if counter else 0,
                    expired_events=counter.expired_events if counter else 0, quarantined=quarantine,
                    review_next_cursor=candidates[-1].search_id if len(candidates) == 50 else None,
                    latest_update=latest_update.isoformat()+"Z" if latest_update else max((r.updated_at.isoformat()+"Z" for r in rows), default=None))


def maintenance():
    now = utcnow()
    while True:
        with sessions()() as db, db.begin():
            expired = db.execute(select(Outbox).where(Outbox.created_at < now-timedelta(days=7))
                .limit(1000).with_for_update(skip_locked=True)).scalars().all()
            pending_bytes, expired_events = 0, 0
            for row in expired:
                if row.published_at is None:
                    pending_bytes += row.size
                    expired_events += 1
                db.delete(row)
            db.flush()
            db.execute(update(Budget).where(Budget.name == "outbox").values(
                pending_bytes=Budget.pending_bytes-pending_bytes, expired_events=Budget.expired_events+expired_events))
        if len(expired) < 1000:
            break
    with sessions()() as db, db.begin():
        # Keep published interaction IDs for context lifetime so retries remain idempotent.
        db.execute(delete(Context).where(Context.expires_at <= now))
        db.execute(delete(Projection).where(Projection.updated_at < now-timedelta(days=7)))
    cursor = ""
    while True:
        with sessions()() as db, db.begin():
            contexts = db.execute(select(Context).where(Context.shared == 1, Context.search_id > cursor)
                .order_by(Context.search_id).limit(100)).scalars().all()
            if not contexts:
                break
            for context in contexts:
                if not context_available(db, context):
                    context.query, context.shared = None, 0
            cursor = contexts[-1].search_id


def retire_shadow(generation):
    if not re.fullmatch(r"replay-[0-9a-f-]{36}", generation):
        raise ValueError("only inactive shadow generations may be retired")
    with sessions()() as db, db.begin():
        for model in (Projection, Receipt, Quarantine, Checkpoint):
            db.execute(delete(model).where(model.generation == generation))
