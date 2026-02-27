"""账号调度器 - 轮询 + 健康优先 + 自动刷新"""
import time
import asyncio
import logging
from typing import Optional
from db import get_active_accounts, mark_account_used, mark_account_error, update_account_tokens
from auth.refresh import refresh_token

logger = logging.getLogger(__name__)

class Scheduler:
    def __init__(self):
        self._lock = asyncio.Lock()

    async def get_account(self) -> Optional[dict]:
        """获取一个可用账号（最久未使用优先，自动刷新过期 token）"""
        async with self._lock:
            accounts = get_active_accounts()
            if not accounts:
                return None

            for account in accounts:
                # token 还有 5 分钟以上有效期，直接用
                if account["expires"] > time.time() + 300:
                    mark_account_used(account["id"])
                    return account

                # token 快过期，尝试刷新
                try:
                    logger.info(f"刷新 token: {account['email']}")
                    new_tokens = await refresh_token(account["refresh"])
                    update_account_tokens(
                        account["id"],
                        new_tokens["access"],
                        new_tokens["refresh"],
                        new_tokens["expires"],
                    )
                    account.update(new_tokens)
                    mark_account_used(account["id"])
                    return account
                except Exception as e:
                    logger.warning(f"刷新失败 {account['email']}: {e}")
                    mark_account_error(account["id"], str(e))
                    continue

            return None

scheduler = Scheduler()
