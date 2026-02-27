import logging
import json
import base64
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import db
from auth.oauth import generate_auth_url, exchange_code, manual_import
from proxy.routes import router as proxy_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    logger.info("OpenAI-Codex-Hub started on :8047")
    yield

app = FastAPI(title="OpenAI Codex Hub", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def verify_api_key(request: Request):
    auth = request.headers.get("Authorization", "")
    key = db.get_setting("api_key") or "sk-codex-hub-2025"
    if not auth.startswith("Bearer ") or auth[7:] != key:
        raise HTTPException(401, "Invalid API key")

app.include_router(proxy_router, dependencies=[Depends(verify_api_key)])

# ── OAuth Callback ──
@app.get("/auth/callback")
async def oauth_callback(code: str, state: str):
    try:
        token_data = await exchange_code(code, state)
        db.add_account(token_data["email"], token_data["access"], token_data["refresh"], token_data["expires"])
        logger.info(f"账号添加成功: {token_data['email']}")
        return HTMLResponse(f"""<!doctype html><html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#0f1117;color:#e2e8f0">
        <h2>✅ 登录成功</h2><p>账号 <b>{token_data['email']}</b> 已添加</p>
        <p><a href="http://192.168.31.201:8047" style="color:#6366f1">返回管理后台</a></p>
        </body></html>""")
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        return HTMLResponse(f"""<!doctype html><html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#0f1117;color:#e2e8f0">
        <h2>❌ 登录失败</h2><p>{e}</p><p><a href="http://192.168.31.201:8047" style="color:#6366f1">返回</a></p>
        </body></html>""", status_code=400)

# ── 账号 API ──
@app.get("/api/accounts")
async def api_accounts():
    return db.get_all_accounts()

@app.post("/api/accounts/oauth")
async def api_oauth_start():
    url, state = generate_auth_url()
    return {"auth_url": url, "state": state}

@app.post("/api/accounts/import")
async def api_import(body: dict):
    try:
        data = await manual_import(body["access"], body["refresh"], int(body["expires"]))
        acct_id = db.add_account(data["email"], data["access"], data["refresh"], data["expires"])
        return {"id": acct_id, "email": data["email"]}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/api/accounts/export")
async def api_export(body: dict = {}):
    ids = body.get("ids")  # 传 ids 列表则只导出选中的，否则全部
    return db.export_accounts(ids if ids else None)

@app.post("/api/accounts/import-batch")
async def api_import_batch(body: dict):
    accounts = body.get("accounts", [])
    count = db.import_accounts_batch(accounts)
    return {"imported": count}

@app.delete("/api/accounts/{account_id}")
async def api_delete(account_id: int):
    db.delete_account(account_id)
    return {"ok": True}

@app.post("/api/accounts/{account_id}/reset")
async def api_reset(account_id: int):
    db.reset_account_status(account_id)
    return {"ok": True}

@app.post("/api/accounts/{account_id}/disable")
async def api_disable(account_id: int):
    db.set_account_disabled(account_id, True)
    return {"ok": True}

@app.post("/api/accounts/{account_id}/enable")
async def api_enable(account_id: int):
    db.set_account_disabled(account_id, False)
    return {"ok": True}

@app.get("/api/accounts/{account_id}/model-stats")
async def api_model_stats(account_id: int):
    return db.get_account_model_stats(account_id)

# ── 统计 & 日志 API ──
@app.get("/api/stats")
async def api_stats():
    return db.get_stats()

@app.get("/api/logs")
async def api_logs(limit: int = 50, offset: int = 0):
    rows, total = db.get_logs(limit, offset)
    return {"total": total, "logs": rows}

# ── 设置 API ──
@app.get("/api/settings")
async def api_get_settings():
    return {"api_key": db.get_setting("api_key"), "rate_limit_per_hour": db.get_setting("rate_limit_per_hour")}

@app.post("/api/settings")
async def api_set_settings(body: dict):
    for k, v in body.items():
        db.set_setting(k, str(v))
    return {"ok": True}

@app.get("/health")
async def health():
    return {"status": "ok", "active_accounts": len(db.get_active_accounts())}

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("web/index.html") as f:
        return f.read()
