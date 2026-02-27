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

### Supported Models (Verified ✅)

| Model | Tier | Notes |
|-------|------|-------|
| `gpt-5-codex` | 🔵 Standard | GPT-5 base Codex |
| `gpt-5.1-codex` | 🔵 Standard | GPT-5.1 standard |
| `gpt-5.1-codex-max` | 🟣 Max | Largest context window |
| `gpt-5.1-codex-mini` | ⚪ Mini | Fast / lightweight |
| `gpt-5.2-codex` | 🔵 Standard | Coding-optimized |
| `gpt-5.3-codex` | 🟢 Latest | Latest stable, recommended ⭐ |

> All models are live-tested against ChatGPT Codex API. Use the **Models** tab in the web UI to scan for new models and run live tests.

### Tracking New Models

OpenAI occasionally adds new Codex models. Three ways to stay current, from easiest to hardest:

**Method 1 — Scan OpenClaw source (Easiest)**

After updating OpenClaw (`npm update -g openclaw`), one command lists all Codex models:

```bash
grep -o '"gpt-[^"]*codex[^"]*"' \
  /opt/homebrew/lib/node_modules/openclaw/node_modules/@mariozechner/pi-ai/dist/models.generated.d.ts \
  | sort -u
```

Compare with the current model list — any new entries are newly added models.

**Method 2 — Probe the Codex API directly**

Send a request with a suspected model name. If it responds, the model is live:

```bash
curl -X POST http://localhost:8047/v1/chat/completions \
  -H "Authorization: Bearer sk-codex-hub-2025" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.4-codex","messages":[{"role":"user","content":"hi"}]}'
```

Or use the **Models** tab in the web UI → click a model's **Test** button, or **⚡ Test All** to batch-verify.

**Method 3 — Watch upstream sources**

- [OpenClaw Releases](https://github.com/openclaw/openclaw/releases) — model list updates ship with new versions
- [@mariozechner/pi-ai](https://www.npmjs.com/package/@mariozechner/pi-ai) on npm — the actual Codex provider package
- [OpenAI announcements](https://openai.com/blog) — official model launches

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

Any OpenAI-compatible client (OpenClaw, Cherry Studio, OpenWebUI, Cursor, etc.):

```
Base URL:  http://<your-server-ip>:8047/v1
API Key:   sk-codex-hub-2025   (configurable in Settings)
```

### curl Examples

**Chat Completions** (`/v1/chat/completions`):

```bash
curl -X POST http://localhost:8047/v1/chat/completions \
  -H "Authorization: Bearer sk-codex-hub-2025" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.3-codex",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

**Streaming** (SSE):

```bash
curl -X POST http://localhost:8047/v1/chat/completions \
  -H "Authorization: Bearer sk-codex-hub-2025" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.3-codex",
    "stream": true,
    "messages": [{"role": "user", "content": "Write a haiku about coding"}]
  }'
```

**Responses API** (`/v1/responses`):

```bash
curl -X POST http://localhost:8047/v1/responses \
  -H "Authorization: Bearer sk-codex-hub-2025" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.3-codex",
    "input": "Explain quicksort in one paragraph"
  }'
```

**List available models** (`/v1/models`):

```bash
curl http://localhost:8047/v1/models \
  -H "Authorization: Bearer sk-codex-hub-2025"
```

### OpenClaw Configuration

Add a custom provider in `~/.openclaw/openclaw.json`:

```jsonc
{
  "models": {
    "providers": [
      {
        "name": "codex-hub",
        "type": "openai",              // standard OpenAI-compatible
        "baseUrl": "http://<your-server-ip>:8047/v1",
        "apiKey": "sk-codex-hub-2025",
        "models": [
          { "id": "gpt-5.3-codex",     "name": "GPT-5.3 Codex (Latest)" },
          { "id": "gpt-5.2-codex",     "name": "GPT-5.2 Codex" },
          { "id": "gpt-5.1-codex",     "name": "GPT-5.1 Codex" },
          { "id": "gpt-5.1-codex-max", "name": "GPT-5.1 Codex Max" },
          { "id": "gpt-5.1-codex-mini","name": "GPT-5.1 Codex Mini" },
          { "id": "gpt-5-codex",       "name": "GPT-5 Codex" }
        ]
      }
    ]
  },
  "defaults": {
    "models": {
      "codex-hub/gpt-5.3-codex": {}
    }
  }
}
```

Then use the model in OpenClaw:

```bash
# Set as default model
openclaw model codex-hub/gpt-5.3-codex

# Or use per-session
/model codex-hub/gpt-5.3-codex
```

### Other Clients

| Client | Base URL | API Key |
|--------|----------|---------|
| Cherry Studio | `http://<ip>:8047/v1` | `sk-codex-hub-2025` |
| OpenWebUI | `http://<ip>:8047/v1` | `sk-codex-hub-2025` |
| Cursor | `http://<ip>:8047/v1` | `sk-codex-hub-2025` |
| Any OpenAI SDK | `http://<ip>:8047/v1` | `sk-codex-hub-2025` |

> **Tip:** The API key is configurable in the web UI → Settings tab.

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
├── models.py        # Model discovery, testing, blacklist
├── auth/
│   ├── oauth.py     # ChatGPT OAuth flow (PKCE)
│   └── refresh.py   # Token refresh
├── proxy/
│   └── routes.py    # Protocol proxy (/v1/chat/completions → Codex)
├── web/
│   └── index.html   # Management UI (responsive)
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
- 📤 **导入/导出** — JSON 批量导入导出账号（支持选择导出）
- 🌐 **双协议** — 同时支持 `/v1/chat/completions` 和 `/v1/responses`
- 📱 **手机适配** — 响应式界面，手机/电脑均可正常使用
- 🐳 **Docker 部署** — 一条命令启动
- 🔍 **模型自动发现** — 自动扫描 OpenClaw 源码，发现新 Codex 模型
- ⚡ **在线测试** — 一键测试所有模型可用性和延迟
- 🔒 **账号禁用/启用** — 临时停用账号不删除

### 已验证可用模型

| 模型 | 级别 | 说明 |
|------|------|------|
| `gpt-5-codex` | 🔵 标准 | GPT-5 基础 Codex |
| `gpt-5.1-codex` | 🔵 标准 | GPT-5.1 标准版 |
| `gpt-5.1-codex-max` | 🟣 最大 | 最大上下文窗口 |
| `gpt-5.1-codex-mini` | ⚪ 轻量 | 快速轻量 |
| `gpt-5.2-codex` | 🔵 标准 | 编码优化 |
| `gpt-5.3-codex` | 🟢 最新 | 最新稳定版，推荐 ⭐ |

> 所有模型均经过实际 API 测试验证。在管理后台「模型」标签页可随时扫描和测试。

### 追踪最新模型

OpenAI 会不定期新增 Codex 模型，三种方式保持同步，从易到难：

**方法一 — 扫描 OpenClaw 源码（最简单）**

更新 OpenClaw（`npm update -g openclaw`）后，一条命令列出所有 Codex 模型：

```bash
grep -o '"gpt-[^"]*codex[^"]*"' \
  /opt/homebrew/lib/node_modules/openclaw/node_modules/@mariozechner/pi-ai/dist/models.generated.d.ts \
  | sort -u
```

跟当前模型列表对比，多出来的就是新模型。

**方法二 — 直接问 Codex API**

用现有账号试投，发个请求改 model 名，能回就是支持的：

```bash
curl -X POST http://localhost:8047/v1/chat/completions \
  -H "Authorization: Bearer sk-codex-hub-2025" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.4-codex","messages":[{"role":"user","content":"hi"}]}'
```

或者在管理后台「模型」标签页 → 点单个模型的**测试**按钮，或 **⚡ 全部测试** 批量验证。

**方法三 — 关注源头**

- [OpenClaw Releases](https://github.com/openclaw/openclaw/releases) — 新版本会带模型列表更新
- [@mariozechner/pi-ai](https://www.npmjs.com/package/@mariozechner/pi-ai) npm 包 — 实际的 Codex 提供商实现
- [OpenAI 官方公告](https://openai.com/blog) — 官方模型发布

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
