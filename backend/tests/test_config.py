from backend.config import DEFAULT_POLYMARKET_PROXY_URL, Settings


def test_settings_default_to_required_local_proxy(monkeypatch):
    monkeypatch.delenv("POLYMARKET_PROXY_URL", raising=False)

    assert Settings.from_env().proxy_url == DEFAULT_POLYMARKET_PROXY_URL


def test_settings_accept_configured_proxy(monkeypatch):
    monkeypatch.setenv("POLYMARKET_PROXY_URL", "http://127.0.0.1:8899")

    assert Settings.from_env().proxy_url == "http://127.0.0.1:8899"


def test_settings_keep_default_cors_origins(monkeypatch):
    monkeypatch.delenv("POLYMARKET_CORS_ORIGINS", raising=False)

    assert Settings.from_env().cors_origins == Settings().cors_origins


def test_settings_accept_lan_cors_origins(monkeypatch):
    monkeypatch.setenv(
        "POLYMARKET_CORS_ORIGINS",
        " http://192.168.3.6:3000/ , , http://192.168.3.6:5173 ",
    )

    assert Settings.from_env().cors_origins == Settings().cors_origins + (
        "http://192.168.3.6:3000",
        "http://192.168.3.6:5173",
    )


def test_weekly_report_recipients(monkeypatch):
    monkeypatch.setenv(
        "POLYMARKET_WEEKLY_REPORT_TO", " a@example.com, b@example.com, a@example.com, "
    )
    assert Settings.from_env().weekly_report_to == ("a@example.com", "b@example.com")
    monkeypatch.setenv("POLYMARKET_WEEKLY_REPORT_TO", "")
    assert Settings.from_env().weekly_report_to == ()


def test_weekly_report_rejects_invalid_recipient_without_echoing_value(monkeypatch):
    import pytest

    monkeypatch.setenv("POLYMARKET_WEEKLY_REPORT_TO", "a@example.com\nBcc: b@example.com")
    with pytest.raises(ValueError, match="POLYMARKET_WEEKLY_REPORT_TO 格式错误") as error:
        Settings.from_env()
    assert "Bcc" not in str(error.value)
