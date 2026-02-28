"""Codex 协议代理 - 逆向自 OpenClaw @mariozechner/pi-ai"""
import json
import base64
import logging
import time
import httpx
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from scheduler import scheduler, MAX_RETRIES
from db import mark_account_error, mark_account_used, mark_account_success, log_request

logger = logging.getLogger(__name__)
router = APIRouter()

CODEX_BASE = "https://chatgpt.com/backend-api/codex/responses"
TIMEOUT = httpx.Timeout(connect=15, read=180, write=30, pool=10)

CODEX_MODELS = [
    "gpt-5-codex", "gpt-5.1-codex", "gpt-5.1-codex-max",
    "gpt-5.1-codex-mini", "gpt-5.2-codex", "gpt-5.3-codex",
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


async def _get_account_or_503(exclude_ids: list = None):
    account = await scheduler.get_account(exclude_ids=exclude_ids)
    if not account:
        raise HTTPException(503, detail="No available accounts")
    return account


def _is_retryable(status_code: int) -> bool:
    """判断 HTTP 状态码是否值得换号重试"""
    return status_code in (401, 429, 500, 502, 503, 504)


# ─────────────────────────────────────────────
# /v1/chat/completions  → 转换为 Codex 协议（带重试）
# ─────────────────────────────────────────────
@router.api_route("/v1/chat/completions", methods=["POST"])
async def proxy_completions(request: Request):
    body_bytes = await request.body()
    try:
        body = json.loads(body_bytes)
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    stream_mode = body.get("stream", False)
    failed_ids = []

    for attempt in range(MAX_RETRIES):
        account = await _get_account_or_503(exclude_ids=failed_ids)

        try:
            account_id = extract_account_id(account["access"])
        except ValueError as e:
            mark_account_error(account["id"], str(e))
            failed_ids.append(account["id"])
            logger.warning(f"[retry {attempt+1}/{MAX_RETRIES}] {account['email']} token 解析失败, 换号")
            continue

        headers = build_codex_headers(account["access"], account_id)
        codex_body = build_codex_body(
            body.get("model", "gpt-5.3-codex"),
            body.get("messages", []),
        )

        if stream_mode:
            # 流式模式：先探测上游是否返回错误，再决定是流给客户端还是换号
            try:
                client = httpx.AsyncClient(timeout=TIMEOUT)
                resp = await client.send(
                    client.build_request("POST", CODEX_BASE, json=codex_body, headers=headers),
                    stream=True
                )

                if resp.status_code >= 400 and _is_retryable(resp.status_code):
                    err = await resp.aread()
                    await resp.aclose()
                    await client.aclose()
                    err_text = err.decode()[:200]
                    logger.warning(f"[retry {attempt+1}/{MAX_RETRIES}] {account['email']} HTTP {resp.status_code}, 换号")
                    await scheduler.handle_request_error(account, resp.status_code, err_text)
                    failed_ids.append(account["id"])
                    continue
                elif resp.status_code >= 400:
                    # 不可重试的错误，直接返回
                    err = await resp.aread()
                    await resp.aclose()
                    await client.aclose()
                    mark_account_error(account["id"], f"HTTP {resp.status_code}")
                    return JSONResponse(
                        {"error": {"message": err.decode()[:200]}},
                        status_code=resp.status_code
                    )

                # 上游 200，开始流式转发
                async def stream_sse(client, resp, account, body):
                    t0 = time.time()
                    out_tokens = 0
                    status = "ok"
                    err_msg = ""
                    try:
                        mark_account_used(account["id"])
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
                        await scheduler.on_request_success(account)
                    except Exception as e:
                        mark_account_error(account["id"], str(e))
                        status, err_msg = "error", str(e)
                        yield f'data: {{"error":{{"message":"{e}"}}}}\n\ndata: [DONE]\n\n'
                    finally:
                        await resp.aclose()
                        await client.aclose()
                        latency = int((time.time() - t0) * 1000)
                        in_tokens = len(json.dumps(body.get("messages",[]))) // 4
                        log_request(account["id"], account["email"], body.get("model",""),
                                    in_tokens, out_tokens, status, err_msg, latency)

                return StreamingResponse(
                    stream_sse(client, resp, account, body),
                    media_type="text/event-stream",
                    headers={"X-Account": account_id}
                )

            except httpx.ConnectError as e:
                logger.warning(f"[retry {attempt+1}/{MAX_RETRIES}] {account['email']} 连接失败: {e}")
                await scheduler.handle_request_error(account, 502, str(e))
                failed_ids.append(account["id"])
                continue

        else:
            # 非流式模式
            full_text = ""
            t0 = time.time()
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                    async with client.stream("POST", CODEX_BASE,
                                             json=codex_body, headers=headers) as resp:
                        if resp.status_code >= 400:
                            err = await resp.aread()
                            err_text = err.decode()[:200]

                            if _is_retryable(resp.status_code):
                                logger.warning(f"[retry {attempt+1}/{MAX_RETRIES}] {account['email']} HTTP {resp.status_code}, 换号")
                                await scheduler.handle_request_error(account, resp.status_code, err_text)
                                failed_ids.append(account["id"])
                                continue
                            else:
                                mark_account_error(account["id"], f"HTTP {resp.status_code}")
                                return JSONResponse({"error": {"message": err_text}},
                                                    status_code=resp.status_code)

                        mark_account_used(account["id"])
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

                await scheduler.on_request_success(account)
                latency = int((time.time() - t0) * 1000)
                in_tokens = len(json.dumps(body.get("messages",[]))) // 4
                out_tokens = len(full_text) // 4
                log_request(account["id"], account["email"], body.get("model",""),
                            in_tokens, out_tokens, "ok", "", latency)

            except (httpx.ConnectError, httpx.ReadTimeout) as e:
                logger.warning(f"[retry {attempt+1}/{MAX_RETRIES}] {account['email']} 网络错误: {e}")
                await scheduler.handle_request_error(account, 502, str(e))
                failed_ids.append(account["id"])
                continue
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

    # 所有重试都用完了
    raise HTTPException(503, detail=f"All accounts exhausted after {MAX_RETRIES} retries")


# ─────────────────────────────────────────────
# /v1/responses  → 直接转发 Codex 协议（带重试）
# ─────────────────────────────────────────────
@router.api_route("/v1/responses", methods=["POST"])
async def proxy_responses(request: Request):
    body_bytes = await request.body()
    try:
        body = json.loads(body_bytes)
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    logger.info(f"[/v1/responses] Incoming body keys: {list(body.keys())}")
    logger.info(f"[/v1/responses] input type: {type(body.get('input')).__name__}, model: {body.get('model')}")
    if isinstance(body.get('input'), list) and len(body['input']) > 0:
        logger.info(f"[/v1/responses] first input item: {json.dumps(body['input'][0], ensure_ascii=False)[:200]}")

    # 预处理 body（只做一次，重试时复用）
    processed_body = _preprocess_responses_body(body)

    failed_ids = []

    for attempt in range(MAX_RETRIES):
        account = await _get_account_or_503(exclude_ids=failed_ids)

        try:
            account_id = extract_account_id(account["access"])
        except ValueError as e:
            mark_account_error(account["id"], str(e))
            failed_ids.append(account["id"])
            logger.warning(f"[retry {attempt+1}/{MAX_RETRIES}] {account['email']} token 解析失败, 换号")
            continue

        headers = build_codex_headers(account["access"], account_id)

        logger.info(f"[/v1/responses] attempt {attempt+1}/{MAX_RETRIES} using {account['email']}")

        try:
            client = httpx.AsyncClient(timeout=TIMEOUT)
            resp = await client.send(
                client.build_request("POST", CODEX_BASE, json=processed_body, headers=headers),
                stream=True
            )

            if resp.status_code >= 400 and _is_retryable(resp.status_code):
                err = await resp.aread()
                await resp.aclose()
                await client.aclose()
                err_text = err.decode()[:200]
                logger.warning(f"[retry {attempt+1}/{MAX_RETRIES}] {account['email']} HTTP {resp.status_code}: {err_text[:100]}")
                await scheduler.handle_request_error(account, resp.status_code, err_text)
                failed_ids.append(account["id"])
                continue
            elif resp.status_code >= 400:
                err = await resp.aread()
                await resp.aclose()
                await client.aclose()
                err_text = err.decode()[:200]
                mark_account_error(account["id"], f"HTTP {resp.status_code}")
                return JSONResponse({"error": {"message": err_text}}, status_code=resp.status_code)

            # 上游 200，开始流式转发
            async def stream(client, resp, account, processed_body):
                t0 = time.time()
                out_tokens = 0
                status = "ok"
                err_msg = ""
                try:
                    mark_account_used(account["id"])
                    async for chunk in resp.aiter_bytes():
                        out_tokens += len(chunk) // 16
                        yield chunk
                    await scheduler.on_request_success(account)
                except Exception as e:
                    mark_account_error(account["id"], str(e))
                    status, err_msg = "error", str(e)
                    yield f'data: {{"error":{{"message":"{e}"}}}}\n\n'.encode()
                finally:
                    await resp.aclose()
                    await client.aclose()
                    latency = int((time.time() - t0) * 1000)
                    in_tokens = len(json.dumps(processed_body.get("input", []))) // 4
                    log_request(account["id"], account["email"], processed_body.get("model", ""),
                                in_tokens, out_tokens, status, err_msg, latency)

            return StreamingResponse(
                stream(client, resp, account, processed_body),
                media_type="text/event-stream",
                headers={"X-Account": account_id}
            )

        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            await client.aclose()
            logger.warning(f"[retry {attempt+1}/{MAX_RETRIES}] {account['email']} 网络错误: {e}")
            await scheduler.handle_request_error(account, 502, str(e))
            failed_ids.append(account["id"])
            continue

    raise HTTPException(503, detail=f"All accounts exhausted after {MAX_RETRIES} retries")


def _preprocess_responses_body(body: dict) -> dict:
    """预处理 /v1/responses 的请求体，转为 Codex 上游格式"""
    body = dict(body)  # 浅拷贝

    # 修正模型名
    if "model" in body:
        body["model"] = body["model"].split("/")[-1]
    body["stream"] = True
    # Codex 上游必须有 instructions，没有就补空
    if "instructions" not in body:
        body["instructions"] = ""
    # input 必须是数组格式，字符串要转换
    if "input" in body and isinstance(body["input"], str):
        body["input"] = [{"role": "user", "content": [{"type": "input_text", "text": body["input"]}]}]
    # 从 input 中提取 system 消息到 instructions
    if isinstance(body.get("input"), list):
        non_system = []
        for item in body["input"]:
            if item.get("role") == "system":
                sys_content = item.get("content", "")
                if isinstance(sys_content, list):
                    sys_content = "\n".join(p.get("text", "") for p in sys_content if isinstance(p, dict))
                if body["instructions"]:
                    body["instructions"] += "\n" + sys_content
                else:
                    body["instructions"] = sys_content
            else:
                if item.get("role") == "user":
                    if isinstance(item.get("content"), str):
                        item["content"] = [{"type": "input_text", "text": item["content"]}]
                    elif isinstance(item.get("content"), list):
                        for part in item["content"]:
                            if isinstance(part, dict) and part.get("type") == "text":
                                part["type"] = "input_text"
                elif item.get("role") == "assistant":
                    if isinstance(item.get("content"), str):
                        item["content"] = [{"type": "output_text", "text": item["content"]}]
                    elif isinstance(item.get("content"), list):
                        for part in item["content"]:
                            if isinstance(part, dict) and part.get("type") == "text":
                                part["type"] = "output_text"
                non_system.append(item)
        body["input"] = non_system
    # 补齐 Codex 上游需要的默认字段
    body.setdefault("store", False)
    body.setdefault("text", {"verbosity": "medium"})
    body.setdefault("include", ["reasoning.encrypted_content"])
    body.setdefault("tool_choice", "auto")
    body.setdefault("parallel_tool_calls", True)
    # 删除 Codex 上游不支持的参数
    for unsupported in ["max_output_tokens", "prompt_cache_key", "temperature", "top_p",
                        "frequency_penalty", "presence_penalty", "logprobs", "top_logprobs",
                        "n", "stop", "seed", "user", "metadata", "response_format"]:
        body.pop(unsupported, None)

    return body


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
