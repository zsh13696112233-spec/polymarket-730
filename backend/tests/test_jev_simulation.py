from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from backend.config import Settings
from backend.jev import ENDPOINT, simulate_jev
from backend.main import create_app

KEY = "test-credential-not-real"
RAW = {
    "model": "jev-1.13.0",
    "answers": {
        "action": {
            "type": "choice",
            "choice": "RECOVER_PRINCIPAL",
            "confidence": 0.7,
            "probabilities": {
                "SELL_ALL": 0.1,
                "RECOVER_PRINCIPAL": 0.8,
                "HOLD": 0.05,
                "ABSTAIN": 0.05,
            },
        }
    },
    "usage": {"input_tokens": 400, "output_tokens": 30},
}


async def test_request_contract_and_redacted_response():
    def handle(request):
        import json

        assert str(request.url) == ENDPOINT
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        payload = json.loads(request.content)
        assert payload["state"] == "synthetic scenario"
        assert payload["model"] == "jev-1.13.0"
        assert payload["questions"]["action"]["type"] == "choice"
        return httpx.Response(200, json={**RAW, "untrusted": KEY})

    result = await simulate_jev(
        "synthetic scenario", KEY, proxy="unused", transport=httpx.MockTransport(handle)
    )
    assert result.answer.choice == "RECOVER_PRINCIPAL"
    assert result.elapsed_ms >= 0
    assert KEY not in result.model_dump_json()


@pytest.mark.parametrize(
    "status", [400, 401, 402, 403, 404, 413, 422, 429, 500, 502, 503, 504, 529, 530]
)
async def test_errors_do_not_echo_upstream_secrets(status):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text=KEY))
    with pytest.raises(HTTPException) as caught:
        await simulate_jev("test", KEY, proxy="unused", transport=transport)
    assert caught.value.status_code == 502
    assert KEY not in caught.value.detail
    assert f"HTTP {status}" in caught.value.detail
    assert "jev-1.13.0" in caught.value.detail


async def test_route_preserves_upstream_status_without_echoing_body(monkeypatch):
    async def failing_evaluator(state, api_key, *, proxy):
        return await simulate_jev(
            state,
            api_key,
            proxy=proxy,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    422, json={"detail": [{"input": api_key, "msg": "private upstream data"}]}
                )
            ),
        )

    monkeypatch.setattr("backend.main.simulate_jev", failing_evaluator)
    app = create_app(settings=Settings(start_monitor=False, trading_enabled=False))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/ai/jev/simulate", headers={"X-Typesafe-Api-Key": KEY}, json={"state": "test"}
        )
    assert response.status_code == 502
    assert "HTTP 422" in response.json()["detail"]
    assert KEY not in response.text
    assert "private upstream data" not in response.text


async def test_timeout():
    def handle(request):
        raise httpx.ReadTimeout(KEY)

    with pytest.raises(HTTPException) as caught:
        await simulate_jev("test", KEY, proxy="unused", transport=httpx.MockTransport(handle))
    assert caught.value.status_code == 504
    assert KEY not in caught.value.detail


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {
            "model": "jev",
            "answers": {
                "action": {
                    "type": "choice",
                    "choice": "BUY",
                    "probabilities": {},
                    "confidence": 0.99,
                }
            },
        },
    ],
)
async def test_malformed_result_rejected(raw):
    with pytest.raises(HTTPException) as caught:
        await simulate_jev(
            "test",
            KEY,
            proxy="unused",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=raw)),
        )
    assert caught.value.status_code == 502


async def test_route_works_without_wallet_or_database(monkeypatch, tmp_path):
    result = await simulate_jev(
        "test",
        KEY,
        proxy="unused",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=RAW)),
    )
    evaluator = AsyncMock(return_value=result)
    monkeypatch.setattr("backend.main.simulate_jev", evaluator)
    database_path = tmp_path / "never-created.db"
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{database_path}",
            start_monitor=False,
            trading_enabled=False,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/ai/jev/simulate", headers={"X-Typesafe-Api-Key": KEY}, json={"state": "test"}
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["answer"]["choice"] == "RECOVER_PRINCIPAL"
        assert KEY not in response.text
        assert evaluator.await_count == 1
        for body in [{"state": "test"}, {"state": " "}, {"state": "x" * 16001}]:
            response = await client.post("/api/ai/jev/simulate", json=body)
            assert response.status_code in (400, 422)
        assert evaluator.await_count == 1
    assert not database_path.exists()
