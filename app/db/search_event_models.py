"""Analytics tables are separate from authoritative processing state."""
from sqlalchemy import Column, String, Integer, DateTime, JSON, Text
from app.db.models import Base


class SearchEventContext(Base):
    __tablename__ = "search_event_contexts"
    search_id = Column(String, primary_key=True)
    owner_id = Column(String, nullable=False, index=True)
    query = Column(Text)
    shared = Column(Integer, nullable=False)
    results = Column(JSON, nullable=False)
    completion = Column(JSON, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    interaction_count = Column(Integer, nullable=False, default=0)


class SearchEventOutbox(Base):
    __tablename__ = "search_event_outbox"
    event_id = Column(String, primary_key=True)
    search_id = Column(String, nullable=False, index=True)
    payload = Column(JSON, nullable=False)
    fingerprint = Column(String, nullable=False)
    size = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, index=True)
    published_at = Column(DateTime, index=True)
    token = Column(String)
    lease_until = Column(DateTime)


class SearchEventBudget(Base):
    __tablename__ = "search_event_budget"
    name = Column(String, primary_key=True)
    pending_bytes = Column(Integer, nullable=False, default=0)
    expired_events = Column(Integer, nullable=False, default=0)


class SearchEventReceipt(Base):
    __tablename__ = "search_event_receipts"
    generation = Column(String, primary_key=True)
    event_id = Column(String, primary_key=True)
    created_at = Column(DateTime, nullable=False)


class SearchEventProjection(Base):
    __tablename__ = "search_event_projections"
    generation = Column(String, primary_key=True)
    search_id = Column(String, primary_key=True)
    state = Column(JSON, nullable=False)
    updated_at = Column(DateTime, nullable=False, index=True)


class SearchEventQuarantine(Base):
    __tablename__ = "search_event_quarantine"
    generation = Column(String, primary_key=True)
    topic = Column(String, primary_key=True)
    partition = Column(Integer, primary_key=True)
    offset = Column(Integer, primary_key=True)
    # Raw bytes may contain invalid/private input: do not persist them here.
    digest = Column(String, nullable=False)
    error = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)


class SearchEventCheckpoint(Base):
    __tablename__ = "search_event_checkpoints"
    generation = Column(String, primary_key=True)
    topic = Column(String, primary_key=True)
    partition = Column(Integer, primary_key=True)
    next_offset = Column(Integer, nullable=False)


class SearchEventConsent(Base):
    __tablename__ = "search_event_consents"
    owner_id = Column(String, primary_key=True)
    withdrawn_before = Column(DateTime, nullable=False)
