"""Codex 模型管理 - 自动发现 + 可用性测试"""
import json
import base64
import subprocess
import logging
import time
import httpx

logger = logging.getLogger(__name__)

OPENCLAW_MODELS_PATH = "/opt/homebrew/lib/node_modules/openclaw/node_modules/@mariozechner/pi-ai/dist/models.generated.d.ts"
CODEX_BASE = "https://chatgpt.com/backend-api/codex/responses"

# 默认已知模型
DEFAULT_MODELS = [
    {"id": "gpt-5-codex", "name": "GPT-5 Codex", "tier": "standard"},
    {"id": "gpt-5.1-codex", "name": "GPT-5.1 Codex", "tier": "standard"},
    {"id": "gpt-5.1-codex-max", "name": "GPT-5.1 Codex Max", "tier": "max"},
    {"id": "gpt-5.1-codex-mini", "name": "GPT-5.1 Codex Mini", "tier": "mini"},
    {"id": "gpt-5.2-codex", "name": "GPT-5.2 Codex", "tier": "standard"},
    {"id": "gpt-5.3-codex", "name": "GPT-5.3 Codex", "tier": "latest"},
]


def scan_openclaw_models() -> list[str]:
    """从 OpenClaw 源码扫描所有 Codex 模型 ID"""
    try:
        with open(OPENCLAW_MODELS_PATH) as f:
            content = f.read()
        import re
        # 匹配所有 codex 相关模型名
        ids = set(re.findall(r'"((?:gpt-[\d.]+[-\w]*codex[-\w]*|codex-[\w-]+))"', content))
        return sorted(ids)
    except Exception as e:
        logger.warning(f"扫描 OpenClaw 模型失败: {e}")
        return []


def get_known_model_ids() -> list[str]:
    return [m["id"] for m in DEFAULT_MODELS]


# 已测试确认不可用的模型
BLACKLIST = {"codex-mini-latest", "gpt-5.3-codex-spark"}

def detect_new_models() -> list[str]:
    """发现 OpenClaw 中新增的 Codex 模型"""
    scanned = scan_openclaw_models()
    known = set(get_known_model_ids()) | BLACKLIST
    return [m for m in scanned if m not in known]


def make_model_entry(model_id: str) -> dict:
    """为新模型生成条目"""
    name = model_id.replace("-", " ").replace(".", ".").title()
    tier = "experimental"
    if "mini" in model_id: tier = "mini"
    elif "max" in model_id: tier = "max"
    elif "spark" in model_id: tier = "experimental"
    elif "codex" in model_id: tier = "standard"
    return {"id": model_id, "name": name, "tier": tier}


def get_all_models() -> list[dict]:
    """获取所有模型（已知 + 新发现）"""
    models = list(DEFAULT_MODELS)
    new_ids = detect_new_models()
    for mid in new_ids:
        models.append(make_model_entry(mid))
    return models


async def test_model(model_id: str, access_token: str) -> dict:
    """测试某个模型是否可用"""
    try:
        parts = access_token.split(".")
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        account_id = payload["https://api.openai.com/auth"]["chatgpt_account_id"]
    except Exception as e:
        return {"model": model_id, "status": "error", "error": f"JWT解析失败: {e}", "latency_ms": 0}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "chatgpt-account-id": account_id,
        "OpenAI-Beta": "responses=experimental",
        "originator": "pi",
        "content-type": "application/json",
    }
    headers["accept"] = "text/event-stream"
    body = {
        "model": model_id,
        "store": False,
        "stream": True,
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "say ok"}]}],
        "text": {"verbosity": "medium"},
        "include": ["reasoning.encrypted_content"],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
    }

    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=30, write=10, pool=5)) as client:
            async with client.stream("POST", CODEX_BASE, json=body, headers=headers) as resp:
                latency = int((time.time() - t0) * 1000)
                if resp.status_code == 200:
                    # 读一小段确认有数据就行
                    got_data = False
                    async for chunk in resp.aiter_text():
                        if chunk.strip():
                            got_data = True
                            break
                    return {"model": model_id, "status": "ok", "latency_ms": latency}
                else:
                    err_bytes = await resp.aread()
                    err = err_bytes.decode()[:200]
                    return {"model": model_id, "status": "error", "error": err, "latency_ms": latency}
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        return {"model": model_id, "status": "error", "error": str(e), "latency_ms": latency}
