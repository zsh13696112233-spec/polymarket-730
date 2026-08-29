from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig


def test_fixed_wallet_apis_are_retired_and_chain_runtime_remains(app_client_factory):
    client, _ = app_client_factory([[]])

    assert client.get("/api/execution-account").status_code == 200
    assert client.get("/api/execution-account").json() is None
    assert client.get("/api/whales/settings").status_code == 200
    assert client.get("/api/copy-trading/overview").status_code == 404
    assert client.get("/api/wallets").status_code == 404
    assert client.get("/api/settings").status_code == 404
    openapi_paths = client.get("/openapi.json").json()["paths"]
    assert "/api/execution-account" in openapi_paths
    assert all("copy-trading" not in path for path in openapi_paths)
    assert all(not path.startswith("/api/wallets") for path in openapi_paths)
    assert not hasattr(client.app.state, "copy_engine")
    assert not hasattr(client.app.state, "monitor")
    assert hasattr(client.app.state, "whale_executor")
    assert hasattr(client.app.state, "whale_scanner")


def test_retirement_migration_preserves_execution_account_and_whale_tables(tmp_path: Path):
    database_path = tmp_path / "retirement.db"
    config = AlembicConfig(str(Path("backend/alembic.ini").resolve()))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{database_path}"
    command.upgrade(config, "0029_wallet_trade_reconciliation_index")

    with sqlite3.connect(database_path) as connection:
        now = "2026-08-26 00:00:00"
        address = "0x" + "1" * 40
        cursor = connection.execute(
            """INSERT INTO watched_wallets
               (address,proxy_wallet,label,wallet_role,enabled,baseline_established,status,
                consecutive_failures,created_at,updated_at)
               VALUES (?,?,?,'self',1,1,'ok',0,?,?)""",
            (address, address, "执行钱包", now, now),
        )
        connection.execute(
            """INSERT INTO execution_accounts
               (id,wallet_id,signer_address,funder_address,signature_type,status,budget_usdc,
                cash_reserve_usdc,max_total_exposure_usdc,daily_buy_limit_usdc,
                daily_loss_limit_usdc,auto_redeem,created_at,updated_at)
               VALUES (1,?,?,?,3,'ready',400,240,160,80,40,1,?,?)""",
            (cursor.lastrowid, address, address, now, now),
        )
        whale_settings_before = connection.execute(
            "SELECT COUNT(*) FROM whale_settings"
        ).fetchone()[0]
        connection.commit()

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        account = connection.execute(
            "SELECT signer_address,funder_address,status FROM execution_accounts WHERE id=1"
        ).fetchone()
        whale_settings_after = connection.execute("SELECT COUNT(*) FROM whale_settings").fetchone()[
            0
        ]
        email_settings_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(email_settings)")
        }

    assert account == (address, address, "ready")
    assert whale_settings_after == whale_settings_before
    assert {"weekly_summary_enabled", "weekly_summary_enabled_at"} <= email_settings_columns
    assert "redemption_executions" in tables
    assert "copy_subscriptions" not in tables
    assert "copy_orders" not in tables
    assert "watched_wallets" not in tables
    assert "position_events" not in tables
