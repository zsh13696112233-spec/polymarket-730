from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


@pytest.mark.parametrize("upgrade_existing", [False, True])
def test_dual_match_amount_migration(tmp_path, upgrade_existing):
    path = tmp_path / "migration.db"
    config = Config(str(Path("backend/alembic.ini").resolve()))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{path}"
    if upgrade_existing:
        command.upgrade(config, "0047_backfill_signal_eligibility")
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE whale_settings SET max_follow_amount_usdc = 300 WHERE id = 1"
            )
            previous = connection.execute("SELECT * FROM whale_settings").fetchone()
            columns = [row[1] for row in connection.execute("PRAGMA table_info(whale_settings)")]
    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM whale_settings WHERE id = 1").fetchone()
        assert row["dual_match_auto_follow_amount_usdc"] is None
        if upgrade_existing:
            assert tuple(row[column] for column in columns) == previous
        connection.execute("UPDATE whale_settings SET dual_match_auto_follow_amount_usdc = 25")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE whale_settings SET dual_match_auto_follow_amount_usdc = 0")
