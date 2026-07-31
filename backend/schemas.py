from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_serializer,
)

DecimalNumber = Annotated[
    Decimal,
    PlainSerializer(lambda value: float(value), return_type=float, when_used="json"),
]


def _as_utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class WalletCreate(APIModel):
    address: str = Field(min_length=1, max_length=500)
    label: str | None = Field(default=None, min_length=1, max_length=100)


class WalletUpdate(APIModel):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None


class WalletRead(APIModel):
    id: int
    address: str
    proxy_wallet: str
    label: str
    enabled: bool
    status: str
    last_success_at: datetime | None
    last_error: str | None
    created_at: datetime

    @field_serializer("last_success_at", "created_at", when_used="json")
    def serialize_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class PurchaseLotRead(APIModel):
    purchase_date: date
    size: DecimalNumber
    avg_price: DecimalNumber
    initial_value: DecimalNumber
    current_value: DecimalNumber
    cash_pnl: DecimalNumber
    percent_pnl: DecimalNumber


class PositionRead(APIModel):
    wallet_id: int
    asset_id: str
    condition_id: str
    title: str
    outcome: str
    icon_url: str | None
    event_slug: str | None
    market_slug: str | None
    size: DecimalNumber
    avg_price: DecimalNumber
    current_price: DecimalNumber
    initial_value: DecimalNumber
    current_value: DecimalNumber
    cash_pnl: DecimalNumber
    percent_pnl: DecimalNumber
    end_date: datetime | None
    purchase_lots: list[PurchaseLotRead] = Field(default_factory=list)

    @field_serializer("end_date", when_used="json")
    def serialize_end_date(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class PositionSummary(APIModel):
    current_value: DecimalNumber
    initial_value: DecimalNumber
    cash_pnl: DecimalNumber
    count: int


class PositionsResponse(APIModel):
    items: list[PositionRead]
    summary: PositionSummary
    purchase_dates: list[date] = Field(default_factory=list)
    purchase_history_complete: bool = False
    purchase_history_error: str | None = None
    as_of: datetime | None
    stale: bool

    @field_serializer("as_of", when_used="json")
    def serialize_as_of(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class FillRead(APIModel):
    id: int
    side: Literal["BUY", "SELL"]
    size: DecimalNumber
    price: DecimalNumber
    amount: DecimalNumber
    timestamp: datetime
    transaction_hash: str | None

    @field_serializer("timestamp", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        serialized = _as_utc_iso(value)
        assert serialized is not None
        return serialized


class EventRead(APIModel):
    id: int
    wallet_id: int
    asset_id: str
    type: Literal["opened", "increased", "decreased", "closed"]
    title: str
    outcome: str
    event_slug: str | None
    delta_size: DecimalNumber
    before_size: DecimalNumber
    after_size: DecimalNumber
    before_avg_price: DecimalNumber
    after_avg_price: DecimalNumber
    average_fill_price: DecimalNumber | None
    current_value: DecimalNumber
    reconciliation_status: str
    first_detected_at: datetime
    settled_at: datetime
    fills: list[FillRead]

    @field_serializer("first_detected_at", "settled_at", when_used="json")
    def serialize_dates(self, value: datetime) -> str:
        serialized = _as_utc_iso(value)
        assert serialized is not None
        return serialized


class EventsResponse(APIModel):
    items: list[EventRead]
    next_cursor: int | None


class HealthRead(APIModel):
    status: Literal["ok"]
    database: Literal["ok"]
