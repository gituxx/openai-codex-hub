"""启动入口 - 同时监听 8047 (主服务) 和 1455 (OAuth callback)"""
import asyncio
import uvicorn
from main import app

async def main():
    config_main = uvicorn.Config(app, host="0.0.0.0", port=8047, log_level="info")
    config_oauth = uvicorn.Config(app, host="127.0.0.1", port=1455, log_level="warning")

    server_main = uvicorn.Server(config_main)
    server_oauth = uvicorn.Server(config_oauth)

    # 两个端口同时跑
    await asyncio.gather(
        server_main.serve(),
        server_oauth.serve(),
    )

if __name__ == "__main__":
    asyncio.run(main())
