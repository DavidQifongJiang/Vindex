"""Optional durable search interaction stream."""
from alembic import op
import sqlalchemy as sa

revision = "20260918_0007"
down_revision = "20260916_0006"
branch_labels = depends_on = None


def upgrade():
    op.create_table("search_event_consents", sa.Column("owner_id", sa.String(), primary_key=True),
        sa.Column("withdrawn_before", sa.DateTime(), nullable=False))
    op.create_table("search_event_checkpoints", sa.Column("generation", sa.String(), primary_key=True),
        sa.Column("topic", sa.String(), primary_key=True), sa.Column("partition", sa.Integer(), primary_key=True),
        sa.Column("next_offset", sa.Integer(), nullable=False))
    op.create_table("search_event_contexts", sa.Column("search_id", sa.String(), primary_key=True),
        sa.Column("owner_id", sa.String(), nullable=False), sa.Column("query", sa.Text()),
        sa.Column("shared", sa.Integer(), nullable=False), sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("completion", sa.JSON(), nullable=False), sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("interaction_count", sa.Integer(), nullable=False))
    op.create_index("ix_search_event_contexts_owner_id", "search_event_contexts", ["owner_id"])
    op.create_index("ix_search_event_contexts_expires_at", "search_event_contexts", ["expires_at"])
    op.create_table("search_event_outbox", sa.Column("event_id", sa.String(), primary_key=True),
        sa.Column("search_id", sa.String(), nullable=False), sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(), nullable=False), sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("published_at", sa.DateTime()),
        sa.Column("token", sa.String()), sa.Column("lease_until", sa.DateTime()))
    for column in ("search_id", "created_at", "published_at"):
        op.create_index("ix_search_event_outbox_"+column, "search_event_outbox", [column])
    table = op.create_table("search_event_budget", sa.Column("name", sa.String(), primary_key=True),
        sa.Column("pending_bytes", sa.Integer(), nullable=False), sa.Column("expired_events", sa.Integer(), nullable=False))
    op.bulk_insert(table, [{"name": "outbox", "pending_bytes": 0, "expired_events": 0}])
    op.create_table("search_event_receipts", sa.Column("generation", sa.String(), primary_key=True),
        sa.Column("event_id", sa.String(), primary_key=True), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_table("search_event_projections", sa.Column("generation", sa.String(), primary_key=True),
        sa.Column("search_id", sa.String(), primary_key=True), sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_index("ix_search_event_projections_updated_at", "search_event_projections", ["updated_at"])
    op.create_table("search_event_quarantine", sa.Column("generation", sa.String(), primary_key=True),
        sa.Column("topic", sa.String(), primary_key=True), sa.Column("partition", sa.Integer(), primary_key=True),
        sa.Column("offset", sa.Integer(), primary_key=True), sa.Column("digest", sa.String(), nullable=False),
        sa.Column("error", sa.String(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False))


def downgrade():
    for name in ("consents", "checkpoints", "quarantine", "projections", "receipts", "budget", "outbox", "contexts"):
        op.drop_table("search_event_"+name)
