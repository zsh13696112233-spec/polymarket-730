from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select

from backend.models import (
    ExecutionAccount,
    WhaleEntry,
    WhaleEntryRuleState,
    WhaleExclusion,
    WhaleSettings,
)
from backend.time_utils import utcnow

A = "0x" + "a" * 40
B = "0x" + "b" * 40
C = "0x" + "c" * 40
EXPORT = "/api/whales/config/export"
IMPORT = "/api/whales/config/import"


@pytest.fixture
def client(app_client_factory):
    client, _ = app_client_factory([[]], trading_enabled=False, start_monitor=False)
    return client


def snapshot(client):
    response = client.get(EXPORT)
    assert response.status_code == 200, response.text
    return response.json()


def test_round_trip_merges_exclusions_disables_execution_and_preserves_account(client, monkeypatch):
    preserved = snapshot(client)["exclusions"]

    async def seed():
        async with client.app.state.database.sessions() as session:
            session.add_all(
                [
                    WhaleExclusion(proxy_wallet=A, label="保留", created_at=utcnow()),
                    WhaleExclusion(proxy_wallet=B, label="旧备注", created_at=utcnow()),
                    ExecutionAccount(
                        id=1,
                        auto_redeem=True,
                        cash_reserve_usdc=Decimal("123.45"),
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    ),
                ]
            )
            settings = await session.get(WhaleSettings, 1)
            settings.new_account_auto_follow_min_price = Decimal("0.512345678901234567")
            await session.commit()

    client.portal.call(seed)
    backup = snapshot(client)
    assert set(backup) == {"product", "version", "exported_at", "settings", "exclusions"}
    assert backup["exclusions"] == preserved + [
        {"proxy_wallet": A, "label": "保留"},
        {"proxy_wallet": B, "label": "旧备注"},
    ]
    assert "last_scan_at" not in backup["settings"]
    assert "keychain_service" not in str(backup)
    assert isinstance(backup["settings"]["new_account_auto_follow_min_price"], str)
    backup["settings"].update(
        new_account_auto_follow_enabled=True,
        large_amount_auto_follow_enabled=True,
        auto_redeem=True,
        monitor_categories=["politics", "crypto"],
        dual_match_auto_follow_amount_usdc=None,
    )
    backup["exclusions"] = [
        {"proxy_wallet": B, "label": None},
        {"proxy_wallet": "0x" + "C" * 40, "label": "新账户"},
        {"proxy_wallet": C, "label": "新账户"},
    ]
    wake = Mock()
    scan = AsyncMock()
    monkeypatch.setattr(client.app.state.whale_scanner, "wake", wake)
    monkeypatch.setattr(client.app.state.whale_scanner, "scan_now", scan)
    account_before = client.get("/api/execution-account").json()
    result = client.post(IMPORT, json=backup)
    assert result.status_code == 200, result.text
    assert {key: result.json()[key] for key in ("added", "updated", "retained")} == {
        "added": 1,
        "updated": 1,
        "retained": 1 + len(preserved),
    }
    actual = snapshot(client)
    for key in (
        "new_account_auto_follow_enabled",
        "large_amount_auto_follow_enabled",
        "auto_redeem",
    ):
        assert actual["settings"][key] is False
        backup["settings"][key] = False
    assert actual["settings"] == backup["settings"]
    assert actual["exclusions"] == preserved + [
        {"proxy_wallet": A, "label": "保留"},
        {"proxy_wallet": B, "label": None},
        {"proxy_wallet": C, "label": "新账户"},
    ]
    assert client.get("/api/execution-account").json() == account_before
    assert client.post(IMPORT, json=backup).json()["added"] == 0
    empty = deepcopy(backup)
    empty["exclusions"] = []
    assert client.post(IMPORT, json=empty).status_code == 200
    assert snapshot(client)["exclusions"] == actual["exclusions"]
    wake.assert_not_called()
    scan.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(version=2),
        lambda p: p.update(product="Other"),
        lambda p: p.update(private_key="not-allowed"),
        lambda p: p["settings"].pop("enabled"),
        lambda p: p["settings"].pop("dual_match_auto_follow_amount_usdc"),
        lambda p: p["settings"].update(window_hours=None),
        lambda p: p["settings"].update(unknown_setting=1),
        lambda p: p["settings"].update(monitor_categories=[]),
        lambda p: p["settings"].update(
            new_account_auto_follow_min_price="0.99", new_account_auto_follow_max_price="0.1"
        ),
        lambda p: p["settings"].update(default_follow_amount_usdc="999999"),
        lambda p: p["settings"].update(
            new_account_auto_follow_low_price_max_price="0.65",
            new_account_auto_follow_low_price_amount_usdc=None,
        ),
        lambda p: p["settings"].update(
            auto_follow_market_max_purchase_count=None, auto_follow_market_max_amount_usdc="30"
        ),
        lambda p: p["exclusions"].append({"proxy_wallet": "invalid", "label": None}),
        lambda p: p["exclusions"].extend(
            [{"proxy_wallet": A, "label": "a"}, {"proxy_wallet": A, "label": "b"}]
        ),
    ],
)
def test_invalid_file_does_not_write_anything(client, change):
    before = snapshot(client)
    payload = deepcopy(before)
    payload["settings"]["registration_window_days"] = 13
    payload["exclusions"] = [{"proxy_wallet": C, "label": "不得保存"}]
    change(payload)
    assert client.post(IMPORT, json=payload).status_code == 422
    after = snapshot(client)
    assert after["settings"] == before["settings"]
    assert after["exclusions"] == before["exclusions"]


def test_failed_database_write_rolls_back_settings_and_exclusions(client, monkeypatch):
    import backend.whale_config as config

    payload = snapshot(client)
    before = deepcopy(payload)
    payload["settings"]["registration_window_days"] = 13
    payload["exclusions"] = [{"proxy_wallet": C, "label": None}]

    async def fail_after_flush(session, addresses):
        await session.flush()
        raise ValueError("模拟写入失败")

    monkeypatch.setattr(config, "deactivate_excluded_entries", fail_after_flush)
    assert client.post(IMPORT, json=payload).status_code == 422
    after = snapshot(client)
    assert after["settings"] == before["settings"]
    assert after["exclusions"] == before["exclusions"]


def test_import_deactivates_existing_signals_without_deleting_history(client):
    async def seed():
        now = utcnow()
        async with client.app.state.database.sessions() as session:
            entry = WhaleEntry(
                proxy_wallet=A,
                asset_id="asset",
                condition_id="0x" + "1" * 64,
                outcome="Yes",
                outcome_index=0,
                gross_buy_usdc=100000,
                gross_buy_size=200000,
                net_size=200000,
                net_ratio=100,
                avg_buy_price=Decimal("0.5"),
                max_single_usdc=100000,
                trade_count=1,
                first_buy_at=now,
                last_buy_at=now,
                status="holding",
                window_start=now,
                computed_at=now,
            )
            session.add(entry)
            await session.flush()
            session.add(
                WhaleEntryRuleState(
                    entry_id=entry.id,
                    rule_type="new_account",
                    active=True,
                    threshold_usdc_snapshot=100000,
                    first_triggered_at=now,
                    last_qualified_at=now,
                )
            )
            await session.commit()

    client.portal.call(seed)
    payload = snapshot(client)
    payload["exclusions"] = [{"proxy_wallet": A, "label": None}]
    assert client.post(IMPORT, json=payload).status_code == 200

    async def verify():
        async with client.app.state.database.sessions() as session:
            assert await session.scalar(select(WhaleEntry)) is not None
            state = await session.scalar(select(WhaleEntryRuleState))
            assert state.active is False
            assert state.inactive_reason == "account_excluded"
            assert state.first_triggered_at is not None

    client.portal.call(verify)


def test_import_waits_for_running_scan_before_writing(client):
    import asyncio

    import httpx

    payload = snapshot(client)
    previous_days = payload["settings"]["registration_window_days"]
    payload["settings"]["registration_window_days"] = 13

    async def exercise():
        lock = client.app.state.whale_scanner.configuration_lock
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app), base_url="http://test"
        ) as http:
            async with lock:
                task = asyncio.create_task(http.post(IMPORT, json=payload))
                await asyncio.sleep(0)
                assert not task.done()
                async with client.app.state.database.sessions() as session:
                    row = await session.get(WhaleSettings, 1)
                    assert row.registration_window_days == previous_days
            response = await asyncio.wait_for(task, timeout=5)
            assert response.status_code == 200, response.text

    client.portal.call(exercise)
    assert snapshot(client)["settings"]["registration_window_days"] == 13
