"""账号调度器 - 智能轮询 + 错误分级 + 冷却自愈 + 自动重试"""
import time
import asyncio
import logging
from typing import Optional
from db import (
    get_active_accounts, get_available_accounts_excluding,
    mark_account_used, mark_account_error, mark_account_cooldown,
    mark_account_success, update_account_tokens,
)
from auth.refresh import refresh_token

logger = logging.getLogger(__name__)

# 冷却时间配置（秒）
COOLDOWN_429 = 60       # 429 Too Many Requests → 冷却 60s
COOLDOWN_401 = 10       # 401 Unauthorized → 先尝试刷新，刷新失败冷却 10s
COOLDOWN_5XX = 30       # 5xx 服务端错误 → 冷却 30s
COOLDOWN_REFRESH_FAIL = 120  # refresh_token 失败 → 冷却 2 分钟
MAX_RETRIES = 3         # 单次请求最多换号重试次数


def classify_error(status_code: int, error_text: str = "") -> tuple[str, int]:
    """错误分级：返回 (类型, 冷却秒数)
    - 'cooldown': 临时冷却，到期自动恢复
    - 'refresh':  需要刷新 token
    - 'fatal':    永久标记 error（需要人工介入）
    """
    if status_code == 429:
        return "cooldown", COOLDOWN_429
    elif status_code == 401:
        return "refresh", COOLDOWN_401
    elif status_code == 403:
        # 403 通常是账号被封或权限问题
        return "fatal", 0
    elif 500 <= status_code < 600:
        return "cooldown", COOLDOWN_5XX
    else:
        return "cooldown", COOLDOWN_5XX


class Scheduler:
    def __init__(self):
        self._lock = asyncio.Lock()

    async def get_account(self, exclude_ids: list = None) -> Optional[dict]:
        """获取一个可用账号（错误率低 + 最久未用优先，自动刷新过期 token）

        Args:
            exclude_ids: 本次请求中已经失败过的账号 ID，跳过不选
        """
        async with self._lock:
            if exclude_ids:
                accounts = get_available_accounts_excluding(exclude_ids)
            else:
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
                    logger.info(f"[scheduler] 刷新 token: {account['email']}")
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
                    logger.warning(f"[scheduler] 刷新失败 {account['email']}: {e}")
                    mark_account_cooldown(account["id"], f"refresh failed: {e}", COOLDOWN_REFRESH_FAIL)
                    continue

            return None

    async def handle_request_error(self, account: dict, status_code: int, error_text: str = ""):
        """处理请求失败：根据错误类型分级处理

        Returns:
            bool: True = 应该重试（换号），False = 不可重试
        """
        error_type, cooldown_secs = classify_error(status_code, error_text)

        if error_type == "cooldown":
            logger.info(f"[scheduler] {account['email']} → 冷却 {cooldown_secs}s (HTTP {status_code})")
            mark_account_cooldown(account["id"], f"HTTP {status_code}: {error_text[:100]}", cooldown_secs)
            return True  # 可以换号重试

        elif error_type == "refresh":
            # 401: 尝试刷新 token
            try:
                logger.info(f"[scheduler] {account['email']} → 401, 尝试刷新 token")
                new_tokens = await refresh_token(account["refresh"])
                update_account_tokens(
                    account["id"],
                    new_tokens["access"],
                    new_tokens["refresh"],
                    new_tokens["expires"],
                )
                logger.info(f"[scheduler] {account['email']} 刷新成功")
                return True  # 刷新成功，可以重试（用新 token）
            except Exception as e:
                logger.warning(f"[scheduler] {account['email']} 刷新失败: {e}")
                mark_account_cooldown(account["id"], f"401 + refresh failed: {e}", COOLDOWN_REFRESH_FAIL)
                return True  # 刷新失败，换号重试

        elif error_type == "fatal":
            logger.error(f"[scheduler] {account['email']} → 永久错误 (HTTP {status_code})")
            mark_account_error(account["id"], f"HTTP {status_code}: {error_text[:200]}")
            return True  # 这个号废了，但可以换别的号

        return False

    async def on_request_success(self, account: dict):
        """请求成功：重置错误计数"""
        mark_account_success(account["id"])


scheduler = Scheduler()
