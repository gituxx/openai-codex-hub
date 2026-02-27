"""Codex 协议代理 - 逆向自 OpenClaw @mariozechner/pi-ai"""
import json
import base64
import logging
import time
import httpx
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from scheduler import scheduler
from db import mark_account_error, log_request

logger = logging.getLogger(__name__)
router = APIRouter()

CODEX_BASE = "https://chatgpt.com/backend-api/codex/responses"
TIMEOUT = httpx.Timeout(connect=15, read=180, write=30, pool=10)

CODEX_MODELS = [
    "gpt-5.1", "gpt-5.1-codex-max", "gpt-5.1-codex-mini",
    "gpt-5.2", "gpt-5.2-codex", "gpt-5.3-codex", "gpt-5.3-codex-spark",
]


def extract_account_id(token: str) -> str:
    """从 JWT access_token 提取 chatgpt_account_id"""
    try:
        parts = token.split(".")
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        account_id = payload["https://api.openai.com/auth"]["chatgpt_account_id"]
        return account_id
    except Exception as e:
        raise ValueError(f"无法从 token 提取 account_id: {e}")


def build_codex_headers(access_token: str, account_id: str) -> dict:
    """构建 Codex 专用请求头"""
    return {
        "Authorization": f"Bearer {access_token}",
        "chatgpt-account-id": account_id,
        "OpenAI-Beta": "responses=experimental",
        "originator": "pi",
        "User-Agent": "pi (darwin 24.0.0; arm64)",
        "accept": "text/event-stream",
        "content-type": "application/json",
    }


def convert_messages_to_codex(messages: list) -> tuple[str, list]:
    """将 OpenAI chat/completions 消息格式转换为 Codex input 格式"""
    system_prompt = ""
    input_messages = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            system_prompt = content
        elif role == "user":
            input_messages.append({
                "role": "user",
                "content": [{"type": "input_text", "text": content}] if isinstance(content, str) else content
            })
        elif role == "assistant":
            input_messages.append({
                "role": "assistant",
                "content": [{"type": "output_text", "text": content}] if isinstance(content, str) else content
            })

    return system_prompt, input_messages


def build_codex_body(model: str, messages: list) -> dict:
    """构建 Codex 请求体"""
    system_prompt, input_messages = convert_messages_to_codex(messages)

    # 模型名去掉 provider 前缀
    model_id = model.split("/")[-1] if "/" in model else model

    return {
        "model": model_id,
        "store": False,
        "stream": True,
        "instructions": system_prompt,
        "input": input_messages,
        "text": {"verbosity": "medium"},
        "include": ["reasoning.encrypted_content"],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
    }


async def _get_account_or_503():
    account = await scheduler.get_account()
    if not account:
        raise HTTPException(503, detail="No available accounts")
    return account


# ─────────────────────────────────────────────
# /v1/chat/completions  → 转换为 Codex 协议
# ─────────────────────────────────────────────
@router.api_route("/v1/chat/completions", methods=["POST"])
async def proxy_completions(request: Request):
    body_bytes = await request.body()
    try:
        body = json.loads(body_bytes)
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    account = await _get_account_or_503()

    try:
        account_id = extract_account_id(account["access"])
    except ValueError as e:
        mark_account_error(account["id"], str(e))
        raise HTTPException(500, str(e))

    headers = build_codex_headers(account["access"], account_id)
    codex_body = build_codex_body(
        body.get("model", "gpt-5.3-codex"),
        body.get("messages", []),
    )
    stream_mode = body.get("stream", False)

    async def stream_sse():
        """流式返回，SSE 格式"""
        t0 = time.time()
        out_tokens = 0
        status = "ok"
        err_msg = ""
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                async with client.stream("POST", CODEX_BASE,
                                         json=codex_body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        err = await resp.aread()
                        err_text = err.decode()
                        mark_account_error(account["id"], f"HTTP {resp.status_code}")
                        status, err_msg = "error", err_text[:200]
                        yield f'data: {{"error":{{"message":"{err_text[:100]}","type":"api_error"}}}}\n\n'
                        return
                    buffer = ""
                    async for chunk in resp.aiter_text():
                        buffer += chunk
                        while "\n\n" in buffer:
                            event, buffer = buffer.split("\n\n", 1)
                            for line in event.split("\n"):
                                if not line.startswith("data:"): continue
                                data_str = line[5:].strip()
                                if not data_str or data_str == "[DONE]": continue
                                try:
                                    event_data = json.loads(data_str)
                                    converted = convert_codex_event_to_openai(event_data, body.get("model", "gpt-5.3-codex"))
                                    if converted:
                                        delta = converted.get("choices",[{}])[0].get("delta",{}).get("content","")
                                        out_tokens += len(delta) // 4
                                        yield f"data: {json.dumps(converted)}\n\n"
                                except Exception:
                                    pass
                    yield "data: [DONE]\n\n"
        except Exception as e:
            mark_account_error(account["id"], str(e))
            status, err_msg = "error", str(e)
            yield f'data: {{"error":{{"message":"{e}"}}}}\n\ndata: [DONE]\n\n'
        finally:
            latency = int((time.time() - t0) * 1000)
            in_tokens = len(json.dumps(body.get("messages",[]))) // 4
            log_request(account["id"], account["email"], body.get("model",""),
                        in_tokens, out_tokens, status, err_msg, latency)

    if stream_mode:
        return StreamingResponse(stream_sse(), media_type="text/event-stream",
                                 headers={"X-Account": account_id})
    else:
        # 非流式：收集完整响应再返回
        full_text = ""
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                async with client.stream("POST", CODEX_BASE,
                                         json=codex_body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        err = await resp.aread()
                        mark_account_error(account["id"], f"HTTP {resp.status_code}")
                        return JSONResponse({"error": {"message": err.decode()[:200]}},
                                            status_code=resp.status_code)

                    buffer = ""
                    async for chunk in resp.aiter_text():
                        buffer += chunk
                        while "\n\n" in buffer:
                            event, buffer = buffer.split("\n\n", 1)
                            for line in event.split("\n"):
                                if not line.startswith("data:"):
                                    continue
                                data_str = line[5:].strip()
                                if not data_str or data_str == "[DONE]":
                                    continue
                                try:
                                    event_data = json.loads(data_str)
                                    text = extract_text_from_codex_event(event_data)
                                    if text:
                                        full_text += text
                                except Exception:
                                    pass
        except Exception as e:
            mark_account_error(account["id"], str(e))
            raise HTTPException(502, str(e))

        return JSONResponse({
            "id": "chatcmpl-codex",
            "object": "chat.completion",
            "model": body.get("model", "gpt-5.3-codex"),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": full_text},
                "finish_reason": "stop"
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        }, headers={"X-Account": account_id})


# ─────────────────────────────────────────────
# /v1/responses  → 直接转发 Codex 协议
# ─────────────────────────────────────────────
@router.api_route("/v1/responses", methods=["POST"])
async def proxy_responses(request: Request):
    body_bytes = await request.body()
    try:
        body = json.loads(body_bytes)
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    account = await _get_account_or_503()

    try:
        account_id = extract_account_id(account["access"])
    except ValueError as e:
        mark_account_error(account["id"], str(e))
        raise HTTPException(500, str(e))

    headers = build_codex_headers(account["access"], account_id)

    # 修正模型名
    if "model" in body:
        body["model"] = body["model"].split("/")[-1]
    body["stream"] = True

    async def stream():
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                async with client.stream("POST", CODEX_BASE,
                                         json=body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        err = await resp.aread()
                        mark_account_error(account["id"], f"HTTP {resp.status_code}")
                        yield f"data: {err.decode()}\n\n"
                        return
                    async for chunk in resp.aiter_bytes():
                        yield chunk
        except Exception as e:
            mark_account_error(account["id"], str(e))
            yield f'data: {{"error":{{"message":"{e}"}}}}\n\n'

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"X-Account": account_id})


# ─────────────────────────────────────────────
# /v1/models
# ─────────────────────────────────────────────
@router.get("/v1/models")
async def list_models():
    import time
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "created": now, "owned_by": "openai-codex"}
            for m in CODEX_MODELS
        ]
    }


# ─────────────────────────────────────────────
# Codex SSE 事件 → OpenAI chat/completions 格式转换
# ─────────────────────────────────────────────
def convert_codex_event_to_openai(event: dict, model: str) -> dict | None:
    """把 Codex SSE 事件转换为 OpenAI chat.completion.chunk 格式"""
    etype = event.get("type", "")

    if etype == "response.output_text.delta":
        delta_text = event.get("delta", "")
        return {
            "id": "chatcmpl-codex",
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": {"content": delta_text}, "finish_reason": None}]
        }

    if etype in ("response.completed", "response.done"):
        return {
            "id": "chatcmpl-codex",
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
        }

    return None


def extract_text_from_codex_event(event: dict) -> str:
    """从 Codex 事件中提取文本（用于非流式模式）"""
    if event.get("type") == "response.output_text.delta":
        return event.get("delta", "")
    return ""
