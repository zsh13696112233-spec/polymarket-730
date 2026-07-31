"""Prevent a trade fill from being attached to multiple position events.

Revision ID: 0002_global_fill_fingerprint
Revises: 0001_initial
Create Date: 2026-07-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_global_fill_fingerprint"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Earlier builds allowed the same deterministic fill fingerprint once per
    # event. Keep its first assignment before enforcing global consumption.
    op.execute(
        """
        DELETE FROM position_event_fills
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM position_event_fills
            GROUP BY fingerprint
        )
        """
    )
    with op.batch_alter_table("position_event_fills") as batch_op:
        batch_op.drop_constraint("uq_event_fill_fingerprint", type_="unique")
        batch_op.create_unique_constraint(
            "uq_position_event_fill_fingerprint",
            ["fingerprint"],
        )


def downgrade() -> None:
    with op.batch_alter_table("position_event_fills") as batch_op:
        batch_op.drop_constraint(
            "uq_position_event_fill_fingerprint",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_event_fill_fingerprint",
            ["event_id", "fingerprint"],
        )
