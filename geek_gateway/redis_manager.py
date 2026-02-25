# -*- coding: utf-8 -*-

"""
GeekGate Redis 连接管理�?

管理 Redis 连接池，支持自动重连和优雅降�?
所有需?Redis 的组件通过此管理器获取连接?
"""

import asyncio
from typing import Optional

from loguru import logger


class RedisManager:
    """
    Redis 连接池管理器，支持优雅降�?

    ?Redis 不可用时，所有依?Redis 的功能自动降级为本地实现?
    ?30 秒尝试重新连�?
    """

    def __init__(self):
        self._pool = None
        self._client = None
        self._available: bool = False
        self._reconnect_task: Optional[asyncio.Task] = None
        self._pubsub = None
        self._url: str = ""
        self._max_connections: int = 50

    async def initialize(self, redis_url: str, max_connections: int = 50) -> None:
        """
        初始?Redis 连接�?

        Args:
            redis_url: Redis 连接 URL (redis://host:port/db)
            max_connections: 最大连接数
        """
        self._url = redis_url
        self._max_connections = max_connections

        if not redis_url:
            logger.info("Redis URL 未配置，跳过 Redis 初始�?)
            return

        try:
            import redis.asyncio as aioredis

            self._pool = aioredis.ConnectionPool.from_url(
                redis_url,
                max_connections=max_connections,
                decode_responses=True,
            )
            self._client = aioredis.Redis(connection_pool=self._pool)

            # 测试连接
            await self._client.ping()
            self._available = True
            logger.info(f"Redis 连接成功: {self._mask_url(redis_url)}")

        except ImportError:
            logger.warning("redis 包未安装，Redis 功能不可�?)
            self._available = False
        except Exception as e:
            logger.warning(f"Redis 连接失败: {e}，将以降级模式运�?)
            self._available = False
            # 启动重连循环
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def close(self) -> None:
        """关闭 Redis 连接池�?""
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        if self._client:
            await self._client.close()
            self._client = None

        if self._pool:
            await self._pool.disconnect()
            self._pool = None

        self._available = False
        logger.info("Redis 连接已关�?)

    @property
    def is_available(self) -> bool:
        """Redis 是否可用�?""
        return self._available

    async def get_client(self):
        """
        获取 Redis 客户�?

        Returns:
            Redis 客户端实例，不可用时返回 None
        """
        if not self._available or not self._client:
            return None

        try:
            await self._client.ping()
            return self._client
        except Exception:
            self._available = False
            if not self._reconnect_task or self._reconnect_task.done():
                self._reconnect_task = asyncio.create_task(self._reconnect_loop())
            return None

    async def _reconnect_loop(self) -> None:
        """?30 秒尝试重新连?Redis�?""
        while not self._available:
            await asyncio.sleep(30)
            try:
                if not self._url:
                    break

                import redis.asyncio as aioredis

                if not self._pool:
                    self._pool = aioredis.ConnectionPool.from_url(
                        self._url,
                        max_connections=self._max_connections,
                        decode_responses=True,
                    )
                    self._client = aioredis.Redis(connection_pool=self._pool)

                await self._client.ping()
                self._available = True
                logger.info(f"Redis 重连成功: {self._mask_url(self._url)}")
                break

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Redis 重连失败: {e}�? 秒后重试")

    def _mask_url(self, url: str) -> str:
        """遮蔽 URL 中的密码信息�?""
        if "@" in url:
            parts = url.split("@")
            return f"redis://***@{parts[-1]}"
        return url


# 全局 Redis 管理器单?
redis_manager = RedisManager()
