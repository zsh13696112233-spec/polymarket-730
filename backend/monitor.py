from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import monotonic

from sqlalchemy import or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.broker import EventBroker
from backend.config import Settings
from backend.db import Database
from backend.models import (
    CurrentPosition,
    PositionChangeCandidate,
    PositionEvent,
    PositionEventFill,
    PositionOverlapAlert,
    PositionOverlapPeriod,
    WalletTrade,
    WatchedWallet,
)
from backend.polymarket import (
    MarketResolution,
    PolymarketAPIError,
    PolymarketClient,
    PositionSnapshot,
    RedemptionSnapshot,
    SettlementEvidence,
    TradeSnapshot,
    fingerprint_trades,
)
from backend.purchase_history import redemption_cost_bases

LOGGER = logging.getLogger(__name__)
ZERO = Decimal("0")
SIZE_TOLERANCE = Decimal("0.000000001")
RECONCILIATION_TOLERANCE = Decimal("0.000001")
RECONCILIATION_RELATIVE_TOLERANCE = Decimal("0.000001")


def fills_reconcile(net_fill_size: Decimal, delta: Decimal) -> bool:
    tolerance = max(
        RECONCILIATION_TOLERANCE,
        abs(delta) * RECONCILIATION_RELATIVE_TOLERANCE,
    )
    return abs(net_fill_size - delta) <= tolerance


def utcnow() -> datetime:
    # SQLite does not retain timezone offsets. All persisted datetimes are naive UTC.
    return datetime.now(UTC).replace(tzinfo=None)


class WalletMonitor:
    def __init__(
        self,
        *,
        database: Database,
        client: PolymarketClient,
        broker: EventBroker,
        settings: Settings,
    ) -> None:
        self.database = database
        self.client = client
        self.broker = broker
        self.settings = settings
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._semaphore = asyncio.Semaphore(settings.max_wallet_concurrency)
        self._wallet_locks: dict[int, asyncio.Lock] = {}
        self._overlap_lock = asyncio.Lock()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="wallet-monitor")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            await self._task
            self._task = None

    def wake(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        while not self._stop.is_set():
            now = utcnow()
            async with self.database.sessions() as session:
                wallet_ids = list(
                    (
                        await session.scalars(
                            select(WatchedWallet.id)
                            .where(
                                WatchedWallet.enabled.is_(True),
                                or_(
                                    WatchedWallet.next_sync_at.is_(None),
                                    WatchedWallet.next_sync_at <= now,
                                ),
                            )
                            .order_by(WatchedWallet.next_sync_at.asc())
                        )
                    ).all()
                )
            if wallet_ids:
                await asyncio.gather(
                    *(self._bounded_sync(wallet_id) for wallet_id in wallet_ids),
                    return_exceptions=True,
                )
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)
            except TimeoutError:
                pass

    async def _bounded_sync(self, wallet_id: int) -> None:
        async with self._semaphore:
            try:
                await self.sync_wallet(wallet_id)
            except Exception:
                LOGGER.exception("Unexpected wallet sync failure for wallet_id=%s", wallet_id)

    async def sync_wallet(self, wallet_id: int) -> bool:
        lock = self._wallet_locks.setdefault(wallet_id, asyncio.Lock())
        async with lock:
            return await self._sync_wallet_unlocked(wallet_id)

    async def _sync_wallet_unlocked(self, wallet_id: int) -> bool:
        sync_started = monotonic()
        attempt_at = utcnow()
        async with self.database.sessions() as session:
            wallet = await session.get(WatchedWallet, wallet_id)
            if wallet is None or not wallet.enabled:
                return False
            wallet.status = "syncing"
            wallet.last_attempt_at = attempt_at
            wallet.updated_at = attempt_at
            await session.commit()
            proxy_wallet = wallet.proxy_wallet
            previous_trade_history_error = wallet.trade_history_error
            previous_redemption_history_error = wallet.redemption_history_error
        await self.broker.publish("sync.status", wallet_id)

        try:
            positions_started = monotonic()
            snapshot = await self.client.fetch_active_positions(proxy_wallet)
            positions_ms = round((monotonic() - positions_started) * 1000)
            trade_history_error = previous_trade_history_error
            redemption_history_error = previous_redemption_history_error
            created_redemptions = 0
            created_trades = 0
            snapshot_assets = {item.asset_id for item in snapshot}
            async with self.database.sessions() as session:
                positions = list(
                    (
                        await session.scalars(
                            select(CurrentPosition).where(CurrentPosition.wallet_id == wallet_id)
                        )
                    ).all()
                )
            confirmable = [
                position
                for position in positions
                if position.asset_id not in snapshot_assets and position.missing_count >= 1
            ]
            known_assets_before_snapshot = {position.asset_id for position in positions}
            evidence = SettlementEvidence(frozenset(), frozenset(), frozenset())
            if confirmable:
                evidence = await self.client.fetch_settlement_evidence(
                    proxy_wallet,
                    condition_ids=(position.condition_id for position in confirmable),
                    last_seen_by_condition={
                        position.condition_id: position.last_seen_at for position in confirmable
                    },
                )
            settlement_resolutions: dict[str, MarketResolution] = {}
            resolved_conditions = [
                position.condition_id
                for position in confirmable
                if position.condition_id in evidence.resolved_condition_ids
            ]
            if resolved_conditions:
                try:
                    settlement_resolutions = await self.client.fetch_market_resolutions(
                        resolved_conditions
                    )
                except Exception as error:
                    LOGGER.warning(
                        "Wallet settlement resolution lookup failed for wallet_id=%s: %s",
                        wallet_id,
                        error,
                    )

            async def sync_histories(*, force_redemptions: bool) -> None:
                nonlocal created_trades
                nonlocal created_redemptions
                nonlocal trade_history_error
                nonlocal redemption_history_error
                trade_result, redemption_result = await asyncio.gather(
                    self._sync_trade_history(
                        wallet_id,
                        snapshot,
                        observed_at=attempt_at,
                        known_assets=known_assets_before_snapshot,
                    ),
                    self._sync_redemptions(
                        wallet_id,
                        observed_at=attempt_at,
                        force=force_redemptions,
                    ),
                    return_exceptions=True,
                )
                if isinstance(trade_result, BaseException):
                    trade_history_error = str(trade_result)[:1000]
                    LOGGER.warning(
                        "Wallet trade history sync failed for wallet_id=%s: %s",
                        wallet_id,
                        trade_result,
                    )
                else:
                    created_trades = trade_result
                    trade_history_error = None
                if isinstance(redemption_result, BaseException):
                    redemption_history_error = str(redemption_result)[:1000]
                    LOGGER.warning(
                        "Wallet redemption history sync failed for wallet_id=%s: %s",
                        wallet_id,
                        redemption_result,
                    )
                else:
                    redemption_polled, created_redemptions = redemption_result
                    if redemption_polled:
                        redemption_history_error = None

            # A disappearing position may already have an on-chain redemption.
            # Import it before creating settlement terminal events so the existing
            # de-duplication path can enrich, rather than duplicate, that event.
            if confirmable:
                await sync_histories(force_redemptions=True)

            snapshot_at = utcnow()
            terminal_events = await self._apply_snapshot(
                wallet_id,
                snapshot,
                evidence=evidence,
                settlement_resolutions=settlement_resolutions,
                observed_at=snapshot_at,
            )
            await self._refresh_overlap_periods(wallet_id, observed_at=snapshot_at)
            await self.broker.publish("positions.updated", wallet_id)

            if not confirmable:
                await sync_histories(force_redemptions=False)
            if created_trades or created_redemptions:
                try:
                    await self._refresh_redemption_costs_for_wallet(wallet_id)
                except Exception as error:
                    redemption_history_error = str(error)[:1000]
                    LOGGER.warning(
                        "Wallet redemption cost refresh failed for wallet_id=%s: %s",
                        wallet_id,
                        error,
                    )

            completed_at = utcnow()
            async with self.database.sessions() as session:
                wallet = await session.get(WatchedWallet, wallet_id)
                if wallet is None:
                    return False
                wallet.status = "ok"
                wallet.last_success_at = snapshot_at
                wallet.last_error = None
                wallet.trade_history_error = trade_history_error
                wallet.redemption_history_error = redemption_history_error
                wallet.consecutive_failures = 0
                wallet.next_sync_at = max(
                    completed_at,
                    attempt_at + timedelta(seconds=self.settings.poll_interval_seconds),
                )
                wallet.updated_at = completed_at
                await session.commit()
            created_events = terminal_events + await self.finalize_due_candidates(
                wallet_id, now=completed_at
            )
            if created_events or created_redemptions:
                await self.broker.publish("events.created", wallet_id)
            await self.broker.publish("sync.status", wallet_id)
            LOGGER.debug(
                "Wallet sync completed wallet_id=%s total_ms=%s positions_ms=%s "
                "positions=%s new_trades=%s new_redemptions=%s",
                wallet_id,
                round((monotonic() - sync_started) * 1000),
                positions_ms,
                len(snapshot),
                created_trades,
                created_redemptions,
            )
            return True
        except Exception as error:
            await self._record_failure(wallet_id, error)
            await self.broker.publish("sync.status", wallet_id)
            return False

    async def _refresh_overlap_periods(
        self,
        wallet_id: int,
        *,
        observed_at: datetime,
    ) -> None:
        async with self._overlap_lock:
            await self._refresh_overlap_periods_unlocked(
                wallet_id,
                observed_at=observed_at,
            )

    async def close_overlap_periods(
        self,
        *,
        ended_at: datetime,
        my_wallet_id: int | None = None,
        tracked_wallet_id: int | None = None,
    ) -> None:
        if my_wallet_id is None and tracked_wallet_id is None:
            return
        async with self._overlap_lock:
            async with self.database.sessions() as session:
                conditions = [PositionOverlapPeriod.ended_at.is_(None)]
                if my_wallet_id is not None:
                    conditions.append(PositionOverlapPeriod.my_wallet_id == my_wallet_id)
                if tracked_wallet_id is not None:
                    conditions.append(PositionOverlapPeriod.tracked_wallet_id == tracked_wallet_id)
                periods = list(
                    (await session.scalars(select(PositionOverlapPeriod).where(*conditions))).all()
                )
                for period in periods:
                    period.ended_at = ended_at
                await session.commit()

    async def _refresh_overlap_periods_unlocked(
        self,
        wallet_id: int,
        *,
        observed_at: datetime,
    ) -> None:
        async with self.database.sessions() as session:
            wallet = await session.get(WatchedWallet, wallet_id)
            if wallet is None:
                return

            if wallet.wallet_role == "self":
                my_wallet = wallet
                tracked_wallets = list(
                    (
                        await session.scalars(
                            select(WatchedWallet).where(
                                WatchedWallet.wallet_role == "tracked",
                                WatchedWallet.enabled.is_(True),
                            )
                        )
                    ).all()
                )
            else:
                tracked_wallets = [wallet] if wallet.enabled else []
                my_wallet = await session.scalar(
                    select(WatchedWallet).where(
                        WatchedWallet.wallet_role == "self",
                        WatchedWallet.enabled.is_(True),
                    )
                )

            if my_wallet is None or not my_wallet.enabled:
                open_periods = list(
                    (
                        await session.scalars(
                            select(PositionOverlapPeriod).where(
                                PositionOverlapPeriod.tracked_wallet_id == wallet_id,
                                PositionOverlapPeriod.ended_at.is_(None),
                            )
                        )
                    ).all()
                )
                for period in open_periods:
                    period.ended_at = observed_at
                await session.commit()
                return

            tracked_ids = {tracked_wallet.id for tracked_wallet in tracked_wallets}
            if wallet.wallet_role == "self":
                orphaned_periods = list(
                    (
                        await session.scalars(
                            select(PositionOverlapPeriod).where(
                                PositionOverlapPeriod.my_wallet_id == my_wallet.id,
                                PositionOverlapPeriod.ended_at.is_(None),
                            )
                        )
                    ).all()
                )
                for period in orphaned_periods:
                    if period.tracked_wallet_id not in tracked_ids:
                        period.ended_at = observed_at

            if not tracked_wallets:
                await session.commit()
                return

            relevant_wallet_ids = [my_wallet.id, *tracked_ids]
            positions = list(
                (
                    await session.scalars(
                        select(CurrentPosition).where(
                            CurrentPosition.wallet_id.in_(relevant_wallet_ids),
                            CurrentPosition.size > ZERO,
                        )
                    )
                ).all()
            )
            positions_by_wallet: dict[int, dict[str, CurrentPosition]] = defaultdict(dict)
            for position in positions:
                positions_by_wallet[position.wallet_id][position.asset_id] = position

            my_positions = positions_by_wallet[my_wallet.id]
            for tracked_wallet in tracked_wallets:
                tracked_positions = positions_by_wallet[tracked_wallet.id]
                open_periods = list(
                    (
                        await session.scalars(
                            select(PositionOverlapPeriod).where(
                                PositionOverlapPeriod.my_wallet_id == my_wallet.id,
                                PositionOverlapPeriod.tracked_wallet_id == tracked_wallet.id,
                                PositionOverlapPeriod.ended_at.is_(None),
                            )
                        )
                    ).all()
                )
                open_by_asset = {period.asset_id: period for period in open_periods}
                common_assets = my_positions.keys() & tracked_positions.keys()

                for asset_id in common_assets:
                    mine = my_positions[asset_id]
                    tracked = tracked_positions[asset_id]
                    period = open_by_asset.get(asset_id)
                    if period is None:
                        period = PositionOverlapPeriod(
                            my_wallet_id=my_wallet.id,
                            tracked_wallet_id=tracked_wallet.id,
                            asset_id=asset_id,
                            condition_id=tracked.condition_id,
                            title=tracked.title,
                            outcome=tracked.outcome,
                            event_slug=tracked.event_slug,
                            market_slug=tracked.market_slug,
                            last_my_size=mine.size,
                            last_tracked_size=tracked.size,
                            started_at=observed_at,
                            ended_at=None,
                        )
                        session.add(period)
                    else:
                        period.condition_id = tracked.condition_id
                        period.title = tracked.title
                        period.outcome = tracked.outcome
                        period.event_slug = tracked.event_slug
                        period.market_slug = tracked.market_slug
                        period.last_my_size = mine.size
                        period.last_tracked_size = tracked.size

                for asset_id, period in open_by_asset.items():
                    if asset_id not in common_assets:
                        period.ended_at = observed_at

            await session.commit()

    async def _sync_trade_history(
        self,
        wallet_id: int,
        snapshot: list[PositionSnapshot],
        *,
        observed_at: datetime,
        known_assets: set[str] | None = None,
    ) -> int:
        active_assets = {item.asset_id for item in snapshot}
        condition_ids = {item.condition_id for item in snapshot}
        async with self.database.sessions() as session:
            wallet = await session.get(WatchedWallet, wallet_id)
            if wallet is None:
                return 0
            if known_assets is None:
                known_assets = set(
                    (
                        await session.scalars(
                            select(CurrentPosition.asset_id).where(
                                CurrentPosition.wallet_id == wallet_id
                            )
                        )
                    ).all()
                )
            start = wallet.trade_history_synced_at
            if active_assets - known_assets:
                start = None
            elif start is not None:
                start -= timedelta(seconds=120)
            proxy_wallet = wallet.proxy_wallet

        if not active_assets or not condition_ids:
            async with self.database.sessions() as session:
                wallet = await session.get(WatchedWallet, wallet_id)
                if wallet is not None and (
                    wallet.trade_history_synced_at is None or wallet.trade_history_error is not None
                ):
                    wallet.trade_history_synced_at = observed_at
                    wallet.trade_history_error = None
                    await session.commit()
            return 0

        trades = await self.client.fetch_trades(
            proxy_wallet,
            condition_ids=condition_ids,
            start=start,
            end=observed_at + timedelta(seconds=30),
        )
        valid_trades = [
            trade
            for trade in trades
            if trade.asset_id in active_assets
            and trade.condition_id in condition_ids
            and trade.size > ZERO
            and trade.side in {"BUY", "SELL"}
        ]
        fingerprinted = self._fingerprinted_trades(proxy_wallet, valid_trades)

        async with self.database.sessions() as session:
            existing_fingerprints: set[str] = set()
            fingerprints = [fingerprint for fingerprint, _ in fingerprinted]
            for offset in range(0, len(fingerprints), 500):
                batch = fingerprints[offset : offset + 500]
                existing_fingerprints.update(
                    (
                        await session.scalars(
                            select(WalletTrade.fingerprint).where(
                                WalletTrade.fingerprint.in_(batch)
                            )
                        )
                    ).all()
                )
            rows = [
                {
                    "wallet_id": wallet_id,
                    "fingerprint": fingerprint,
                    "asset_id": trade.asset_id,
                    "condition_id": trade.condition_id,
                    "side": trade.side,
                    "size": trade.size,
                    "price": trade.price,
                    "amount": trade.size * trade.price,
                    "timestamp": trade.timestamp,
                    "transaction_hash": trade.transaction_hash,
                    "title": trade.title,
                    "outcome": trade.outcome,
                    "outcome_index": trade.outcome_index,
                    "event_slug": trade.event_slug,
                    "market_slug": trade.market_slug,
                    "imported_at": observed_at,
                }
                for fingerprint, trade in fingerprinted
                if fingerprint not in existing_fingerprints
            ]
            created = 0
            if rows and self.database.settings.database_url.startswith("sqlite"):
                result = await session.execute(
                    sqlite_insert(WalletTrade)
                    .values(rows)
                    .on_conflict_do_nothing(index_elements=["fingerprint"])
                )
                created = max(0, int(result.rowcount or 0))
            else:
                for row in rows:
                    try:
                        async with session.begin_nested():
                            session.add(WalletTrade(**row))
                            await session.flush()
                            created += 1
                    except IntegrityError:
                        continue
            wallet = await session.get(WatchedWallet, wallet_id)
            if wallet is not None:
                wallet.trade_history_synced_at = observed_at
                wallet.trade_history_error = None
            await session.commit()
            return created

    async def _sync_redemptions(
        self,
        wallet_id: int,
        *,
        observed_at: datetime,
        force: bool = False,
    ) -> tuple[bool, int]:
        async with self.database.sessions() as session:
            wallet = await session.get(WatchedWallet, wallet_id)
            if wallet is None:
                return False, 0
            if (
                not force
                and wallet.redemption_history_synced_at is not None
                and (observed_at - wallet.redemption_history_synced_at).total_seconds()
                < self.settings.redemption_poll_interval_seconds
            ):
                return False, 0
            start = wallet.redemption_history_synced_at or wallet.created_at
            if wallet.redemption_history_synced_at is not None:
                start -= timedelta(seconds=120)
            proxy_wallet = wallet.proxy_wallet

        redemptions = await self.client.fetch_redemptions(
            proxy_wallet,
            start=start,
            end=observed_at + timedelta(seconds=30),
        )
        fingerprinted = [
            (self._redemption_fingerprint(proxy_wallet, redemption), redemption)
            for redemption in sorted(
                redemptions,
                key=lambda item: (
                    item.timestamp,
                    item.transaction_hash or "",
                    item.condition_id,
                    item.outcome_index if item.outcome_index is not None else -1,
                ),
            )
        ]

        async with self.database.sessions() as session:
            existing_fingerprints: set[str] = set()
            fingerprints = [fingerprint for fingerprint, _ in fingerprinted]
            for offset in range(0, len(fingerprints), 500):
                batch = fingerprints[offset : offset + 500]
                existing_fingerprints.update(
                    (
                        await session.scalars(
                            select(PositionEvent.source_fingerprint).where(
                                PositionEvent.source_fingerprint.in_(batch)
                            )
                        )
                    ).all()
                )

            created = 0
            for fingerprint, redemption in fingerprinted:
                if fingerprint in existing_fingerprints:
                    continue
                session.add(
                    PositionEvent(
                        wallet_id=wallet_id,
                        asset_id=self._redemption_asset_id(redemption),
                        condition_id=redemption.condition_id,
                        type="redeemed",
                        title=redemption.title,
                        outcome=redemption.outcome,
                        event_slug=redemption.event_slug,
                        delta_size=-redemption.size,
                        before_size=redemption.size,
                        after_size=ZERO,
                        before_avg_price=ZERO,
                        after_avg_price=ZERO,
                        average_fill_price=None,
                        current_value=ZERO,
                        reconciliation_status="onchain",
                        first_detected_at=redemption.timestamp,
                        settled_at=redemption.timestamp,
                        source_fingerprint=fingerprint,
                        payout_amount=redemption.usdc_size,
                        redemption_cost_basis=None,
                        transaction_hash=redemption.transaction_hash,
                    )
                )
                existing_fingerprints.add(fingerprint)
                created += 1

            wallet = await session.get(WatchedWallet, wallet_id)
            if wallet is not None:
                wallet.redemption_history_synced_at = observed_at
                wallet.redemption_history_error = None
            await session.commit()
            return True, created

    async def _refresh_redemption_costs_for_wallet(self, wallet_id: int) -> None:
        async with self.database.sessions() as session:
            await self._refresh_redemption_costs(session, wallet_id)
            await session.commit()

    async def _refresh_redemption_costs(
        self,
        session: AsyncSession,
        wallet_id: int,
    ) -> None:
        redemption_events = list(
            (
                await session.scalars(
                    select(PositionEvent)
                    .where(
                        PositionEvent.wallet_id == wallet_id,
                        PositionEvent.type == "redeemed",
                    )
                    .order_by(PositionEvent.settled_at.asc(), PositionEvent.id.asc())
                )
            ).all()
        )
        if not redemption_events:
            return

        condition_ids = {event.condition_id for event in redemption_events}
        trades = list(
            (
                await session.scalars(
                    select(WalletTrade)
                    .where(
                        WalletTrade.wallet_id == wallet_id,
                        WalletTrade.condition_id.in_(condition_ids),
                    )
                    .order_by(WalletTrade.timestamp.asc(), WalletTrade.id.asc())
                )
            ).all()
        )
        positions = list(
            (
                await session.scalars(
                    select(CurrentPosition).where(
                        CurrentPosition.wallet_id == wallet_id,
                        CurrentPosition.condition_id.in_(condition_ids),
                    )
                )
            ).all()
        )
        trade_assets_by_condition: dict[str, set[str]] = defaultdict(set)
        trades_by_asset: dict[str, list[WalletTrade]] = defaultdict(list)
        for trade in trades:
            trade_assets_by_condition[trade.condition_id].add(trade.asset_id)
            trades_by_asset[trade.asset_id].append(trade)

        known_assets: dict[tuple[str, str], set[str]] = defaultdict(set)
        for position in positions:
            known_assets[(position.condition_id, position.outcome)].add(position.asset_id)
        historical_events = list(
            (
                await session.scalars(
                    select(PositionEvent).where(
                        PositionEvent.wallet_id == wallet_id,
                        PositionEvent.condition_id.in_(condition_ids),
                        PositionEvent.type != "redeemed",
                    )
                )
            ).all()
        )
        for event in historical_events:
            known_assets[(event.condition_id, event.outcome)].add(event.asset_id)

        events_by_asset: dict[str, list[PositionEvent]] = defaultdict(list)
        redeemed_sizes_by_asset: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for event in redemption_events:
            asset_id: str | None = None
            if not event.asset_id.startswith("redeem:") and event.asset_id in trades_by_asset:
                asset_id = event.asset_id
            else:
                outcome_assets = known_assets.get(
                    (event.condition_id, event.outcome),
                    set(),
                )
                if len(outcome_assets) == 1:
                    asset_id = next(iter(outcome_assets))
                else:
                    condition_assets = trade_assets_by_condition.get(
                        event.condition_id,
                        set(),
                    )
                    if len(condition_assets) == 1:
                        asset_id = next(iter(condition_assets))
                    else:
                        # Data API redemption rows can initially omit both the asset
                        # and outcome, then become enriched after our short overlap
                        # window has passed.  In a multi-outcome condition, recover
                        # the asset from the unique pre-redemption trade balance that
                        # matches the redeemed share count.
                        size_matches: list[str] = []
                        for candidate_asset in condition_assets:
                            net_size = sum(
                                (
                                    trade.size if trade.side == "BUY" else -trade.size
                                    for trade in trades_by_asset[candidate_asset]
                                    if trade.timestamp <= event.settled_at
                                ),
                                ZERO,
                            )
                            remaining_size = net_size - redeemed_sizes_by_asset[candidate_asset]
                            if fills_reconcile(remaining_size, event.before_size):
                                size_matches.append(candidate_asset)
                        if len(size_matches) == 1:
                            asset_id = size_matches[0]
            if asset_id is None:
                continue
            event.asset_id = asset_id
            if not event.outcome:
                inferred_outcomes = {
                    trade.outcome for trade in trades_by_asset[asset_id] if trade.outcome
                }
                if len(inferred_outcomes) == 1:
                    event.outcome = next(iter(inferred_outcomes))
            redeemed_sizes_by_asset[asset_id] += event.before_size
            events_by_asset[asset_id].append(event)

        for asset_id, events in events_by_asset.items():
            costs = redemption_cost_bases(
                trades_by_asset.get(asset_id, []),
                [(event.id, event.settled_at, event.before_size) for event in events],
            )
            for event in events:
                event.redemption_cost_basis = costs.get(event.id)

    async def _record_failure(self, wallet_id: int, error: Exception) -> None:
        failed_at = utcnow()
        async with self.database.sessions() as session:
            wallet = await session.get(WatchedWallet, wallet_id)
            if wallet is None:
                return
            wallet.consecutive_failures += 1
            delay = min(
                self.settings.max_backoff_seconds,
                self.settings.poll_interval_seconds
                * (2 ** max(0, wallet.consecutive_failures - 1)),
            )
            if isinstance(error, PolymarketAPIError) and error.retry_after is not None:
                delay = max(delay, error.retry_after)
            wallet.status = "error"
            wallet.last_error = str(error)[:1000]
            wallet.next_sync_at = failed_at + timedelta(seconds=delay)
            wallet.updated_at = failed_at
            await session.commit()

    async def _apply_snapshot(
        self,
        wallet_id: int,
        snapshot: list[PositionSnapshot],
        *,
        evidence: SettlementEvidence,
        observed_at: datetime,
        settlement_resolutions: dict[str, MarketResolution] | None = None,
    ) -> int:
        incoming = {item.asset_id: item for item in snapshot}
        settlement_resolutions = settlement_resolutions or {}
        terminal_events = 0
        async with self.database.sessions() as session:
            wallet = await session.get(WatchedWallet, wallet_id)
            if wallet is None:
                return terminal_events
            positions = list(
                (
                    await session.scalars(
                        select(CurrentPosition).where(CurrentPosition.wallet_id == wallet_id)
                    )
                ).all()
            )
            existing = {position.asset_id: position for position in positions}
            candidates = {
                candidate.asset_id: candidate
                for candidate in (
                    await session.scalars(
                        select(PositionChangeCandidate).where(
                            PositionChangeCandidate.wallet_id == wallet_id
                        )
                    )
                ).all()
            }

            if not wallet.baseline_established:
                for item in snapshot:
                    session.add(self._new_position(wallet_id, item, observed_at))
                wallet.baseline_established = True
                wallet.updated_at = observed_at
                await session.commit()
                return terminal_events

            for asset_id, item in incoming.items():
                position = existing.get(asset_id)
                if position is None:
                    self._record_change(
                        session=session,
                        candidates=candidates,
                        wallet_id=wallet_id,
                        item=item,
                        before_size=ZERO,
                        before_avg_price=ZERO,
                        observed_at=observed_at,
                    )
                    session.add(self._new_position(wallet_id, item, observed_at))
                    continue

                before_size = position.size
                before_avg_price = position.avg_price
                if abs(before_size - item.size) > SIZE_TOLERANCE:
                    self._record_change(
                        session=session,
                        candidates=candidates,
                        wallet_id=wallet_id,
                        item=item,
                        before_size=before_size,
                        before_avg_price=before_avg_price,
                        observed_at=observed_at,
                    )
                self._update_position(position, item, observed_at)

            for position in positions:
                if position.asset_id in incoming:
                    continue
                if position.missing_count < 1:
                    position.missing_count = 1
                    position.updated_at = observed_at
                    continue
                if evidence.is_settlement(position.asset_id, position.condition_id):
                    # Settlement explains only the remaining balance disappearing.
                    # A pending candidate may contain a real trade (for example
                    # 100 -> 50) that happened before resolution; keep it so that
                    # change can settle normally. A resolved market additionally
                    # needs a durable terminal signal for the copy-trading engine.
                    resolution = settlement_resolutions.get(position.condition_id)
                    payout = (
                        resolution.payout_by_asset_id.get(position.asset_id)
                        if resolution is not None
                        else None
                    )
                    if payout is not None:
                        terminal_type = "redeemed" if payout > ZERO else "closed"
                        prior_terminals = list(
                            (
                                await session.scalars(
                                    select(PositionEvent).where(
                                        PositionEvent.wallet_id == wallet_id,
                                        PositionEvent.condition_id == position.condition_id,
                                        PositionEvent.type == terminal_type,
                                        PositionEvent.settled_at >= position.last_seen_at,
                                    )
                                )
                            ).all()
                        )
                        existing_terminal = next(
                            (
                                event
                                for event in prior_terminals
                                if event.asset_id == position.asset_id
                                or (
                                    event.asset_id.startswith("redeem:")
                                    and (not event.outcome or event.outcome == position.outcome)
                                    and fills_reconcile(event.before_size, position.size)
                                )
                            ),
                            None,
                        )
                        if existing_terminal is not None:
                            if existing_terminal.asset_id.startswith("redeem:"):
                                existing_terminal.asset_id = position.asset_id
                            if not existing_terminal.outcome:
                                existing_terminal.outcome = position.outcome
                        else:
                            fingerprint = hashlib.sha256(
                                (
                                    f"settlement:{wallet_id}:{position.asset_id}:"
                                    f"{resolution.resolved_at.isoformat()}"
                                ).encode()
                            ).hexdigest()
                            session.add(
                                PositionEvent(
                                    wallet_id=wallet_id,
                                    asset_id=position.asset_id,
                                    condition_id=position.condition_id,
                                    type=terminal_type,
                                    title=position.title,
                                    outcome=position.outcome,
                                    event_slug=position.event_slug,
                                    delta_size=-position.size,
                                    before_size=position.size,
                                    after_size=ZERO,
                                    before_avg_price=position.avg_price,
                                    after_avg_price=ZERO,
                                    average_fill_price=None,
                                    current_value=ZERO,
                                    reconciliation_status="settlement",
                                    first_detected_at=observed_at,
                                    settled_at=resolution.resolved_at,
                                    source_fingerprint=fingerprint,
                                    payout_amount=position.size * payout,
                                    redemption_cost_basis=None,
                                    transaction_hash=None,
                                )
                            )
                            terminal_events += 1
                    await session.delete(position)
                    continue
                closing_item = PositionSnapshot(
                    asset_id=position.asset_id,
                    condition_id=position.condition_id,
                    title=position.title,
                    outcome=position.outcome,
                    outcome_index=position.outcome_index,
                    icon_url=position.icon_url,
                    event_slug=position.event_slug,
                    market_slug=position.market_slug,
                    size=ZERO,
                    avg_price=ZERO,
                    current_price=position.current_price,
                    initial_value=ZERO,
                    current_value=ZERO,
                    cash_pnl=ZERO,
                    percent_pnl=ZERO,
                    total_bought=position.total_bought,
                    realized_pnl=position.realized_pnl,
                    end_date=position.end_date,
                )
                self._record_change(
                    session=session,
                    candidates=candidates,
                    wallet_id=wallet_id,
                    item=closing_item,
                    before_size=position.size,
                    before_avg_price=position.avg_price,
                    observed_at=observed_at,
                )
                await session.delete(position)
            wallet.updated_at = observed_at
            await session.commit()
        return terminal_events

    def _record_change(
        self,
        *,
        session: AsyncSession,
        candidates: dict[str, PositionChangeCandidate],
        wallet_id: int,
        item: PositionSnapshot,
        before_size: Decimal,
        before_avg_price: Decimal,
        observed_at: datetime,
    ) -> None:
        candidate = candidates.get(item.asset_id)
        if candidate is None:
            if abs(item.size - before_size) <= SIZE_TOLERANCE:
                return
            candidate = PositionChangeCandidate(
                wallet_id=wallet_id,
                asset_id=item.asset_id,
                condition_id=item.condition_id,
                title=item.title,
                outcome=item.outcome,
                event_slug=item.event_slug,
                start_size=before_size,
                latest_size=item.size,
                before_avg_price=before_avg_price,
                after_avg_price=item.avg_price,
                latest_current_value=item.current_value,
                first_changed_at=observed_at,
                last_changed_at=observed_at,
                hard_deadline_at=observed_at + timedelta(seconds=self.settings.hard_window_seconds),
            )
            session.add(candidate)
            candidates[item.asset_id] = candidate
        else:
            candidate.latest_size = item.size
            candidate.after_avg_price = item.avg_price
            candidate.latest_current_value = item.current_value
            candidate.title = item.title
            candidate.outcome = item.outcome
            candidate.event_slug = item.event_slug
            candidate.last_changed_at = observed_at

        if abs(candidate.latest_size - candidate.start_size) <= SIZE_TOLERANCE:
            session.sync_session.delete(candidate)
            candidates.pop(item.asset_id, None)

    @staticmethod
    def _new_position(
        wallet_id: int, item: PositionSnapshot, observed_at: datetime
    ) -> CurrentPosition:
        return CurrentPosition(
            wallet_id=wallet_id,
            asset_id=item.asset_id,
            condition_id=item.condition_id,
            title=item.title,
            outcome=item.outcome,
            outcome_index=item.outcome_index,
            icon_url=item.icon_url,
            event_slug=item.event_slug,
            market_slug=item.market_slug,
            size=item.size,
            avg_price=item.avg_price,
            current_price=item.current_price,
            initial_value=item.initial_value,
            current_value=item.current_value,
            cash_pnl=item.cash_pnl,
            percent_pnl=item.percent_pnl,
            total_bought=item.total_bought,
            realized_pnl=item.realized_pnl,
            end_date=item.end_date,
            missing_count=0,
            first_seen_at=observed_at,
            last_seen_at=observed_at,
            updated_at=observed_at,
        )

    @staticmethod
    def _update_position(
        position: CurrentPosition,
        item: PositionSnapshot,
        observed_at: datetime,
    ) -> None:
        position.condition_id = item.condition_id
        position.title = item.title
        position.outcome = item.outcome
        position.outcome_index = item.outcome_index
        position.icon_url = item.icon_url
        position.event_slug = item.event_slug
        position.market_slug = item.market_slug
        position.size = item.size
        position.avg_price = item.avg_price
        position.current_price = item.current_price
        position.initial_value = item.initial_value
        position.current_value = item.current_value
        position.cash_pnl = item.cash_pnl
        position.percent_pnl = item.percent_pnl
        position.total_bought = item.total_bought
        position.realized_pnl = item.realized_pnl
        position.end_date = item.end_date
        position.missing_count = 0
        position.last_seen_at = observed_at
        position.updated_at = observed_at

    async def finalize_due_candidates(
        self,
        wallet_id: int,
        *,
        now: datetime | None = None,
    ) -> int:
        now = now or utcnow()
        quiet_cutoff = now - timedelta(seconds=self.settings.quiet_window_seconds)
        async with self.database.sessions() as session:
            wallet = await session.get(WatchedWallet, wallet_id)
            if wallet is None:
                return 0
            candidates = list(
                (
                    await session.scalars(
                        select(PositionChangeCandidate).where(
                            PositionChangeCandidate.wallet_id == wallet_id,
                            or_(
                                PositionChangeCandidate.last_changed_at <= quiet_cutoff,
                                PositionChangeCandidate.hard_deadline_at <= now,
                            ),
                        )
                    )
                ).all()
            )
            proxy_wallet = wallet.proxy_wallet
            meaningful_candidates: list[PositionChangeCandidate] = []
            pruned_candidate = False
            for candidate in candidates:
                if abs(candidate.latest_size - candidate.start_size) <= SIZE_TOLERANCE:
                    await session.delete(candidate)
                    pruned_candidate = True
                else:
                    meaningful_candidates.append(candidate)
            if pruned_candidate:
                await session.commit()
            candidates = meaningful_candidates
        if not candidates:
            return 0

        first_changed_at = min(candidate.first_changed_at for candidate in candidates)
        try:
            trades = await self.client.fetch_trades(
                proxy_wallet,
                condition_ids=(candidate.condition_id for candidate in candidates),
                start=first_changed_at - timedelta(seconds=120),
                end=now + timedelta(seconds=30),
            )
        except PolymarketAPIError:
            hard_due_ids = {
                candidate.id for candidate in candidates if candidate.hard_deadline_at <= now
            }
            if not hard_due_ids:
                return 0
            candidates = [candidate for candidate in candidates if candidate.id in hard_due_ids]
            trades = []

        trades_by_asset: dict[str, list[TradeSnapshot]] = defaultdict(list)
        for trade in trades:
            trades_by_asset[trade.asset_id].append(trade)

        created = 0
        alertable_event_ids: list[int] = []
        async with self.database.sessions() as session:
            for stale_candidate in candidates:
                candidate = await session.get(PositionChangeCandidate, stale_candidate.id)
                if candidate is None:
                    continue
                if abs(candidate.latest_size - candidate.start_size) <= SIZE_TOLERANCE:
                    await session.delete(candidate)
                    continue
                candidate_trades = [
                    trade
                    for trade in trades_by_asset.get(candidate.asset_id, [])
                    if candidate.first_changed_at - timedelta(seconds=120)
                    <= trade.timestamp
                    <= now + timedelta(seconds=30)
                ]
                fingerprinted_trades = self._fingerprinted_trades(proxy_wallet, candidate_trades)
                fingerprints = [fingerprint for fingerprint, _ in fingerprinted_trades]
                consumed_fingerprints: set[str] = set()
                if fingerprints:
                    consumed_fingerprints = set(
                        (
                            await session.scalars(
                                select(PositionEventFill.fingerprint)
                                .join(
                                    PositionEvent,
                                    PositionEventFill.event_id == PositionEvent.id,
                                )
                                .where(
                                    PositionEvent.wallet_id == wallet_id,
                                    PositionEvent.asset_id == candidate.asset_id,
                                    PositionEventFill.fingerprint.in_(fingerprints),
                                )
                            )
                        ).all()
                    )
                unconsumed_trades = [
                    (fingerprint, trade)
                    for fingerprint, trade in fingerprinted_trades
                    if fingerprint not in consumed_fingerprints
                ]
                event = self._build_event(
                    candidate,
                    [trade for _, trade in unconsumed_trades],
                    now,
                )
                session.add(event)
                await session.flush()
                if (
                    wallet.wallet_role == "tracked"
                    and event.type in {"increased", "decreased", "closed"}
                    and event.id is not None
                ):
                    alertable_event_ids.append(event.id)
                for fingerprint, trade in unconsumed_trades:
                    session.add(
                        PositionEventFill(
                            event_id=event.id,
                            fingerprint=fingerprint,
                            side=trade.side,
                            size=trade.size,
                            price=trade.price,
                            amount=trade.size * trade.price,
                            timestamp=trade.timestamp,
                            transaction_hash=trade.transaction_hash,
                        )
                    )
                await session.delete(candidate)
                created += 1
            await session.commit()
        created_alerts = await self._create_overlap_alerts(
            wallet_id,
            alertable_event_ids,
            created_at=now,
        )
        if created_alerts:
            await self.broker.publish("overlap-alerts.created", wallet_id)
        return created

    async def _create_overlap_alerts(
        self,
        tracked_wallet_id: int,
        event_ids: list[int],
        *,
        created_at: datetime,
    ) -> int:
        if not event_ids:
            return 0
        async with self._overlap_lock:
            async with self.database.sessions() as session:
                existing_event_ids = set(
                    (
                        await session.scalars(
                            select(PositionOverlapAlert.event_id).where(
                                PositionOverlapAlert.event_id.in_(event_ids)
                            )
                        )
                    ).all()
                )
                events = list(
                    (
                        await session.scalars(
                            select(PositionEvent).where(PositionEvent.id.in_(event_ids))
                        )
                    ).all()
                )
                created = 0
                for event in events:
                    if event.id in existing_event_ids:
                        continue
                    overlap_period = await session.scalar(
                        select(PositionOverlapPeriod)
                        .where(
                            PositionOverlapPeriod.tracked_wallet_id == tracked_wallet_id,
                            PositionOverlapPeriod.asset_id == event.asset_id,
                            PositionOverlapPeriod.started_at <= event.first_detected_at,
                            or_(
                                PositionOverlapPeriod.ended_at.is_(None),
                                PositionOverlapPeriod.ended_at >= event.first_detected_at,
                            ),
                        )
                        .order_by(PositionOverlapPeriod.started_at.desc())
                    )
                    if overlap_period is None:
                        continue
                    session.add(
                        PositionOverlapAlert(
                            period_id=overlap_period.id,
                            event_id=event.id,
                            my_wallet_id=overlap_period.my_wallet_id,
                            tracked_wallet_id=tracked_wallet_id,
                            created_at=created_at,
                            read_at=None,
                        )
                    )
                    created += 1
                await session.commit()
                return created

    @staticmethod
    def _build_event(
        candidate: PositionChangeCandidate,
        trades: list[TradeSnapshot],
        settled_at: datetime,
    ) -> PositionEvent:
        delta = candidate.latest_size - candidate.start_size
        if candidate.start_size == ZERO and candidate.latest_size > ZERO:
            event_type = "opened"
        elif candidate.latest_size == ZERO and candidate.start_size > ZERO:
            event_type = "closed"
        elif delta > ZERO:
            event_type = "increased"
        else:
            event_type = "decreased"

        net_fill_size = sum(
            (trade.size if trade.side == "BUY" else -trade.size for trade in trades),
            start=ZERO,
        )
        if not trades:
            reconciliation_status = "unavailable"
            average_fill_price = None
        elif fills_reconcile(net_fill_size, delta):
            reconciliation_status = "matched"
            average_fill_price = WalletMonitor._weighted_average_price(trades)
        else:
            reconciliation_status = "partial"
            average_fill_price = WalletMonitor._weighted_average_price(trades)

        return PositionEvent(
            wallet_id=candidate.wallet_id,
            asset_id=candidate.asset_id,
            condition_id=candidate.condition_id,
            type=event_type,
            title=candidate.title,
            outcome=candidate.outcome,
            event_slug=candidate.event_slug,
            delta_size=delta,
            before_size=candidate.start_size,
            after_size=candidate.latest_size,
            before_avg_price=candidate.before_avg_price,
            after_avg_price=candidate.after_avg_price,
            average_fill_price=average_fill_price,
            current_value=candidate.latest_current_value,
            reconciliation_status=reconciliation_status,
            first_detected_at=candidate.first_changed_at,
            settled_at=settled_at,
        )

    @staticmethod
    def _weighted_average_price(trades: Iterable[TradeSnapshot]) -> Decimal | None:
        total_size = ZERO
        total_amount = ZERO
        for trade in trades:
            total_size += abs(trade.size)
            total_amount += abs(trade.size) * trade.price
        if total_size == ZERO:
            return None
        return total_amount / total_size

    @staticmethod
    def _redemption_asset_id(redemption: RedemptionSnapshot) -> str:
        if redemption.asset_id:
            return redemption.asset_id
        outcome_index = (
            str(redemption.outcome_index) if redemption.outcome_index is not None else "unknown"
        )
        return f"redeem:{redemption.condition_id}:{outcome_index}"

    @staticmethod
    def _redemption_fingerprint(
        proxy_wallet: str,
        redemption: RedemptionSnapshot,
    ) -> str:
        base = "|".join(
            [
                proxy_wallet,
                "REDEEM",
                redemption.transaction_hash or "",
                redemption.condition_id,
                str(redemption.outcome_index),
                redemption.timestamp.isoformat(),
                str(redemption.size),
                str(redemption.usdc_size),
            ]
        )
        return hashlib.sha256(base.encode()).hexdigest()

    @staticmethod
    def _fingerprinted_trades(
        proxy_wallet: str,
        trades: list[TradeSnapshot],
    ) -> list[tuple[str, TradeSnapshot]]:
        return fingerprint_trades(proxy_wallet, trades)
