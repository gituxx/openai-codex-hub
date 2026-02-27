"""Token 自动刷新"""
import httpx
import time
from auth.oauth import CLIENT_ID, TOKEN_URL

async def refresh_token(refresh: str) -> dict:
    """用 refresh_token 换新的 access_token"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": refresh,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp.raise_for_status()
        data = resp.json()

    expires_at = int(time.time()) + data.get("expires_in", 3600)
    return {
        "access": data["access_token"],
        "refresh": data.get("refresh_token", refresh),  # 有些 provider 不轮换 refresh
        "expires": expires_at,
    }
