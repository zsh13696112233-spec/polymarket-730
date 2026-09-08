import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


@pytest.mark.parametrize("existing", [False, True])
def test_position_quality_migration_preserves_existing_entries(tmp_path, existing):
    path = tmp_path / "positions.db"
    config = Config(str(Path("backend/alembic.ini").resolve()))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{path}"
    if existing:
        command.upgrade(config, "0044_whale_monitor_categories")
        with sqlite3.connect(path) as connection:
            columns = list(connection.execute("PRAGMA table_info(whale_entries)"))
            values = {}
            for _, name, kind, required, _default, _primary in columns:
                if name == "id":
                    values[name] = 7
                elif required:
                    values[name] = (
                        "2026-09-07 00:00:00"
                        if "DATE" in kind
                        else 0
                        if any(t in kind for t in ("NUMERIC", "INTEGER", "BOOLEAN"))
                        else "sample"
                    )
            values.update(
                proxy_wallet="0x" + "1" * 40,
                asset_id="asset-yes",
                condition_id="0x" + "a" * 64,
                status="holding",
                gross_buy_usdc=150000,
                gross_buy_size=300000,
                net_size=240000,
                net_ratio=80,
                avg_buy_price=0.5,
            )
            connection.execute(
                f"INSERT INTO whale_entries ({','.join(values)}) "
                f"VALUES ({','.join('?' for _ in values)})",
                tuple(values.values()),
            )
            previous = connection.execute("SELECT * FROM whale_entries WHERE id=7").fetchone()
    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        columns_after = {row[1] for row in connection.execute("PRAGMA table_info(whale_entries)")}
        assert {"discovery_source", "position_cost_usdc", "opposite_size"} <= columns_after
        if existing:
            row = connection.execute("SELECT * FROM whale_entries WHERE id=7").fetchone()
            assert tuple(row[column[1]] for column in columns) == previous
            assert row["discovery_source"] == "trades"
            assert row["position_cost_usdc"] is None
            assert row["opposite_size"] is None
