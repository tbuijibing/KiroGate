# 实施计划：KiroGate 分布式部署

## 概述

按照设计文档的初始化顺序，将 KiroGate 从单节点架构迁移为分布式多节点架构。实施顺序为：依赖管理 → 配置模块 → Redis 连接管理 → 数据库抽象层 → 认证缓存 → 指标系统 → Token 分配器与防风控 → 健康检查器 → 用户配额 → 用户面板 → 管理面板 → Docker 编排 → 数据迁移脚本 → 生命周期管理。

## 任务

- [x] 1. 依赖管理与配置模块
  - [x] 1.1 更新 `requirements.txt`，添加分布式部署依赖
    - 添加 `asyncpg>=0.29.0,<1.0.0`
    - 添加 `sqlalchemy[asyncio]>=2.0.0,<3.0.0`
    - 添加 `redis[hiredis]>=5.0.0,<6.0.0`
    - 添加 `aiosqlite>=0.20.0,<1.0.0`
    - 确保新增依赖不与现有依赖产生版本冲突
    - _需求: 9.1, 9.2, 9.3, 9.4_

  - [x] 1.2 扩展 `kiro_gateway/config.py` 的 Settings 类
    - 添加 `DATABASE_URL` 字段，默认值 `sqlite:///data/kirogate.db`
    - 添加 `REDIS_URL` 字段，默认值为空字符串
    - 添加 `NODE_ID` 字段，默认值为自动生成的 UUID
    - 添加 `DB_POOL_SIZE`（默认 20）、`DB_MAX_OVERFLOW`（默认 10）字段
    - 添加 `REDIS_MAX_CONNECTIONS`（默认 50）字段
    - 添加 Token 防风控配置项：`TOKEN_RPM_LIMIT`（默认 10）、`TOKEN_RPH_LIMIT`（默认 200）、`TOKEN_MAX_CONCURRENT`（默认 2）、`TOKEN_MAX_CONSECUTIVE_USES`（默认 5）
    - 添加用户配额配置项：`DEFAULT_USER_DAILY_QUOTA`（默认 500）、`DEFAULT_USER_MONTHLY_QUOTA`（默认 10000）、`DEFAULT_KEY_RPM_LIMIT`（默认 30）
    - 实现 `is_distributed` 属性：`DATABASE_URL` 以 `postgresql` 开头且 `REDIS_URL` 非空时返回 True
    - 在 `validate_security_defaults` 中增加分布式模式安全检查：`ADMIN_SECRET_KEY` 和 `USER_SESSION_SECRET` 不得使用默认值
    - _需求: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 7.3, 12.1, 12.2, 12.5, 12.8, 13.3, 13.4, 13.9_

  - [ ]* 1.3 为配置模块编写属性测试
    - **属性 1: 部署模式判定一致性** — 对于任意 `DATABASE_URL` 和 `REDIS_URL` 组合，`is_distributed` 的返回值应与 `DATABASE_URL.startswith("postgresql") and bool(REDIS_URL)` 一致
    - **验证: 需求 1.4, 1.5**

- [x] 2. Redis 连接管理器
  - [x] 2.1 创建 `kiro_gateway/redis_manager.py`
    - 实现 `RedisManager` 类，包含连接池初始化、关闭、可用性检查
    - 实现 `get_client()` 方法，不可用时返回 None
    - 实现每 30 秒自动重连循环 `_reconnect_loop()`
    - 创建全局单例 `redis_manager`
    - _需求: 10.4_

  - [ ]* 2.2 为 Redis 连接管理器编写单元测试
    - 测试初始化和关闭流程
    - 测试连接不可用时 `get_client()` 返回 None
    - 测试重连逻辑
    - _需求: 10.4_

- [x] 3. 检查点 - 基础设施验证
  - 确保所有测试通过，如有问题请向用户确认。

- [x] 4. 数据库抽象层
  - [x] 4.1 创建 `kiro_gateway/db_backend.py`
    - 实现 `DatabaseBackend` 抽象基类，定义 `initialize`、`close`、`execute`、`fetch_one`、`fetch_all`、`transaction` 接口
    - 实现 `SQLiteBackend`，使用 `aiosqlite` 包装现有 SQLite 操作为异步接口
    - 实现 `PostgreSQLBackend`，使用 `SQLAlchemy async` + `asyncpg`，连接池由 `DB_POOL_SIZE` 和 `DB_MAX_OVERFLOW` 控制
    - 处理 Schema 差异：`AUTOINCREMENT` → `SERIAL`，`INTEGER`(布尔) → `BOOLEAN`，`REAL` → `DOUBLE PRECISION`
    - PostgreSQL 连接失败时记录错误日志并抛出包含连接目标地址的异常
    - _需求: 2.1, 2.2, 2.3, 2.4, 2.6, 2.9, 2.10_

  - [x] 4.2 重构 `kiro_gateway/database.py` 使用数据库抽象层
    - 将 `UserDatabase` 改为门面类，根据 `settings.is_distributed` 委托给对应后端
    - 移除 `threading.Lock`，改用数据库事务和连接池保证并发安全
    - 将所有同步方法改为 `async` 方法，保持接口签名兼容
    - 应用启动时自动创建所有必要的数据库表
    - 新增 `user_quotas`、`audit_logs`、`activity_logs`、`user_notifications` 表
    - 为 `tokens` 表添加扩展字段：`consecutive_fails`、`cooldown_until`、`consecutive_uses`、`risk_score`
    - _需求: 2.5, 2.6, 2.7, 2.8, 2.10, 13.13, 14.9_

  - [ ]* 4.3 为数据库抽象层编写属性测试
    - **属性 2: 数据往返一致性** — 写入数据库的数据通过读取后应与原始数据一致
    - **验证: 需求 2.7, 2.8**

  - [ ]* 4.4 为数据库抽象层编写单元测试
    - 测试 SQLiteBackend 的 CRUD 操作
    - 测试事务回滚行为
    - 测试表自动创建
    - _需求: 2.10_

- [x] 5. 认证缓存迁移
  - [x] 5.1 重构 `kiro_gateway/auth_cache.py` 实现双层缓存
    - 分布式模式：本地热缓存（最多 50 条）+ Redis 共享缓存（TTL 3600 秒）
    - 单节点模式：保持现有内存 OrderedDict LRU 缓存不变
    - 实现缓存查找流程：本地热缓存 → Redis → 创建新实例
    - 缓存更新时同时写入本地热缓存和 Redis
    - Redis 不可用时降级为仅本地热缓存，记录警告日志
    - Redis Key: `kirogate:auth_cache:{token_hash}`
    - _需求: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

  - [ ]* 5.2 为认证缓存编写单元测试
    - 测试本地热缓存命中
    - 测试 Redis 缓存命中和回填本地缓存
    - 测试 Redis 不可用时的降级行为
    - _需求: 3.6_

- [x] 6. 指标系统迁移
  - [x] 6.1 重构 `kiro_gateway/metrics.py` 支持 Redis 后端
    - 分布式模式：使用 Redis 原子计数器（INCR/INCRBY）存储累计指标
    - 使用 Redis Hash 存储分维度指标（按端点、状态码、模型）
    - 使用 Redis Sorted Set 存储延迟分位数数据（P50/P95/P99）
    - 使用 Redis List 存储最近 100 条请求记录
    - 使用 Redis Set 存储 IP 封禁列表
    - 使用 Redis String 存储全局开关（站点启用、自用模式等）
    - 使用 Redis Pipeline 批量写入指标
    - 单节点模式：保持现有内存字典和 SQLite 持久化不变
    - Redis 不可用时降级为本地内存计数，恢复后通过 INCRBY 同步
    - Prometheus 导出端点聚合所有节点指标
    - _需求: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10_

  - [ ]* 6.2 为指标系统编写属性测试
    - **属性 3: 计数器单调递增** — 对于任意序列的 `inc_request` 调用，总请求数应等于调用次数
    - **验证: 需求 4.1**

- [x] 7. 检查点 - 核心模块验证
  - 确保所有测试通过，如有问题请向用户确认。

- [x] 8. 分布式 Token 分配与防风控
  - [x] 8.1 重构 `kiro_gateway/token_allocator.py` 支持分布式分配
    - 分布式模式：使用 Redis Sorted Set 存储 Token 评分
    - 使用 Redis 分布式锁（SETNX + TTL 5s）进行 Token 分配
    - 获取锁失败（超时 2s）时降级为本地缓存评分
    - 每 30 秒从数据库同步 Token 状态到 Redis Sorted Set
    - 使用 Redis 原子操作（HINCRBY）更新跨节点成功/失败计数
    - 单节点模式：保持现有内存字典和 asyncio.Lock 不变
    - _需求: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8_

  - [x] 8.2 实现 Token 防风控策略
    - 使用 Redis 原子计数器（带 TTL）跟踪每个 Token 的 RPM（TTL 60s）、RPH（TTL 3600s）和并发数
    - Token 达到 RPM/RPH/并发上限时跳过该 Token
    - 请求转发前插入 0.5-3.0 秒随机延迟
    - 同一 Token 连续使用达到 `TOKEN_MAX_CONSECUTIVE_USES` 次后强制轮换
    - 实现指数退避冷却策略：连续失败 2 次 → 5 分钟冷却，3 次 → 30 分钟冷却，5 次 → 暂停
    - 所有 Token 受限时返回 429 + `Retry-After` 响应头
    - 请求完成后通过 DECR 释放并发计数
    - 在评分算法中引入风控安全因子（权重 30%）
    - _需求: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.8, 12.9, 12.10, 12.11, 12.12, 12.15, 12.16_

  - [ ]* 8.3 为 Token 分配编写属性测试
    - **属性 4: 并发安全** — 对于任意数量的并发分配请求，同一 Token 的并发使用数不超过 `TOKEN_MAX_CONCURRENT`
    - **验证: 需求 5.8, 12.5, 12.6**
    - **属性 5: 速率限制有效性** — 对于任意请求序列，单个 Token 的 RPM 不超过 `TOKEN_RPM_LIMIT`，RPH 不超过 `TOKEN_RPH_LIMIT`
    - **验证: 需求 12.3, 12.4**

  - [ ]* 8.4 为防风控策略编写单元测试
    - 测试冷却期触发和恢复
    - 测试连续使用强制轮换
    - 测试所有 Token 受限时返回 429
    - _需求: 12.10, 12.11, 12.12, 12.16_

- [x] 9. 健康检查器领导者选举
  - [x] 9.1 重构 `kiro_gateway/health_checker.py` 实现领导者选举
    - 分布式模式：使用 Redis 分布式锁，key 为 `kirogate:health_checker:leader`，TTL 60 秒
    - 每 30 秒尝试续约领导者锁
    - 持有锁时执行 Token 健康检查，未持有锁时待命
    - 领导者节点崩溃后锁在 60 秒内自动释放
    - 单节点模式：直接执行健康检查，无需领导者选举
    - 日志记录领导者状态变更
    - 每个 Token 检查时间添加 0-30 秒随机偏移
    - 相邻 Token 检查间隔至少 3 秒
    - _需求: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 12.13, 12.14_

  - [ ]* 9.2 为领导者选举编写属性测试
    - **属性 6: 领导者唯一性** — 在任意时刻，集群中最多只有一个节点持有领导者锁
    - **验证: 需求 6.1, 6.5**

  - [ ]* 9.3 为健康检查器编写单元测试
    - 测试锁获取和续约流程
    - 测试锁过期后的重新竞选
    - 测试检查间隔随机偏移
    - _需求: 6.2, 6.5, 12.13_

- [x] 10. 检查点 - 分布式核心功能验证
  - 确保所有测试通过，如有问题请向用户确认。

- [x] 11. 用户配额与会话一致性
  - [x] 11.1 实现用户配额管理
    - 在 `kiro_gateway/user_manager.py` 或新建模块中实现配额检查逻辑
    - 分布式模式：使用 Redis 原子计数器跟踪每日/每月请求数（带 TTL）
    - 单节点模式：使用 `user_quotas` 表跟踪
    - 用户达到每日/每月配额时返回 429 + 配额重置时间
    - API Key 达到 RPM 限制时返回 429
    - 在中间件中集成配额检查流程
    - _需求: 13.3, 13.4, 13.5, 13.6, 13.9, 13.10, 13.14_

  - [x] 11.2 实现会话一致性保障
    - 确保所有节点使用相同的 `ADMIN_SECRET_KEY`、`USER_SESSION_SECRET`、`TOKEN_ENCRYPT_KEY`
    - 通过 PostgreSQL 中 `users.session_version` 字段实现跨节点会话吊销
    - 管理员吊销会话时递增 `session_version`，所有节点验证时检查版本号
    - _需求: 7.1, 7.2, 7.4, 7.5_

  - [ ]* 11.3 为用户配额编写属性测试
    - **属性 7: 配额计数准确性** — 对于任意请求序列，用户的请求计数应等于实际请求次数
    - **验证: 需求 13.5, 13.6, 13.14**

  - [ ]* 11.4 为会话一致性编写单元测试
    - 测试跨节点 session cookie 验证
    - 测试会话吊销后旧 session 被拒绝
    - _需求: 7.2, 7.5_

- [x] 12. 用户面板功能
  - [x] 12.1 实现用户面板数据接口
    - 在 `kiro_gateway/routes.py` 中添加用户统计数据 API 端点
    - 返回总请求数、今日请求数、本月请求数、成功率、已捐赠 Token 数量
    - 返回配额使用情况（每日/每月剩余量）
    - 返回用户所捐赠 Token 的实时健康状态（状态、成功率、最后使用时间、Risk_Score）
    - 返回 API Key 列表（创建时间、最后使用时间、总请求数、状态）
    - 返回最近 50 条 API 请求记录
    - 返回未读通知列表
    - _需求: 13.1, 13.2, 13.7, 13.8, 13.13_

  - [x] 12.2 实现用户通知系统
    - Token 状态变更为暂停/无效时创建通知记录
    - 配额使用量达到 80% 时创建配额预警通知
    - 用户访问面板时展示未读通知
    - _需求: 13.11, 13.12_

  - [x] 12.3 更新 `kiro_gateway/pages.py` 用户面板页面
    - 添加使用统计展示区域
    - 添加配额进度条展示
    - 添加 Token 健康状态列表
    - 添加 API Key 管理列表
    - 添加最近请求记录表格
    - 添加通知提醒展示
    - _需求: 13.1, 13.2, 13.7, 13.8, 13.11, 13.12, 13.13_

- [x] 13. 管理面板功能
  - [x] 13.1 实现管理面板数据接口
    - 在 `kiro_gateway/routes.py` 中添加集群概览 API 端点
    - 返回在线节点数量、各节点健康状态、连接数、最近 1 分钟请求数
    - 返回全局 Token 池状态（Risk_Score、RPM/RPH 使用量、并发数、连续失败次数、状态）
    - 返回集群实时聚合指标（总请求数、成功率、平均延迟、P95/P99 延迟）
    - 实现节点心跳上报（每 10 秒向 Redis 写入心跳，TTL 30 秒）
    - _需求: 14.1, 14.2, 14.7_

  - [x] 13.2 实现 Token 风控管理接口
    - 实现手动暂停/恢复单个 Token 的 API 端点
    - 实现批量暂停所有高风险 Token（Risk_Score > 0.7）的 API 端点
    - 实现 Risk_Score 计算函数
    - 高风险 Token 在管理面板中以醒目样式标记
    - _需求: 14.3, 14.4, 14.5_

  - [x] 13.3 实现用户配额配置与批量管理接口
    - 实现管理员为单个用户设置自定义每日/每月配额的 API 端点
    - 实现批量审批待审核用户的 API 端点
    - 实现批量封禁违规用户的 API 端点
    - _需求: 14.6, 14.11_

  - [x] 13.4 实现审计日志系统
    - 实现 `log_audit()` 工具函数，记录管理员操作到 `audit_logs` 表
    - 在所有管理员操作中调用审计日志记录
    - 实现审计日志查询 API，支持按操作类型和时间范围筛选
    - _需求: 14.9, 14.10_

  - [x] 13.5 实现配置热重载功能
    - 实现 `POST /admin/config/reload` API 端点
    - 将可热重载配置存储在 Redis Hash `kirogate:config:hot_reload` 中
    - 通过 Redis Pub/Sub（频道 `kirogate:config_reload`）通知所有节点更新配置
    - 每个节点启动时订阅配置更新频道
    - 所有节点在 10 秒内应用新配置
    - _需求: 14.12, 14.13_

  - [x] 13.6 更新 `kiro_gateway/pages.py` 管理面板页面
    - 添加集群概览展示区域
    - 添加 Token 风控仪表盘（Risk_Score、使用量、状态）
    - 添加 Token 管理操作按钮（暂停、恢复、批量暂停）
    - 添加用户配额配置界面
    - 添加集群实时指标展示
    - 添加审计日志列表（支持筛选）
    - 添加批量用户管理界面
    - 添加配置热重载界面
    - _需求: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 14.10, 14.11, 14.12_

  - [ ]* 13.7 为审计日志编写单元测试
    - 测试审计日志记录完整性
    - 测试按操作类型和时间范围筛选
    - _需求: 14.9, 14.10_

- [x] 14. 检查点 - 用户与管理功能验证
  - 确保所有测试通过，如有问题请向用户确认。

- [x] 15. Docker 编排与负载均衡
  - [x] 15.1 创建 `docker-compose.distributed.yml`
    - 定义 PostgreSQL 服务（postgres:16-alpine），配置持久化存储卷和健康检查
    - 定义 Redis 服务（redis:7-alpine），配置 AOF 持久化和存储卷
    - 定义 KiroGate 应用服务，配置 3 副本（支持环境变量调整）
    - 定义 Nginx 服务，挂载 nginx.conf
    - 配置服务启动依赖顺序：PostgreSQL → Redis → KiroGate → Nginx
    - 配置所有节点共享的环境变量（DATABASE_URL、REDIS_URL、密钥等）
    - 保留现有 `docker-compose.yml` 不变
    - _需求: 8.1, 8.2, 8.7, 8.8, 8.9_

  - [x] 15.2 创建 `nginx.conf`
    - 配置 `least_conn` 负载均衡策略
    - 配置 `/health` 端点健康检查
    - 配置 WebSocket 连接升级（支持 SSE 流式响应）
    - 配置反向代理头（Host、X-Real-IP、X-Forwarded-For）
    - 设置 `proxy_read_timeout 300s` 和 `proxy_buffering off`
    - _需求: 8.3, 8.4, 8.5, 8.6_

- [x] 16. 数据迁移脚本
  - [x] 16.1 创建 `migrate_sqlite_to_pg.py`
    - 连接源 SQLite 数据库和目标 PostgreSQL 数据库
    - 按表顺序导出数据（users → tokens → api_keys → ...）
    - 使用 PostgreSQL 批量导入
    - 迁移过程中显示进度（已迁移表/总表数，已迁移行数）
    - 任何表迁移失败时回滚整个事务，显示失败表名和错误原因
    - _需求: 10.7, 14.14, 14.15_

  - [ ]* 16.2 为数据迁移脚本编写单元测试
    - 测试正常迁移流程
    - 测试迁移失败时的回滚行为
    - _需求: 14.15_

- [x] 17. 应用生命周期管理与向后兼容
  - [x] 17.1 更新 `main.py` 的 lifespan 函数
    - 实现启动顺序：数据库连接池 → Redis 连接池 → 指标系统 → 认证缓存 → Token 分配器 → 健康检查器 → 配置热重载订阅 → 节点心跳上报
    - 实现关闭顺序（反序）：心跳 → 配置订阅 → 健康检查器（释放领导者锁）→ Token 分配器 → 认证缓存 → 指标系统（刷新待写入数据）→ Redis → 数据库
    - 启动日志输出当前部署模式及连接的外部服务地址
    - 配置 `--timeout-graceful-shutdown 30` 支持优雅关闭
    - 关闭过程中 `/health` 返回 503
    - _需求: 11.1, 11.2, 11.3, 11.4, 11.5, 10.3_

  - [x] 17.2 扩展 `/health` 端点
    - 返回 PostgreSQL 和 Redis 连接状态
    - 返回部署模式、节点 ID、运行时间
    - 单节点模式下不显示 PostgreSQL 和 Redis 字段
    - PostgreSQL 连接断开时返回 503 + 每 10 秒重连
    - Redis 连接断开时降级为本地实现 + 每 30 秒重连
    - _需求: 10.1, 10.2, 10.5, 10.6_

  - [ ]* 17.3 为生命周期管理编写单元测试
    - 测试启动和关闭顺序
    - 测试健康检查端点在不同状态下的响应
    - _需求: 11.1, 11.2, 11.5_

- [x] 18. 最终检查点 - 全面验证
  - 确保所有测试通过，如有问题请向用户确认。

## 备注

- 标记 `*` 的任务为可选任务，可跳过以加速 MVP 交付
- 每个任务引用了具体的需求编号，确保可追溯性
- 检查点任务用于阶段性验证，确保增量开发的正确性
- 属性测试验证通用正确性属性，单元测试验证具体示例和边界情况
- 设计文档使用 Python 语言，所有实现任务均使用 Python
