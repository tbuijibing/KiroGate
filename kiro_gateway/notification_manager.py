# -*- coding: utf-8 -*-
"""KiroGate 用户通知管理模块。"""

import time

from loguru import logger


class NotificationManager:
    """用户通知管理器。"""

    async def create_notification(self, user_id: int, ntype: str, message: str) -> None:
        """Create a notification record in user_notifications table."""
        from kiro_gateway.database import user_db

        try:
            await user_db._backend.execute(
                """INSERT INTO user_notifications (user_id, type, message, is_read, created_at)
                   VALUES (?, ?, ?, 0, ?)""",
                (user_id, ntype, message, int(time.time() * 1000)),
            )
        except Exception as e:
            logger.warning(f"Failed to create notification for user {user_id}: {e}")

    async def notify_token_suspended(self, user_id: int, token_id: int) -> None:
        """Notify user that their token has been suspended."""
        await self.create_notification(
            user_id,
            "token_suspended",
            f"您的 Token #{token_id} 已被暂停使用，请检查 Token 状态。",
        )

    async def notify_token_invalid(self, user_id: int, token_id: int) -> None:
        """Notify user that their token is invalid."""
        await self.create_notification(
            user_id,
            "token_invalid",
            f"您的 Token #{token_id} 已失效，请更新或移除该 Token。",
        )

    async def notify_quota_warning(self, user_id: int, quota_type: str, usage_pct: float) -> None:
        """Notify user that quota usage reached 80%."""
        label = "每日" if quota_type == "daily" else "每月"
        await self.create_notification(
            user_id,
            "quota_warning",
            f"您的{label}配额已使用 {usage_pct:.0f}%，请注意控制使用量。",
        )

    async def mark_read(self, user_id: int, notification_id: int) -> None:
        """Mark a notification as read."""
        from kiro_gateway.database import user_db

        await user_db._backend.execute(
            "UPDATE user_notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
            (notification_id, user_id),
        )

    async def mark_all_read(self, user_id: int) -> None:
        """Mark all notifications as read for a user."""
        from kiro_gateway.database import user_db

        await user_db._backend.execute(
            "UPDATE user_notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0",
            (user_id,),
        )

    async def get_unread_count(self, user_id: int) -> int:
        """Get count of unread notifications."""
        from kiro_gateway.database import user_db

        row = await user_db._backend.fetch_one(
            "SELECT COUNT(*) as cnt FROM user_notifications WHERE user_id = ? AND is_read = 0",
            (user_id,),
        )
        return row["cnt"] if row else 0


notification_manager = NotificationManager()
