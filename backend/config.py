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
    request_timeout_seconds: float = 12.0
    data_api_concurrency: int = 10
    gamma_api_concurrency: int = 6
    clob_api_concurrency: int = 10
    max_backoff_seconds: float = 300.0
    start_monitor: bool = True
    trading_enabled: bool = True
    whale_enabled: bool = True
    whale_scan_interval_seconds: float = 60.0
    whale_max_scan_pages: int = 20
    whale_profile_batch_limit: int = 50
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_from_name: str = "PolyCopy"
    smtp_security: str = "starttls"
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
            request_timeout_seconds=float(os.getenv("POLYMARKET_REQUEST_TIMEOUT_SECONDS", "12")),
            data_api_concurrency=int(os.getenv("POLYMARKET_DATA_API_CONCURRENCY", "10")),
            gamma_api_concurrency=int(os.getenv("POLYMARKET_GAMMA_API_CONCURRENCY", "6")),
            clob_api_concurrency=int(os.getenv("POLYMARKET_CLOB_API_CONCURRENCY", "10")),
            max_backoff_seconds=float(os.getenv("POLYMARKET_MAX_BACKOFF_SECONDS", "300")),
            start_monitor=os.getenv("POLYMARKET_START_MONITOR", "1").lower()
            not in {"0", "false", "no"},
            trading_enabled=os.getenv("POLYMARKET_TRADING_ENABLED", "1").lower()
            in {"1", "true", "yes"},
            whale_enabled=os.getenv("POLYMARKET_WHALE_ENABLED", "1").lower()
            in {"1", "true", "yes"},
            whale_scan_interval_seconds=float(
                os.getenv("POLYMARKET_WHALE_SCAN_INTERVAL_SECONDS", "60")
            ),
            whale_max_scan_pages=int(os.getenv("POLYMARKET_WHALE_MAX_SCAN_PAGES", "20")),
            whale_profile_batch_limit=int(os.getenv("POLYMARKET_WHALE_PROFILE_BATCH_LIMIT", "50")),
            smtp_host=os.getenv("POLYMARKET_SMTP_HOST") or None,
            smtp_port=int(os.getenv("POLYMARKET_SMTP_PORT", "587")),
            smtp_username=os.getenv("POLYMARKET_SMTP_USERNAME") or None,
            smtp_password=os.getenv("POLYMARKET_SMTP_PASSWORD") or None,
            smtp_from_email=os.getenv("POLYMARKET_SMTP_FROM_EMAIL") or None,
            smtp_from_name=os.getenv("POLYMARKET_SMTP_FROM_NAME", "PolyCopy"),
            smtp_security=os.getenv("POLYMARKET_SMTP_SECURITY", "starttls").lower(),
        )

    @property
    def smtp_configured(self) -> bool:
        return bool(
            self.smtp_host
            and self.smtp_from_email
            and self.smtp_username
            and self.smtp_password
            and self.smtp_security in {"starttls", "ssl", "none"}
        )

    @property
    def whale_failure_log_path(self) -> Path:
        prefix = "sqlite+aiosqlite:///"
        if self.database_url.startswith(prefix):
            database_path = self.database_url.removeprefix(prefix)
            if database_path != ":memory:" and not database_path.startswith("file:"):
                return Path(database_path).expanduser().resolve().parent / "whale-failures.jsonl"
        return Path("data/whale-failures.jsonl").resolve()

    def ensure_sqlite_directory(self) -> None:
        prefix = "sqlite+aiosqlite:///"
        if not self.database_url.startswith(prefix):
            return
        database_path = self.database_url.removeprefix(prefix)
        if database_path == ":memory:" or database_path.startswith("file:"):
            return
        Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
