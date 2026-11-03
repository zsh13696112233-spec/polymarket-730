from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.models import WhaleSettings
from backend.whale_config import source_amount_tiers


@pytest.mark.parametrize("previous", [None, "0049_auto_take_profit", "0050_source_amount_tiers"])
def test_source_tiers_storage_upgrade_and_model(tmp_path, previous):
    path = tmp_path / "source-tiers.db"
    config = Config(str(Path("backend/alembic.ini").resolve()))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{path}"
    tiers = [{"min_source_amount_usdc": "1000000", "follow_amount_usdc": "35"}]
    if previous:
        command.upgrade(config, previous)
    if previous == "0050_source_amount_tiers":
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE whale_settings SET new_account_auto_follow_source_tiers_enabled = 1, "
                "new_account_auto_follow_source_tiers_json = ? WHERE id = 1",
                (json.dumps(tiers),),
            )
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as session:
            row = session.get(WhaleSettings, 1)
            assert row.new_account_auto_follow_source_tiers_enabled == (
                previous == "0050_source_amount_tiers"
            )
            assert json.loads(row.new_account_auto_follow_source_tiers_json) == (
                tiers if previous == "0050_source_amount_tiers" else []
            )
            values = {
                column.name: getattr(row, column.name) for column in WhaleSettings.__table__.columns
            }
            parsed = source_amount_tiers(values, "new_account")
            if parsed:
                assert parsed[0].follow_amount_usdc == Decimal("35")
    finally:
        engine.dispose()
