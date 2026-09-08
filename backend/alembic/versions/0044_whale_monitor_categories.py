"""Add shared market categories for whale discovery."""

import sqlalchemy as sa
from alembic import op

revision = "0044_whale_monitor_categories"
down_revision = "0043_wallet_position_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("whale_settings") as batch:
        batch.add_column(
            sa.Column(
                "monitor_categories_json",
                sa.Text(),
                nullable=False,
                server_default='["sports","esports","politics","crypto","science_tech","entertainment","other"]',
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("whale_settings") as batch:
        batch.drop_column("monitor_categories_json")
