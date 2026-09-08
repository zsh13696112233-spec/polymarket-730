import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

CUTOFF = "2026-09-07 12:00:00.000000"
EARLIER = "2026-09-07 11:00:00.000000"
LATER = "2026-09-07 12:05:00.000000"
CONDITION = "0x" + "a" * 64


def insert_required(connection, table, **overrides):
    values = {}
    for _, name, kind, required, default, primary in connection.execute(
        f"PRAGMA table_info({table})"
    ):
        if required and default is None and not primary:
            values[name] = (
                EARLIER
                if "DATE" in kind
                else (
                    0
                    if any(part in kind for part in ("NUMERIC", "INTEGER", "BOOLEAN"))
                    else "sample"
                )
            )
    values.update(overrides)
    connection.execute(
        f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
        tuple(values.values()),
    )


@pytest.mark.parametrize("existing", [False, True])
def test_backfill_eligibility_upgrade_recovers_without_replaying_buys(tmp_path, existing):
    path = tmp_path / "eligibility.db"
    config = Config(str(Path("backend/alembic.ini").resolve()))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{path}"
    wallets = ["0x" + character * 40 for character in "1234"]
    if existing:
        command.upgrade(config, "0046_whale_market_scan_progress")
        with sqlite3.connect(path) as connection:
            connection.execute(
                "INSERT INTO whale_market_scan_states(condition_id,auto_follow_after) VALUES (?,?)",
                (CONDITION, CUTOFF),
            )
            for index in (0, 2, 3):
                insert_required(
                    connection,
                    "whale_trades",
                    proxy_wallet=wallets[index],
                    asset_id="yes",
                    condition_id=CONDITION,
                    fingerprint=f"history-{index}",
                    side="BUY",
                    timestamp=EARLIER,
                    imported_at=CUTOFF if index != 3 else EARLIER,
                )
            # A recorded post-cutoff buy must not be replayed merely by upgrading.
            insert_required(
                connection,
                "whale_trades",
                proxy_wallet=wallets[0],
                asset_id="yes",
                condition_id=CONDITION,
                fingerprint="later",
                side="BUY",
                timestamp=LATER,
                imported_at=LATER,
            )
            for index in (1, 2):
                insert_required(
                    connection,
                    "whale_entries",
                    id=index,
                    proxy_wallet=wallets[index],
                    asset_id="yes",
                    condition_id=CONDITION,
                    last_buy_at=LATER if index == 1 else EARLIER,
                    status="holding",
                )
            # A different wallet's valid core signal was suppressed by 0046.
            insert_required(
                connection,
                "whale_entry_rule_states",
                entry_id=1,
                rule_type="new_account",
                first_triggered_at=EARLIER,
            )
            # An existing terminal decision must remain terminal.
            insert_required(
                connection,
                "whale_auto_follow_decisions",
                entry_id=2,
                proxy_wallet=wallets[2],
                asset_id="yes",
                condition_id=CONDITION,
                category="other",
                status="skipped",
            )
            previous = {
                table: connection.execute(f"SELECT * FROM {table}").fetchall()
                for table in (
                    "whale_entries",
                    "whale_entry_rule_states",
                    "whale_auto_follow_decisions",
                )
            }
    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT proxy_wallet, auto_follow_after, awaiting_new_buy "
            "FROM whale_backfill_signal_states"
        ).fetchall()
        if not existing:
            assert rows == []
        else:
            assert {row[0]: row[1:] for row in rows} == {
                wallets[0]: (LATER, 1),
                wallets[1]: (LATER, 1),
                wallets[2]: (CUTOFF, 0),
            }
            for table, before in previous.items():
                assert connection.execute(f"SELECT * FROM {table}").fetchall() == before
