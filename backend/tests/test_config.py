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
