# -*- coding: utf-8 -*-

"""
KiroGate 智能 Token 分配器。

实现基于成功率、新鲜度、负载均衡和风控安全的 Token 智能分配算法。
支持双模式运行：
- 单节点模式：内存字典 + asyncio.Lock（原有行为）
- 分布式模式：Redis Sorted Set + 分布式锁 + 原子计数器
"""

import asyncio
import random
import time
from typing import Dict, List, Optional, Tuple

from loguru import logger

from kiro_gateway.database import user_db, DonatedToken
from kiro_gateway.auth import KiroAuthManager
from kiro_gateway.config import settings


# Redis key constants
_SCORES_KEY = "kirogate:tokens:scores"
_ALLOC_LOCK_KEY = "kirogate:token_alloc:lock"
_TOKEN_PREFIX = "kirogate:token"

# Cooldown policy: consecutive_fails -> cooldown_seconds (None = suspend)
COOLDOWN_POLICY = {
    2: 300,     # 连续失败 2 次 → 冷却 5 分钟
    3: 1800,    # 连续失败 3 次 → 冷却 30 分钟
    5: None,    # 连续失败 5 次 → 暂停
}


class NoTokenAvailable(Exception):
    """No active token available for allocation."""

    def __init__(self, message: str = "No tokens available", retry_after: Optional[int] = None):
        super().__init__(message)
        self.retry_after = retry_after


class SmartTokenAllocator:
    """
    智能 Token 分配器。

    双模式运行：
    - 单节点：asyncio.Lock + 内存字典
    - 分布式：Redis Sorted Set + 分布式锁 + 原子计数器
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._token_managers: dict[int, KiroAuthManager] = {}
        # Local cached scores for lock-failure degradation
        self._cached_scores: Dict[int, float] = {}
        # Background sync task
        self._sync_task: Optional[asyncio.Task] = None
        # Track last allocated token id for consecutive-use detection (single-node)
        self._last_token_id: Optional[int] = None
        self._consecutive_count: int = 0

    # ==================== Lifecycle ====================

    async def initialize(self) -> None:
        """Initialize the allocator. In distributed mode, sync scores to Redis."""
        if settings.is_distributed:
            await self._sync_scores_to_redis()
            self._sync_task = asyncio.create_task(self._sync_scores_loop())
            logger.info("TokenAllocator: 分布式模式初始化完成，评分同步已启动")
        else:
            logger.info("TokenAllocator: 单节点模式初始化完成")

    async def shutdown(self) -> None:
        """Shutdown the allocator and clean up resources."""
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None
        self._token_managers.clear()
        self._cached_scores.clear()
        logger.info("TokenAllocator: 已关闭")

    # ==================== Score Calculation ====================

    def calculate_score(self, token: DonatedToken, risk_data: Optional[dict] = None) -> float:
        """
        计算 Token 评分 (0-100)。

        评分因子：
        - 成功率 (权重 40%)
        - 新鲜度 (权重 15%)
        - 负载均衡 (权重 15%)
        - 风控安全 (权重 30%)
        """
        now = int(time.time() * 1000)

        # 成功率 (权重 40%)
        total = token.success_count + token.fail_count
        if total == 0:
            success_rate = 1.0
        else:
            success_rate = token.success_count / total

        if success_rate < settings.token_min_success_rate and total > 10:
            base_score = success_rate * 20
        else:
            base_score = success_rate * 40

        # 新鲜度 (权重 15%)
        if token.last_used:
            hours_since_use = (now - token.last_used) / 3600000
        else:
            hours_since_use = 0

        if hours_since_use < 1:
            freshness = 15.0
        elif hours_since_use < 24:
            freshness = 12.0
        else:
            freshness = max(3.0, 15.0 - hours_since_use / 24)

        # 负载均衡 (权重 15%)
        usage_score = max(0.0, 15.0 - (total / 100))

        # 风控安全 (权重 30%)
        risk_score = 30.0
        if risk_data:
            rpm_limit = settings.token_rpm_limit or 1
            rph_limit = settings.token_rph_limit or 1
            max_concurrent = settings.token_max_concurrent or 1
            max_consecutive = settings.token_max_consecutive_uses or 1

            rpm_usage = risk_data.get("rpm", 0) / rpm_limit
            rph_usage = risk_data.get("rph", 0) / rph_limit
            concurrent = risk_data.get("concurrent", 0) / max_concurrent
            consecutive = risk_data.get("consecutive_uses", 0) / max_consecutive

            risk_penalty = max(rpm_usage, rph_usage, concurrent, consecutive)
            risk_score = max(0.0, 30.0 * (1.0 - risk_penalty))

        return base_score + freshness + usage_score + risk_score

    # ==================== Token Allocation ====================

    async def get_best_token(self, user_id: Optional[int] = None) -> Tuple[DonatedToken, KiroAuthManager]:
        """
        获取最优 Token。

        对于有用户的请求，优先使用用户自己的私有 Token。
        否则使用公共 Token 池。

        Returns:
            (DonatedToken, KiroAuthManager) tuple

        Raises:
            NoTokenAvailable: 无可用 Token（含 retry_after 属性用于 429 响应）
        """
        from kiro_gateway.metrics import metrics
        self_use_enabled = await metrics.is_self_use_enabled()

        if user_id:
            user_tokens = await user_db.get_user_tokens(user_id)
            active_tokens = [
                t for t in user_tokens
                if t.status == "active" and (not self_use_enabled or t.visibility == "private")
            ]
            if active_tokens:
                if settings.is_distributed:
                    token = await self._select_token_distributed(active_tokens)
                else:
                    token = await self._select_token_local(active_tokens)
                if token:
                    manager = await self._get_manager(token)
                    return token, manager

        if self_use_enabled:
            raise NoTokenAvailable("Self-use mode: public token pool is disabled")

        # 使用公共 Token 池
        if settings.is_distributed:
            return await self._allocate_distributed(user_id)
        else:
            return await self._allocate_local()

    async def _allocate_local(self) -> Tuple[DonatedToken, KiroAuthManager]:
        """单节点模式 Token 分配。"""
        public_tokens = await user_db.get_public_tokens()
        if not public_tokens:
            raise NoTokenAvailable("No public tokens available")

        now = int(time.time())

        # Filter out tokens in cooldown or suspended
        available = []
        for t in public_tokens:
            if t.cooldown_until and t.cooldown_until > now * 1000:
                continue
            available.append(t)

        if not available:
            # Calculate retry_after from earliest cooldown expiry
            retry_after = self._calc_retry_after(public_tokens)
            raise NoTokenAvailable("All tokens are rate-limited or cooling down", retry_after=retry_after)

        # Filter low success rate tokens (give new tokens a chance)
        good_tokens = [
            t for t in available
            if t.success_rate >= settings.token_min_success_rate or
               (t.success_count + t.fail_count) < 10
        ]
        if not good_tokens:
            good_tokens = available

        # Check consecutive use limit
        best = max(good_tokens, key=lambda t: self.calculate_score(t))

        async with self._lock:
            if (self._last_token_id == best.id and
                    self._consecutive_count >= settings.token_max_consecutive_uses):
                # Force rotation: pick next best
                alternatives = [t for t in good_tokens if t.id != best.id]
                if alternatives:
                    best = max(alternatives, key=lambda t: self.calculate_score(t))
                    self._last_token_id = best.id
                    self._consecutive_count = 1
                else:
                    # Only one token available, reset counter
                    self._consecutive_count = 0
            elif self._last_token_id == best.id:
                self._consecutive_count += 1
            else:
                self._last_token_id = best.id
                self._consecutive_count = 1

        manager = await self._get_manager(best)
        return best, manager

    async def _allocate_distributed(self, user_id: Optional[int] = None) -> Tuple[DonatedToken, KiroAuthManager]:
        """分布式模式 Token 分配。"""
        from kiro_gateway.redis_manager import redis_manager

        client = await redis_manager.get_client()
        if not client:
            # Redis unavailable, degrade to local
            logger.warning("TokenAllocator: Redis 不可用，降级为本地分配")
            return await self._allocate_local()

        # Try to acquire distributed lock
        lock_acquired = await self._try_acquire_lock(client)

        if lock_acquired:
            try:
                return await self._allocate_with_lock(client)
            finally:
                # Release lock
                try:
                    await client.delete(_ALLOC_LOCK_KEY)
                except Exception:
                    pass
        else:
            # Lock failed, degrade to local cached scores
            logger.debug("TokenAllocator: 获取分布式锁失败，降级为本地缓存评分")
            return await self._allocate_from_cache()

    async def _try_acquire_lock(self, client) -> bool:
        """Try to acquire the distributed allocation lock with 2s timeout."""
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            acquired = await client.set(
                _ALLOC_LOCK_KEY,
                settings.node_id,
                nx=True,
                ex=5,
            )
            if acquired:
                return True
            await asyncio.sleep(0.05)
        return False

    async def _allocate_with_lock(self, client) -> Tuple[DonatedToken, KiroAuthManager]:
        """Allocate token while holding the distributed lock."""
        # Get scored tokens from Redis Sorted Set (highest score first)
        scored_members = await client.zrevrange(_SCORES_KEY, 0, -1, withscores=True)

        if not scored_members:
            raise NoTokenAvailable("No tokens in score set")

        now = int(time.time())
        min_retry_after: Optional[int] = None

        for member, _score in scored_members:
            try:
                token_id = int(member)
            except (ValueError, TypeError):
                continue

            # Check cooldown
            cooldown_key = f"{_TOKEN_PREFIX}:{token_id}:cooldown"
            cooldown_val = await client.get(cooldown_key)
            if cooldown_val:
                ttl = await client.ttl(cooldown_key)
                if ttl > 0 and (min_retry_after is None or ttl < min_retry_after):
                    min_retry_after = ttl
                continue

            # Check RPM limit
            rpm_key = f"{_TOKEN_PREFIX}:{token_id}:rpm"
            rpm_val = await client.get(rpm_key)
            current_rpm = int(rpm_val) if rpm_val else 0
            if current_rpm >= settings.token_rpm_limit:
                ttl = await client.ttl(rpm_key)
                if ttl > 0 and (min_retry_after is None or ttl < min_retry_after):
                    min_retry_after = ttl
                continue

            # Check RPH limit
            rph_key = f"{_TOKEN_PREFIX}:{token_id}:rph"
            rph_val = await client.get(rph_key)
            current_rph = int(rph_val) if rph_val else 0
            if current_rph >= settings.token_rph_limit:
                ttl = await client.ttl(rph_key)
                if ttl > 0 and (min_retry_after is None or ttl < min_retry_after):
                    min_retry_after = ttl
                continue

            # Check concurrent limit
            concurrent_key = f"{_TOKEN_PREFIX}:{token_id}:concurrent"
            concurrent_val = await client.get(concurrent_key)
            current_concurrent = int(concurrent_val) if concurrent_val else 0
            if current_concurrent >= settings.token_max_concurrent:
                if min_retry_after is None or 5 < min_retry_after:
                    min_retry_after = 5
                continue

            # Check consecutive use limit
            consecutive_key = f"{_TOKEN_PREFIX}:{token_id}:consecutive"
            consecutive_val = await client.get(consecutive_key)
            current_consecutive = int(consecutive_val) if consecutive_val else 0
            if current_consecutive >= settings.token_max_consecutive_uses:
                # Force rotation: skip this token, reset its consecutive counter
                await client.delete(consecutive_key)
                continue

            # Token is available! Increment counters atomically
            pipe = client.pipeline()
            # RPM with TTL 60s
            pipe.incr(rpm_key)
            pipe.expire(rpm_key, 60)
            # RPH with TTL 3600s
            pipe.incr(rph_key)
            pipe.expire(rph_key, 3600)
            # Concurrent count
            pipe.incr(concurrent_key)
            # Consecutive use count
            pipe.incr(consecutive_key)
            await pipe.execute()

            # Set TTL on rpm/rph only if they're new keys (first request)
            # The expire above handles this

            # Fetch the token from database
            token = await user_db.get_token_by_id(token_id)
            if not token or token.status != "active":
                # Token no longer valid, clean up and continue
                await client.zrem(_SCORES_KEY, str(token_id))
                continue

            manager = await self._get_manager(token)
            return token, manager

        # All tokens are limited
        raise NoTokenAvailable(
            "All tokens are rate-limited or cooling down",
            retry_after=min_retry_after or 30,
        )

    async def _allocate_from_cache(self) -> Tuple[DonatedToken, KiroAuthManager]:
        """Allocate from local cached scores when lock acquisition fails."""
        if not self._cached_scores:
            # No cache, fall back to local allocation
            return await self._allocate_local()

        # Sort by cached score descending
        sorted_ids = sorted(self._cached_scores.keys(), key=lambda k: self._cached_scores[k], reverse=True)

        for token_id in sorted_ids:
            token = await user_db.get_token_by_id(token_id)
            if token and token.status == "active":
                manager = await self._get_manager(token)
                return token, manager

        return await self._allocate_local()

    async def _select_token_distributed(self, tokens: List[DonatedToken]) -> Optional[DonatedToken]:
        """Select best token from a list using Redis risk data (for user's private tokens)."""
        from kiro_gateway.redis_manager import redis_manager
        client = await redis_manager.get_client()

        best_token = None
        best_score = -1.0

        for token in tokens:
            risk_data = {}
            if client:
                try:
                    risk_data = await self._get_risk_data_from_redis(client, token.id)
                except Exception:
                    pass

                # Skip if rate-limited
                if self._is_token_limited(risk_data):
                    continue

            score = self.calculate_score(token, risk_data if risk_data else None)
            if score > best_score:
                best_score = score
                best_token = token

        return best_token

    async def _select_token_local(self, tokens: List[DonatedToken]) -> Optional[DonatedToken]:
        """Select best token from a list using local scoring (single-node)."""
        now = int(time.time())
        available = [
            t for t in tokens
            if not t.cooldown_until or t.cooldown_until <= now * 1000
        ]
        if not available:
            return None
        return max(available, key=lambda t: self.calculate_score(t))

    def _is_token_limited(self, risk_data: dict) -> bool:
        """Check if a token is rate-limited based on risk data."""
        if risk_data.get("rpm", 0) >= settings.token_rpm_limit:
            return True
        if risk_data.get("rph", 0) >= settings.token_rph_limit:
            return True
        if risk_data.get("concurrent", 0) >= settings.token_max_concurrent:
            return True
        if risk_data.get("in_cooldown", False):
            return True
        return False

    # ==================== Risk Data ====================

    async def get_risk_data(self, token_id: int) -> dict:
        """
        Get risk data for a token from Redis.

        Returns dict with keys: rpm, rph, concurrent, consecutive_uses, in_cooldown
        """
        if not settings.is_distributed:
            return {}

        from kiro_gateway.redis_manager import redis_manager
        client = await redis_manager.get_client()
        if not client:
            return {}

        try:
            return await self._get_risk_data_from_redis(client, token_id)
        except Exception as e:
            logger.debug(f"TokenAllocator: 获取 Token {token_id} 风控数据失败: {e}")
            return {}

    async def _get_risk_data_from_redis(self, client, token_id: int) -> dict:
        """Read risk counters from Redis for a token."""
        rpm_key = f"{_TOKEN_PREFIX}:{token_id}:rpm"
        rph_key = f"{_TOKEN_PREFIX}:{token_id}:rph"
        concurrent_key = f"{_TOKEN_PREFIX}:{token_id}:concurrent"
        consecutive_key = f"{_TOKEN_PREFIX}:{token_id}:consecutive"
        cooldown_key = f"{_TOKEN_PREFIX}:{token_id}:cooldown"

        pipe = client.pipeline()
        pipe.get(rpm_key)
        pipe.get(rph_key)
        pipe.get(concurrent_key)
        pipe.get(consecutive_key)
        pipe.exists(cooldown_key)
        results = await pipe.execute()

        return {
            "rpm": int(results[0]) if results[0] else 0,
            "rph": int(results[1]) if results[1] else 0,
            "concurrent": int(results[2]) if results[2] else 0,
            "consecutive_uses": int(results[3]) if results[3] else 0,
            "in_cooldown": bool(results[4]),
        }

    # ==================== Random Delay ====================

    @staticmethod
    async def apply_random_delay() -> float:
        """
        Apply random delay before forwarding request (0.5-3.0s).

        Returns the actual delay applied in seconds.
        """
        delay = random.uniform(0.5, 3.0)
        await asyncio.sleep(delay)
        return delay

    # ==================== Token Release & Usage Recording ====================

    async def release_token(self, token_id: int) -> None:
        """
        Release concurrent count for a token after request completes.

        Must be called in a finally block after each request.
        """
        if not settings.is_distributed:
            return

        from kiro_gateway.redis_manager import redis_manager
        client = await redis_manager.get_client()
        if not client:
            return

        try:
            concurrent_key = f"{_TOKEN_PREFIX}:{token_id}:concurrent"
            val = await client.decr(concurrent_key)
            # Prevent negative values
            if val is not None and int(val) < 0:
                await client.set(concurrent_key, 0)
        except Exception as e:
            logger.debug(f"TokenAllocator: 释放 Token {token_id} 并发计数失败: {e}")

    async def record_usage(self, token_id: int, success: bool) -> None:
        """
        Record Token usage result.

        In distributed mode, uses Redis HINCRBY for cross-node stats
        and applies cooldown policy on consecutive failures.
        """
        if settings.is_distributed:
            await self._record_usage_distributed(token_id, success)
        else:
            await self._record_usage_local(token_id, success)

    async def _record_usage_local(self, token_id: int, success: bool) -> None:
        """Record usage in single-node mode."""
        await user_db.record_token_usage(token_id, success)

        if not success:
            # Update consecutive fails in database
            token = await user_db.get_token_by_id(token_id)
            if token:
                new_fails = token.consecutive_fails + 1
                await self._apply_cooldown_policy(token_id, new_fails)
        else:
            # Reset consecutive fails on success
            try:
                await user_db.update_token_risk_fields(token_id, consecutive_fails=0, cooldown_until=0)
            except Exception:
                pass

    async def _record_usage_distributed(self, token_id: int, success: bool) -> None:
        """Record usage in distributed mode via Redis."""
        from kiro_gateway.redis_manager import redis_manager
        client = await redis_manager.get_client()

        # Always record to database
        await user_db.record_token_usage(token_id, success)

        if not client:
            return

        try:
            stats_key = f"{_TOKEN_PREFIX}:{token_id}:stats"
            if success:
                await client.hincrby(stats_key, "success", 1)
                # Reset consecutive fails
                await client.delete(f"{_TOKEN_PREFIX}:{token_id}:cooldown")
                # Update DB
                try:
                    await user_db.update_token_risk_fields(token_id, consecutive_fails=0, cooldown_until=0)
                except Exception:
                    pass
            else:
                await client.hincrby(stats_key, "fail", 1)
                # Increment consecutive fails and check cooldown
                token = await user_db.get_token_by_id(token_id)
                if token:
                    new_fails = token.consecutive_fails + 1
                    await self._apply_cooldown_policy(token_id, new_fails, client)
        except Exception as e:
            logger.debug(f"TokenAllocator: 记录 Token {token_id} 使用结果到 Redis 失败: {e}")

    async def _apply_cooldown_policy(
        self, token_id: int, consecutive_fails: int, client=None
    ) -> None:
        """Apply exponential backoff cooldown policy based on consecutive failures."""
        cooldown_seconds = None
        suspend = False

        # Find the matching cooldown tier (check from highest to lowest)
        for threshold in sorted(COOLDOWN_POLICY.keys(), reverse=True):
            if consecutive_fails >= threshold:
                policy_value = COOLDOWN_POLICY[threshold]
                if policy_value is None:
                    suspend = True
                else:
                    cooldown_seconds = policy_value
                break

        now = int(time.time())

        if suspend:
            # Suspend token - set status to 'invalid' or a suspended state
            logger.warning(f"Token {token_id}: 连续失败 {consecutive_fails} 次，已暂停")
            await user_db.update_token_risk_fields(
                token_id,
                consecutive_fails=consecutive_fails,
                cooldown_until=0,
            )
            await user_db.set_token_status(token_id, "invalid")
            # Notify token owner
            try:
                from kiro_gateway.notification_manager import notification_manager
                token = await user_db.get_token_by_id(token_id)
                if token and token.user_id:
                    await notification_manager.notify_token_suspended(token.user_id, token_id)
            except Exception as ne:
                logger.debug(f"Failed to notify user about token {token_id}: {ne}")
            if client:
                # Set a long cooldown in Redis
                cooldown_key = f"{_TOKEN_PREFIX}:{token_id}:cooldown"
                await client.set(cooldown_key, "suspended")
                # Remove from score set
                await client.zrem(_SCORES_KEY, str(token_id))
        elif cooldown_seconds:
            cooldown_until = (now + cooldown_seconds) * 1000  # milliseconds
            logger.info(
                f"Token {token_id}: 连续失败 {consecutive_fails} 次，"
                f"冷却 {cooldown_seconds // 60} 分钟"
            )
            await user_db.update_token_risk_fields(
                token_id,
                consecutive_fails=consecutive_fails,
                cooldown_until=cooldown_until,
            )
            if client:
                cooldown_key = f"{_TOKEN_PREFIX}:{token_id}:cooldown"
                await client.set(cooldown_key, str(cooldown_seconds), ex=cooldown_seconds)
        else:
            # Below cooldown threshold, just update the count
            await user_db.update_token_risk_fields(
                token_id,
                consecutive_fails=consecutive_fails,
            )

    # ==================== Manager Cache ====================

    async def _get_manager(self, token: DonatedToken) -> KiroAuthManager:
        """获取或创建 Token 对应的 AuthManager（线程安全）。"""
        async with self._lock:
            if token.id in self._token_managers:
                return self._token_managers[token.id]

            refresh_token = await user_db.get_decrypted_token(token.id)
            if not refresh_token:
                raise NoTokenAvailable(f"Failed to decrypt token {token.id}")

            manager = KiroAuthManager(
                refresh_token=refresh_token,
                region=settings.region,
                profile_arn=settings.profile_arn,
            )

            self._token_managers[token.id] = manager
            return manager

    def clear_manager(self, token_id: int) -> None:
        """清除缓存的 AuthManager。"""
        if token_id in self._token_managers:
            del self._token_managers[token_id]

    # ==================== Score Sync (Distributed) ====================

    async def _sync_scores_loop(self) -> None:
        """Background task: sync token scores to Redis every 30 seconds."""
        while True:
            try:
                await asyncio.sleep(30)
                await self._sync_scores_to_redis()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"TokenAllocator: 评分同步失败: {e}")

    async def _sync_scores_to_redis(self) -> None:
        """Sync all active token scores from database to Redis Sorted Set."""
        from kiro_gateway.redis_manager import redis_manager

        client = await redis_manager.get_client()
        if not client:
            return

        try:
            tokens = await user_db.get_all_active_tokens()
            if not tokens:
                return

            scores_map = {}
            for token in tokens:
                risk_data = await self._get_risk_data_from_redis(client, token.id)
                score = self.calculate_score(token, risk_data)
                scores_map[str(token.id)] = score
                # Update local cache
                self._cached_scores[token.id] = score

            if scores_map:
                # Use ZADD to update all scores atomically
                await client.zadd(_SCORES_KEY, scores_map)

            # Clean up tokens that are no longer active
            existing_members = await client.zrange(_SCORES_KEY, 0, -1)
            active_ids = {str(t.id) for t in tokens}
            stale = [m for m in existing_members if m not in active_ids]
            if stale:
                await client.zrem(_SCORES_KEY, *stale)

            logger.debug(f"TokenAllocator: 已同步 {len(scores_map)} 个 Token 评分到 Redis")
        except Exception as e:
            logger.warning(f"TokenAllocator: 评分同步到 Redis 失败: {e}")

    # ==================== Helpers ====================

    @staticmethod
    def _calc_retry_after(tokens: List[DonatedToken]) -> int:
        """Calculate the shortest time until a token becomes available."""
        now = int(time.time() * 1000)
        min_wait = None
        for t in tokens:
            if t.cooldown_until and t.cooldown_until > now:
                wait = (t.cooldown_until - now) / 1000  # to seconds
                if min_wait is None or wait < min_wait:
                    min_wait = wait
        return int(min_wait) if min_wait else 30


# Global allocator instance
token_allocator = SmartTokenAllocator()
