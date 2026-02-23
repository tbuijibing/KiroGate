# -*- coding: utf-8 -*-

# KiroGate
# Based on kiro-openai-gateway by Jwadow (https://github.com/Jwadow/kiro-openai-gateway)
# Original Copyright (C) 2025 Jwadow
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""
AuthManager Cache for Multi-Tenant Support.

Manages multiple KiroAuthManager instances for different refresh tokens.

Dual-layer caching architecture:
- Single-node mode: In-memory OrderedDict LRU cache (max 100 entries)
- Distributed mode: Local hot cache (max 50) + Redis shared cache (TTL 3600s)

Cache lookup flow (distributed):
  Local hot cache → Redis → Create new instance → Write both layers
"""

import asyncio
import hashlib
import json
from collections import OrderedDict
from typing import Optional

from loguru import logger

from kiro_gateway.auth import KiroAuthManager
from kiro_gateway.config import settings


# Redis cache TTL in seconds
_REDIS_CACHE_TTL = 3600

# Redis key prefix
_REDIS_KEY_PREFIX = "kirogate:auth_cache:"


class AuthManagerCache:
    """
    LRU Cache for KiroAuthManager instances.

    Supports two modes:
    - Single-node: OrderedDict LRU cache (max_size entries)
    - Distributed: Local hot cache (hot_cache_size) + Redis shared cache

    Thread-safe using asyncio.Lock.
    """

    def __init__(self, max_size: int = 100, hot_cache_size: int = 50):
        """
        Initialize AuthManager cache.

        Args:
            max_size: Maximum entries for single-node LRU cache (default: 100)
            hot_cache_size: Maximum entries for local hot cache in distributed mode (default: 50)
        """
        self.max_size = max_size
        self._hot_cache_size = hot_cache_size

        # Single-node mode: full LRU cache
        self.cache: OrderedDict[str, KiroAuthManager] = OrderedDict()

        # Distributed mode: local hot cache
        self._hot_cache: OrderedDict[str, KiroAuthManager] = OrderedDict()

        self.lock = asyncio.Lock()
        logger.info(
            f"AuthManager cache initialized with max_size={max_size}, "
            f"hot_cache_size={hot_cache_size}"
        )

    async def get_or_create(
        self,
        refresh_token: str,
        region: Optional[str] = None,
        profile_arn: Optional[str] = None,
    ) -> KiroAuthManager:
        """
        Get or create AuthManager for given refresh token.

        In single-node mode, uses in-memory OrderedDict LRU cache.
        In distributed mode, checks local hot cache → Redis → creates new.

        Args:
            refresh_token: Kiro refresh token
            region: AWS region (defaults to settings.region)
            profile_arn: AWS profile ARN (defaults to settings.profile_arn)

        Returns:
            KiroAuthManager instance for the refresh token
        """
        if settings.is_distributed:
            return await self._get_or_create_distributed(refresh_token, region, profile_arn)
        return await self._get_or_create_local(refresh_token, region, profile_arn)

    async def _get_or_create_local(
        self,
        refresh_token: str,
        region: Optional[str] = None,
        profile_arn: Optional[str] = None,
    ) -> KiroAuthManager:
        """Single-node mode: standard OrderedDict LRU cache."""
        async with self.lock:
            if refresh_token in self.cache:
                self.cache.move_to_end(refresh_token)
                logger.debug(f"AuthManager cache hit for token: {self._mask_token(refresh_token)}")
                return self.cache[refresh_token]

            logger.info(f"Creating new AuthManager for token: {self._mask_token(refresh_token)}")
            auth_manager = KiroAuthManager(
                refresh_token=refresh_token,
                region=region or settings.region,
                profile_arn=profile_arn or settings.profile_arn,
            )

            self.cache[refresh_token] = auth_manager

            if len(self.cache) > self.max_size:
                oldest_token, _ = self.cache.popitem(last=False)
                logger.info(
                    f"AuthManager cache full, evicted oldest token: "
                    f"{self._mask_token(oldest_token)}"
                )

            logger.debug(f"AuthManager cache size: {len(self.cache)}/{self.max_size}")
            return auth_manager

    async def _get_or_create_distributed(
        self,
        refresh_token: str,
        region: Optional[str] = None,
        profile_arn: Optional[str] = None,
    ) -> KiroAuthManager:
        """Distributed mode: local hot cache → Redis → create new."""
        async with self.lock:
            # 1. Check local hot cache
            if refresh_token in self._hot_cache:
                self._hot_cache.move_to_end(refresh_token)
                logger.debug(
                    f"AuthManager hot cache hit for token: {self._mask_token(refresh_token)}"
                )
                return self._hot_cache[refresh_token]

            # 2. Check Redis
            auth_manager = await self._get_from_redis(refresh_token, region, profile_arn)
            if auth_manager is not None:
                self._put_hot_cache(refresh_token, auth_manager)
                logger.debug(
                    f"AuthManager Redis cache hit for token: {self._mask_token(refresh_token)}"
                )
                return auth_manager

            # 3. Create new instance
            logger.info(f"Creating new AuthManager for token: {self._mask_token(refresh_token)}")
            auth_manager = KiroAuthManager(
                refresh_token=refresh_token,
                region=region or settings.region,
                profile_arn=profile_arn or settings.profile_arn,
            )

            # 4. Write to both local hot cache and Redis
            self._put_hot_cache(refresh_token, auth_manager)
            await self._put_to_redis(refresh_token, auth_manager)

            logger.debug(
                f"AuthManager hot cache size: {len(self._hot_cache)}/{self._hot_cache_size}"
            )
            return auth_manager

    def _put_hot_cache(self, refresh_token: str, auth_manager: KiroAuthManager) -> None:
        """Add entry to local hot cache with LRU eviction."""
        self._hot_cache[refresh_token] = auth_manager
        self._hot_cache.move_to_end(refresh_token)

        if len(self._hot_cache) > self._hot_cache_size:
            oldest_token, _ = self._hot_cache.popitem(last=False)
            logger.debug(
                f"Hot cache full, evicted oldest token: {self._mask_token(oldest_token)}"
            )

    @staticmethod
    def _redis_key(refresh_token: str) -> str:
        """Generate Redis key from token hash."""
        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        return f"{_REDIS_KEY_PREFIX}{token_hash}"

    @staticmethod
    def _serialize_manager(auth_manager: KiroAuthManager) -> str:
        """Serialize essential AuthManager fields to JSON for Redis storage."""
        data = {
            "refresh_token": auth_manager._refresh_token,
            "region": auth_manager._region,
            "profile_arn": auth_manager._profile_arn,
            "access_token": auth_manager._access_token,
            "expires_at": auth_manager._expires_at.isoformat() if auth_manager._expires_at else None,
        }
        return json.dumps(data)

    @staticmethod
    def _deserialize_manager(data_str: str) -> KiroAuthManager:
        """Deserialize JSON from Redis into a KiroAuthManager instance."""
        from datetime import datetime

        data = json.loads(data_str)
        manager = KiroAuthManager(
            refresh_token=data["refresh_token"],
            region=data.get("region", "us-east-1"),
            profile_arn=data.get("profile_arn"),
        )
        # Restore cached token state
        if data.get("access_token"):
            manager._access_token = data["access_token"]
        if data.get("expires_at"):
            manager._expires_at = datetime.fromisoformat(data["expires_at"])
        return manager

    async def _get_from_redis(
        self,
        refresh_token: str,
        region: Optional[str] = None,
        profile_arn: Optional[str] = None,
    ) -> Optional[KiroAuthManager]:
        """Try to load AuthManager state from Redis. Returns None on miss or error."""
        try:
            from kiro_gateway.redis_manager import redis_manager

            client = await redis_manager.get_client()
            if client is None:
                logger.warning("Redis 不可用，降级为仅本地热缓存")
                return None

            key = self._redis_key(refresh_token)
            data_str = await client.get(key)
            if data_str is None:
                return None

            return self._deserialize_manager(data_str)

        except Exception as e:
            logger.warning(f"从 Redis 读取认证缓存失败: {e}，降级为本地缓存")
            return None

    async def _put_to_redis(self, refresh_token: str, auth_manager: KiroAuthManager) -> None:
        """Write AuthManager state to Redis with TTL. Fails silently with warning."""
        try:
            from kiro_gateway.redis_manager import redis_manager

            client = await redis_manager.get_client()
            if client is None:
                logger.warning("Redis 不可用，跳过写入 Redis 缓存")
                return

            key = self._redis_key(refresh_token)
            data_str = self._serialize_manager(auth_manager)
            await client.set(key, data_str, ex=_REDIS_CACHE_TTL)

        except Exception as e:
            logger.warning(f"写入 Redis 认证缓存失败: {e}，仅保留本地缓存")

    async def clear(self) -> None:
        """Clear all cached AuthManager instances."""
        async with self.lock:
            count = len(self.cache)
            self.cache.clear()

            hot_count = len(self._hot_cache)
            self._hot_cache.clear()

            total = count + hot_count
            logger.info(f"AuthManager cache cleared, removed {total} instances")

    async def remove(self, refresh_token: str) -> bool:
        """
        Remove specific AuthManager from cache.

        Args:
            refresh_token: Refresh token to remove

        Returns:
            True if removed from any layer, False if not found
        """
        async with self.lock:
            removed = False

            if refresh_token in self.cache:
                del self.cache[refresh_token]
                removed = True

            if refresh_token in self._hot_cache:
                del self._hot_cache[refresh_token]
                removed = True

            # Also remove from Redis in distributed mode
            if settings.is_distributed:
                try:
                    from kiro_gateway.redis_manager import redis_manager

                    client = await redis_manager.get_client()
                    if client is not None:
                        key = self._redis_key(refresh_token)
                        await client.delete(key)
                except Exception as e:
                    logger.warning(f"从 Redis 删除认证缓存失败: {e}")

            if removed:
                logger.info(f"Removed AuthManager from cache: {self._mask_token(refresh_token)}")
            return removed

    def _mask_token(self, token: str) -> str:
        """
        Mask token for logging (show only first and last 4 chars).

        Args:
            token: Token to mask

        Returns:
            Masked token string
        """
        if len(token) <= 8:
            return "***"
        return f"{token[:4]}...{token[-4:]}"

    @property
    def size(self) -> int:
        """Get current cache size (local cache in single-node, hot cache in distributed)."""
        if settings.is_distributed:
            return len(self._hot_cache)
        return len(self.cache)


# Global cache instance
auth_cache = AuthManagerCache(max_size=100, hot_cache_size=50)
