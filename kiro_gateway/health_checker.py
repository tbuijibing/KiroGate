# -*- coding: utf-8 -*-

"""
KiroGate Token 健康检查器。

后台任务，定期检查所有活跃 Token 的有效性。
分布式模式下使用 Redis 领导者选举，确保只有一个节点执行健康检查。
"""

import asyncio
import random
from typing import Optional

from loguru import logger

from kiro_gateway.config import settings
from kiro_gateway.database import user_db
from kiro_gateway.auth import KiroAuthManager


# Redis key for leader election
LEADER_LOCK_KEY = "kirogate:health_checker:leader"
LEADER_LOCK_TTL = 60  # seconds
LEADER_RENEW_INTERVAL = 30  # seconds
MIN_TOKEN_CHECK_GAP = 3  # minimum seconds between adjacent token checks
MAX_RANDOM_OFFSET = 30  # maximum random offset seconds per token check


class TokenHealthChecker:
    """Token 健康检查后台任务，支持分布式领导者选举。"""

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._check_interval = settings.token_health_check_interval
        self._is_leader = False
        self._node_id = settings.node_id

    async def start(self) -> None:
        """Start the health check background task."""
        if self._running:
            logger.warning("Token health checker is already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())

        mode = "distributed (leader election)" if settings.is_distributed else "single-node"
        logger.info(
            f"Token health checker started (interval: {self._check_interval}s, mode: {mode})"
        )

    async def stop(self) -> None:
        """Stop the health check background task."""
        self._running = False

        # Release leader lock if held
        if self._is_leader and settings.is_distributed:
            await self._release_leader()

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Token health checker stopped")

    async def _run_loop(self) -> None:
        """Main health check loop with leader election support."""
        while self._running:
            try:
                if settings.is_distributed:
                    await self._distributed_loop_tick()
                else:
                    # Single-node mode: run health checks directly
                    await asyncio.sleep(self._check_interval)
                    await self.check_all_tokens()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check loop error: {e}")
                await asyncio.sleep(60)

    async def _distributed_loop_tick(self) -> None:
        """One tick of the distributed mode loop: try leader election, then act."""
        was_leader = self._is_leader

        # Try to acquire or renew leader lock
        if self._is_leader:
            renewed = await self._renew_leader()
            if not renewed:
                self._is_leader = False
                logger.warning(
                    f"Node {self._node_id} lost leader lock for health checker"
                )
        else:
            acquired = await self._try_acquire_leader()
            if acquired:
                self._is_leader = True
                logger.info(
                    f"Node {self._node_id} acquired leader lock for health checker"
                )

        if self._is_leader:
            # Leader: run health checks
            await self.check_all_tokens()
            # Wait for the next cycle (renew interval)
            await asyncio.sleep(LEADER_RENEW_INTERVAL)
        else:
            # Standby: just wait and retry
            if was_leader:
                logger.info(
                    f"Node {self._node_id} entering standby mode for health checker"
                )
            await asyncio.sleep(LEADER_RENEW_INTERVAL)

    async def _try_acquire_leader(self) -> bool:
        """
        Try to acquire the leader lock using Redis SET NX EX.

        Returns:
            True if lock was acquired, False otherwise.
        """
        try:
            from kiro_gateway.redis_manager import redis_manager

            client = await redis_manager.get_client()
            if not client:
                logger.debug("Redis not available, cannot acquire leader lock")
                return False

            # SET key value NX EX ttl
            acquired = await client.set(
                LEADER_LOCK_KEY,
                self._node_id,
                nx=True,
                ex=LEADER_LOCK_TTL,
            )
            return bool(acquired)

        except Exception as e:
            logger.error(f"Failed to acquire leader lock: {e}")
            return False

    async def _renew_leader(self) -> bool:
        """
        Renew the leader lock if we still own it.

        Returns:
            True if lock was renewed, False if lost.
        """
        try:
            from kiro_gateway.redis_manager import redis_manager

            client = await redis_manager.get_client()
            if not client:
                logger.warning("Redis not available, cannot renew leader lock")
                return False

            # Check if we still own the lock
            current_owner = await client.get(LEADER_LOCK_KEY)
            if current_owner == self._node_id:
                await client.expire(LEADER_LOCK_KEY, LEADER_LOCK_TTL)
                logger.debug(
                    f"Node {self._node_id} renewed leader lock (TTL: {LEADER_LOCK_TTL}s)"
                )
                return True
            else:
                logger.warning(
                    f"Leader lock owned by {current_owner}, not {self._node_id}"
                )
                return False

        except Exception as e:
            logger.error(f"Failed to renew leader lock: {e}")
            return False

    async def _release_leader(self) -> None:
        """Release the leader lock if we own it (used during shutdown)."""
        try:
            from kiro_gateway.redis_manager import redis_manager

            client = await redis_manager.get_client()
            if not client:
                return

            # Only delete if we own the lock
            current_owner = await client.get(LEADER_LOCK_KEY)
            if current_owner == self._node_id:
                await client.delete(LEADER_LOCK_KEY)
                logger.info(
                    f"Node {self._node_id} released leader lock for health checker"
                )
            self._is_leader = False

        except Exception as e:
            logger.error(f"Failed to release leader lock: {e}")
            self._is_leader = False

    async def check_all_tokens(self) -> dict:
        """
        Check all active tokens.

        In distributed mode, adds random offsets and minimum gaps between checks.

        Returns:
            Summary of check results
        """
        tokens = await user_db.get_all_active_tokens()
        if not tokens:
            logger.debug("No active tokens to check")
            return {"checked": 0, "valid": 0, "invalid": 0}

        logger.info(f"Starting health check for {len(tokens)} tokens")

        valid_count = 0
        invalid_count = 0

        for i, token in enumerate(tokens):
            try:
                # In distributed mode, add random offset per token
                if settings.is_distributed:
                    offset = random.uniform(0, MAX_RANDOM_OFFSET)
                    await asyncio.sleep(offset)

                is_valid = await self.check_token(token.id)
                if is_valid:
                    valid_count += 1
                else:
                    invalid_count += 1
                    await user_db.set_token_status(token.id, "invalid")
                    logger.warning(f"Token {token.id} marked as invalid")
                    # Notify token owner
                    try:
                        from kiro_gateway.notification_manager import notification_manager
                        await notification_manager.notify_token_invalid(token.user_id, token.id)
                    except Exception as ne:
                        logger.debug(f"Failed to notify user about token {token.id}: {ne}")
            except Exception as e:
                logger.error(f"Failed to check token {token.id}: {e}")
                invalid_count += 1

            # Ensure minimum gap between adjacent token checks
            if settings.is_distributed:
                await asyncio.sleep(MIN_TOKEN_CHECK_GAP)
            else:
                # Single-node: small delay to avoid rate limiting
                await asyncio.sleep(1)

        logger.info(f"Health check complete: {valid_count} valid, {invalid_count} invalid")
        return {
            "checked": len(tokens),
            "valid": valid_count,
            "invalid": invalid_count,
        }

    async def check_token(self, token_id: int) -> bool:
        """
        Check a single token's validity.

        Args:
            token_id: Token ID to check

        Returns:
            True if token is valid, False otherwise
        """
        # Get decrypted token
        refresh_token = await user_db.get_decrypted_token(token_id)
        if not refresh_token:
            await user_db.record_health_check(token_id, False, "Failed to decrypt token")
            return False

        # Try to get access token
        try:
            manager = KiroAuthManager(
                refresh_token=refresh_token,
                region=settings.region,
                profile_arn=settings.profile_arn,
            )
            access_token = await manager.get_access_token()

            if access_token:
                await user_db.record_health_check(token_id, True)
                return True
            else:
                await user_db.record_health_check(token_id, False, "No access token returned")
                return False

        except Exception as e:
            error_msg = str(e)[:200]
            await user_db.record_health_check(token_id, False, error_msg)
            return False


# Global health checker instance
health_checker = TokenHealthChecker()
