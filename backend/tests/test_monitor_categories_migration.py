from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


@pytest.mark.parametrize("upgrade_existing", [False, True])
def test_monitor_categories_migration_defaults_and_preserves_settings(tmp_path, upgrade_existing):
    path = tmp_path / "migration.db"
    config = Config(str(Path("backend/alembic.ini").resolve()))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{path}"
    if upgrade_existing:
        command.upgrade(config, "0043_wallet_position_orders")
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE whale_settings SET registration_window_days = 5, "
                "new_account_auto_follow_categories_json = ? WHERE id = 1",
                ('["esports"]',),
            )
            previous = connection.execute("SELECT * FROM whale_settings").fetchone()
            columns = [row[1] for row in connection.execute("PRAGMA table_info(whale_settings)")]
    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM whale_settings WHERE id = 1").fetchone()
        assert set(json.loads(row["monitor_categories_json"])) == {
            "sports",
            "esports",
            "politics",
            "crypto",
            "science_tech",
            "entertainment",
            "other",
        }
        if upgrade_existing:
            assert tuple(row[column] for column in columns) == previous
