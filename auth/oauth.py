"""OAuth 流程 - 复用 OpenClaw 的 ChatGPT OAuth 实现"""
import httpx
import json
import time
import secrets
import hashlib
import base64
from urllib.parse import urlencode, urlparse, parse_qs

# OpenAI OAuth 配置（从 OpenClaw 源码逆向）
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REDIRECT_URI = "http://localhost:1455/auth/callback"
AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
SCOPE = "openid profile email offline_access"

# Codex API
CODEX_API_BASE = "https://api.openai.com"
USERINFO_URL = "https://auth.openai.com/userinfo"

# 存储 state → code_verifier 映射（内存，重启丢失无所谓）
_pending_states: dict = {}

def _generate_pkce():
    """生成 PKCE code_verifier 和 code_challenge"""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge

def generate_auth_url() -> tuple[str, str]:
    """生成 OAuth 授权 URL，返回 (url, state)"""
    state = secrets.token_hex(16)
    verifier, challenge = _generate_pkce()
    _pending_states[state] = verifier

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": "pi",
    }
    url = f"{AUTH_URL}?{urlencode(params)}"
    return url, state

async def exchange_code(code: str, state: str) -> dict:
    """用 code 换 access_token + refresh_token"""
    verifier = _pending_states.pop(state, None)
    if not verifier:
        raise ValueError(f"Unknown state: {state}，可能已过期或重复使用")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(TOKEN_URL, data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp.raise_for_status()
        data = resp.json()

    access = data["access_token"]
    refresh = data.get("refresh_token", "")
    expires_in = data.get("expires_in", 3600)
    expires_at = int(time.time()) + expires_in

    # 获取邮箱
    email = get_email(access)

    return {
        "email": email,
        "access": access,
        "refresh": refresh,
        "expires": expires_at,
    }

async def manual_import(access: str, refresh: str, expires: int) -> dict:
    """手动导入 token（从 OpenClaw auth.json 复制）"""
    email = get_email(access)
    return {
        "email": email,
        "access": access,
        "refresh": refresh,
        "expires": expires,
    }

def get_email(access_token: str) -> str:
    """从 JWT payload 直接解析邮箱（无需网络请求）"""
    try:
        parts = access_token.split(".")
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        profile = payload.get("https://api.openai.com/profile", {})
        return profile.get("email", "unknown@openai.com")
    except Exception:
        return "unknown@openai.com"
