# -*- coding: utf-8 -*-

"""
KiroGate 数据库后端抽象层。

提供 SQLite 和 PostgreSQL 两种后端实现，
通过统一的异步接口供 UserDatabase 使用。
"""

from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

from loguru import logger


class DatabaseBackend(ABC):
    """数据库后端抽象接口。"""

    @abstractmethod
    async def initialize(self) -> None:
        """初始化数据库连接。"""
        ...

    @abstractmethod
    async def close(self) -> None:
        """关闭数据库连接。"""
        ...

    @abstractmethod
    async def execute(self, query: str, params: tuple = ()) -> Any:
        """执行写操作（INSERT/UPDATE/DELETE），返回 lastrowid。"""
        ...

    @abstractmethod
    async def fetch_one(self, query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        """查询单行，返回字典或 None。"""
        ...

    @abstractmethod
    async def fetch_all(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """查询多行，返回字典列表。"""
        ...

    @abstractmethod
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """事务上下文管理器。"""
        ...

    @abstractmethod
    async def executescript(self, script: str) -> None:
        """执行多条 SQL 语句（用于建表等）。"""
        ...


class SQLiteBackend(DatabaseBackend):
    """SQLite 后端，使用 aiosqlite。"""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn = None

    async def initialize(self) -> None:
        import aiosqlite
        from pathlib import Path

        # 确保目录存在
        db_file = self._db_path
        if db_file.startswith("sqlite:///"):
            db_file = db_file[len("sqlite:///"):]

        Path(db_file).parent.mkdir(parents=True, exist_ok=True)

        self._conn = await aiosqlite.connect(db_file)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        logger.info(f"SQLite 数据库已连接: {db_file}")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("SQLite 数据库已关闭")

    async def execute(self, query: str, params: tuple = ()) -> Any:
        cursor = await self._conn.execute(query, params)
        await self._conn.commit()
        return cursor.lastrowid

    async def fetch_one(self, query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        cursor = await self._conn.execute(query, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def fetch_all(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        cursor = await self._conn.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        await self._conn.execute("BEGIN")
        try:
            yield
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

    async def executescript(self, script: str) -> None:
        await self._conn.executescript(script)
        await self._conn.commit()


class PostgreSQLBackend(DatabaseBackend):
    """PostgreSQL 后端，使用 SQLAlchemy async + asyncpg。"""

    def __init__(self, database_url: str, pool_size: int = 20, max_overflow: int = 10):
        self._database_url = database_url
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._engine = None

    async def initialize(self) -> None:
        try:
            from sqlalchemy.ext.asyncio import create_async_engine
            from sqlalchemy import text

            self._engine = create_async_engine(
                self._database_url,
                pool_size=self._pool_size,
                max_overflow=self._max_overflow,
                pool_pre_ping=True,
                pool_recycle=3600,
            )

            # 测试连接
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

            logger.info(f"PostgreSQL 数据库已连接: {self._mask_url(self._database_url)}")

        except Exception as e:
            target = self._mask_url(self._database_url)
            logger.error(f"PostgreSQL 连接失败 ({target}): {e}")
            raise ConnectionError(f"无法连接到 PostgreSQL ({target}): {e}") from e

    async def close(self) -> None:
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            logger.info("PostgreSQL 数据库已关闭")

    async def execute(self, query: str, params: tuple = ()) -> Any:
        from sqlalchemy import text

        # 将 ? 占位符转换为 :p0, :p1, ... 格式
        converted_query, named_params = self._convert_params(query, params)

        async with self._engine.begin() as conn:
            result = await conn.execute(text(converted_query), named_params)
            # 尝试获取 inserted id
            try:
                row = result.fetchone()
                if row:
                    return row[0]
            except Exception:
                pass
            return result.lastrowid if hasattr(result, "lastrowid") else None

    async def fetch_one(self, query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        from sqlalchemy import text

        converted_query, named_params = self._convert_params(query, params)

        async with self._engine.connect() as conn:
            result = await conn.execute(text(converted_query), named_params)
            row = result.mappings().fetchone()
            if row is None:
                return None
            return dict(row)

    async def fetch_all(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        from sqlalchemy import text

        converted_query, named_params = self._convert_params(query, params)

        async with self._engine.connect() as conn:
            result = await conn.execute(text(converted_query), named_params)
            rows = result.mappings().fetchall()
            return [dict(r) for r in rows]

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        async with self._engine.begin() as conn:
            yield

    async def executescript(self, script: str) -> None:
        from sqlalchemy import text

        # PostgreSQL 不支持 executescript，逐条执行
        statements = [s.strip() for s in script.split(";") if s.strip()]
        async with self._engine.begin() as conn:
            for stmt in statements:
                if stmt:
                    await conn.execute(text(stmt))

    def _convert_params(self, query: str, params: tuple) -> tuple:
        """将 ? 占位符转换为 SQLAlchemy 命名参数格式。"""
        if not params:
            return query, {}

        named_params = {}
        converted = query
        for i, val in enumerate(params):
            param_name = f"p{i}"
            named_params[param_name] = val
            converted = converted.replace("?", f":{param_name}", 1)

        return converted, named_params

    def _mask_url(self, url: str) -> str:
        """遮蔽 URL 中的密码。"""
        if "@" in url:
            # postgresql+asyncpg://user:pass@host:port/db
            prefix = url.split("://")[0]
            after_at = url.split("@")[-1]
            return f"{prefix}://***@{after_at}"
        return url


def create_backend() -> DatabaseBackend:
    """根据配置创建对应的数据库后端。"""
    from kiro_gateway.config import settings

    if settings.is_distributed:
        logger.info("使用 PostgreSQL 数据库后端")
        return PostgreSQLBackend(
            database_url=settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
        )
    else:
        logger.info("使用 SQLite 数据库后端")
        return SQLiteBackend(db_path=settings.database_url)


# SQL Schema 转换工具
def convert_schema_to_pg(sqlite_schema: str) -> str:
    """将 SQLite schema 转换为 PostgreSQL 兼容格式。"""
    import re

    pg_schema = sqlite_schema

    # AUTOINCREMENT → SERIAL
    pg_schema = re.sub(
        r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
        "SERIAL PRIMARY KEY",
        pg_schema,
        flags=re.IGNORECASE,
    )

    # INTEGER PRIMARY KEY (without AUTOINCREMENT) → SERIAL PRIMARY KEY
    pg_schema = re.sub(
        r"INTEGER\s+PRIMARY\s+KEY(?!\s+AUTOINCREMENT)",
        "SERIAL PRIMARY KEY",
        pg_schema,
        flags=re.IGNORECASE,
    )

    # REAL → DOUBLE PRECISION
    pg_schema = re.sub(r"\bREAL\b", "DOUBLE PRECISION", pg_schema, flags=re.IGNORECASE)

    # IF NOT EXISTS is supported in both, keep as is

    return pg_schema
