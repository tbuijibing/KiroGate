# -*- coding: utf-8 -*-

"""
GeekGate 用户配额管理模块?

管理用户每日/每月请求配额?API Key RPM 限制?
分布式模式使?Redis 原子计数器，单节点模式使用数据库�?
"""

import calendar
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

from loguru import logger

from geek_gateway.config import settings


class QuotaManager:
    """
    用户配额管理�?

    分布式模式：使用 Redis INCR + TTL 跟踪每日/每月请求数和 API Key RPM?
    单节点模式：使用 user_quotas 表跟�?
    """

    # Redis key prefixes
    _USER_DAILY_KEY = "GeekGate:user:{user_id}:daily_count"
    _USER_MONTHLY_KEY = "GeekGate:user:{user_id}:monthly_count"
    _APIKEY_RPM_KEY = "GeekGate:apikey:{api_key_id}:rpm"

    async def check_user_quota(self, user_id: int) -> Tuple[bool, Optional[dict]]:
        """
        Check if user has remaining daily/monthly quota.

        Args:
            user_id: User ID

        Returns:
            (allowed, quota_info) ?allowed=True if within quota,
            quota_info contains reset times when denied.
        """
        if settings.is_distributed:
            return await self._check_user_quota_redis(user_id)
        return await self._check_user_quota_db(user_id)

    async def check_api_key_rpm(self, api_key_id: int) -> Tuple[bool, Optional[int]]:
        """
        Check if API key is within RPM limit.

        Args:
            api_key_id: API Key ID

        Returns:
            (allowed, retry_after) ?allowed=True if within limit,
            retry_after is seconds until counter resets.
        """
        if settings.is_distributed:
            return await self._check_api_key_rpm_redis(api_key_id)
        # Single-node: no RPM tracking in DB, always allow
        # (RPM is only meaningful in distributed mode with Redis TTL counters)
        return True, None

    async def increment_user_usage(self, user_id: int) -> None:
        """
        Increment daily and monthly usage counters for a user.

        Args:
            user_id: User ID
        """
        if settings.is_distributed:
            await self._increment_user_usage_redis(user_id)
        else:
            await self._increment_user_usage_db(user_id)
        await self._check_quota_warning(user_id)

    async def get_user_quota_info(self, user_id: int) -> dict:
        """
        Get current quota usage info for user panel display.

        Args:
            user_id: User ID

        Returns:
            Dict with daily_used, daily_quota, monthly_used, monthly_quota,
            daily_reset_at, monthly_reset_at.
        """
        if settings.is_distributed:
            return await self._get_user_quota_info_redis(user_id)
        return await self._get_user_quota_info_db(user_id)

    # ==================== Redis Implementation ====================

    async def _get_redis_client(self):
        """Get Redis client, returns None if unavailable."""
        from geek_gateway.redis_manager import redis_manager
        return await redis_manager.get_client()

    def _seconds_until_midnight_utc(self) -> int:
        """Calculate seconds until midnight UTC."""
        now = datetime.now(timezone.utc)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        # Move to next day
        import datetime as dt_module
        next_midnight = midnight + dt_module.timedelta(days=1)
        return max(1, int((next_midnight - now).total_seconds()))

    def _seconds_until_end_of_month_utc(self) -> int:
        """Calculate seconds until end of current month UTC."""
        now = datetime.now(timezone.utc)
        _, last_day = calendar.monthrange(now.year, now.month)
        end_of_month = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=0)
        remaining = int((end_of_month - now).total_seconds()) + 1
        return max(1, remaining)

    def _daily_reset_timestamp(self) -> int:
        """Get the timestamp (ms) when daily quota resets (next midnight UTC)."""
        now = datetime.now(timezone.utc)
        import datetime as dt_module
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + dt_module.timedelta(days=1)
        return int(midnight.timestamp() * 1000)

    def _monthly_reset_timestamp(self) -> int:
        """Get the timestamp (ms) when monthly quota resets (end of month UTC)."""
        now = datetime.now(timezone.utc)
        _, last_day = calendar.monthrange(now.year, now.month)
        end_of_month = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=0)
        return int(end_of_month.timestamp() * 1000) + 1000

    async def _get_user_quotas(self, user_id: int) -> Tuple[int, int]:
        """Get user's daily and monthly quota limits from DB."""
        from geek_gateway.database import user_db
        row = await user_db._backend.fetch_one(
            "SELECT daily_quota, monthly_quota FROM user_quotas WHERE user_id = ?",
            (user_id,),
        )
        if row:
            return row["daily_quota"], row["monthly_quota"]
        return settings.default_user_daily_quota, settings.default_user_monthly_quota

    async def _check_user_quota_redis(self, user_id: int) -> Tuple[bool, Optional[dict]]:
        """Check user quota using Redis counters."""
        client = await self._get_redis_client()
        if not client:
            # Redis unavailable ?allow request (graceful degradation)
            logger.debug(f"Redis unavailable for quota check, allowing user {user_id}")
            return True, None

        daily_key = self._USER_DAILY_KEY.format(user_id=user_id)
        monthly_key = self._USER_MONTHLY_KEY.format(user_id=user_id)

        try:
            daily_quota, monthly_quota = await self._get_user_quotas(user_id)

            pipe = client.pipeline()
            pipe.get(daily_key)
            pipe.get(monthly_key)
            daily_count_raw, monthly_count_raw = await pipe.execute()

            daily_count = int(daily_count_raw) if daily_count_raw else 0
            monthly_count = int(monthly_count_raw) if monthly_count_raw else 0

            if daily_count >= daily_quota:
                return False, {
                    "reason": "daily_quota_exceeded",
                    "daily_used": daily_count,
                    "daily_quota": daily_quota,
                    "retry_after": self._seconds_until_midnight_utc(),
                    "reset_at": self._daily_reset_timestamp(),
                }

            if monthly_count >= monthly_quota:
                return False, {
                    "reason": "monthly_quota_exceeded",
                    "monthly_used": monthly_count,
                    "monthly_quota": monthly_quota,
                    "retry_after": self._seconds_until_end_of_month_utc(),
                    "reset_at": self._monthly_reset_timestamp(),
                }

            return True, None

        except Exception as e:
            logger.warning(f"Redis quota check failed for user {user_id}: {e}")
            return True, None

    async def _check_api_key_rpm_redis(self, api_key_id: int) -> Tuple[bool, Optional[int]]:
        """Check API key RPM using Redis counter."""
        client = await self._get_redis_client()
        if not client:
            return True, None

        rpm_key = self._APIKEY_RPM_KEY.format(api_key_id=api_key_id)

        try:
            current = await client.get(rpm_key)
            current_count = int(current) if current else 0

            if current_count >= settings.default_key_rpm_limit:
                ttl = await client.ttl(rpm_key)
                retry_after = max(1, ttl) if ttl > 0 else 60
                return False, retry_after

            return True, None

        except Exception as e:
            logger.warning(f"Redis RPM check failed for API key {api_key_id}: {e}")
            return True, None

    async def _increment_user_usage_redis(self, user_id: int) -> None:
        """Increment user usage counters in Redis."""
        client = await self._get_redis_client()
        if not client:
            return

        daily_key = self._USER_DAILY_KEY.format(user_id=user_id)
        monthly_key = self._USER_MONTHLY_KEY.format(user_id=user_id)

        try:
            pipe = client.pipeline()
            pipe.incr(daily_key)
            pipe.incr(monthly_key)
            results = await pipe.execute()

            # Set TTL only on first increment (when count becomes 1)
            if results[0] == 1:
                await client.expire(daily_key, self._seconds_until_midnight_utc())
            if results[1] == 1:
                await client.expire(monthly_key, self._seconds_until_end_of_month_utc())

        except Exception as e:
            logger.warning(f"Redis usage increment failed for user {user_id}: {e}")

    async def increment_api_key_rpm(self, api_key_id: int) -> None:
        """Increment API key RPM counter in Redis."""
        if not settings.is_distributed:
            return

        client = await self._get_redis_client()
        if not client:
            return

        rpm_key = self._APIKEY_RPM_KEY.format(api_key_id=api_key_id)

        try:
            count = await client.incr(rpm_key)
            if count == 1:
                await client.expire(rpm_key, 60)
        except Exception as e:
            logger.warning(f"Redis RPM increment failed for API key {api_key_id}: {e}")

    async def _get_user_quota_info_redis(self, user_id: int) -> dict:
        """Get quota info from Redis for user panel."""
        daily_quota, monthly_quota = await self._get_user_quotas(user_id)

        client = await self._get_redis_client()
        if not client:
            return {
                "daily_used": 0,
                "daily_quota": daily_quota,
                "monthly_used": 0,
                "monthly_quota": monthly_quota,
                "daily_reset_at": self._daily_reset_timestamp(),
                "monthly_reset_at": self._monthly_reset_timestamp(),
            }

        daily_key = self._USER_DAILY_KEY.format(user_id=user_id)
        monthly_key = self._USER_MONTHLY_KEY.format(user_id=user_id)

        try:
            pipe = client.pipeline()
            pipe.get(daily_key)
            pipe.get(monthly_key)
            daily_raw, monthly_raw = await pipe.execute()

            return {
                "daily_used": int(daily_raw) if daily_raw else 0,
                "daily_quota": daily_quota,
                "monthly_used": int(monthly_raw) if monthly_raw else 0,
                "monthly_quota": monthly_quota,
                "daily_reset_at": self._daily_reset_timestamp(),
                "monthly_reset_at": self._monthly_reset_timestamp(),
            }
        except Exception as e:
            logger.warning(f"Redis quota info fetch failed for user {user_id}: {e}")
            return {
                "daily_used": 0,
                "daily_quota": daily_quota,
                "monthly_used": 0,
                "monthly_quota": monthly_quota,
                "daily_reset_at": self._daily_reset_timestamp(),
                "monthly_reset_at": self._monthly_reset_timestamp(),
            }

    # ==================== Database (Single-Node) Implementation ====================

    async def _ensure_user_quota_row(self, user_id: int) -> None:
        """Ensure a user_quotas row exists, creating with defaults if needed."""
        from geek_gateway.database import user_db
        row = await user_db._backend.fetch_one(
            "SELECT user_id FROM user_quotas WHERE user_id = ?", (user_id,)
        )
        if not row:
            now_ts = int(time.time())
            daily_reset = self._daily_reset_timestamp()
            monthly_reset = self._monthly_reset_timestamp()
            try:
                await user_db._backend.execute(
                    """INSERT INTO user_quotas
                       (user_id, daily_quota, monthly_quota, daily_used, monthly_used,
                        daily_reset_at, monthly_reset_at)
                       VALUES (?, ?, ?, 0, 0, ?, ?)""",
                    (user_id, settings.default_user_daily_quota,
                     settings.default_user_monthly_quota, daily_reset, monthly_reset),
                )
            except Exception:
                pass  # Race condition ?row may already exist

    async def _reset_expired_quotas_db(self, user_id: int) -> None:
        """Reset expired daily/monthly counters in the database."""
        from geek_gateway.database import user_db
        now_ms = int(time.time() * 1000)

        row = await user_db._backend.fetch_one(
            "SELECT daily_reset_at, monthly_reset_at FROM user_quotas WHERE user_id = ?",
            (user_id,),
        )
        if not row:
            return

        updates = []
        params = []

        if now_ms >= row["daily_reset_at"]:
            updates.append("daily_used = 0")
            updates.append("daily_reset_at = ?")
            params.append(self._daily_reset_timestamp())

        if now_ms >= row["monthly_reset_at"]:
            updates.append("monthly_used = 0")
            updates.append("monthly_reset_at = ?")
            params.append(self._monthly_reset_timestamp())

        if updates:
            params.append(user_id)
            await user_db._backend.execute(
                f"UPDATE user_quotas SET {', '.join(updates)} WHERE user_id = ?",
                tuple(params),
            )

    async def _check_user_quota_db(self, user_id: int) -> Tuple[bool, Optional[dict]]:
        """Check user quota using database table."""
        from geek_gateway.database import user_db

        await self._ensure_user_quota_row(user_id)
        await self._reset_expired_quotas_db(user_id)

        row = await user_db._backend.fetch_one(
            """SELECT daily_quota, monthly_quota, daily_used, monthly_used,
                      daily_reset_at, monthly_reset_at
               FROM user_quotas WHERE user_id = ?""",
            (user_id,),
        )
        if not row:
            return True, None

        if row["daily_used"] >= row["daily_quota"]:
            return False, {
                "reason": "daily_quota_exceeded",
                "daily_used": row["daily_used"],
                "daily_quota": row["daily_quota"],
                "retry_after": self._seconds_until_midnight_utc(),
                "reset_at": row["daily_reset_at"],
            }

        if row["monthly_used"] >= row["monthly_quota"]:
            return False, {
                "reason": "monthly_quota_exceeded",
                "monthly_used": row["monthly_used"],
                "monthly_quota": row["monthly_quota"],
                "retry_after": self._seconds_until_end_of_month_utc(),
                "reset_at": row["monthly_reset_at"],
            }

        return True, None

    async def _increment_user_usage_db(self, user_id: int) -> None:
        """Increment user usage counters in database."""
        from geek_gateway.database import user_db

        await self._ensure_user_quota_row(user_id)
        await self._reset_expired_quotas_db(user_id)

        await user_db._backend.execute(
            "UPDATE user_quotas SET daily_used = daily_used + 1, monthly_used = monthly_used + 1 WHERE user_id = ?",
            (user_id,),
        )

    async def _get_user_quota_info_db(self, user_id: int) -> dict:
        """Get quota info from database for user panel."""
        from geek_gateway.database import user_db

        await self._ensure_user_quota_row(user_id)
        await self._reset_expired_quotas_db(user_id)

        row = await user_db._backend.fetch_one(
            """SELECT daily_quota, monthly_quota, daily_used, monthly_used,
                      daily_reset_at, monthly_reset_at
               FROM user_quotas WHERE user_id = ?""",
            (user_id,),
        )
        if not row:
            return {
                "daily_used": 0,
                "daily_quota": settings.default_user_daily_quota,
                "monthly_used": 0,
                "monthly_quota": settings.default_user_monthly_quota,
                "daily_reset_at": self._daily_reset_timestamp(),
                "monthly_reset_at": self._monthly_reset_timestamp(),
            }

        return {
            "daily_used": row["daily_used"],
            "daily_quota": row["daily_quota"],
            "monthly_used": row["monthly_used"],
            "monthly_quota": row["monthly_quota"],
            "daily_reset_at": row["daily_reset_at"],
            "monthly_reset_at": row["monthly_reset_at"],
        }

    async def _check_quota_warning(self, user_id: int) -> None:
        """Check if quota usage reached 80% and send warning notification."""
        try:
            info = await self.get_user_quota_info(user_id)
            from geek_gateway.notification_manager import notification_manager

            daily_pct = info["daily_used"] / max(1, info["daily_quota"]) * 100
            if daily_pct >= 80 and (info["daily_used"] - 1) / max(1, info["daily_quota"]) * 100 < 80:
                await notification_manager.notify_quota_warning(user_id, "daily", daily_pct)

            monthly_pct = info["monthly_used"] / max(1, info["monthly_quota"]) * 100
            if monthly_pct >= 80 and (info["monthly_used"] - 1) / max(1, info["monthly_quota"]) * 100 < 80:
                await notification_manager.notify_quota_warning(user_id, "monthly", monthly_pct)
        except Exception as e:
            logger.debug(f"Quota warning check failed for user {user_id}: {e}")


# Global quota manager singleton
quota_manager = QuotaManager()
