"""Stateless, advisory-only TypeSafe simulation. No wallet or trading dependencies."""

from __future__ import annotations

import time

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from backend.schemas import JevChoiceRead, JevSimulationRead

MODEL = "jev-1.13.0"
ENDPOINT = "https://api.typesafe.ai/v1/systemone"


def _redact(value: object, api_key: str) -> object:
    if isinstance(value, str):
        return value.replace(api_key, "[REDACTED]")
    if isinstance(value, list):
        return [_redact(item, api_key) for item in value]
    if isinstance(value, dict):
        return {str(_redact(key, api_key)): _redact(item, api_key) for key, item in value.items()}
    return value


async def simulate_jev(
    state: str, api_key: str, *, proxy: str, transport: httpx.AsyncBaseTransport | None = None
) -> JevSimulationRead:
    payload = {
        "model": MODEL,
        "state": state,
        "questions": {
            "action": {
                "type": "choice",
                "instructions": (
                    "Based on the match and position information, which action is appropriate?"
                ),
                "criteria": {
                    "SELL_ALL": "Sell all shares.",
                    "RECOVER_PRINCIPAL": "Sell enough shares to recover the original cost.",
                    "HOLD": "Keep holding the shares.",
                    "ABSTAIN": "Not enough information to decide.",
                },
            }
        },
    }
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(
            proxy=proxy if transport is None else None,
            transport=transport,
            trust_env=False,
            timeout=20,
        ) as client:
            response = await client.post(
                ENDPOINT, headers={"Authorization": f"Bearer {api_key}"}, json=payload
            )
            response.raise_for_status()
        # Upstream text is untrusted; never echo credentials, even on success.
        raw = _redact(response.json(), api_key)
        answer = JevChoiceRead.model_validate(raw["answers"]["action"])
        model = raw["model"]
        if not isinstance(model, str) or not model:
            raise ValueError("Missing model")
        return JevSimulationRead(
            model=model,
            answer=answer,
            elapsed_ms=round((time.monotonic() - started) * 1000),
            raw_response=raw,
        )
    except httpx.HTTPStatusError as error:
        messages = {
            400: "请求被拒绝，请检查输入内容与模型参数。",
            401: "API Key 无效，请检查后重试。",
            402: "账户付款或额度检查未通过，请在 TypeSafe 控制台检查余额与账单。",
            403: "TypeSafe 拒绝访问，请检查账户权限。",
            404: "接口或模型不可用，请核对 TypeSafe 当前支持的模型。",
            413: "请求内容过大，请缩短比赛信息后重试。",
            422: "请求参数未通过校验，请核对比赛信息及模型请求格式。",
            429: "TypeSafe 请求额度受限，请稍后重试。",
            500: "服务处理请求时发生内部错误，请稍后重试；持续失败请联系 TypeSafe。",
            502: "上游网关异常，请检查代理状态或稍后重试。",
            503: "服务暂时不可用，请稍后重试。",
            504: "上游网关超时，请稍后重试。",
            529: "TypeSafe 服务繁忙，请稍后重试。",
        }
        upstream_status = error.response.status_code
        hint = messages.get(upstream_status, "收到未预期响应，请根据状态码检查服务与代理。")
        raise HTTPException(
            502,
            f"TypeSafe 调用失败（上游 HTTP {upstream_status}，模型 {MODEL}）：{hint}",
        ) from None
    except httpx.TimeoutException:
        raise HTTPException(504, "TypeSafe 响应超时，请稍后重试。") from None
    except httpx.RequestError:
        raise HTTPException(502, "无法连接 TypeSafe，请检查代理与网络后重试。") from None
    except (ValueError, KeyError, TypeError, ValidationError):
        raise HTTPException(502, "TypeSafe 返回格式异常，未生成有效建议，请重试。") from None
