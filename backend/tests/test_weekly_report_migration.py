import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


@pytest.mark.parametrize("existing", [False, True])
def test_weekly_report_migration_preserves_delivery_history(tmp_path, existing):
    path = tmp_path / "reports.db"
    config = Config(str(Path("backend/alembic.ini").resolve()))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{path}"
    insert = """INSERT INTO whale_email_deliveries
        (notification_kind, condition_id, dedupe_key, rule_key, rules_json, entry_ids_json,
         recipient_email, market_title, wallet_label, subject, body_text,
         status, attempt_count, created_at)
        VALUES (?, 'weekly', ?, 'weekly', '[]', '[]', 'a@example.com',
                'report', 'report', 'subject', 'preserve body', 'sent', 1,
                '2026-09-13 22:00:00')"""
    if existing:
        command.upgrade(config, "0050_source_amount_tiers")
        with sqlite3.connect(path) as connection:
            connection.execute(insert, ("weekly_summary", "old"))
            before = connection.execute("SELECT * FROM whale_email_deliveries").fetchall()
    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        if existing:
            assert connection.execute("SELECT * FROM whale_email_deliveries").fetchall() == before
        connection.execute(insert, ("weekly_auto_follow_report", "new"))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, ("weekly_auto_follow_report", "new"))
    command.downgrade(config, "0050_source_amount_tiers")
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT notification_kind, body_text FROM whale_email_deliveries WHERE dedupe_key='new'"
        ).fetchone() == ("weekly_summary", "preserve body")
    command.upgrade(config, "head")
