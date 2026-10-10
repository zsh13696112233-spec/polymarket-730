from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import Database
from backend.models import WhaleEntry, WhaleEntryRuleState, WhaleExclusion, WhaleSettings
from backend.schemas import (
    WhaleConfigBackup,
    WhaleConfigExclusion,
    WhaleConfigImportRead,
    WhaleConfigSettings,
    WhaleSettingsUpdate,
)
from backend.time_utils import utcnow

CATEGORY_FIELDS = {
    "monitor_categories": "monitor_categories_json",
    "new_account_auto_follow_categories": "new_account_auto_follow_categories_json",
    "large_amount_auto_follow_categories": "large_amount_auto_follow_categories_json",
}


def settings_values(payload: WhaleSettingsUpdate) -> dict[str, Any]:
    values = payload.model_dump(exclude_none=True)
    for nullable_key in (
        "dual_match_auto_follow_amount_usdc",
        "new_account_auto_follow_low_price_max_price",
        "new_account_auto_follow_low_price_amount_usdc",
        "large_amount_auto_follow_low_price_max_price",
        "large_amount_auto_follow_low_price_amount_usdc",
        "auto_follow_market_max_purchase_count",
        "auto_follow_market_max_amount_usdc",
    ):
        if nullable_key in payload.model_fields_set and getattr(payload, nullable_key) is None:
            values[nullable_key] = None
    for public_key, storage_key in CATEGORY_FIELDS.items():
        categories = values.pop(public_key, None)
        if categories is not None:
            values[storage_key] = json.dumps(categories, separators=(",", ":"))
    # The page exposes one “重仓阈值”.  Keep single and cumulative gates in
    # lockstep when only the cumulative value is supplied, otherwise raising
    # the visible threshold would not necessarily narrow results.
    if "cumulative_threshold_usdc" in values and "single_trade_threshold_usdc" not in values:
        values["single_trade_threshold_usdc"] = values["cumulative_threshold_usdc"]
    if "new_account_threshold_usdc" in values:
        values["single_trade_threshold_usdc"] = values["new_account_threshold_usdc"]
        values["cumulative_threshold_usdc"] = values["new_account_threshold_usdc"]
    elif "cumulative_threshold_usdc" in values:
        values["new_account_threshold_usdc"] = values["cumulative_threshold_usdc"]
        values["single_trade_threshold_usdc"] = values["cumulative_threshold_usdc"]
    elif "single_trade_threshold_usdc" in values:
        values["new_account_threshold_usdc"] = values["single_trade_threshold_usdc"]
        values["cumulative_threshold_usdc"] = values["single_trade_threshold_usdc"]
    return values


def validate_settings(merged: dict[str, Any]) -> None:
    if merged["single_trade_threshold_usdc"] < merged["collect_filter_amount_usdc"]:
        raise ValueError("单笔重仓阈值不能低于采集金额阈值")
    if merged["cumulative_threshold_usdc"] < merged["collect_filter_amount_usdc"]:
        raise ValueError("累计重仓阈值不能低于采集金额阈值")
    if merged["new_account_threshold_usdc"] < merged["collect_filter_amount_usdc"]:
        raise ValueError("新号大额门槛不能低于采集金额阈值")
    if merged["large_amount_threshold_usdc"] < merged["collect_filter_amount_usdc"]:
        raise ValueError("全量超大额门槛不能低于采集金额阈值")
    if merged["exited_ratio_threshold"] >= merged["holding_ratio_threshold"]:
        raise ValueError("退出比例阈值必须低于持有比例阈值")
    if merged["default_follow_amount_usdc"] > merged["max_follow_amount_usdc"]:
        raise ValueError("默认买入金额不能超过单笔买入上限")
    for label, prefix in (
        ("新号大额", "new_account"),
        ("全量超大额", "large_amount"),
    ):
        amount = merged[f"{prefix}_auto_follow_amount_usdc"]
        minimum = merged[f"{prefix}_auto_follow_min_price"]
        maximum = merged[f"{prefix}_auto_follow_max_price"]
        low_maximum = merged[f"{prefix}_auto_follow_low_price_max_price"]
        low_amount = merged[f"{prefix}_auto_follow_low_price_amount_usdc"]
        if amount > merged["max_follow_amount_usdc"]:
            raise ValueError(f"{label}自动跟单金额不能超过单笔买入上限")
        if minimum > maximum:
            raise ValueError(f"{label}自动跟单最低买价不能高于最高买价")
        if (low_maximum is None) != (low_amount is None):
            raise ValueError(f"{label}低价分界与低价金额必须同时设置或同时清除")
        if low_maximum is not None and low_amount is not None:
            if not minimum < low_maximum < maximum:
                raise ValueError(f"{label}低价分界必须严格位于实际买价区间内")
            if low_amount >= amount:
                raise ValueError(f"{label}低价金额必须小于基础单笔金额")
            if low_amount > merged["max_follow_amount_usdc"]:
                raise ValueError(f"{label}低价金额不能超过单笔买入上限")
    dual_amount = merged["dual_match_auto_follow_amount_usdc"]
    if dual_amount is not None and dual_amount > merged["max_follow_amount_usdc"]:
        raise ValueError("双重命中金额不能超过单笔买入上限")
    market_count_cap = merged["auto_follow_market_max_purchase_count"]
    market_amount_cap = merged["auto_follow_market_max_amount_usdc"]
    if (market_count_cap is None) != (market_amount_cap is None):
        raise ValueError("单市场最大购买次数与累计金额必须同时设置或同时清除")
    if market_count_cap is not None and market_amount_cap is not None:
        enabled_amounts = []
        for prefix in ("new_account", "large_amount"):
            if not merged[f"{prefix}_auto_follow_enabled"]:
                continue
            enabled_amounts.append(merged[f"{prefix}_auto_follow_amount_usdc"])
        if enabled_amounts and dual_amount is not None:
            enabled_amounts.append(dual_amount)
        if enabled_amounts and market_amount_cap < max(enabled_amounts):
            raise ValueError("单市场累计金额不能低于已开启策略的基础单笔金额")


def apply_settings(row: WhaleSettings, values: dict[str, Any]) -> None:
    merged = {
        column.name: values.get(column.name, getattr(row, column.name))
        for column in WhaleSettings.__table__.columns
    }
    validate_settings(merged)
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_at = utcnow()


async def export_config(database: Database) -> WhaleConfigBackup:
    async with database.sessions() as session:
        row = await session.get(WhaleSettings, 1)
        if row is None:
            raise ValueError("巨鲸模块尚未初始化，请稍后重试")
        values = {
            field: (
                json.loads(getattr(row, CATEGORY_FIELDS[field]))
                if field in CATEGORY_FIELDS
                else getattr(row, field)
            )
            for field in WhaleConfigSettings.model_fields
        }
        exclusions = list(
            (
                await session.scalars(select(WhaleExclusion).order_by(WhaleExclusion.proxy_wallet))
            ).all()
        )
        return WhaleConfigBackup(
            product="PolyCopy",
            version=1,
            exported_at=utcnow(),
            settings=WhaleConfigSettings.model_validate(values),
            exclusions=[
                WhaleConfigExclusion(proxy_wallet=item.proxy_wallet, label=item.label)
                for item in exclusions
            ],
        )


async def deactivate_excluded_entries(session: AsyncSession, addresses: list[str]) -> None:
    if not addresses:
        return
    entry_ids = select(WhaleEntry.id).where(func.lower(WhaleEntry.proxy_wallet).in_(addresses))
    await session.execute(
        update(WhaleEntryRuleState)
        .where(WhaleEntryRuleState.entry_id.in_(entry_ids), WhaleEntryRuleState.active.is_(True))
        .values(active=False, inactive_at=utcnow(), inactive_reason="account_excluded")
    )


async def import_config(database: Database, payload: WhaleConfigBackup) -> WhaleConfigImportRead:
    # Validate the file's original switches as well; disabling execution must not bypass limits.
    values = payload.settings.model_dump()
    for public_key, storage_key in CATEGORY_FIELDS.items():
        values[storage_key] = json.dumps(values.pop(public_key), separators=(",", ":"))
    validate_settings(values)
    for key in (
        "new_account_auto_follow_enabled",
        "large_amount_auto_follow_enabled",
        "auto_redeem",
    ):
        values[key] = False
    added = updated = 0
    async with database.sessions() as session, session.begin():
        row = await session.get(WhaleSettings, 1)
        if row is None:
            raise ValueError("巨鲸模块尚未初始化，请稍后重试")
        apply_settings(row, values)
        existing = {
            item.proxy_wallet: item
            for item in (await session.scalars(select(WhaleExclusion))).all()
        }
        for item in payload.exclusions:
            current = existing.get(item.proxy_wallet)
            if current is None:
                session.add(
                    WhaleExclusion(
                        proxy_wallet=item.proxy_wallet, label=item.label, created_at=utcnow()
                    )
                )
                added += 1
            elif current.label != item.label:
                current.label = item.label
                updated += 1
        await deactivate_excluded_entries(
            session, [item.proxy_wallet for item in payload.exclusions]
        )
    return WhaleConfigImportRead(
        added=added,
        updated=updated,
        retained=len(existing) - updated,
        message="配置已导入，黑名单已合并；自动跟单和监测自动赎回已关闭，请核对后手动开启。",
    )
