# -*- coding: utf-8 -*-

"""
GeekGate 用户系统数据库模�?

管理用户、Token、API Key 等数据的存储?
支持 SQLite（单节点）和 PostgreSQL（分布式）双后端?
"""

import hashlib
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from cryptography.fernet import Fernet
from loguru import logger

from geek_gateway.config import settings

# Database file path (kept for backward compatibility)
USER_DB_FILE = os.getenv("USER_DB_FILE", "data/users.db")


def _derive_key(secret: str) -> bytes:
    """Derive a Fernet-compatible key from secret string."""
    return hashlib.sha256(secret.encode()).digest()[:32]


def _get_fernet() -> Fernet:
    """Get Fernet instance for token encryption."""
    import base64
    key = _derive_key(settings.token_encrypt_key)
    return Fernet(base64.urlsafe_b64encode(key))


@dataclass
class User:
    """User data model."""
    id: int
    linuxdo_id: Optional[str]
    github_id: Optional[str]
    email: Optional[str]
    username: str
    avatar_url: Optional[str]
    trust_level: int
    is_admin: bool
    is_banned: bool
    approval_status: str
    password_hash: Optional[str]
    session_version: int
    created_at: int
    last_login: Optional[int]


@dataclass
class DonatedToken:
    """Donated token data model."""
    id: int
    user_id: int
    token_hash: str
    auth_type: str  # 'social' or 'idc'
    visibility: str  # 'public' or 'private'
    status: str  # 'active', 'invalid', 'expired'
    success_count: int
    fail_count: int
    last_used: Optional[int]
    last_check: Optional[int]
    created_at: int
    # 账号信息缓存
    account_email: Optional[str] = None
    account_status: Optional[str] = None
    account_usage: Optional[float] = None
    account_limit: Optional[float] = None
    account_checked_at: Optional[int] = None
    # 防风控扩展字?
    consecutive_fails: int = 0
    cooldown_until: int = 0
    consecutive_uses: int = 0
    risk_score: float = 0.0

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.fail_count
        return self.success_count / total if total > 0 else 1.0


@dataclass
class APIKey:
    """API Key data model."""
    id: int
    user_id: int
    key_prefix: str
    name: Optional[str]
    is_active: bool
    request_count: int
    last_used: Optional[int]
    created_at: int


@dataclass
class ImportKey:
    """Admin-generated import key data model."""
    id: int
    user_id: int
    key_prefix: str
    name: Optional[str]
    is_active: bool
    request_count: int
    last_used: Optional[int]
    created_at: int


class UserDatabase:
    """User system database manager using async backend."""

    def __init__(self):
        self._backend = None
        self._fernet = _get_fernet()

    async def initialize(self) -> None:
        """Initialize the database backend and create tables."""
        from geek_gateway.db_backend import create_backend
        self._backend = create_backend()
        await self._backend.initialize()
        await self._init_db()
        logger.info("UserDatabase initialized")

    async def close(self) -> None:
        """Close the database backend."""
        if self._backend:
            await self._backend.close()
            self._backend = None

    async def _init_db(self) -> None:
        """Initialize database schema."""
        await self._backend.executescript('''
            -- Users table
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                linuxdo_id TEXT,
                github_id TEXT,
                email TEXT,
                username TEXT NOT NULL,
                avatar_url TEXT,
                trust_level INTEGER DEFAULT 0,
                is_admin INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                approval_status TEXT DEFAULT 'approved',
                password_hash TEXT,
                session_version INTEGER DEFAULT 1,
                created_at INTEGER NOT NULL,
                last_login INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_users_linuxdo ON users(linuxdo_id);
            CREATE INDEX IF NOT EXISTS idx_users_github ON users(github_id);

            -- Donated tokens table
            CREATE TABLE IF NOT EXISTS tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                refresh_token_encrypted TEXT NOT NULL,
                token_hash TEXT UNIQUE NOT NULL,
                visibility TEXT DEFAULT 'private',
                is_anonymous INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                success_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                last_used INTEGER,
                last_check INTEGER,
                created_at INTEGER NOT NULL,
                consecutive_fails INTEGER DEFAULT 0,
                cooldown_until INTEGER DEFAULT 0,
                consecutive_uses INTEGER DEFAULT 0,
                risk_score REAL DEFAULT 0.0,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id);
            CREATE INDEX IF NOT EXISTS idx_tokens_visibility ON tokens(visibility, status);
            CREATE INDEX IF NOT EXISTS idx_tokens_hash ON tokens(token_hash);

            -- API Keys table
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                key_hash TEXT UNIQUE NOT NULL,
                key_prefix TEXT NOT NULL,
                name TEXT,
                is_active INTEGER DEFAULT 1,
                request_count INTEGER DEFAULT 0,
                last_used INTEGER,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_apikeys_user ON api_keys(user_id);
            CREATE INDEX IF NOT EXISTS idx_apikeys_hash ON api_keys(key_hash);

            -- Import Keys table (admin-generated)
            CREATE TABLE IF NOT EXISTS import_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                key_hash TEXT UNIQUE NOT NULL,
                key_prefix TEXT NOT NULL,
                name TEXT,
                is_active INTEGER DEFAULT 1,
                request_count INTEGER DEFAULT 0,
                last_used INTEGER,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_import_keys_user ON import_keys(user_id);
            CREATE INDEX IF NOT EXISTS idx_import_keys_hash ON import_keys(key_hash);

            -- Token health check logs
            CREATE TABLE IF NOT EXISTS token_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id INTEGER NOT NULL,
                check_time INTEGER NOT NULL,
                is_valid INTEGER NOT NULL,
                error_msg TEXT,
                FOREIGN KEY (token_id) REFERENCES tokens(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_health_token ON token_health(token_id);

            -- Site announcements
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                allow_guest INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_announcements_active ON announcements(is_active, updated_at);

            -- Announcement status per user
            CREATE TABLE IF NOT EXISTS announcement_status (
                user_id INTEGER NOT NULL,
                announcement_id INTEGER NOT NULL,
                is_read INTEGER DEFAULT 0,
                is_dismissed INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, announcement_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (announcement_id) REFERENCES announcements(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_announcement_status_user ON announcement_status(user_id);
            CREATE INDEX IF NOT EXISTS idx_announcement_status_announcement ON announcement_status(announcement_id);

            -- User quotas table
            CREATE TABLE IF NOT EXISTS user_quotas (
                user_id INTEGER PRIMARY KEY,
                daily_quota INTEGER NOT NULL DEFAULT 500,
                monthly_quota INTEGER NOT NULL DEFAULT 10000,
                daily_used INTEGER NOT NULL DEFAULT 0,
                monthly_used INTEGER NOT NULL DEFAULT 0,
                daily_reset_at INTEGER NOT NULL DEFAULT 0,
                monthly_reset_at INTEGER NOT NULL DEFAULT 0
            );

            -- Audit logs table
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_username TEXT NOT NULL,
                action_type TEXT NOT NULL,
                target_type TEXT,
                target_id TEXT,
                details TEXT,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action_type, created_at);

            -- Activity logs table
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                api_key_id INTEGER,
                model TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                latency_ms INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_logs(user_id, created_at);

            -- User notifications table
            CREATE TABLE IF NOT EXISTS user_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                message TEXT NOT NULL,
                is_read INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_notifications_user ON user_notifications(user_id, is_read)
        ''')

        # Add columns that may not exist in older schemas (migration support)
        await self._ensure_columns()

    async def _ensure_columns(self) -> None:
        """Add columns that may be missing from older database schemas."""
        # For SQLite, use PRAGMA to check columns; for PostgreSQL, use information_schema
        # We use a try/except approach that works for both backends
        alter_statements = [
            ("tokens", "is_anonymous", "ALTER TABLE tokens ADD COLUMN is_anonymous INTEGER DEFAULT 0"),
            ("tokens", "account_email", "ALTER TABLE tokens ADD COLUMN account_email TEXT"),
            ("tokens", "account_status", "ALTER TABLE tokens ADD COLUMN account_status TEXT"),
            ("tokens", "account_usage", "ALTER TABLE tokens ADD COLUMN account_usage REAL"),
            ("tokens", "account_limit", "ALTER TABLE tokens ADD COLUMN account_limit REAL"),
            ("tokens", "account_checked_at", "ALTER TABLE tokens ADD COLUMN account_checked_at INTEGER"),
            ("tokens", "auth_type", "ALTER TABLE tokens ADD COLUMN auth_type TEXT DEFAULT 'social'"),
            ("tokens", "client_id_encrypted", "ALTER TABLE tokens ADD COLUMN client_id_encrypted TEXT"),
            ("tokens", "client_secret_encrypted", "ALTER TABLE tokens ADD COLUMN client_secret_encrypted TEXT"),
            ("tokens", "consecutive_fails", "ALTER TABLE tokens ADD COLUMN consecutive_fails INTEGER DEFAULT 0"),
            ("tokens", "cooldown_until", "ALTER TABLE tokens ADD COLUMN cooldown_until INTEGER DEFAULT 0"),
            ("tokens", "consecutive_uses", "ALTER TABLE tokens ADD COLUMN consecutive_uses INTEGER DEFAULT 0"),
            ("tokens", "risk_score", "ALTER TABLE tokens ADD COLUMN risk_score REAL DEFAULT 0.0"),
            ("users", "email", "ALTER TABLE users ADD COLUMN email TEXT"),
            ("users", "approval_status", "ALTER TABLE users ADD COLUMN approval_status TEXT DEFAULT 'approved'"),
            ("users", "password_hash", "ALTER TABLE users ADD COLUMN password_hash TEXT"),
            ("users", "session_version", "ALTER TABLE users ADD COLUMN session_version INTEGER DEFAULT 1"),
            ("announcements", "allow_guest", "ALTER TABLE announcements ADD COLUMN allow_guest INTEGER DEFAULT 0"),
        ]
        for _table, _col, stmt in alter_statements:
            try:
                await self._backend.execute(stmt)
            except Exception:
                pass  # Column already exists

        # Unique index on users.email
        try:
            await self._backend.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        except Exception:
            pass


    # ==================== Row Conversion Helpers (synchronous) ====================

    def _row_to_user(self, row: Dict[str, Any]) -> User:
        """Convert database row dict to User object."""
        session_version = row.get("session_version") or 1
        return User(
            id=row["id"],
            linuxdo_id=row.get("linuxdo_id"),
            github_id=row.get("github_id"),
            email=row.get("email"),
            username=row["username"],
            avatar_url=row.get("avatar_url"),
            trust_level=row.get("trust_level", 0),
            is_admin=bool(row.get("is_admin", 0)),
            is_banned=bool(row.get("is_banned", 0)),
            approval_status=row.get("approval_status") or "approved",
            password_hash=row.get("password_hash"),
            session_version=session_version,
            created_at=row["created_at"],
            last_login=row.get("last_login"),
        )

    def _row_to_token(self, row: Dict[str, Any]) -> DonatedToken:
        """Convert database row dict to DonatedToken object."""
        return DonatedToken(
            id=row["id"],
            user_id=row["user_id"],
            token_hash=row["token_hash"],
            auth_type=row.get("auth_type") or "social",
            visibility=row["visibility"],
            status=row["status"],
            success_count=row["success_count"],
            fail_count=row["fail_count"],
            last_used=row.get("last_used"),
            last_check=row.get("last_check"),
            created_at=row["created_at"],
            account_email=row.get("account_email"),
            account_status=row.get("account_status"),
            account_usage=row.get("account_usage"),
            account_limit=row.get("account_limit"),
            account_checked_at=row.get("account_checked_at"),
            consecutive_fails=row.get("consecutive_fails", 0) or 0,
            cooldown_until=row.get("cooldown_until", 0) or 0,
            consecutive_uses=row.get("consecutive_uses", 0) or 0,
            risk_score=row.get("risk_score", 0.0) or 0.0,
        )

    def _row_to_apikey(self, row: Dict[str, Any]) -> APIKey:
        """Convert database row dict to APIKey object."""
        return APIKey(
            id=row["id"],
            user_id=row["user_id"],
            key_prefix=row["key_prefix"],
            name=row.get("name"),
            is_active=bool(row.get("is_active", 1)),
            request_count=row.get("request_count", 0),
            last_used=row.get("last_used"),
            created_at=row["created_at"],
        )

    def _row_to_import_key(self, row: Dict[str, Any]) -> ImportKey:
        """Convert database row dict to ImportKey object."""
        return ImportKey(
            id=row["id"],
            user_id=row["user_id"],
            key_prefix=row["key_prefix"],
            name=row.get("name"),
            is_active=bool(row.get("is_active", 1)),
            request_count=row.get("request_count", 0),
            last_used=row.get("last_used"),
            created_at=row["created_at"],
        )

    def _row_to_announcement(self, row: Dict[str, Any]) -> Dict:
        """Convert database row dict to announcement dict."""
        return {
            "id": row["id"],
            "content": row["content"],
            "is_active": bool(row.get("is_active", 0)),
            "allow_guest": bool(row.get("allow_guest", 0)),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # ==================== Encryption Helpers (synchronous) ====================

    def _hash_token(self, token: str) -> str:
        """Hash token for storage and lookup."""
        return hashlib.sha256(token.encode()).hexdigest()

    def _encrypt_token(self, token: str) -> str:
        """Encrypt token for storage."""
        return self._fernet.encrypt(token.encode()).decode()

    def _decrypt_token(self, encrypted: str) -> str:
        """Decrypt token from storage."""
        return self._fernet.decrypt(encrypted.encode()).decode()


    # ==================== User Methods ====================

    async def create_user(
        self,
        username: str,
        linuxdo_id: Optional[str] = None,
        github_id: Optional[str] = None,
        email: Optional[str] = None,
        avatar_url: Optional[str] = None,
        trust_level: int = 0,
        approval_status: str = "approved",
        password_hash: Optional[str] = None,
    ) -> User:
        """Create a new user."""
        if not linuxdo_id and not github_id and not email:
            raise ValueError("必须提供 linuxdo_id、github_id ?email")

        now = int(time.time() * 1000)
        user_id = await self._backend.execute(
            """INSERT INTO users
               (linuxdo_id, github_id, email, username, avatar_url, trust_level,
                approval_status, password_hash, session_version, created_at, last_login)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (linuxdo_id, github_id, email, username, avatar_url, trust_level,
             approval_status, password_hash, 1, now, now),
        )
        return User(
            id=user_id,
            linuxdo_id=linuxdo_id,
            github_id=github_id,
            email=email,
            username=username,
            avatar_url=avatar_url,
            trust_level=trust_level,
            is_admin=False,
            is_banned=False,
            approval_status=approval_status,
            password_hash=password_hash,
            session_version=1,
            created_at=now,
            last_login=now,
        )

    async def get_user(self, user_id: int) -> Optional[User]:
        """Get user by ID."""
        row = await self._backend.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
        return self._row_to_user(row) if row else None

    async def get_user_by_linuxdo(self, linuxdo_id: str) -> Optional[User]:
        """Get user by LinuxDo ID."""
        row = await self._backend.fetch_one("SELECT * FROM users WHERE linuxdo_id = ?", (linuxdo_id,))
        return self._row_to_user(row) if row else None

    async def get_user_by_github(self, github_id: str) -> Optional[User]:
        """Get user by GitHub ID."""
        row = await self._backend.fetch_one("SELECT * FROM users WHERE github_id = ?", (github_id,))
        return self._row_to_user(row) if row else None

    async def get_user_by_email(self, email: str) -> Optional[User]:
        """Get user by email."""
        row = await self._backend.fetch_one("SELECT * FROM users WHERE email = ?", (email,))
        return self._row_to_user(row) if row else None

    async def get_or_create_user_by_linuxdo(
        self,
        linuxdo_id: str,
        username: str,
        avatar_url: Optional[str] = None,
        trust_level: int = 0,
    ) -> User:
        """Get user by LinuxDo ID, or create if not exists."""
        row = await self._backend.fetch_one("SELECT * FROM users WHERE linuxdo_id = ?", (linuxdo_id,))
        if row:
            return self._row_to_user(row)

        now = int(time.time() * 1000)
        user_id = await self._backend.execute(
            """INSERT INTO users (linuxdo_id, username, avatar_url, trust_level, created_at, last_login)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (linuxdo_id, username, avatar_url, trust_level, now, now),
        )
        return User(
            id=user_id,
            linuxdo_id=linuxdo_id,
            github_id=None,
            email=None,
            username=username,
            avatar_url=avatar_url,
            trust_level=trust_level,
            is_admin=False,
            is_banned=False,
            approval_status="approved",
            password_hash=None,
            session_version=1,
            created_at=now,
            last_login=now,
        )

    async def get_or_create_user_by_github(
        self,
        github_id: str,
        username: str,
        avatar_url: Optional[str] = None,
        trust_level: int = 0,
    ) -> User:
        """Get user by GitHub ID, or create if not exists."""
        row = await self._backend.fetch_one("SELECT * FROM users WHERE github_id = ?", (github_id,))
        if row:
            return self._row_to_user(row)

        now = int(time.time() * 1000)
        user_id = await self._backend.execute(
            """INSERT INTO users (github_id, username, avatar_url, trust_level, created_at, last_login)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (github_id, username, avatar_url, trust_level, now, now),
        )
        return User(
            id=user_id,
            linuxdo_id=None,
            github_id=github_id,
            email=None,
            username=username,
            avatar_url=avatar_url,
            trust_level=trust_level,
            is_admin=False,
            is_banned=False,
            approval_status="approved",
            password_hash=None,
            session_version=1,
            created_at=now,
            last_login=now,
        )

    async def update_last_login(self, user_id: int) -> None:
        """Update user's last login time."""
        now = int(time.time() * 1000)
        await self._backend.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, user_id))

    async def set_user_admin(self, user_id: int, is_admin: bool) -> None:
        """Set user admin status."""
        await self._backend.execute(
            "UPDATE users SET is_admin = ? WHERE id = ?", (1 if is_admin else 0, user_id)
        )

    async def set_user_banned(self, user_id: int, is_banned: bool) -> None:
        """Set user banned status."""
        await self._backend.execute(
            "UPDATE users SET is_banned = ? WHERE id = ?", (1 if is_banned else 0, user_id)
        )

    async def set_user_approval_status(self, user_id: int, status: str) -> None:
        """Set user approval status."""
        allowed = {"pending", "approved", "rejected"}
        if status not in allowed:
            raise ValueError("无效的审核状�?)
        await self._backend.execute(
            "UPDATE users SET approval_status = ? WHERE id = ?", (status, user_id)
        )

    async def get_all_users(
        self,
        limit: int = 100,
        offset: int = 0,
        search: str = "",
        is_admin: Optional[bool] = None,
        is_banned: Optional[bool] = None,
        approval_status: Optional[str] = None,
        trust_level: Optional[int] = None,
        sort_field: str = "created_at",
        sort_order: str = "desc",
    ) -> List[User]:
        """Get users with pagination, filters, and sorting."""
        where: list[str] = []
        params: list = []
        if search:
            like = f"%{search}%"
            where.append(
                "(username LIKE ? OR CAST(id AS TEXT) LIKE ? OR linuxdo_id LIKE ? OR github_id LIKE ? OR email LIKE ?)"
            )
            params.extend([like, like, like, like, like])
        if is_admin is not None:
            where.append("is_admin = ?")
            params.append(1 if is_admin else 0)
        if is_banned is not None:
            where.append("is_banned = ?")
            params.append(1 if is_banned else 0)
        if approval_status:
            where.append("approval_status = ?")
            params.append(approval_status)
        if trust_level is not None:
            where.append("trust_level = ?")
            params.append(trust_level)

        allowed_sort = {
            "id": "id",
            "username": "username",
            "created_at": "created_at",
            "last_login": "last_login",
            "trust_level": "trust_level",
            "token_count": "(SELECT COUNT(*) FROM tokens t WHERE t.user_id = users.id)",
            "api_key_count": "(SELECT COUNT(*) FROM api_keys k WHERE k.user_id = users.id AND k.is_active = 1)",
            "is_banned": "is_banned",
            "approval_status": "approval_status",
        }
        sort_column = allowed_sort.get(sort_field, "created_at")
        order = "ASC" if sort_order.lower() == "asc" else "DESC"

        query = "SELECT * FROM users"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += f" ORDER BY {sort_column} {order} LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = await self._backend.fetch_all(query, tuple(params))
        return [self._row_to_user(r) for r in rows]

    async def get_user_count(
        self,
        search: str = "",
        is_admin: Optional[bool] = None,
        is_banned: Optional[bool] = None,
        approval_status: Optional[str] = None,
        trust_level: Optional[int] = None,
    ) -> int:
        """Get total user count with optional filters."""
        where: list[str] = []
        params: list = []
        if search:
            like = f"%{search}%"
            where.append(
                "(username LIKE ? OR CAST(id AS TEXT) LIKE ? OR linuxdo_id LIKE ? OR github_id LIKE ? OR email LIKE ?)"
            )
            params.extend([like, like, like, like, like])
        if is_admin is not None:
            where.append("is_admin = ?")
            params.append(1 if is_admin else 0)
        if is_banned is not None:
            where.append("is_banned = ?")
            params.append(1 if is_banned else 0)
        if approval_status:
            where.append("approval_status = ?")
            params.append(approval_status)
        if trust_level is not None:
            where.append("trust_level = ?")
            params.append(trust_level)

        query = "SELECT COUNT(*) as cnt FROM users"
        if where:
            query += " WHERE " + " AND ".join(where)
        row = await self._backend.fetch_one(query, tuple(params))
        return row["cnt"] if row else 0

    async def get_session_version(self, user_id: int) -> int:
        """Get current session version for a user."""
        row = await self._backend.fetch_one(
            "SELECT session_version FROM users WHERE id = ?", (user_id,)
        )
        if row:
            return row["session_version"] or 1
        return 1

    async def increment_session_version(self, user_id: int) -> int:
        """Increment session version for a user, invalidating all existing sessions."""
        await self._backend.execute(
            "UPDATE users SET session_version = COALESCE(session_version, 0) + 1 WHERE id = ?",
            (user_id,),
        )
        row = await self._backend.fetch_one(
            "SELECT session_version FROM users WHERE id = ?", (user_id,)
        )
        return row["session_version"] if row else 1


    # ==================== Announcement Methods ====================

    async def get_latest_announcement(self) -> Optional[Dict]:
        """Get the latest announcement (active or inactive)."""
        row = await self._backend.fetch_one(
            "SELECT * FROM announcements ORDER BY updated_at DESC LIMIT 1"
        )
        return self._row_to_announcement(row) if row else None

    async def get_active_announcement(self) -> Optional[Dict]:
        """Get the latest active announcement."""
        row = await self._backend.fetch_one(
            "SELECT * FROM announcements WHERE is_active = 1 ORDER BY updated_at DESC LIMIT 1"
        )
        return self._row_to_announcement(row) if row else None

    async def deactivate_announcements(self) -> None:
        """Deactivate all announcements."""
        await self._backend.execute("UPDATE announcements SET is_active = 0 WHERE is_active = 1")

    async def create_announcement(self, content: str, is_active: bool, allow_guest: bool = False) -> int:
        """Create a new announcement."""
        now = int(time.time() * 1000)
        return await self._backend.execute(
            """INSERT INTO announcements (content, is_active, allow_guest, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (content, 1 if is_active else 0, 1 if allow_guest else 0, now, now),
        )

    async def get_announcement_status(self, user_id: int, announcement_id: int) -> Dict:
        """Get announcement status for a user."""
        row = await self._backend.fetch_one(
            """SELECT is_read, is_dismissed
               FROM announcement_status
               WHERE user_id = ? AND announcement_id = ?""",
            (user_id, announcement_id),
        )
        if not row:
            return {"is_read": False, "is_dismissed": False}
        return {
            "is_read": bool(row["is_read"]),
            "is_dismissed": bool(row["is_dismissed"]),
        }

    async def set_announcement_status(
        self,
        user_id: int,
        announcement_id: int,
        is_read: Optional[bool] = None,
        is_dismissed: Optional[bool] = None,
    ) -> None:
        """Update announcement status for a user."""
        now = int(time.time() * 1000)
        existing = await self._backend.fetch_one(
            """SELECT is_read, is_dismissed
               FROM announcement_status
               WHERE user_id = ? AND announcement_id = ?""",
            (user_id, announcement_id),
        )
        if existing:
            new_is_read = existing["is_read"] if is_read is None else (1 if is_read else 0)
            new_is_dismissed = existing["is_dismissed"] if is_dismissed is None else (1 if is_dismissed else 0)
            await self._backend.execute(
                """UPDATE announcement_status
                   SET is_read = ?, is_dismissed = ?, updated_at = ?
                   WHERE user_id = ? AND announcement_id = ?""",
                (new_is_read, new_is_dismissed, now, user_id, announcement_id),
            )
        else:
            await self._backend.execute(
                """INSERT INTO announcement_status
                   (user_id, announcement_id, is_read, is_dismissed, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, announcement_id, 1 if is_read else 0, 1 if is_dismissed else 0, now, now),
            )

    async def mark_announcement_read(self, user_id: int, announcement_id: int) -> None:
        """Mark announcement as read."""
        await self.set_announcement_status(user_id, announcement_id, is_read=True)

    async def mark_announcement_dismissed(self, user_id: int, announcement_id: int) -> None:
        """Mark announcement as dismissed."""
        await self.set_announcement_status(user_id, announcement_id, is_dismissed=True)

    # ==================== Token Methods ====================

    async def get_user_tokens(
        self,
        user_id: int,
        limit: Optional[int] = 100,
        offset: int = 0,
        search: str = "",
        status: Optional[str] = None,
        visibility: Optional[str] = None,
        sort_field: str = "id",
        sort_order: str = "desc",
    ) -> List[DonatedToken]:
        """Get tokens for a user with pagination and filters."""
        where = ["user_id = ?"]
        params: list = [user_id]
        if search:
            like = f"%{search}%"
            where.append("(CAST(id AS TEXT) LIKE ? OR status LIKE ? OR visibility LIKE ?)")
            params.extend([like, like, like])
        if status:
            where.append("status = ?")
            params.append(status)
        if visibility:
            where.append("visibility = ?")
            params.append(visibility)

        allowed_sort = {
            "id": "id",
            "visibility": "visibility",
            "status": "status",
            "last_used": "last_used",
            "created_at": "created_at",
            "success_rate": (
                "CASE WHEN (success_count + fail_count) > 0 "
                "THEN CAST(success_count AS REAL) / (success_count + fail_count) "
                "ELSE 1.0 END"
            ),
        }
        sort_column = allowed_sort.get(sort_field, "id")
        order = "ASC" if sort_order.lower() == "asc" else "DESC"

        query = "SELECT * FROM tokens WHERE " + " AND ".join(where)
        query += f" ORDER BY {sort_column} {order}"
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        rows = await self._backend.fetch_all(query, tuple(params))
        return [self._row_to_token(r) for r in rows]

    async def get_user_tokens_count(
        self,
        user_id: int,
        search: str = "",
        status: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> int:
        """Get token count for a user with optional filters."""
        where = ["user_id = ?"]
        params: list = [user_id]
        if search:
            like = f"%{search}%"
            where.append("(CAST(id AS TEXT) LIKE ? OR status LIKE ? OR visibility LIKE ?)")
            params.extend([like, like, like])
        if status:
            where.append("status = ?")
            params.append(status)
        if visibility:
            where.append("visibility = ?")
            params.append(visibility)
        query = "SELECT COUNT(*) as cnt FROM tokens WHERE " + " AND ".join(where)
        row = await self._backend.fetch_one(query, tuple(params))
        return row["cnt"] if row else 0

    async def get_public_tokens(self, status: str = "active") -> List[DonatedToken]:
        """Get all public tokens with given status."""
        rows = await self._backend.fetch_all(
            "SELECT * FROM tokens WHERE visibility = 'public' AND status = ?",
            (status,),
        )
        return [self._row_to_token(r) for r in rows]

    async def get_all_active_tokens(self) -> List[DonatedToken]:
        """Get all active tokens (for health check)."""
        rows = await self._backend.fetch_all(
            "SELECT * FROM tokens WHERE status = 'active'"
        )
        return [self._row_to_token(r) for r in rows]

    async def get_token_by_id(self, token_id: int) -> Optional[DonatedToken]:
        """Get token by ID."""
        row = await self._backend.fetch_one(
            "SELECT * FROM tokens WHERE id = ?", (token_id,)
        )
        return self._row_to_token(row) if row else None    
 
    async def token_exists(self, refresh_token: str) -> bool:
        """Check if a token with the given refresh_token already exists."""
        token_hash = self._hash_token(refresh_token)
        row = await self._backend.fetch_one(
            "SELECT 1 FROM tokens WHERE token_hash = ?", (token_hash,)
        )
        return row is not None

    async def donate_token(
        self,
        user_id: int,
        refresh_token: str,
        visibility: str = "private",
        anonymous: bool = False,
        auth_type: str = "social",
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Donate a token to the pool.
        
        Returns:
            Tuple of (success, message)
        """
        # Check if token already exists
        token_hash = self._hash_token(refresh_token)
        existing = await self._backend.fetch_one(
            "SELECT 1 FROM tokens WHERE token_hash = ?", (token_hash,)
        )
        if existing:
            return False, "Token 已存�?
        
        # Encrypt the token
        encrypted_token = self._encrypt_token(refresh_token)
        
        # Encrypt client credentials if provided
        client_id_encrypted = self._encrypt_token(client_id) if client_id else None
        client_secret_encrypted = self._encrypt_token(client_secret) if client_secret else None
        
        now = int(time.time() * 1000)
        
        try:
            await self._backend.execute(
                """INSERT INTO tokens 
                   (user_id, refresh_token_encrypted, token_hash, visibility, 
                    is_anonymous, status, auth_type, client_id_encrypted, 
                    client_secret_encrypted, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, encrypted_token, token_hash, visibility,
                 1 if anonymous else 0, "active", auth_type,
                 client_id_encrypted, client_secret_encrypted, now)
            )
            return True, "Token 添加成功"
        except Exception as e:
            logger.error(f"Failed to donate token: {e}")
            return False, f"添加失败: {str(e)}"

    async def get_decrypted_token(self, token_id: int) -> Optional[str]:
        """Get decrypted refresh token by ID."""
        row = await self._backend.fetch_one(
            "SELECT refresh_token_encrypted FROM tokens WHERE id = ?", (token_id,)
        )
        if row:
            return self._decrypt_token(row["refresh_token_encrypted"])
        return None
    async def get_token_credentials(self, token_id: int) -> Optional[Dict[str, Any]]:
        """
        Get decrypted token credentials including refresh_token, client_id, client_secret.

        Returns:
            Dict with refresh_token, client_id, client_secret (or None if not found)
        """
        row = await self._backend.fetch_one(
            """SELECT refresh_token_encrypted, client_id_encrypted, client_secret_encrypted, auth_type
               FROM tokens WHERE id = ?""",
            (token_id,)
        )
        if not row:
            return None

        result = {
            "refresh_token": self._decrypt_token(row["refresh_token_encrypted"]),
            "auth_type": row.get("auth_type") or "social",
        }

        # Decrypt client credentials if present (for IDC auth type)
        if row.get("client_id_encrypted"):
            result["client_id"] = self._decrypt_token(row["client_id_encrypted"])
        if row.get("client_secret_encrypted"):
            result["client_secret"] = self._decrypt_token(row["client_secret_encrypted"])

        return result

    async def set_token_status(self, token_id: int, status: str) -> bool:
        """Set token status (active/invalid/expired/suspended)."""
        if status not in ("active", "invalid", "expired", "suspended"):
            return False
        await self._backend.execute(
            "UPDATE tokens SET status = ? WHERE id = ?", (status, token_id)
        )
        return True

    async def set_token_visibility(self, token_id: int, visibility: str) -> bool:
        """Set token visibility (public/private)."""
        if visibility not in ("public", "private"):
            return False
        await self._backend.execute(
            "UPDATE tokens SET visibility = ? WHERE id = ?", (visibility, token_id)
        )
        return True

    async def record_token_usage(self, token_id: int, success: bool) -> None:
        """Record token usage result."""
        now = int(time.time() * 1000)
        if success:
            await self._backend.execute(
                "UPDATE tokens SET success_count = success_count + 1, last_used = ? WHERE id = ?",
                (now, token_id),
            )
        else:
            await self._backend.execute(
                "UPDATE tokens SET fail_count = fail_count + 1, last_used = ? WHERE id = ?",
                (now, token_id),
            )

    async def update_token_risk_fields(
        self,
        token_id: int,
        consecutive_fails: Optional[int] = None,
        cooldown_until: Optional[int] = None,
        consecutive_uses: Optional[int] = None,
        risk_score: Optional[float] = None,
    ) -> None:
        """Update token risk-control fields."""
        updates = []
        params: list = []
        if consecutive_fails is not None:
            updates.append("consecutive_fails = ?")
            params.append(consecutive_fails)
        if cooldown_until is not None:
            updates.append("cooldown_until = ?")
            params.append(cooldown_until)
        if consecutive_uses is not None:
            updates.append("consecutive_uses = ?")
            params.append(consecutive_uses)
        if risk_score is not None:
            updates.append("risk_score = ?")
            params.append(risk_score)
        if not updates:
            return
        params.append(token_id)
        query = f"UPDATE tokens SET {', '.join(updates)} WHERE id = ?"
        await self._backend.execute(query, tuple(params))

    async def record_health_check(
        self, token_id: int, is_valid: bool, error_msg: Optional[str] = None
    ) -> None:
        """Record token health check result."""
        now = int(time.time() * 1000)
        await self._backend.execute(
            "INSERT INTO token_health (token_id, check_time, is_valid, error_msg) VALUES (?, ?, ?, ?)",
            (token_id, now, 1 if is_valid else 0, error_msg),
        )
        await self._backend.execute(
            "UPDATE tokens SET last_check = ? WHERE id = ?", (now, token_id)
        )

    async def delete_token(self, token_id: int, user_id: Optional[int] = None) -> bool:
        """Delete a token. If user_id provided, verify ownership."""
        if user_id:
            await self._backend.execute(
                "DELETE FROM tokens WHERE id = ? AND user_id = ?", (token_id, user_id)
            )
        else:
            await self._backend.execute("DELETE FROM tokens WHERE id = ?", (token_id,))
        return True

    async def admin_delete_token(self, token_id: int) -> bool:
        """Admin delete a token (no ownership check)."""
        await self._backend.execute("DELETE FROM tokens WHERE id = ?", (token_id,))
        return True

    async def update_token_account_info(
        self,
        token_id: int,
        email: Optional[str] = None,
        status: Optional[str] = None,
        usage: Optional[float] = None,
        limit: Optional[float] = None
    ) -> None:
        """Update cached account info for a token."""
        now = int(time.time() * 1000)
        await self._backend.execute(
            """UPDATE tokens SET 
               account_email = COALESCE(?, account_email),
               account_status = COALESCE(?, account_status),
               account_usage = COALESCE(?, account_usage),
               account_limit = COALESCE(?, account_limit),
               account_checked_at = ?
               WHERE id = ?""",
            (email, status, usage, limit, now, token_id)
        )

    async def get_token_count(self, user_id: int) -> Dict[str, int]:
        """Get token counts by status for a user."""
        rows = await self._backend.fetch_all(
            "SELECT status, COUNT(*) as cnt FROM tokens WHERE user_id = ? GROUP BY status",
            (user_id,),
        )
        result: Dict[str, int] = {"active": 0, "invalid": 0, "expired": 0, "total": 0}
        for row in rows:
            status = row["status"]
            count = row["cnt"]
            result[status] = count
            result["total"] += count
        return result

    async def get_public_tokens_with_users(self, status: str = "active") -> List[Dict]:
        """Get public tokens with user information."""
        rows = await self._backend.fetch_all(
            """SELECT t.*, u.username, u.avatar_url
               FROM tokens t
               JOIN users u ON t.user_id = u.id
               WHERE t.visibility = 'public' AND t.status = ?
               ORDER BY t.success_count DESC""",
            (status,),
        )
        result = []
        for r in rows:
            total = r["success_count"] + r["fail_count"]
            success_rate = r["success_count"] / total if total > 0 else 1.0
            result.append({
                "id": r["id"],
                "username": r["username"],
                "avatar_url": r.get("avatar_url"),
                "status": r["status"],
                "success_rate": success_rate,
                "success_count": r["success_count"],
                "fail_count": r["fail_count"],
                "last_used": r.get("last_used"),
                "created_at": r["created_at"],
            })
        return result

    # ==================== API Key Methods ====================

    async def generate_api_key(self, user_id: int, name: Optional[str] = None) -> Tuple[str, APIKey]:
        """
        Generate a new API key for user.

        Returns:
            (plain_key, APIKey object) - plain_key is only returned once!
        """
        random_part = secrets.token_hex(24)
        plain_key = f"sk-{random_part}"
        key_hash = self._hash_token(plain_key)
        key_prefix = f"sk-{random_part[:4]}...{random_part[-4:]}"
        now = int(time.time() * 1000)

        key_id = await self._backend.execute(
            """INSERT INTO api_keys (user_id, key_hash, key_prefix, name, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, key_hash, key_prefix, name, now)
        )

        return plain_key, APIKey(
            id=key_id,
            user_id=user_id,
            key_prefix=key_prefix,
            name=name,
            is_active=True,
            request_count=0,
            last_used=None,
            created_at=now
        )

    async def record_api_key_usage(self, key_id: int) -> None:
        """Record API key usage (increment count and update last_used)."""
        now = int(time.time() * 1000)
        await self._backend.execute(
            "UPDATE api_keys SET request_count = request_count + 1, last_used = ? WHERE id = ?",
            (now, key_id),
        )

    async def verify_api_key(self, api_key: str) -> Optional[Tuple[int, int]]:
        """
        Verify an API key and return (user_id, key_id) if valid.
        
        Returns:
            Tuple of (user_id, key_id) if valid, None otherwise
        """
        key_hash = self._hash_token(api_key)
        row = await self._backend.fetch_one(
            "SELECT id, user_id FROM api_keys WHERE key_hash = ? AND is_active = 1",
            (key_hash,)
        )
        if row:
            return row["user_id"], row["id"]
        return None

    async def get_api_key_count(self, user_id: int) -> int:
        """Get count of active API keys for a user."""
        row = await self._backend.fetch_one(
            "SELECT COUNT(*) as cnt FROM api_keys WHERE user_id = ? AND is_active = 1",
            (user_id,)
        )
        return row["cnt"] if row else 0

    async def get_user_api_keys(
        self,
        user_id: int,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> List[APIKey]:
        """Get API keys for a user."""
        query = "SELECT * FROM api_keys WHERE user_id = ? ORDER BY created_at DESC"
        params: list = [user_id]
        if limit:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        rows = await self._backend.fetch_all(query, tuple(params))
        return [self._row_to_apikey(r) for r in rows]

    async def get_all_tokens_with_users(
        self,
        limit: int = 100,
        offset: int = 0,
        search: str = "",
        visibility: Optional[str] = None,
        status: Optional[str] = None,
        user_id: Optional[int] = None,
        sort_field: str = "created_at",
        sort_order: str = "desc"
    ) -> List[Dict]:
        """Get tokens with user info for admin panel (pagination + filters)."""
        where: List[str] = []
        params: List = []
        
        if search:
            like = f"%{search}%"
            where.append("(u.username LIKE ? OR CAST(t.user_id AS TEXT) LIKE ? OR CAST(t.id AS TEXT) LIKE ?)")
            params.extend([like, like, like])
        if visibility:
            where.append("t.visibility = ?")
            params.append(visibility)
        if status:
            where.append("t.status = ?")
            params.append(status)
        if user_id is not None:
            where.append("t.user_id = ?")
            params.append(user_id)

        allowed_sort = {
            "id": "t.id",
            "username": "u.username",
            "created_at": "t.created_at",
            "last_used": "t.last_used",
            "success_rate": (
                "CASE WHEN (t.success_count + t.fail_count) > 0 "
                "THEN CAST(t.success_count AS REAL) / (t.success_count + t.fail_count) "
                "ELSE 1.0 END"
            ),
            "use_count": "(t.success_count + t.fail_count)",
        }
        sort_column = allowed_sort.get(sort_field, "t.created_at")
        order = "ASC" if sort_order.lower() == "asc" else "DESC"

        query = (
            "SELECT t.*, u.username "
            "FROM tokens t "
            "LEFT JOIN users u ON t.user_id = u.id"
        )
        if where:
            query += " WHERE " + " AND ".join(where)
        query += f" ORDER BY {sort_column} {order} LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = await self._backend.fetch_all(query, tuple(params))
        return [
            {
                "id": r["id"],
                "user_id": r["user_id"],
                "username": r.get("username"),
                "visibility": r["visibility"],
                "status": r["status"],
                "success_count": r["success_count"],
                "fail_count": r["fail_count"],
                "success_rate": r["success_count"] / max(r["success_count"] + r["fail_count"], 1),
                "last_used": r.get("last_used"),
                "created_at": r["created_at"]
            }
            for r in rows
        ]

    async def get_tokens_count(
        self,
        search: str = "",
        visibility: Optional[str] = None,
        status: Optional[str] = None,
        user_id: Optional[int] = None
    ) -> int:
        """Get token count with optional filters."""
        where: List[str] = []
        params: List = []
        
        if search:
            like = f"%{search}%"
            where.append("(u.username LIKE ? OR CAST(t.user_id AS TEXT) LIKE ? OR CAST(t.id AS TEXT) LIKE ?)")
            params.extend([like, like, like])
        if visibility:
            where.append("t.visibility = ?")
            params.append(visibility)
        if status:
            where.append("t.status = ?")
            params.append(status)
        if user_id is not None:
            where.append("t.user_id = ?")
            params.append(user_id)

        query = (
            "SELECT COUNT(*) as cnt "
            "FROM tokens t "
            "LEFT JOIN users u ON t.user_id = u.id"
        )
        if where:
            query += " WHERE " + " AND ".join(where)
        
        row = await self._backend.fetch_one(query, tuple(params))
        return row["cnt"] if row else 0

    async def get_tokens_success_rate_avg(self) -> float:
        """Get average success rate across all tokens."""
        query = (
            "SELECT AVG(CASE WHEN (success_count + fail_count) > 0 "
            "THEN CAST(success_count AS REAL) / (success_count + fail_count) "
            "ELSE 1.0 END) as avg_rate FROM tokens"
        )
        row = await self._backend.fetch_one(query, ())
        return row["avg_rate"] if row and row["avg_rate"] is not None else 1.0

    # ==================== Import Key Methods ====================

    async def generate_import_key(self, user_id: int, name: Optional[str] = None) -> Tuple[str, ImportKey]:
        """Generate a new import key for a user."""
        plain_key = secrets.token_urlsafe(32)
        key_hash = self._hash_token(plain_key)
        key_prefix = plain_key[:8]
        now = int(time.time() * 1000)
        
        key_id = await self._backend.execute(
            """INSERT INTO import_keys (user_id, key_hash, key_prefix, name, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, key_hash, key_prefix, name, now)
        )
        
        import_key = ImportKey(
            id=key_id,
            user_id=user_id,
            key_prefix=key_prefix,
            name=name,
            is_active=True,
            request_count=0,
            last_used=None,
            created_at=now
        )
        return plain_key, import_key

    async def delete_import_key(self, key_id: int) -> bool:
        """Delete an import key."""
        await self._backend.execute("DELETE FROM import_keys WHERE id = ?", (key_id,))
        return True

    async def verify_import_key(self, import_key: str) -> Optional[Tuple[int, ImportKey]]:
        """
        Verify an import key and return (user_id, ImportKey) if valid.
        """
        key_hash = self._hash_token(import_key)
        row = await self._backend.fetch_one(
            "SELECT * FROM import_keys WHERE key_hash = ? AND is_active = 1",
            (key_hash,)
        )
        if row:
            return row["user_id"], self._row_to_import_key(row)
        return None

    async def record_import_key_usage(self, key_id: int) -> None:
        """Record import key usage."""
        now = int(time.time() * 1000)
        await self._backend.execute(
            "UPDATE import_keys SET request_count = request_count + 1, last_used = ? WHERE id = ?",
            (now, key_id)
        )


# Global database instance
user_db = UserDatabase()
