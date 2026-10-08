"""Local collection subscription and public cache; preserve all existing IDs."""

import sqlalchemy as sa
from alembic import op

revision = "0052_collection_sync"
down_revision = "0051_weekly_auto_follow_report"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("whale_settings") as batch:
        batch.alter_column("monitor_categories_json", server_default='["sports","esports"]')
    op.create_table(
        "collection_sync_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("host", sa.String(255), nullable=False, server_default=""),
        sa.Column("port", sa.Integer(), nullable=False, server_default="8731"),
        sa.Column("subscription_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_id", sa.String(64), nullable=True),
        sa.Column("categories_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("rule_batch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_table(
        "collection_cache",
        sa.Column("condition_id", sa.String(66), primary_key=True),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("collection_cache")
    op.drop_table("collection_sync_state")
