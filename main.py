import logging
import json
import hashlib
import secrets
import base64
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Depends, Cookie, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

import db
from models import get_all_models, detect_new_models, test_model, scan_openclaw_models
from auth.oauth import generate_auth_url, exchange_code, manual_import
from proxy.routes import router as proxy_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── 会话管理 ──
_sessions: dict[str, float] = {}  # token -> expires_at
SESSION_TTL = 86400  # 24 小时


def _create_session() -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time() + SESSION_TTL
    return token


def _verify_session(token: str) -> bool:
    if not token or token not in _sessions:
        return False
    if _sessions[token] < time.time():
        del _sessions[token]
        return False
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # 确保 web_password 有默认值
    if not db.get_setting("web_password"):
        db.set_setting("web_password", "admin")
        logger.warning("⚠️ 使用默认密码 'admin'，请尽快在设置页面修改！")
    logger.info("OpenAI-Codex-Hub started on :8047")
    yield

app = FastAPI(title="OpenAI Codex Hub", lifespan=lifespan)

# CORS 允许内网访问
from starlette.middleware.base import BaseHTTPMiddleware

class DynamicCORSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        origin = request.headers.get("origin", "")
        # 允许 localhost / 127.0.0.1 / 192.168.* 内网
        allowed = not origin or any(p in origin for p in ["localhost", "127.0.0.1", "192.168."])
        response = await call_next(request)
        if allowed and origin:
            response.headers["access-control-allow-origin"] = origin
            response.headers["access-control-allow-credentials"] = "true"
            response.headers["access-control-allow-methods"] = "*"
            response.headers["access-control-allow-headers"] = "*"
        return response

app.add_middleware(DynamicCORSMiddleware)


def verify_api_key(request: Request):
    """代理流量鉴权（Bearer API Key）"""
    auth = request.headers.get("Authorization", "")
    key = db.get_setting("api_key") or "sk-codex-hub-2025"
    if not auth.startswith("Bearer ") or auth[7:] != key:
        raise HTTPException(401, "Invalid API key")


def verify_web_session(request: Request):
    """管理面鉴权（Cookie Session）"""
    token = request.cookies.get("hub_session")
    if not _verify_session(token):
        raise HTTPException(401, "Unauthorized — please login")


app.include_router(proxy_router, dependencies=[Depends(verify_api_key)])

# ── 登录 / 登出 ──
@app.post("/api/login")
async def api_login(body: dict, response: Response):
    password = body.get("password", "")
    correct = db.get_setting("web_password") or "admin"
    if password != correct:
        raise HTTPException(401, "密码错误")
    token = _create_session()
    response.set_cookie("hub_session", token, httponly=True, samesite="lax", max_age=SESSION_TTL)
    return {"ok": True}


@app.get("/api/auth-check")
async def api_auth_check(request: Request):
    token = request.cookies.get("hub_session")
    if _verify_session(token):
        return {"authenticated": True}
    raise HTTPException(401, "Not authenticated")


@app.post("/api/logout")
async def api_logout(response: Response):
    response.delete_cookie("hub_session")
    return {"ok": True}


# ── OAuth Callback ──
@app.get("/auth/callback")
async def oauth_callback(code: str, state: str, request: Request):
    base = f"{request.url.scheme}://{request.headers.get('host', 'localhost:8047')}"
    try:
        token_data = await exchange_code(code, state)
        db.add_account(token_data["email"], token_data["access"], token_data["refresh"], token_data["expires"])
        logger.info(f"账号添加成功: {token_data['email']}")
        return HTMLResponse(f"""<!doctype html><html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#0f1117;color:#e2e8f0">
        <h2>✅ 登录成功</h2><p>账号 <b>{token_data['email']}</b> 已添加</p>
        <p><a href="{base}" style="color:#6366f1">返回管理后台</a></p>
        </body></html>""")
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        return HTMLResponse(f"""<!doctype html><html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#0f1117;color:#e2e8f0">
        <h2>❌ 登录失败</h2><p>{e}</p><p><a href="{base}" style="color:#6366f1">返回</a></p>
        </body></html>""", status_code=400)

# ── 账号 API（需登录） ──
@app.get("/api/accounts", dependencies=[Depends(verify_web_session)])
async def api_accounts():
    return db.get_all_accounts()

@app.post("/api/accounts/oauth", dependencies=[Depends(verify_web_session)])
async def api_oauth_start():
    url, state = generate_auth_url()
    return {"auth_url": url, "state": state}

@app.post("/api/accounts/import", dependencies=[Depends(verify_web_session)])
async def api_import(body: dict):
    try:
        data = await manual_import(body["access"], body["refresh"], int(body["expires"]))
        acct_id = db.add_account(data["email"], data["access"], data["refresh"], data["expires"])
        return {"id": acct_id, "email": data["email"]}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/api/accounts/export", dependencies=[Depends(verify_web_session)])
async def api_export(body: dict = None):
    body = body or {}
    ids = body.get("ids")
    return db.export_accounts(ids if ids else None)

@app.post("/api/accounts/import-batch", dependencies=[Depends(verify_web_session)])
async def api_import_batch(body: dict):
    accounts = body.get("accounts", [])
    count = db.import_accounts_batch(accounts)
    return {"imported": count}

@app.delete("/api/accounts/{account_id}", dependencies=[Depends(verify_web_session)])
async def api_delete(account_id: int):
    db.delete_account(account_id)
    return {"ok": True}

@app.post("/api/accounts/{account_id}/reset", dependencies=[Depends(verify_web_session)])
async def api_reset(account_id: int):
    db.reset_account_status(account_id)
    return {"ok": True}

@app.post("/api/accounts/{account_id}/disable", dependencies=[Depends(verify_web_session)])
async def api_disable(account_id: int):
    db.set_account_disabled(account_id, True)
    return {"ok": True}

@app.post("/api/accounts/{account_id}/enable", dependencies=[Depends(verify_web_session)])
async def api_enable(account_id: int):
    db.set_account_disabled(account_id, False)
    return {"ok": True}

@app.get("/api/accounts/{account_id}/model-stats", dependencies=[Depends(verify_web_session)])
async def api_model_stats(account_id: int):
    return db.get_account_model_stats(account_id)

# ── 统计 & 日志 API（需登录） ──
@app.get("/api/health-stats", dependencies=[Depends(verify_web_session)])
async def api_health_stats():
    return db.get_health_stats()

@app.get("/api/stats", dependencies=[Depends(verify_web_session)])
async def api_stats():
    return db.get_stats()

@app.get("/api/logs", dependencies=[Depends(verify_web_session)])
async def api_logs(limit: int = 50, offset: int = 0):
    rows, total = db.get_logs(limit, offset)
    return {"total": total, "logs": rows}

# ── 设置 API（需登录） ──
@app.get("/api/settings", dependencies=[Depends(verify_web_session)])
async def api_get_settings():
    key = db.get_setting("api_key") or ""
    # API key 掩码返回，仅显示前3位和后4位
    if len(key) > 8:
        masked_key = key[:3] + "***" + key[-4:]
    else:
        masked_key = "***"
    return {
        "api_key_masked": masked_key,
        "rate_limit_per_hour": db.get_setting("rate_limit_per_hour"),
        "web_password_set": bool(db.get_setting("web_password")),
    }

@app.post("/api/settings", dependencies=[Depends(verify_web_session)])
async def api_set_settings(body: dict):
    allowed_keys = {"api_key", "rate_limit_per_hour", "web_password"}
    for k, v in body.items():
        if k in allowed_keys and v:  # 空值不覆盖
            db.set_setting(k, str(v))
    return {"ok": True}

# ── 模型 API（需登录） ──
@app.get("/api/models", dependencies=[Depends(verify_web_session)])
async def api_models():
    return get_all_models()

@app.get("/api/models/scan", dependencies=[Depends(verify_web_session)])
async def api_scan():
    new = detect_new_models()
    all_scanned = scan_openclaw_models()
    return {"scanned": all_scanned, "new": new}

@app.post("/api/models/test", dependencies=[Depends(verify_web_session)])
async def api_test_model(body: dict):
    model_id = body.get("model")
    if not model_id:
        raise HTTPException(400, "missing model")
    accounts = db.get_active_accounts()
    if not accounts:
        raise HTTPException(503, "No active accounts")
    result = await test_model(model_id, accounts[0]["access"])
    return result

@app.post("/api/models/test-all", dependencies=[Depends(verify_web_session)])
async def api_test_all():
    accounts = db.get_active_accounts()
    if not accounts:
        raise HTTPException(503, "No active accounts")
    models = get_all_models()
    results = []
    for m in models:
        r = await test_model(m["id"], accounts[0]["access"])
        results.append(r)
    return results

@app.get("/health")
async def health():
    return {"status": "ok", "active_accounts": len(db.get_active_accounts())}

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("web/index.html") as f:
        return f.read()
