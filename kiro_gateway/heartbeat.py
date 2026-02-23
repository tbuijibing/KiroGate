# -*- coding: utf-8 -*-

"""
KiroGate 节点心跳上报模块。

每 10 秒向 Redis 写入当前节点心跳信息（TTL 30 秒），
管理面板通过扫描心跳 key 获取在线节点列表。
"""

import asyncio
import time
from typing import Optional

from loguru import logger


# Redis key 前缀
_NODE_PREFIX = "kirogate:node"
_NODES_SET_KEY = "kirogate:nodes"


class NodeHeartbeat:
    """
    节点心跳管理器。

    每 10 秒向 Redis 写入心跳 Hash，TTL 30 秒。
    心跳包含节点 ID、状态、运行时间、连接数、最近 1 分钟请求数。
    """

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running: bool = False
        self._start_time: float = time.time()

    async def start(self) -> None:
        """启动心跳上报循环。"""
        from kiro_gateway.config import settings

        if not settings.is_distributed:
            logger.debug("单节点模式，跳过心跳上报")
            return

        self._running = True
        self._start_time = time.time()
        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"节点心跳上报已启动 (node_id={settings.node_id})")

    async def stop(self) -> None:
        """停止心跳上报并清理 Redis 中的心跳数据。"""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # 清理 Redis 中的心跳数据
        await self._cleanup_heartbeat()
        logger.info("节点心跳上报已停止")

    async def _heartbeat_loop(self) -> None:
        """心跳上报循环，每 10 秒执行一次。"""
        while self._running:
            try:
                await self._send_heartbeat()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"心跳上报失败: {e}")

            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                break

    async def _send_heartbeat(self) -> None:
        """向 Redis 写入心跳信息。"""
        from kiro_gateway.config import settings
        from kiro_gateway.redis_manager import redis_manager
        from kiro_gateway.metrics import metrics

        client = await redis_manager.get_client()
        if not client:
            return

        node_id = settings.node_id
        heartbeat_key = f"{_NODE_PREFIX}:{node_id}:heartbeat"
        uptime = int(time.time() - self._start_time)

        # 获取当前节点连接数和最近 1 分钟请求数
        connections = metrics._active_connections if hasattr(metrics, "_active_connections") else 0
        requests_1m = await self._get_requests_last_minute()

        heartbeat_data = {
            "node_id": node_id,
            "status": "online",
            "uptime": str(uptime),
            "connections": str(connections),
            "last_heartbeat": str(int(time.time())),
            "requests_1m": str(requests_1m),
        }

        try:
            pipe = client.pipeline()
            # 写入心跳 Hash，TTL 30 秒
            pipe.hset(heartbeat_key, mapping=heartbeat_data)
            pipe.expire(heartbeat_key, 30)
            # 将节点 ID 加入已知节点集合
            pipe.sadd(_NODES_SET_KEY, node_id)
            await pipe.execute()
        except Exception as e:
            logger.debug(f"心跳写入 Redis 失败: {e}")

    async def _get_requests_last_minute(self) -> int:
        """获取当前节点最近 1 分钟的请求数。"""
        try:
            from kiro_gateway.metrics import metrics

            # 尝试从 Redis 获取全局请求数（近似值）
            if hasattr(metrics, "_request_count_1m"):
                return metrics._request_count_1m

            # 降级：使用本地 response_times 长度作为近似值
            if hasattr(metrics, "_response_times"):
                return len(metrics._response_times)

            return 0
        except Exception:
            return 0

    async def _cleanup_heartbeat(self) -> None:
        """清理当前节点的心跳数据。"""
        try:
            from kiro_gateway.config import settings
            from kiro_gateway.redis_manager import redis_manager

            client = await redis_manager.get_client()
            if not client:
                return

            node_id = settings.node_id
            heartbeat_key = f"{_NODE_PREFIX}:{node_id}:heartbeat"

            pipe = client.pipeline()
            pipe.delete(heartbeat_key)
            pipe.srem(_NODES_SET_KEY, node_id)
            await pipe.execute()
        except Exception as e:
            logger.debug(f"清理心跳数据失败: {e}")

    @staticmethod
    async def get_online_nodes() -> list:
        """
        获取所有在线节点信息。

        Returns:
            在线节点列表，每个节点包含 node_id、status、uptime、connections、
            last_heartbeat、requests_1m 字段
        """
        from kiro_gateway.redis_manager import redis_manager

        client = await redis_manager.get_client()
        if not client:
            return []

        try:
            # 获取所有已知节点 ID
            node_ids = await client.smembers(_NODES_SET_KEY)
            if not node_ids:
                return []

            nodes = []
            stale_nodes = []

            for node_id in node_ids:
                heartbeat_key = f"{_NODE_PREFIX}:{node_id}:heartbeat"
                data = await client.hgetall(heartbeat_key)

                if data:
                    nodes.append({
                        "node_id": data.get("node_id", node_id),
                        "status": data.get("status", "unknown"),
                        "uptime": int(data.get("uptime", 0)),
                        "connections": int(data.get("connections", 0)),
                        "last_heartbeat": int(data.get("last_heartbeat", 0)),
                        "requests_1m": int(data.get("requests_1m", 0)),
                    })
                else:
                    # 心跳已过期，标记为离线待清理
                    stale_nodes.append(node_id)

            # 清理过期节点
            if stale_nodes:
                try:
                    await client.srem(_NODES_SET_KEY, *stale_nodes)
                except Exception:
                    pass

            return nodes
        except Exception as e:
            logger.debug(f"获取在线节点失败: {e}")
            return []


# 全局心跳管理器单例
node_heartbeat = NodeHeartbeat()
