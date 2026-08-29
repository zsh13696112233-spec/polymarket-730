from __future__ import annotations

import asyncio
import json
import smtplib
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from email.message import EmailMessage
from email.utils import formataddr
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import Settings
from backend.db import Database
from backend.keychain import KeychainReference, MacOSKeychain
from backend.models import (
    EmailRecipient,
    EmailSettings,
    WhaleEmailDelivery,
    WhaleEntry,
    WhaleEntryRuleState,
    WhaleMarket,
    WhaleWallet,
)
from backend.time_utils import utcnow

BEIJING = ZoneInfo("Asia/Shanghai")
RULE_LABELS = {"new_account": "新号大额", "large_amount": "全量超大额"}
MARKET_CATEGORY_LABELS = {
    "esports": "电竞",
    "sports": "传统体育",
    "politics": "政治",
    "crypto": "加密",
    "science_tech": "科学与科技",
    "entertainment": "娱乐",
    "other": "其他",
}
SCIENCE_TECH_TAGS = frozenset(
    {"science", "spacex", "space-exploration", "software-updates", "climate", "weather"}
)
ENTERTAINMENT_TAGS = frozenset(
    {
        "movies",
        "music",
        "awards",
        "box-office",
        "celebrity-events",
        "new-releases",
        "gaming",
        "video-games",
    }
)
RETRY_DELAYS = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
)
MAX_ATTEMPTS = 5
SMTP_KEYCHAIN_SERVICE = "com.polycopy.smtp"
WEEKLY_SUMMARY_CONDITION_ID = "weekly-summary"


@dataclass(frozen=True, slots=True)
class SMTPTransport:
    host: str
    port: int
    security: str
    username: str
    password: str
    from_email: str
    from_name: str


@dataclass(frozen=True, slots=True)
class WhaleEmailCandidate:
    entry_id: int
    new_rules: frozenset[str]


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def weekly_summary_period(now: datetime) -> tuple[datetime, datetime, datetime]:
    """Return scheduled time and previous Beijing calendar week as naive UTC values."""
    aware_now = _as_utc_naive(now).replace(tzinfo=UTC)
    local_now = aware_now.astimezone(BEIJING)
    current_week_start_local = datetime.combine(
        local_now.date() - timedelta(days=local_now.weekday()),
        time.min,
        tzinfo=BEIJING,
    )
    period_end = current_week_start_local.astimezone(UTC).replace(tzinfo=None)
    period_start = (
        (current_week_start_local - timedelta(days=7)).astimezone(UTC).replace(tzinfo=None)
    )
    return period_end, period_start, period_end


def next_weekly_summary_run(now: datetime) -> datetime:
    aware_now = _as_utc_naive(now).replace(tzinfo=UTC)
    local_now = aware_now.astimezone(BEIJING)
    days_until_monday = 7 - local_now.weekday()
    next_local = datetime.combine(
        local_now.date() + timedelta(days=days_until_monday),
        time.min,
        tzinfo=BEIJING,
    )
    return next_local.astimezone(UTC).replace(tzinfo=None)


def _ordered_rules(rules: set[str] | frozenset[str]) -> list[str]:
    return [rule for rule in ("new_account", "large_amount") if rule in rules]


def _decimal_text(value, *, places: int = 2) -> str:
    return f"{value:,.{places}f}".rstrip("0").rstrip(".")


def _wallet_label(wallet: WhaleWallet | None, entry: WhaleEntry) -> str:
    return (
        (wallet.display_name or wallet.pseudonym) if wallet is not None else None
    ) or entry.proxy_wallet


def _market_category_label(tags_json: str | None) -> str:
    try:
        tags = json.loads(tags_json or "[]")
    except (TypeError, ValueError):
        tags = []
    slugs = {
        str(tag.get("slug") or "").strip().lower()
        for tag in tags
        if isinstance(tag, dict) and tag.get("slug")
    }
    if "esports" in slugs:
        category = "esports"
    elif "sports" in slugs:
        category = "sports"
    elif "politics" in slugs:
        category = "politics"
    elif "crypto" in slugs:
        category = "crypto"
    elif slugs & SCIENCE_TECH_TAGS:
        category = "science_tech"
    elif slugs & ENTERTAINMENT_TAGS:
        category = "entertainment"
    else:
        category = "other"
    return MARKET_CATEGORY_LABELS[category]


def _add_entry_delivery(
    session: AsyncSession,
    *,
    entry: WhaleEntry,
    market: WhaleMarket | None,
    wallet: WhaleWallet | None,
    new_rules: frozenset[str],
    recipient: str,
    triggered_at: datetime,
) -> None:
    rules = _ordered_rules(new_rules)
    rule_key = ",".join(rules)
    rule_labels = " + ".join(RULE_LABELS[rule] for rule in rules)
    market_title = market.title if market is not None else entry.condition_id
    wallet_label = _wallet_label(wallet, entry)
    local_time = triggered_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(BEIJING)
    body = "\n".join(
        (
            f"命中规则：{rule_labels}",
            f"市场：{market_title} / {entry.outcome}",
            f"钱包：{wallet_label}（{entry.proxy_wallet}）",
            f"近 24 小时累计买入：{_decimal_text(entry.gross_buy_usdc)} USDC",
            f"买入均价：{_decimal_text(entry.avg_buy_price, places=4)} USDC"
            f"（{_decimal_text(entry.avg_buy_price * 100)}¢）",
            f"触发时间：{local_time:%Y-%m-%d %H:%M:%S} 北京时间",
        )
    )
    session.add(
        WhaleEmailDelivery(
            entry_id=entry.id,
            notification_kind="entry",
            condition_id=entry.condition_id,
            entry_ids_json=json.dumps([entry.id]),
            dedupe_key=f"entry:{entry.id}:{rule_key}",
            rule_key=rule_key,
            rules_json=json.dumps(rules),
            recipient_email=recipient,
            market_title=market_title,
            wallet_label=wallet_label,
            subject=f"[PolyCopy] {rule_labels}提醒",
            body_text=body,
            status="pending",
            attempt_count=0,
            next_attempt_at=triggered_at,
            created_at=triggered_at,
        )
    )


def _add_divergence_delivery(
    session: AsyncSession,
    *,
    condition_id: str,
    market: WhaleMarket | None,
    directional_entries: list[WhaleEntry],
    active_rules: dict[int, set[str]],
    wallets: dict[str, WhaleWallet],
    recipient: str,
    upgraded: bool,
    triggered_at: datetime,
) -> None:
    sides: dict[str, list[WhaleEntry]] = defaultdict(list)
    for entry in directional_entries:
        sides[entry.asset_id].append(entry)
    ordered_sides = sorted(
        sides.values(),
        key=lambda entries: sum((entry.gross_buy_usdc for entry in entries), start=0),
        reverse=True,
    )
    side_totals = [
        sum((entry.gross_buy_usdc for entry in entries), start=0) for entries in ordered_sides
    ]
    total_usdc = sum(side_totals, start=0)
    difference_usdc = side_totals[0] - side_totals[1]
    market_title = market.title if market is not None else condition_id
    local_time = triggered_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(BEIJING)
    body_lines = [
        "市场状态升级为方向分歧。" if upgraded else "同一市场相反方向同时出现大额有效持仓。",
        f"市场：{market_title}",
        "",
    ]
    for entries, side_total in zip(ordered_sides, side_totals, strict=True):
        share = side_total / total_usdc * 100 if total_usdc else 0
        body_lines.append(
            f"{entries[0].outcome}：{_decimal_text(side_total)} USDC（{_decimal_text(share)}%）"
        )
        for entry in sorted(entries, key=lambda item: item.gross_buy_usdc, reverse=True):
            label = _wallet_label(wallets.get(entry.proxy_wallet), entry)
            body_lines.append(
                f"- {label}（{entry.proxy_wallet}）："
                f"{_decimal_text(entry.gross_buy_usdc)} USDC，"
                f"均价 {_decimal_text(entry.avg_buy_price, places=4)} USDC"
            )
        body_lines.append("")
    body_lines.extend(
        (
            f"两侧方向性买入合计：{_decimal_text(total_usdc)} USDC",
            f"领先方向净差：{_decimal_text(difference_usdc)} USDC",
            "结论：方向高度分歧，不应把任一侧视为明确跟单信号。",
            f"触发时间：{local_time:%Y-%m-%d %H:%M:%S} 北京时间",
        )
    )
    entry_ids = sorted(entry.id for entry in directional_entries)
    rules = _ordered_rules(
        set().union(*(active_rules.get(entry.id, set()) for entry in directional_entries))
    )
    labels = list(
        dict.fromkeys(
            _wallet_label(wallets.get(entry.proxy_wallet), entry) for entry in directional_entries
        )
    )
    subject_prefix = "市场状态升级：方向分歧" if upgraded else "分歧市场提醒"
    session.add(
        WhaleEmailDelivery(
            entry_id=None,
            notification_kind="divergence",
            condition_id=condition_id,
            entry_ids_json=json.dumps(entry_ids),
            dedupe_key=f"divergence:{condition_id}",
            rule_key="market_divergence",
            rules_json=json.dumps(rules),
            recipient_email=recipient,
            market_title=market_title,
            wallet_label=f"{len(labels)} 个钱包 · {len(ordered_sides)} 个方向",
            subject=f"[PolyCopy] {subject_prefix}｜{market_title}",
            body_text="\n".join(body_lines),
            status="pending",
            attempt_count=0,
            next_attempt_at=triggered_at,
            created_at=triggered_at,
        )
    )


async def enqueue_whale_email_deliveries(
    session: AsyncSession,
    *,
    candidates: list[WhaleEmailCandidate],
    triggered_at: datetime,
) -> None:
    if not candidates:
        return
    recipients = list(
        await session.scalars(select(EmailRecipient.email).where(EmailRecipient.enabled.is_(True)))
    )
    if not recipients:
        return
    candidate_rules = {candidate.entry_id: candidate.new_rules for candidate in candidates}
    candidate_entries = list(
        await session.scalars(select(WhaleEntry).where(WhaleEntry.id.in_(candidate_rules)))
    )
    if not candidate_entries:
        return
    condition_ids = {entry.condition_id for entry in candidate_entries}
    all_entries = list(
        await session.scalars(select(WhaleEntry).where(WhaleEntry.condition_id.in_(condition_ids)))
    )
    entries_by_id = {entry.id: entry for entry in all_entries}
    markets = {
        market.condition_id: market
        for market in await session.scalars(
            select(WhaleMarket).where(WhaleMarket.condition_id.in_(condition_ids))
        )
    }
    wallet_addresses = {entry.proxy_wallet for entry in all_entries}
    wallets = {
        wallet.proxy_wallet: wallet
        for wallet in await session.scalars(
            select(WhaleWallet).where(WhaleWallet.proxy_wallet.in_(wallet_addresses))
        )
    }
    active_rules: dict[int, set[str]] = defaultdict(set)
    for entry_id, rule_type in (
        await session.execute(
            select(WhaleEntryRuleState.entry_id, WhaleEntryRuleState.rule_type)
            .join(WhaleEntry, WhaleEntry.id == WhaleEntryRuleState.entry_id)
            .where(
                WhaleEntry.condition_id.in_(condition_ids),
                WhaleEntryRuleState.active.is_(True),
                WhaleEntry.net_size > 0,
            )
        )
    ).all():
        active_rules[entry_id].add(rule_type)

    existing_deliveries = list(
        await session.scalars(
            select(WhaleEmailDelivery).where(
                WhaleEmailDelivery.condition_id.in_(condition_ids),
                WhaleEmailDelivery.recipient_email.in_(recipients),
            )
        )
    )
    existing_keys = {
        (delivery.dedupe_key, delivery.recipient_email) for delivery in existing_deliveries
    }
    prior_entry_recipients = {
        (delivery.condition_id, delivery.recipient_email)
        for delivery in existing_deliveries
        if delivery.notification_kind == "entry"
    }
    candidates_by_condition: dict[str, list[WhaleEntry]] = defaultdict(list)
    for entry in candidate_entries:
        candidates_by_condition[entry.condition_id].append(entry)

    for condition_id, condition_candidates in candidates_by_condition.items():
        active_entries = [
            entry
            for entry in all_entries
            if entry.condition_id == condition_id and entry.id in active_rules
        ]
        wallet_sides: dict[str, set[str]] = defaultdict(set)
        for entry in active_entries:
            wallet_sides[entry.proxy_wallet.lower()].add(entry.asset_id)
        hedging_wallets = {wallet for wallet, sides in wallet_sides.items() if len(sides) > 1}
        directional_entries = [
            entry for entry in active_entries if entry.proxy_wallet.lower() not in hedging_wallets
        ]
        divergent = len({entry.asset_id for entry in directional_entries}) >= 2
        if divergent:
            for recipient in recipients:
                dedupe_key = f"divergence:{condition_id}"
                if (dedupe_key, recipient) in existing_keys:
                    continue
                _add_divergence_delivery(
                    session,
                    condition_id=condition_id,
                    market=markets.get(condition_id),
                    directional_entries=directional_entries,
                    active_rules=active_rules,
                    wallets=wallets,
                    recipient=recipient,
                    upgraded=(condition_id, recipient) in prior_entry_recipients,
                    triggered_at=triggered_at,
                )
            continue

        for entry in condition_candidates:
            rules = candidate_rules[entry.id]
            rule_key = ",".join(_ordered_rules(rules))
            dedupe_key = f"entry:{entry.id}:{rule_key}"
            for recipient in recipients:
                if (dedupe_key, recipient) in existing_keys:
                    continue
                _add_entry_delivery(
                    session,
                    entry=entries_by_id[entry.id],
                    market=markets.get(condition_id),
                    wallet=wallets.get(entry.proxy_wallet),
                    new_rules=rules,
                    recipient=recipient,
                    triggered_at=triggered_at,
                )


async def weekly_email_summary_metrics(
    database: Database,
    *,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, int | Decimal | None]:
    """Summarize unique emailed signals that settled in the requested Beijing week."""
    async with database.sessions() as session:
        settled_entries = list(
            await session.scalars(
                select(WhaleEntry)
                .join(WhaleEmailDelivery, WhaleEmailDelivery.entry_id == WhaleEntry.id)
                .where(
                    WhaleEmailDelivery.notification_kind == "entry",
                    WhaleEmailDelivery.status == "sent",
                    WhaleEmailDelivery.sent_at.is_not(None),
                    WhaleEmailDelivery.sent_at <= WhaleEntry.settled_at,
                    WhaleEntry.settled_at >= period_start,
                    WhaleEntry.settled_at < period_end,
                )
                .distinct()
            )
        )
        pending_count = int(
            await session.scalar(
                select(func.count(func.distinct(WhaleEmailDelivery.entry_id)))
                .join(WhaleEntry, WhaleEntry.id == WhaleEmailDelivery.entry_id)
                .where(
                    WhaleEmailDelivery.notification_kind == "entry",
                    WhaleEmailDelivery.status == "sent",
                    WhaleEmailDelivery.sent_at.is_not(None),
                    WhaleEmailDelivery.sent_at < period_end,
                    or_(
                        WhaleEntry.settled_at.is_(None),
                        WhaleEntry.settled_at > period_end,
                    ),
                )
            )
            or 0
        )

    hit_count = sum(entry.settlement_price == 1 for entry in settled_entries)
    miss_count = sum(entry.settlement_price == 0 for entry in settled_entries)
    special_count = len(settled_entries) - hit_count - miss_count
    effective_count = hit_count + miss_count
    hit_rate_percent = (
        Decimal(hit_count) / Decimal(effective_count) * Decimal("100") if effective_count else None
    )
    return {
        "settled_count": len(settled_entries),
        "effective_count": effective_count,
        "hit_count": hit_count,
        "miss_count": miss_count,
        "special_count": special_count,
        "pending_count": pending_count,
        "hit_rate_percent": hit_rate_percent,
    }


def _weekly_summary_content(
    *,
    period_start: datetime,
    period_end: datetime,
    metrics: dict[str, int | Decimal | None],
) -> tuple[str, str, str]:
    local_start = period_start.replace(tzinfo=UTC).astimezone(BEIJING).date()
    local_end = (period_end.replace(tzinfo=UTC).astimezone(BEIJING) - timedelta(days=1)).date()
    period_label = f"{local_start:%Y-%m-%d} 至 {local_end:%Y-%m-%d}"
    hit_rate = metrics["hit_rate_percent"]
    hit_rate_text = (
        f"{_decimal_text(hit_rate)}%" if isinstance(hit_rate, Decimal) else "暂无有效样本"
    )
    subject = f"[PolyCopy] 每周邮件命中率｜{local_start:%m-%d} 至 {local_end:%m-%d}"
    body = "\n".join(
        (
            "PolyCopy 每周邮件命中率汇总",
            f"统计周期：{period_label}（北京时间）",
            "统计口径：统计周期内新结算，且结算前已成功发送提醒的唯一信号",
            "",
            f"有效样本：{metrics['effective_count']}",
            f"命中：{metrics['hit_count']}",
            f"未命中：{metrics['miss_count']}",
            f"特殊结算：{metrics['special_count']}（不计入命中率）",
            f"有效命中率：{hit_rate_text}",
            "",
            f"截止本周期末仍待结算：{metrics['pending_count']}",
        )
    )
    return subject, body, period_label


async def enqueue_due_weekly_summary(
    database: Database,
    *,
    now: datetime | None = None,
) -> int:
    """Enqueue the most recent weekly report once per active recipient."""
    current = _as_utc_naive(now or utcnow())
    scheduled_for, period_start, period_end = weekly_summary_period(current)
    if current < scheduled_for:
        return 0

    async with database.sessions() as session:
        settings = await session.get(EmailSettings, 1)
        if (
            settings is None
            or not settings.notifications_enabled
            or not settings.weekly_summary_enabled
            or settings.weekly_summary_enabled_at is None
            or settings.weekly_summary_enabled_at > scheduled_for
        ):
            return 0
        recipients = list(
            await session.scalars(
                select(EmailRecipient.email)
                .where(EmailRecipient.enabled.is_(True))
                .order_by(EmailRecipient.email)
            )
        )
        if not recipients:
            return 0
        local_period_start = period_start.replace(tzinfo=UTC).astimezone(BEIJING).date()
        dedupe_key = f"weekly-summary:{local_period_start.isoformat()}"
        existing_recipients = set(
            await session.scalars(
                select(WhaleEmailDelivery.recipient_email).where(
                    WhaleEmailDelivery.dedupe_key == dedupe_key,
                    WhaleEmailDelivery.recipient_email.in_(recipients),
                )
            )
        )
        missing_recipients = [email for email in recipients if email not in existing_recipients]
        if not missing_recipients:
            return 0

    metrics = await weekly_email_summary_metrics(
        database,
        period_start=period_start,
        period_end=period_end,
    )
    subject, body, period_label = _weekly_summary_content(
        period_start=period_start,
        period_end=period_end,
        metrics=metrics,
    )
    async with database.sessions() as session:
        for recipient in missing_recipients:
            session.add(
                WhaleEmailDelivery(
                    entry_id=None,
                    notification_kind="weekly_summary",
                    condition_id=WEEKLY_SUMMARY_CONDITION_ID,
                    entry_ids_json="[]",
                    dedupe_key=dedupe_key,
                    rule_key="weekly_summary",
                    rules_json="[]",
                    recipient_email=recipient,
                    market_title=f"每周邮件命中率｜{period_label}",
                    wallet_label=(
                        f"命中 {metrics['hit_count']} · 未命中 {metrics['miss_count']} · "
                        f"待结算 {metrics['pending_count']}"
                    ),
                    subject=subject,
                    body_text=body,
                    status="pending",
                    attempt_count=0,
                    next_attempt_at=current,
                    created_at=current,
                )
            )
        await session.commit()
    return len(missing_recipients)


class WhaleEmailNotifier:
    def __init__(
        self,
        *,
        database: Database,
        settings: Settings,
        keychain: MacOSKeychain | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.keychain = keychain
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._test_lock = asyncio.Lock()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="whale-email-notifier")

    def wake(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        await self.recover_stale_deliveries()
        while True:
            try:
                await enqueue_due_weekly_summary(self.database)
                sent = await self.deliver_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                sent = False
            self._wake.clear()
            if sent:
                continue
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=30)
            except TimeoutError:
                pass

    async def recover_stale_deliveries(self) -> None:
        cutoff = utcnow() - timedelta(minutes=10)
        async with self.database.sessions() as session:
            rows = list(
                await session.scalars(
                    select(WhaleEmailDelivery).where(
                        WhaleEmailDelivery.status == "sending",
                        WhaleEmailDelivery.locked_at < cutoff,
                    )
                )
            )
            for row in rows:
                row.status = "retrying"
                row.locked_at = None
                row.next_attempt_at = utcnow()
                row.last_error = "发送进程中断，任务已恢复"
            await session.commit()

    async def deliver_once(self) -> bool:
        now = utcnow()
        async with self.database.sessions() as session:
            row = await session.scalar(
                select(WhaleEmailDelivery)
                .where(
                    WhaleEmailDelivery.status.in_(("pending", "retrying")),
                    or_(
                        WhaleEmailDelivery.next_attempt_at.is_(None),
                        WhaleEmailDelivery.next_attempt_at <= now,
                    ),
                )
                .order_by(WhaleEmailDelivery.created_at, WhaleEmailDelivery.id)
                .limit(1)
            )
            if row is None:
                return False
            row.status = "sending"
            row.locked_at = now
            row.attempt_count += 1
            await session.commit()
            delivery_id = row.id

        try:
            transport = await self.load_transport()
            await asyncio.to_thread(self._send, row, transport)
        except Exception as error:
            async with self.database.sessions() as session:
                current = await session.get(WhaleEmailDelivery, delivery_id)
                if current is not None:
                    current.locked_at = None
                    current.last_error = str(error)[:2000]
                    if current.attempt_count >= MAX_ATTEMPTS:
                        current.status = "failed"
                        current.next_attempt_at = None
                    else:
                        current.status = "retrying"
                        current.next_attempt_at = utcnow() + RETRY_DELAYS[current.attempt_count - 1]
                    await session.commit()
            return True

        async with self.database.sessions() as session:
            current = await session.get(WhaleEmailDelivery, delivery_id)
            if current is not None:
                current.status = "sent"
                current.sent_at = utcnow()
                current.next_attempt_at = None
                current.locked_at = None
                current.last_error = None
                await session.commit()
        return True

    async def load_transport(self) -> SMTPTransport:
        async with self.database.sessions() as session:
            row = await session.get(EmailSettings, 1)
        if row is not None and row.smtp_host:
            host = row.smtp_host
            port = row.smtp_port
            security = row.smtp_security
            username = row.smtp_username or ""
            from_email = row.smtp_from_email or username
            from_name = row.smtp_from_name
            if row.smtp_keychain_service and row.smtp_keychain_account and self.keychain:
                password = await asyncio.to_thread(
                    self.keychain.get_secret,
                    KeychainReference(
                        service=row.smtp_keychain_service,
                        account=row.smtp_keychain_account,
                    ),
                )
            else:
                password = self.settings.smtp_password or ""
        else:
            host = self.settings.smtp_host or ""
            port = self.settings.smtp_port
            security = self.settings.smtp_security
            username = self.settings.smtp_username or ""
            from_email = self.settings.smtp_from_email or username
            from_name = self.settings.smtp_from_name
            password = self.settings.smtp_password or ""
        if not host or not username or not password or not from_email:
            raise RuntimeError("SMTP 配置不完整，请填写邮箱账号和客户端授权码")
        return SMTPTransport(
            host=host,
            port=port,
            security=security,
            username=username,
            password=password,
            from_email=from_email,
            from_name=from_name,
        )

    async def test_connection(self, *, recipient_email: str | None = None) -> dict:
        async with self._test_lock:
            transport = await self.load_transport()
            return await asyncio.to_thread(self._test_connection, transport, recipient_email)

    def _send(self, delivery: WhaleEmailDelivery, transport: SMTPTransport) -> None:
        message = EmailMessage()
        message["Subject"] = delivery.subject
        message["From"] = formataddr((transport.from_name, transport.from_email))
        message["To"] = delivery.recipient_email
        message_domain = transport.from_email.split("@")[-1]
        message["Message-ID"] = f"<whale-delivery-{delivery.id}@{message_domain}>"
        message.set_content(delivery.body_text)

        with self._open_client(transport) as client:
            client.send_message(message)

    def _test_connection(self, transport: SMTPTransport, recipient_email: str | None) -> dict:
        message_sent = False
        with self._open_client(transport) as client:
            code, _ = client.noop()
            if code != 250:
                raise RuntimeError(f"SMTP 会话检查失败（状态码 {code}）")
            if recipient_email:
                message = EmailMessage()
                message["Subject"] = "[PolyCopy] 163 邮件连接测试"
                message["From"] = formataddr((transport.from_name, transport.from_email))
                message["To"] = recipient_email
                message["Message-ID"] = (
                    f"<smtp-test-{int(datetime.now().timestamp())}@"
                    f"{transport.from_email.split('@')[-1]}>"
                )
                message.set_content("PolyCopy 已成功连接 163 SMTP 并提交此测试邮件。")
                client.send_message(message)
                message_sent = True
        return {
            "status": "ok",
            "connection": "ok",
            "tls": "not_used" if transport.security == "none" else "ok",
            "authentication": "ok",
            "message_sent": message_sent,
            "detail": "测试邮件已提交给 163 SMTP" if message_sent else "163 SMTP 连接及认证成功",
        }

    def _open_client(self, transport: SMTPTransport):
        smtp_class = smtplib.SMTP_SSL if transport.security == "ssl" else smtplib.SMTP
        client = smtp_class(transport.host, transport.port, timeout=15)
        try:
            if transport.security == "starttls":
                client.starttls()
            client.login(transport.username, transport.password)
            return client
        except Exception:
            client.close()
            raise


async def list_whale_email_deliveries(
    database: Database, *, status: str, limit: int, offset: int
) -> dict:
    filters = [] if status == "all" else [WhaleEmailDelivery.status == status]
    async with database.sessions() as session:
        total = int(
            await session.scalar(select(func.count(WhaleEmailDelivery.id)).where(*filters)) or 0
        )
        rows = list(
            await session.scalars(
                select(WhaleEmailDelivery)
                .where(*filters)
                .order_by(WhaleEmailDelivery.created_at.desc(), WhaleEmailDelivery.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        row_entry_ids = {row.id: json.loads(row.entry_ids_json) for row in rows}
        entry_ids = list(
            dict.fromkeys(entry_id for ids in row_entry_ids.values() for entry_id in ids)
        )
        entries = list(
            await session.scalars(select(WhaleEntry).where(WhaleEntry.id.in_(entry_ids)))
        )
        entries_by_id = {entry.id: entry for entry in entries}
        condition_ids = {row.condition_id for row in rows}
        markets = list(
            await session.scalars(
                select(WhaleMarket).where(WhaleMarket.condition_id.in_(condition_ids))
            )
        )
        markets_by_condition = {market.condition_id: market for market in markets}

    def result_for(row: WhaleEmailDelivery) -> str:
        if row.notification_kind == "divergence" or row.entry_id is None:
            return "not_applicable"
        entry = entries_by_id.get(row.entry_id)
        settlement_price = entry.settlement_price if entry is not None else None
        if settlement_price is None:
            return "pending"
        if settlement_price == 1:
            return "hit"
        if settlement_price == 0:
            return "miss"
        return "special"

    def market_summaries_for(row: WhaleEmailDelivery) -> list[dict]:
        market = markets_by_condition.get(row.condition_id)
        category_label = _market_category_label(market.tags_json if market is not None else None)
        grouped: dict[str, list[WhaleEntry]] = defaultdict(list)
        for entry_id in row_entry_ids[row.id]:
            entry = entries_by_id.get(entry_id)
            if entry is not None:
                grouped[entry.asset_id].append(entry)

        summaries = []
        for grouped_entries in grouped.values():
            gross_buy_usdc = sum(
                (entry.gross_buy_usdc for entry in grouped_entries), start=Decimal("0")
            ).quantize(Decimal("0.01"))
            gross_buy_size = sum(
                (entry.gross_buy_size for entry in grouped_entries), start=Decimal("0")
            )
            summaries.append(
                {
                    "category_label": category_label,
                    "outcome": grouped_entries[0].outcome,
                    "avg_buy_price": (
                        gross_buy_usdc / gross_buy_size
                        if gross_buy_size > 0
                        else grouped_entries[0].avg_buy_price
                    ).quantize(Decimal("0.0001")),
                    "gross_buy_usdc": gross_buy_usdc,
                }
            )
        return sorted(summaries, key=lambda item: item["gross_buy_usdc"], reverse=True)

    return {
        "total": total,
        "items": [
            {
                "id": row.id,
                "entry_id": row.entry_id,
                "notification_kind": row.notification_kind,
                "condition_id": row.condition_id,
                "entry_ids": row_entry_ids[row.id],
                "rules": json.loads(row.rules_json),
                "recipient_email": row.recipient_email,
                "market_title": row.market_title,
                "wallet_label": row.wallet_label,
                "market_summaries": market_summaries_for(row),
                "subject": row.subject,
                "body_text": row.body_text,
                "result": result_for(row),
                "status": row.status,
                "attempt_count": row.attempt_count,
                "next_attempt_at": row.next_attempt_at,
                "last_error": row.last_error,
                "created_at": row.created_at,
                "sent_at": row.sent_at,
            }
            for row in rows
        ],
    }
