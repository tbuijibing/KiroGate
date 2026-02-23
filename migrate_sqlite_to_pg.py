#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SQLite → PostgreSQL 数据迁移脚本。

将 KiroGate 的 SQLite 数据库中的所有数据迁移到 PostgreSQL。
按照外键依赖顺序逐表迁移，整个过程在一个事务中完成，
任何表迁移失败时回滚整个事务。

用法:
    python migrate_sqlite_to_pg.py
    python migrate_sqlite_to_pg.py --sqlite-path data/users.db --pg-url postgresql://user:pass@host:5432/kirogate
"""

import argparse
import asyncio
import os
import sys
import time

import aiosqlite
import asyncpg


# 按外键依赖顺序排列的表列表
TABLES_IN_ORDER = [
    "users",
    "tokens",
    "api_keys",
    "import_keys",
    "announcements",
    "announcement_status",
    "user_quotas",
    "audit_logs",
    "activity_logs",
    "user_notifications",
]

# SQLite 中使用 INTEGER 表示布尔值的列（需转换为 PostgreSQL BOOLEAN）
BOOLEAN_COLUMNS = {
    "users": {"is_admin", "is_banned"},
    "tokens": {"is_anonymous"},
    "api_keys": {"is_active"},
    "import_keys": {"is_active"},
    "announcements": {"is_active", "allow_guest"},
    "announcement_status": {"is_read", "is_dismissed"},
    "user_notifications": {"is_read"},
}

# 使用 SERIAL 自增的 id 列（迁移时需跳过，让 PG 自动生成）
SERIAL_ID_TABLES = {
    "users",
    "tokens",
    "api_keys",
    "import_keys",
    "announcements",
    "audit_logs",
    "activity_logs",
    "user_notifications",
}


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="将 KiroGate SQLite 数据迁移到 PostgreSQL"
    )
    parser.add_argument(
        "--sqlite-path",
        default="data/users.db",
        help="SQLite 数据库文件路径（默认: data/users.db）",
    )
    parser.add_argument(
        "--pg-url",
        default=None,
        help="PostgreSQL 连接 URL（默认: 从 DATABASE_URL 环境变量读取）",
    )
    return parser.parse_args()


def get_pg_url(args: argparse.Namespace) -> str:
    """获取 PostgreSQL 连接 URL，优先使用命令行参数，其次环境变量。"""
    if args.pg_url:
        url = args.pg_url
    else:
        url = os.environ.get("DATABASE_URL", "")

    if not url:
        print("错误: 未指定 PostgreSQL 连接 URL。")
        print("请通过 --pg-url 参数或 DATABASE_URL 环境变量提供。")
        sys.exit(1)

    # 将 SQLAlchemy 格式的 URL 转换为 asyncpg 格式
    # postgresql+asyncpg://... → postgresql://...
    if "+asyncpg" in url:
        url = url.replace("+asyncpg", "")

    return url


async def get_sqlite_columns(db: aiosqlite.Connection, table: str) -> list[str]:
    """获取 SQLite 表的列名列表。"""
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return [row[1] for row in rows]


async def get_sqlite_row_count(db: aiosqlite.Connection, table: str) -> int:
    """获取 SQLite 表的行数。"""
    cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")
    row = await cursor.fetchone()
    return row[0]


def convert_row(table: str, columns: list[str], row: tuple) -> tuple:
    """将 SQLite 行数据转换为 PostgreSQL 兼容格式。

    主要处理 INTEGER → BOOLEAN 的转换。
    """
    bool_cols = BOOLEAN_COLUMNS.get(table, set())
    if not bool_cols:
        return row

    converted = []
    for col, val in zip(columns, row):
        if col in bool_cols and val is not None:
            # SQLite 中 0/1 → PostgreSQL BOOLEAN
            converted.append(bool(val))
        else:
            converted.append(val)
    return tuple(converted)


async def migrate_table(
    sqlite_db: aiosqlite.Connection,
    pg_conn: asyncpg.Connection,
    table: str,
    table_index: int,
    total_tables: int,
) -> int:
    """迁移单个表的数据。

    返回迁移的行数。
    """
    # 获取列信息
    all_columns = await get_sqlite_columns(sqlite_db, table)
    if not all_columns:
        print(f"  ⚠ 表 {table} 不存在或无列定义，跳过")
        return 0

    # 判断是否需要跳过 id 列（SERIAL 自增表）
    skip_id = table in SERIAL_ID_TABLES and "id" in all_columns
    if skip_id:
        columns = [c for c in all_columns if c != "id"]
    else:
        columns = all_columns

    row_count = await get_sqlite_row_count(sqlite_db, table)
    print(f"  [{table_index}/{total_tables}] 迁移表 {table}（{row_count} 行）...")

    if row_count == 0:
        print(f"  ✓ 表 {table} 无数据，跳过")
        return 0

    # 从 SQLite 读取所有数据
    select_cols = ", ".join(all_columns)
    cursor = await sqlite_db.execute(f"SELECT {select_cols} FROM {table}")
    rows = await cursor.fetchall()

    # 转换数据并批量插入 PostgreSQL
    insert_cols = ", ".join(columns)
    placeholders = ", ".join(f"${i+1}" for i in range(len(columns)))
    insert_sql = f"INSERT INTO {table} ({insert_cols}) VALUES ({placeholders})"

    migrated = 0
    batch_size = 500
    for batch_start in range(0, len(rows), batch_size):
        batch = rows[batch_start : batch_start + batch_size]
        pg_rows = []
        for row in batch:
            # 转换布尔值
            converted = convert_row(table, all_columns, tuple(row))
            if skip_id:
                # 去掉 id 列的值
                id_idx = all_columns.index("id")
                converted = converted[:id_idx] + converted[id_idx + 1 :]
            pg_rows.append(converted)

        # 使用 executemany 批量插入
        await pg_conn.executemany(insert_sql, pg_rows)
        migrated += len(pg_rows)

        # 显示批次进度
        if row_count > batch_size:
            print(f"    已迁移 {migrated}/{row_count} 行...")

    # 如果跳过了 id 列，需要重置序列值
    if skip_id:
        seq_name = f"{table}_id_seq"
        await pg_conn.execute(
            f"SELECT setval('{seq_name}', COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
        )

    print(f"  ✓ 表 {table} 迁移完成（{migrated} 行）")
    return migrated


async def main():
    """主函数：执行完整的数据迁移流程。"""
    args = parse_args()
    sqlite_path = args.sqlite_path
    pg_url = get_pg_url(args)

    # 检查 SQLite 文件是否存在
    if not os.path.exists(sqlite_path):
        print(f"错误: SQLite 数据库文件不存在: {sqlite_path}")
        sys.exit(1)

    print("=" * 60)
    print("KiroGate 数据迁移工具")
    print("=" * 60)
    print(f"源数据库: {sqlite_path}")
    print(f"目标数据库: {pg_url.split('@')[-1] if '@' in pg_url else pg_url}")
    print(f"迁移表数量: {len(TABLES_IN_ORDER)}")
    print("=" * 60)
    print()

    start_time = time.time()
    total_rows = 0
    sqlite_db = None
    pg_conn = None

    try:
        # 连接 SQLite 数据库
        print("正在连接 SQLite 数据库...")
        sqlite_db = await aiosqlite.connect(sqlite_path)
        print("✓ SQLite 连接成功")

        # 连接 PostgreSQL 数据库
        print("正在连接 PostgreSQL 数据库...")
        pg_conn = await asyncpg.connect(pg_url)
        print("✓ PostgreSQL 连接成功")
        print()

        # 开始事务
        print("开始迁移（事务模式）...")
        print()
        async with pg_conn.transaction():
            # 按顺序迁移每个表
            for idx, table in enumerate(TABLES_IN_ORDER, start=1):
                try:
                    rows = await migrate_table(
                        sqlite_db, pg_conn, table, idx, len(TABLES_IN_ORDER)
                    )
                    total_rows += rows
                except Exception as e:
                    print(f"  ✗ 表 {table} 迁移失败: {e}")
                    print()
                    print("=" * 60)
                    print("迁移失败，正在回滚所有更改...")
                    print(f"失败表名: {table}")
                    print(f"错误原因: {e}")
                    print("=" * 60)
                    raise  # 重新抛出异常以触发事务回滚

        # 迁移成功
        elapsed = time.time() - start_time
        print()
        print("=" * 60)
        print("迁移完成！")
        print("=" * 60)
        print(f"迁移表数: {len(TABLES_IN_ORDER)}")
        print(f"迁移行数: {total_rows}")
        print(f"耗时: {elapsed:.2f} 秒")
        print("=" * 60)

    except Exception as e:
        # 事务已自动回滚
        elapsed = time.time() - start_time
        print()
        print("=" * 60)
        print("迁移失败！")
        print("=" * 60)
        print(f"错误: {e}")
        print(f"耗时: {elapsed:.2f} 秒")
        print("=" * 60)
        sys.exit(1)

    finally:
        # 关闭连接
        if sqlite_db:
            await sqlite_db.close()
        if pg_conn:
            await pg_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
