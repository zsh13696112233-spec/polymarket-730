import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


@pytest.mark.parametrize("existing", [False, True])
def test_market_scan_progress_migration_preserves_settings(tmp_path, existing):
    path = tmp_path / "backfill.db"
    config = Config(str(Path("backend/alembic.ini").resolve()))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{path}"
    if existing:
        command.upgrade(config, "0045_whale_position_quality")
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE whale_settings SET last_scan_error='keep me' WHERE id=1")
            previous = connection.execute("SELECT * FROM whale_settings").fetchall()
    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        if existing:
            assert connection.execute("SELECT * FROM whale_settings").fetchall() == previous
        connection.execute(
            "INSERT INTO whale_market_scan_states(condition_id,pending_ranges_json) VALUES (?,?)",
            ("market", "[[1,2,0]]"),
        )
        connection.execute(
            "INSERT INTO whale_market_scan_pages(condition_id,payload_json) VALUES (?,?)",
            ("market", "[]"),
        )
    # A new connection represents process restart, including durable pending work.
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT pending_ranges_json FROM whale_market_scan_states WHERE condition_id='market'"
        ).fetchone() == ("[[1,2,0]]",)
        assert connection.execute("SELECT COUNT(*) FROM whale_market_scan_pages").fetchone() == (1,)
