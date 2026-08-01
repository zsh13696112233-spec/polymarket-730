from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = "sqlite+aiosqlite:///./data/polymarket-watch.db"
    data_api_url: str = "https://data-api.polymarket.com"
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    clob_api_url: str = "https://clob.polymarket.com"
    relayer_api_url: str = "https://relayer-v2.polymarket.com"
    polygon_rpc_url: str = "https://polygon.drpc.org"
    poll_interval_seconds: float = 15.0
    quiet_window_seconds: float = 45.0
    hard_window_seconds: float = 180.0
    max_wallet_concurrency: int = 3
    request_timeout_seconds: float = 12.0
    max_backoff_seconds: float = 300.0
    start_monitor: bool = True
    cors_origins: tuple[str, ...] = field(
        default=(
            "http://127.0.0.1:3000",
            "http://localhost:3000",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        )
    )

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.getenv(
                "POLYMARKET_DATABASE_URL",
                "sqlite+aiosqlite:///./data/polymarket-watch.db",
            ),
            data_api_url=os.getenv("POLYMARKET_DATA_API_URL", "https://data-api.polymarket.com"),
            gamma_api_url=os.getenv("POLYMARKET_GAMMA_API_URL", "https://gamma-api.polymarket.com"),
            clob_api_url=os.getenv("POLYMARKET_CLOB_API_URL", "https://clob.polymarket.com"),
            relayer_api_url=os.getenv(
                "POLYMARKET_RELAYER_API_URL", "https://relayer-v2.polymarket.com"
            ),
            polygon_rpc_url=os.getenv("POLYMARKET_POLYGON_RPC_URL", "https://polygon.drpc.org"),
            poll_interval_seconds=float(os.getenv("POLYMARKET_POLL_INTERVAL_SECONDS", "15")),
            quiet_window_seconds=float(os.getenv("POLYMARKET_SETTLE_QUIET_SECONDS", "45")),
            hard_window_seconds=float(os.getenv("POLYMARKET_SETTLE_HARD_SECONDS", "180")),
            max_wallet_concurrency=int(os.getenv("POLYMARKET_MAX_CONCURRENCY", "3")),
            request_timeout_seconds=float(os.getenv("POLYMARKET_REQUEST_TIMEOUT_SECONDS", "12")),
            max_backoff_seconds=float(os.getenv("POLYMARKET_MAX_BACKOFF_SECONDS", "300")),
            start_monitor=os.getenv("POLYMARKET_START_MONITOR", "1").lower()
            not in {"0", "false", "no"},
        )

    def ensure_sqlite_directory(self) -> None:
        prefix = "sqlite+aiosqlite:///"
        if not self.database_url.startswith(prefix):
            return
        database_path = self.database_url.removeprefix(prefix)
        if database_path == ":memory:" or database_path.startswith("file:"):
            return
        Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
