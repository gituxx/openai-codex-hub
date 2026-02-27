# OpenAI Codex Hub 🦞

> 多账号管理 · 智能轮询 · OpenAI 兼容协议 · 本地 AI 中转站

**English** | [中文](#中文)

---

## What is this?

OpenAI Codex Hub is a self-hosted proxy server that manages multiple ChatGPT OAuth accounts and exposes a standard OpenAI-compatible API. It lets you use Codex models (`gpt-5.x-codex`) via any OpenAI-compatible client — without paying for API credits.

**How it works:** ChatGPT Plus/Pro/Team accounts include Codex model access. This hub manages the OAuth tokens, handles automatic token refresh, and load-balances requests across multiple accounts.

### Features

- 🔐 **Multi-account OAuth** — Add ChatGPT accounts via browser login flow
- 🔄 **Smart round-robin** — Least-recently-used scheduling, automatic failover
- ♻️ **Auto token refresh** — Silently refreshes expired access tokens using refresh tokens
- 📊 **Traffic logs** — Per-request logging with latency, token counts, status
- 📈 **Token statistics** — Aggregated input/output token tracking per account
- 📤 **Import / Export** — Batch JSON import/export for account portability
- 🌐 **Dual protocol** — `/v1/chat/completions` + `/v1/responses` both supported
- 📱 **Mobile-friendly** — Responsive UI works on phone and desktop
- 🐳 **Docker-ready** — Single `docker-compose up` deployment

### Supported Models

| Model | Notes |
|-------|-------|
| `gpt-5.1` | Base reasoning |
| `gpt-5.1-codex-max` | Max context |
| `gpt-5.1-codex-mini` | Fast / lightweight |
| `gpt-5.2` | Balanced |
| `gpt-5.2-codex` | Coding-optimized |
| `gpt-5.3-codex` | Latest stable |
| `gpt-5.3-codex-spark` | Experimental |

---

## Quick Start

### Local (Python)

```bash
git clone https://github.com/gituxx/openai-codex-hub
cd openai-codex-hub
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 run.py
```

Open **http://localhost:8047**

### Docker

```bash
docker-compose up -d
```

Open **http://localhost:8047**

---

## Adding Accounts

1. Open the web UI → click **「+ 添加账号」**
2. Click **「打开登录页面」** — browser opens ChatGPT OAuth
3. Complete login (requires proxy/VPN in mainland China)
4. Paste the callback URL (`http://localhost:1455/auth/callback?code=...`) back into the input box
5. Done ✅

Or use **Manual Import**: copy `access` / `refresh` / `expires` from `~/.openclaw/agents/main/agent/auth.json` (openai-codex key).

---

## Client Configuration

Any OpenAI-compatible client (OpenClaw, Cherry Studio, OpenWebUI, etc.):

```
Base URL:  http://<your-server-ip>:8047/v1
API Key:   sk-codex-hub-2025   (configurable in Settings)
```

Example with curl:

```bash
curl -X POST http://localhost:8047/v1/chat/completions \
  -H "Authorization: Bearer sk-codex-hub-2025" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.3-codex",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

---

## Architecture

```
Client (OpenClaw / curl / any app)
  │
  │  POST /v1/chat/completions  (standard OpenAI format)
  ▼
OpenAI Codex Hub  :8047
  │  ┌─ Account Scheduler (round-robin, health-first)
  │  ├─ Token Manager (auto-refresh on expiry)
  │  └─ Protocol Converter
  │
  │  POST https://chatgpt.com/backend-api/codex/responses
  │  Headers: chatgpt-account-id + OpenAI-Beta: responses=experimental
  ▼
ChatGPT Codex API  (SSE stream)
  │
  ▼
Client  (standard chat.completion response)
```

**Key protocol details** (reverse-engineered from OpenClaw source):
- Endpoint: `https://chatgpt.com/backend-api/codex/responses` (not `api.openai.com`)
- Required header: `chatgpt-account-id` (extracted from JWT)
- Required header: `OpenAI-Beta: responses=experimental`
- Required header: `originator: pi`

---

## File Structure

```
openai-codex-hub/
├── main.py          # FastAPI app + admin API routes
├── run.py           # Multi-port launcher (8047 + 1455)
├── db.py            # SQLite: accounts, logs, settings
├── scheduler.py     # Round-robin account scheduler
├── auth/
│   ├── oauth.py     # ChatGPT OAuth flow (PKCE)
│   └── refresh.py   # Token refresh
├── proxy/
│   └── routes.py    # Protocol proxy (/v1/chat/completions → Codex)
├── web/
│   └── index.html   # Management UI
├── requirements.txt
├── docker-compose.yml
└── Dockerfile
```

---

## Docker Compose

```yaml
version: '3.8'
services:
  codex-hub:
    build: .
    ports:
      - "8047:8047"
      - "1455:1455"
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

---

## Notes

- **Mainland China**: `auth.openai.com` requires a proxy/VPN. The hub itself (once tokens are added) works without proxy if your server has direct access.
- **Token expiry**: Access tokens last ~10 days; refresh tokens last months. The hub auto-refreshes silently.
- **Rate limits**: ChatGPT enforces per-account usage limits. Set `rate_limit_per_hour` in Settings.
- **Legal**: This uses your own browser OAuth session — same as manually using ChatGPT. Use responsibly.

---

## 中文

### 这是什么？

OpenAI Codex Hub 是一个自托管的代理服务器，管理多个 ChatGPT OAuth 账号，对外提供标准 OpenAI 兼容 API，让你免费使用 Codex 系列模型（`gpt-5.x-codex`）。

**原理**：ChatGPT Plus/Pro/Team 订阅包含 Codex 模型访问权限。本 Hub 管理 OAuth Token，自动刷新过期 Token，并在多个账号之间智能轮询请求。

### 功能

- 🔐 **多账号 OAuth** — 通过浏览器登录流程添加 ChatGPT 账号
- 🔄 **智能轮询** — 最久未使用优先调度，自动故障切换
- ♻️ **Token 自动刷新** — access token 过期后静默刷新
- 📊 **请求日志** — 每次请求的延迟、Token 数、状态全部记录
- 📈 **Token 统计** — 按账号统计输入/输出 Token 用量
- 📤 **导入/导出** — JSON 批量导入导出账号
- 🌐 **双协议** — 同时支持 `/v1/chat/completions` 和 `/v1/responses`
- 📱 **手机适配** — 响应式界面，手机/电脑均可正常使用
- 🐳 **Docker 部署** — 一条命令启动

### 快速开始

```bash
git clone https://github.com/gituxx/openai-codex-hub
cd openai-codex-hub
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 run.py
```

打开 **http://localhost:8047**

### 客户端配置

```
Base URL:  http://<服务器IP>:8047/v1
API Key:   sk-codex-hub-2025（可在设置页修改）
```

兼容：OpenClaw、Cherry Studio、OpenWebUI、Cursor、任意 OpenAI 兼容客户端

### 添加账号

1. 打开管理后台 → 点「添加账号」
2. 点「打开登录页面」（国内需开代理）
3. 完成 ChatGPT 登录
4. 将回调地址粘贴回输入框
5. 完成 ✅

也支持从 OpenClaw `auth.json` 手动导入 Token。

---

## License

MIT
