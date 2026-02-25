# -*- coding: utf-8 -*-

"""
GeekGate 配置热重载模�?

通过 Redis Pub/Sub 实现跨节点配置热重载?
管理员修改配置后，所有节点在 10 秒内应用新配�?
"""

import asyncio
import json
from typing import Optional

from loguru import logger


# 支持热重载的配置?
HOT_RELOAD_KEYS = {
    "token_rpm_limit",
    "token_rph_limit",
    "token_max_concurrent",
    "default_user_daily_quota",
    "default_user_monthly_quota",
}

REDIS_CONFIG_HASH = "GeekGate:config:hot_reload"
REDIS_CONFIG_CHANNEL = "GeekGate:config_reload"


class ConfigReloader:
    """
    配置热重载器?

    分布式模式下通过 Redis Pub/Sub 订阅配置变更通知?
    收到消息后从 Redis Hash 拉取最新配置并更新本地 settings?
    单节点模式下直接更新内存配置?
    """

    def __init__(self):
        self._pubsub = None
        self._listen_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        """启动配置热重载订阅�?""
        from geek_gateway.redis_manager import redis_manager

        client = await redis_manager.get_client()
        if not client:
            logger.info("Redis 不可用，配置热重载仅支持单节点模�?)
            return

        try:
            self._pubsub = client.pubsub()
            await self._pubsub.subscribe(REDIS_CONFIG_CHANNEL)
            self._running = True
            self._listen_task = asyncio.create_task(self._listen())
            logger.info(f"配置热重载订阅已启动，频�?{REDIS_CONFIG_CHANNEL}")
        except Exception as e:
            logger.warning(f"配置热重载订阅启动失�?{e}")

    async def stop(self) -> None:
        """停止配置热重载订阅�?""
        self._running = False

        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None

        if self._pubsub:
            try:
                await self._pubsub.unsubscribe(REDIS_CONFIG_CHANNEL)
                await self._pubsub.close()
            except Exception:
                pass
            self._pubsub = None

        logger.info("配置热重载订阅已停止")

    async def _listen(self) -> None:
        """监听 Redis Pub/Sub 消息�?""
        try:
            while self._running and self._pubsub:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message and message.get("type") == "message":
                    await self._on_message(message.get("data", ""))
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"配置热重载监听异�?{e}")

    async def _on_message(self, data: str) -> None:
        """
        处理配置变更消息，从 Redis 拉取最新配置并更新本地 settings?

        Args:
            data: Pub/Sub 消息内容（JSON 格式的变?key 列表?
        """
        from geek_gateway.config import settings
        from geek_gateway.redis_manager import redis_manager

        try:
            changed_keys = json.loads(data) if isinstance(data, str) else [data]
        except (json.JSONDecodeError, TypeError):
            changed_keys = [data] if data else []

        client = await redis_manager.get_client()
        if not client:
            return

        try:
            config_data = await client.hgetall(REDIS_CONFIG_HASH)
            applied = []
            for key in changed_keys:
                if key in HOT_RELOAD_KEYS and key in config_data:
                    value = config_data[key]
                    _apply_config(settings, key, value)
                    applied.append(key)

            if applied:
                logger.info(f"配置热重载已应用: {', '.join(applied)}")
        except Exception as e:
            logger.warning(f"配置热重载应用失�?{e}")


def _apply_config(settings_obj, key: str, value: str) -> None:
    """将配置值应用到 settings 对象�?""
    try:
        # 所有支持的热重载配置项都是 int 类型
        setattr(settings_obj, key, int(value))
    except (ValueError, TypeError) as e:
        logger.warning(f"配置值转换失�?{key}={value}, {e}")


# 全局配置热重载器单例
config_reloader = ConfigReloader()
