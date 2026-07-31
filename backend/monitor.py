from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.broker import EventBroker
from backend.config import Settings
from backend.db import Database
from backend.models import (
    CurrentPosition,
    PositionChangeCandidate,
    PositionEvent,
    PositionEventFill,
    WalletTrade,
    WatchedWallet,
)
from backend.polymarket import (
    PolymarketAPIError,
    PolymarketClient,
    PositionSnapshot,
    SettlementEvidence,
    TradeSnapshot,
)

LOGGER = logging.getLogger(__name__)
ZERO = Decimal("0")
SIZE_TOLERANCE = Decimal("0.000000001")
RECONCILIATION_TOLERANCE = Decimal("0.000001")


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
        await self.broker.publish("sync.status", wallet_id)

        try:
            snapshot = await self.client.fetch_active_positions(proxy_wallet)
            trade_history_error: str | None = None
            try:
                await self._sync_trade_history(
                    wallet_id,
                    snapshot,
                    observed_at=attempt_at,
                )
            except Exception as error:
                trade_history_error = str(error)[:1000]
                LOGGER.warning(
                    "Wallet trade history sync failed for wallet_id=%s: %s",
                    wallet_id,
                    error,
                )
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
            evidence = SettlementEvidence(frozenset(), frozenset(), frozenset())
            if confirmable:
                evidence = await self.client.fetch_settlement_evidence(
                    proxy_wallet,
                    condition_ids=(position.condition_id for position in confirmable),
                    last_seen_by_condition={
                        position.condition_id: position.last_seen_at for position in confirmable
                    },
                )

            success_at = utcnow()
            await self._apply_snapshot(
                wallet_id,
                snapshot,
                evidence=evidence,
                observed_at=success_at,
            )
            async with self.database.sessions() as session:
                wallet = await session.get(WatchedWallet, wallet_id)
                if wallet is None:
                    return False
                wallet.status = "ok"
                wallet.last_success_at = success_at
                wallet.last_error = None
                wallet.trade_history_error = trade_history_error
                wallet.consecutive_failures = 0
                wallet.next_sync_at = success_at + timedelta(
                    seconds=self.settings.poll_interval_seconds
                )
                wallet.updated_at = success_at
                await session.commit()
            await self.broker.publish("positions.updated", wallet_id)
            created_events = await self.finalize_due_candidates(wallet_id, now=success_at)
            if created_events:
                await self.broker.publish("events.created", wallet_id)
            await self.broker.publish("sync.status", wallet_id)
            return True
        except Exception as error:
            await self._record_failure(wallet_id, error)
            await self.broker.publish("sync.status", wallet_id)
            return False

    async def _sync_trade_history(
        self,
        wallet_id: int,
        snapshot: list[PositionSnapshot],
        *,
        observed_at: datetime,
    ) -> None:
        active_assets = {item.asset_id for item in snapshot}
        condition_ids = {item.condition_id for item in snapshot}
        async with self.database.sessions() as session:
            wallet = await session.get(WatchedWallet, wallet_id)
            if wallet is None:
                return
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
            for fingerprint, trade in fingerprinted:
                if fingerprint in existing_fingerprints:
                    continue
                session.add(
                    WalletTrade(
                        wallet_id=wallet_id,
                        fingerprint=fingerprint,
                        asset_id=trade.asset_id,
                        condition_id=trade.condition_id,
                        side=trade.side,
                        size=trade.size,
                        price=trade.price,
                        amount=trade.size * trade.price,
                        timestamp=trade.timestamp,
                        transaction_hash=trade.transaction_hash,
                        imported_at=observed_at,
                    )
                )
            wallet = await session.get(WatchedWallet, wallet_id)
            if wallet is not None:
                wallet.trade_history_synced_at = observed_at
                wallet.trade_history_error = None
            await session.commit()

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
    ) -> None:
        incoming = {item.asset_id: item for item in snapshot}
        async with self.database.sessions() as session:
            wallet = await session.get(WatchedWallet, wallet_id)
            if wallet is None:
                return
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
                return

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
                    # change can settle normally, while suppressing only 50 -> 0.
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
        elif abs(net_fill_size - delta) <= RECONCILIATION_TOLERANCE:
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
    def _fingerprinted_trades(
        proxy_wallet: str,
        trades: list[TradeSnapshot],
    ) -> list[tuple[str, TradeSnapshot]]:
        occurrences: dict[str, int] = defaultdict(int)
        results: list[tuple[str, TradeSnapshot]] = []
        for trade in sorted(
            trades,
            key=lambda item: (
                item.timestamp,
                item.transaction_hash or "",
                item.asset_id,
                item.side,
                item.price,
                item.size,
            ),
        ):
            base = "|".join(
                [
                    proxy_wallet,
                    trade.transaction_hash or "",
                    trade.asset_id,
                    trade.side,
                    str(trade.price),
                    str(trade.size),
                ]
            )
            occurrence = occurrences[base]
            occurrences[base] += 1
            fingerprint = hashlib.sha256(f"{base}|{occurrence}".encode()).hexdigest()
            results.append((fingerprint, trade))
        return results
