"""Remove events caused only by SQLite numeric round-trip noise.

Revision ID: 0003_remove_phantom_zero_events
Revises: 0002_global_fill_fingerprint
Create Date: 2026-07-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_remove_phantom_zero_events"
down_revision: str | None = "0002_global_fill_fingerprint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Alembic's SQLite connection does not rely on foreign-key cascade being enabled,
    # so remove child rows explicitly before deleting only sub-nanoshare events.
    op.execute(
        """
        DELETE FROM position_event_fills
        WHERE event_id IN (
            SELECT id
            FROM position_events
            WHERE ABS(delta_size) <= 0.000000001
        )
        """
    )
    op.execute(
        """
        DELETE FROM position_events
        WHERE ABS(delta_size) <= 0.000000001
        """
    )
    op.execute(
        """
        DELETE FROM position_change_candidates
        WHERE ABS(latest_size - start_size) <= 0.000000001
        """
    )


def downgrade() -> None:
    # Data-only cleanup cannot reconstruct invalid historical events.
    pass
