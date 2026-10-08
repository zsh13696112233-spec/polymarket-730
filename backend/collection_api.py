"""Local-only connection configuration; the public server has no mutation routes."""

import json
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request

from backend.collection_store import dumps
from backend.collection_sync import request_collection
from backend.models import CollectionSyncState
from backend.schemas import CollectionConnectionRead, CollectionConnectionUpdate
from backend.whale_requests import capture_whale_requests

router = APIRouter(prefix="/api/collection")


async def connection_read(database):
    async with database.sessions() as session:
        row = await session.get(CollectionSyncState, 1)
        return dict(
            host=row.host,
            port=row.port,
            subscription_version=row.subscription_version,
            source_id=row.source_id,
            categories=json.loads(row.categories_json),
            server=json.loads(row.status_json),
            last_error=row.last_error,
        )


async def test_connection(payload):
    if not payload.host:
        raise HTTPException(422, "请填写服务器 IP")
    host = f"[{payload.host}]" if ":" in payload.host else payload.host
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            return await request_collection(client, f"http://{host}:{payload.port}", "status")
    except Exception:
        raise HTTPException(502, "无法连接采集服务器，请检查 IP、端口和协议版本") from None


@router.get("/connection", response_model=CollectionConnectionRead)
async def read_connection(request: Request):
    return await connection_read(request.app.state.database)


@router.post("/connection/test")
async def check_connection(payload: CollectionConnectionUpdate, request: Request):
    with capture_whale_requests(
        request.app.state.whale_request_monitor, f"connection-{uuid4().hex}"
    ):
        return await test_connection(payload)


@router.put("/connection", response_model=CollectionConnectionRead)
async def save_connection(payload: CollectionConnectionUpdate, request: Request):
    scanner = request.app.state.whale_scanner
    async with scanner.subscription_lock:
        async with request.app.state.database.sessions() as session:
            row = await session.get(CollectionSyncState, 1)
            if row.host != payload.host or row.port != payload.port:
                row.host, row.port = payload.host, payload.port
                row.subscription_version += 1
                row.categories_json = "{}"
                row.source_id = None
                row.status_json = dumps({})
                row.last_error = "连接已更新，等待同步"
                scanner.recovering = True
            await session.commit()
    scanner.wake()
    return await connection_read(request.app.state.database)
