# 需求文档：KiroGate 分布式部署

## 简介

KiroGate 当前为单节点架构，使用 SQLite 数据库、内存缓存和本地状态管理。为支持 10 万并发在线用户，需要将系统迁移为分布式多节点架构。核心改造包括：数据库迁移至 PostgreSQL、缓存迁移至 Redis、指标系统分布式化、Token 分配器支持跨节点并发安全、健康检查器实现领导者选举，以及通过 Docker Compose 和 Nginx 实现多节点编排与负载均衡。同时需保留单节点部署模式的向后兼容性。此外，系统需要实现 Token 防风控策略以防止 Kiro 账号被风控标记，提供完善的用户端管理功能（使用统计、配额管理、Token 状态监控），以及管理端管理功能（集群概览、全局 Token 池风控、用户配额配置、审计日志）。

## 术语表

- **KiroGate**：基于 FastAPI 的 API 网关，将 Kiro IDE API 代理为 OpenAI/Anthropic 兼容接口
- **Node（节点）**：运行 KiroGate 应用实例的单个容器或进程
- **Cluster（集群）**：由多个 Node 组成的 KiroGate 分布式部署
- **PostgreSQL**：关系型数据库，用于替代 SQLite 作为共享持久化存储
- **Redis**：内存数据存储，用于分布式缓存、原子计数器、分布式锁和会话共享
- **Token**：用户捐赠的 Kiro refresh token，用于代理 API 请求
- **Token_Allocator（Token 分配器）**：根据评分算法选择最优 Token 的模块
- **Health_Checker（健康检查器）**：后台任务，定期验证 Token 有效性
- **Auth_Cache（认证缓存）**：缓存 KiroAuthManager 实例的 LRU 缓存模块
- **Metrics（指标系统）**：收集和导出请求统计、延迟、Token 用量等运行指标的模块
- **Leader_Election（领导者选举）**：通过 Redis 分布式锁确保集群中仅一个节点执行特定任务的机制
- **Settings（配置模块）**：基于 Pydantic Settings 的集中配置管理模块
- **UserDatabase（用户数据库）**：管理用户、Token、API Key 等数据的 CRUD 模块
- **RPM（Requests Per Minute）**：每分钟请求数，用于 Token 级别的速率限制
- **RPH（Requests Per Hour）**：每小时请求数，用于 Token 级别的速率限制
- **Quota（配额）**：分配给用户的请求使用限额，按日或按月计算
- **Risk_Score（风险评分）**：基于 Token 使用频率、失败率、并发数等因素计算的风险指标，用于评估 Token 被风控的概率
- **Cooldown（冷却期）**：Token 在连续失败后被暂停使用的时间段，采用指数退避策略
- **Exponential_Backoff（指数退避）**：失败后逐步增加等待时间的策略，避免对上游服务造成持续压力
- **Audit_Log（审计日志）**：记录管理员操作的日志系统，包含操作人、操作类型、操作时间和操作详情
- **Activity_Log（活动日志）**：记录用户 API 请求的日志，包含请求模型、状态、延迟等信息
- **Hot_Reload（热重载）**：在不重启服务的情况下动态更新系统配置的能力

## 需求

### 需求 1：配置模块扩展

**用户故事：** 作为运维人员，我希望通过环境变量配置数据库和 Redis 连接信息，以便灵活切换单节点和分布式部署模式。

#### 验收标准

1. THE Settings SHALL 提供 `DATABASE_URL` 配置项，默认值为 `sqlite:///data/kirogate.db`，支持 PostgreSQL 连接字符串格式 `postgresql+asyncpg://user:pass@host:port/dbname`
2. THE Settings SHALL 提供 `REDIS_URL` 配置项，默认值为空字符串，支持 Redis 连接字符串格式 `redis://host:port/db`
3. THE Settings SHALL 提供 `NODE_ID` 配置项，用于标识当前节点，默认值为自动生成的 UUID
4. WHEN `DATABASE_URL` 以 `sqlite` 开头时，THE Settings SHALL 将部署模式识别为单节点模式
5. WHEN `DATABASE_URL` 以 `postgresql` 开头且 `REDIS_URL` 非空时，THE Settings SHALL 将部署模式识别为分布式模式
6. THE Settings SHALL 提供 `DB_POOL_SIZE` 配置项，默认值为 20，用于控制 PostgreSQL 连接池大小
7. THE Settings SHALL 提供 `DB_MAX_OVERFLOW` 配置项，默认值为 10，用于控制连接池最大溢出连接数
8. THE Settings SHALL 提供 `REDIS_MAX_CONNECTIONS` 配置项，默认值为 50，用于控制 Redis 连接池大小

### 需求 2：数据库迁移（SQLite → PostgreSQL）

**用户故事：** 作为运维人员，我希望系统使用 PostgreSQL 作为共享数据库，以便多个节点可以同时读写同一份数据。

#### 验收标准

1. WHEN 部署模式为分布式模式时，THE UserDatabase SHALL 使用 SQLAlchemy async + asyncpg 连接 PostgreSQL 数据库
2. WHEN 部署模式为单节点模式时，THE UserDatabase SHALL 继续使用 SQLite 数据库，保持现有行为不变
3. THE UserDatabase SHALL 使用连接池管理 PostgreSQL 连接，池大小由 `DB_POOL_SIZE` 和 `DB_MAX_OVERFLOW` 配置控制
4. THE UserDatabase SHALL 将所有 SQLite 的 `AUTOINCREMENT` 主键替换为 PostgreSQL 的 `SERIAL` 类型
5. THE UserDatabase SHALL 移除 `threading.Lock`，改用数据库事务和连接池保证并发安全
6. THE UserDatabase SHALL 将所有同步数据库操作改为异步操作（async/await）
7. WHEN 多个节点同时写入同一条记录时，THE UserDatabase SHALL 通过数据库事务保证数据一致性，避免脏写
8. THE UserDatabase SHALL 保持所有现有 CRUD 方法的接口签名兼容，仅将返回类型改为协程
9. IF PostgreSQL 连接失败，THEN THE UserDatabase SHALL 记录错误日志并抛出明确的连接异常，包含连接目标地址信息
10. THE UserDatabase SHALL 在应用启动时自动创建所有必要的数据库表（如表不存在）

### 需求 3：认证缓存迁移（内存 → Redis）

**用户故事：** 作为用户，我希望在任意节点登录后，后续请求被路由到其他节点时仍然保持认证状态。

#### 验收标准

1. WHEN 部署模式为分布式模式时，THE Auth_Cache SHALL 使用 Redis 作为共享缓存后端存储 KiroAuthManager 的序列化状态
2. WHEN 部署模式为单节点模式时，THE Auth_Cache SHALL 继续使用内存 OrderedDict LRU 缓存
3. THE Auth_Cache SHALL 在每个节点维护本地热缓存（最多 50 个条目），减少 Redis 访问频率
4. WHEN 本地热缓存未命中时，THE Auth_Cache SHALL 从 Redis 获取缓存数据并更新本地热缓存
5. THE Auth_Cache SHALL 为 Redis 中的每个缓存条目设置 TTL（默认 3600 秒），防止过期数据堆积
6. IF Redis 连接不可用，THEN THE Auth_Cache SHALL 降级为仅使用本地热缓存，并记录警告日志
7. WHEN 缓存条目被更新时，THE Auth_Cache SHALL 同时更新本地热缓存和 Redis 中的数据

### 需求 4：指标系统迁移（内存 + SQLite → Redis）

**用户故事：** 作为管理员，我希望在管理面板中看到所有节点的聚合指标，而非单个节点的局部数据。

#### 验收标准

1. WHEN 部署模式为分布式模式时，THE Metrics SHALL 使用 Redis 原子计数器（INCR/INCRBY）存储请求计数、错误计数、重试计数等累计指标
2. WHEN 部署模式为单节点模式时，THE Metrics SHALL 继续使用内存字典和 SQLite 持久化
3. THE Metrics SHALL 使用 Redis Hash 存储每个端点、状态码、模型的分维度指标
4. THE Metrics SHALL 使用 Redis Sorted Set 存储延迟分位数数据，支持 P50/P95/P99 计算
5. THE Metrics SHALL 使用 Redis List 存储最近请求记录（最多 100 条），支持管理面板展示
6. THE Metrics SHALL 将 IP 封禁列表存储在 Redis Set 中，确保所有节点共享封禁状态
7. THE Metrics SHALL 将站点启用/禁用、自用模式等全局开关存储在 Redis 中，确保所有节点状态一致
8. THE Metrics SHALL 在 Prometheus 导出端点中聚合所有节点的指标数据
9. IF Redis 连接不可用，THEN THE Metrics SHALL 降级为本地内存计数，并在 Redis 恢复后尝试同步累计值
10. THE Metrics SHALL 使用 Redis Pipeline 批量写入指标，减少网络往返次数

### 需求 5：分布式 Token 分配

**用户故事：** 作为用户，我希望在高并发场景下，多个节点不会同时将同一个 Token 分配给不同请求，导致 Token 过载或冲突。

#### 验收标准

1. WHEN 部署模式为分布式模式时，THE Token_Allocator SHALL 使用 Redis Sorted Set 存储 Token 评分，以 Token ID 为成员、评分为分数
2. THE Token_Allocator SHALL 在分配 Token 时使用 Redis 分布式锁（SETNX + TTL），锁的持有时间上限为 5 秒
3. WHEN 获取分布式锁成功时，THE Token_Allocator SHALL 从 Redis Sorted Set 中选取评分最高的 Token 并更新其使用计数
4. IF 获取分布式锁失败（超时 2 秒），THEN THE Token_Allocator SHALL 降级为从本地缓存的评分数据中选取 Token，并记录警告日志
5. THE Token_Allocator SHALL 每 30 秒从数据库同步一次 Token 状态到 Redis Sorted Set，更新评分
6. WHEN 部署模式为单节点模式时，THE Token_Allocator SHALL 继续使用内存字典和 asyncio.Lock
7. THE Token_Allocator SHALL 在 Token 使用后通过 Redis 原子操作（HINCRBY）更新成功/失败计数，确保跨节点计数准确
8. FOR ALL 并发 Token 分配请求，THE Token_Allocator SHALL 保证同一时刻同一 Token 的并发使用数不超过配置的上限值

### 需求 6：健康检查器领导者选举

**用户故事：** 作为运维人员，我希望集群中仅有一个节点执行 Token 健康检查，避免多个节点重复检查浪费资源和触发速率限制。

#### 验收标准

1. WHEN 部署模式为分布式模式时，THE Health_Checker SHALL 使用 Redis 分布式锁实现领导者选举，锁的 key 为 `kirogate:health_checker:leader`
2. THE Health_Checker SHALL 每 30 秒尝试续约领导者锁，锁的 TTL 为 60 秒
3. WHEN 当前节点持有领导者锁时，THE Health_Checker SHALL 执行 Token 健康检查任务
4. WHEN 当前节点未持有领导者锁时，THE Health_Checker SHALL 处于待命状态，每 30 秒尝试获取锁
5. IF 持有领导者锁的节点崩溃，THEN 领导者锁 SHALL 在 TTL 到期后（60 秒内）自动释放，其他节点 SHALL 竞争获取锁
6. WHEN 部署模式为单节点模式时，THE Health_Checker SHALL 直接执行健康检查，无需领导者选举
7. THE Health_Checker SHALL 在日志中记录领导者状态变更（获取锁、续约成功、续约失败、释放锁）

### 需求 7：会话一致性

**用户故事：** 作为用户，我希望在一个节点登录后，请求被负载均衡到其他节点时，会话仍然有效，无需重新登录。

#### 验收标准

1. THE Cluster SHALL 确保所有节点使用相同的 `ADMIN_SECRET_KEY`、`USER_SESSION_SECRET` 和 `TOKEN_ENCRYPT_KEY` 配置值
2. WHEN 用户在任意节点完成 OAuth 登录后，THE Cluster 中的任意其他节点 SHALL 能够验证该用户的 session cookie
3. THE Settings SHALL 在分布式模式启动时验证 `ADMIN_SECRET_KEY` 和 `USER_SESSION_SECRET` 未使用默认值，验证失败时拒绝启动
4. THE Cluster SHALL 通过共享 PostgreSQL 数据库中的 session_version 字段实现跨节点的会话吊销功能
5. WHEN 管理员在任意节点吊销用户会话时，THE Cluster 中的所有节点 SHALL 在下次请求验证时拒绝该用户的旧会话

### 需求 8：Docker 编排与负载均衡

**用户故事：** 作为运维人员，我希望通过一个 Docker Compose 文件即可启动完整的分布式集群，包括数据库、缓存和多个应用节点。

#### 验收标准

1. THE Cluster SHALL 提供 `docker-compose.distributed.yml` 文件，包含 PostgreSQL、Redis、Nginx 和多个 KiroGate 应用节点的服务定义
2. THE `docker-compose.distributed.yml` SHALL 将 KiroGate 应用服务配置为 3 个副本（replicas: 3），支持通过环境变量调整副本数
3. THE Cluster SHALL 提供 `nginx.conf` 配置文件，实现对多个 KiroGate 节点的 HTTP 反向代理和负载均衡
4. THE `nginx.conf` SHALL 使用 `least_conn` 负载均衡策略，将请求分发到连接数最少的节点
5. THE `nginx.conf` SHALL 配置 `/health` 端点的健康检查，自动将不健康的节点从负载均衡池中移除
6. THE `nginx.conf` SHALL 支持 WebSocket 连接升级，确保流式响应（SSE）正常工作
7. THE `docker-compose.distributed.yml` SHALL 为 PostgreSQL 和 Redis 配置持久化存储卷
8. THE `docker-compose.distributed.yml` SHALL 配置服务启动依赖顺序：PostgreSQL → Redis → KiroGate → Nginx
9. THE Cluster SHALL 保留现有的 `docker-compose.yml` 不变，用于单节点部署模式

### 需求 9：依赖管理

**用户故事：** 作为开发者，我希望分布式部署所需的新依赖被正确声明，以便 Docker 构建和本地开发环境能正确安装。

#### 验收标准

1. THE 项目 SHALL 在 `requirements.txt` 中添加 `asyncpg>=0.29.0` 依赖，用于 PostgreSQL 异步连接
2. THE 项目 SHALL 在 `requirements.txt` 中添加 `sqlalchemy[asyncio]>=2.0.0` 依赖，用于异步 ORM 和连接池管理
3. THE 项目 SHALL 在 `requirements.txt` 中添加 `redis[hiredis]>=5.0.0` 依赖，用于 Redis 连接和高性能解析
4. THE 项目 SHALL 确保新增依赖不与现有依赖产生版本冲突

### 需求 10：向后兼容与优雅降级

**用户故事：** 作为现有用户，我希望升级到新版本后，不配置 PostgreSQL 和 Redis 时，系统仍然以单节点模式正常运行，无需修改任何现有配置。

#### 验收标准

1. WHEN `DATABASE_URL` 未配置或以 `sqlite` 开头时，THE KiroGate SHALL 以单节点模式运行，使用 SQLite 和内存缓存，行为与升级前完全一致
2. WHEN `REDIS_URL` 未配置时，THE KiroGate SHALL 禁用所有依赖 Redis 的分布式功能（分布式锁、共享缓存、共享指标），回退到本地实现
3. THE KiroGate SHALL 在启动日志中明确输出当前部署模式（单节点模式或分布式模式）及连接的外部服务地址
4. IF 运行过程中 Redis 连接断开，THEN THE KiroGate SHALL 将所有 Redis 依赖功能降级为本地实现，并每 30 秒尝试重新连接
5. IF 运行过程中 PostgreSQL 连接断开，THEN THE KiroGate SHALL 返回 503 Service Unavailable 响应，并每 10 秒尝试重新连接
6. THE KiroGate SHALL 在 `/health` 端点中报告 PostgreSQL 和 Redis 的连接状态
7. WHEN 从单节点模式迁移到分布式模式时，THE KiroGate SHALL 提供数据迁移脚本，将 SQLite 中的现有数据导入 PostgreSQL

### 需求 11：应用生命周期管理

**用户故事：** 作为运维人员，我希望应用启动时正确初始化所有分布式组件，关闭时正确释放资源，支持零停机滚动更新。

#### 验收标准

1. WHEN 应用启动时，THE KiroGate SHALL 按以下顺序初始化组件：数据库连接池 → Redis 连接池 → 指标系统 → 认证缓存 → Token 分配器 → 健康检查器
2. WHEN 应用关闭时，THE KiroGate SHALL 按以下顺序释放资源：健康检查器（释放领导者锁）→ Token 分配器 → 认证缓存 → 指标系统（刷新待写入数据）→ Redis 连接池 → 数据库连接池
3. THE KiroGate SHALL 在收到 SIGTERM 信号后，等待当前正在处理的请求完成（最多 30 秒），然后执行优雅关闭
4. WHEN 执行滚动更新时，THE Cluster SHALL 确保至少有一个节点处于健康状态，通过 Nginx 健康检查自动摘除正在关闭的节点
5. THE KiroGate SHALL 在启动完成后向 `/health` 端点返回 200 状态码，在关闭过程中返回 503 状态码

### 需求 12：Token 防风控策略

**用户故事：** 作为运维人员，我希望系统对 Token 的使用频率和模式进行智能控制，以便降低 Kiro 账号被上游风控系统标记或封禁的风险。

#### 验收标准

1. THE Settings SHALL 提供 `TOKEN_RPM_LIMIT` 配置项（默认值为 10），用于控制单个 Token 每分钟最大请求数
2. THE Settings SHALL 提供 `TOKEN_RPH_LIMIT` 配置项（默认值为 200），用于控制单个 Token 每小时最大请求数
3. WHEN 单个 Token 的当前分钟请求数达到 `TOKEN_RPM_LIMIT` 时，THE Token_Allocator SHALL 跳过该 Token 并选择下一个可用 Token
4. WHEN 单个 Token 的当前小时请求数达到 `TOKEN_RPH_LIMIT` 时，THE Token_Allocator SHALL 跳过该 Token 并选择下一个可用 Token
5. THE Settings SHALL 提供 `TOKEN_MAX_CONCURRENT` 配置项（默认值为 2），用于控制单个 Token 的最大并发请求数
6. WHEN 单个 Token 的当前并发请求数达到 `TOKEN_MAX_CONCURRENT` 时，THE Token_Allocator SHALL 跳过该 Token 并选择下一个可用 Token
7. THE Token_Allocator SHALL 在转发请求前插入随机延迟，延迟范围为 0.5 秒至 3.0 秒（均匀分布）
8. THE Settings SHALL 提供 `TOKEN_MAX_CONSECUTIVE_USES` 配置项（默认值为 5），用于控制同一 Token 连续使用的最大次数
9. WHEN 同一 Token 连续被分配 `TOKEN_MAX_CONSECUTIVE_USES` 次后，THE Token_Allocator SHALL 强制轮换到下一个可用 Token
10. WHEN 单个 Token 连续失败 2 次时，THE Token_Allocator SHALL 将该 Token 置于 5 分钟冷却期
11. WHEN 单个 Token 连续失败 3 次时，THE Token_Allocator SHALL 将该 Token 置于 30 分钟冷却期
12. WHEN 单个 Token 连续失败达到 5 次时，THE Token_Allocator SHALL 将该 Token 标记为暂停状态，等待管理员手动恢复或下次健康检查通过后自动恢复
13. THE Health_Checker SHALL 在每次检查周期中为各 Token 的检查时间添加随机偏移量（0 至 30 秒），避免所有 Token 同时被检查
14. THE Health_Checker SHALL 将相邻 Token 的检查间隔设置为至少 3 秒，避免短时间内对上游发送大量验证请求
15. WHEN 部署模式为分布式模式时，THE Token_Allocator SHALL 使用 Redis 原子计数器（带 TTL）跟踪每个 Token 的 RPM、RPH 和并发数，确保跨节点计数准确
16. IF 所有可用 Token 均已达到速率限制或处于冷却期，THEN THE Token_Allocator SHALL 返回 429 Too Many Requests 响应，并在响应头中包含 `Retry-After` 字段，值为最近一个 Token 解除限制的预计秒数

### 需求 13：用户端管理

**用户故事：** 作为用户，我希望在用户面板中查看个人使用统计、管理 Token 和 API Key，并及时收到 Token 状态变更通知，以便掌握自己的资源使用情况。

#### 验收标准

1. THE 用户面板 SHALL 展示当前用户的使用统计信息，包括总请求数、今日请求数、本月请求数、总体成功率和已捐赠 Token 数量
2. THE 用户面板 SHALL 展示当前用户的配额使用情况，包括每日配额剩余量和每月配额剩余量，以进度条形式直观显示
3. THE Settings SHALL 提供 `DEFAULT_USER_DAILY_QUOTA` 配置项（默认值为 500），用于设置用户默认每日请求配额
4. THE Settings SHALL 提供 `DEFAULT_USER_MONTHLY_QUOTA` 配置项（默认值为 10000），用于设置用户默认每月请求配额
5. WHEN 用户的每日请求数达到其每日配额时，THE KiroGate SHALL 拒绝该用户的后续请求并返回 429 状态码，响应体中包含配额重置时间
6. WHEN 用户的每月请求数达到其每月配额时，THE KiroGate SHALL 拒绝该用户的后续请求并返回 429 状态码，响应体中包含配额重置时间
7. THE 用户面板 SHALL 展示当前用户所捐赠 Token 的实时健康状态，包括每个 Token 的状态（正常、冷却中、暂停）、成功率、最后使用时间和当前 Risk_Score
8. THE 用户面板 SHALL 展示当前用户的 API Key 列表，每个 Key 显示创建时间、最后使用时间、总请求数和当前状态（启用/禁用）
9. THE Settings SHALL 提供 `DEFAULT_KEY_RPM_LIMIT` 配置项（默认值为 30），用于设置单个 API Key 的默认每分钟请求限制
10. WHEN 单个 API Key 的当前分钟请求数达到其 RPM 限制时，THE KiroGate SHALL 拒绝该 Key 的后续请求并返回 429 状态码
11. WHEN 用户捐赠的 Token 状态变更为暂停或无效时，THE 用户面板 SHALL 在用户下次访问时展示通知提醒
12. WHEN 用户的配额使用量达到 80% 时，THE 用户面板 SHALL 在用户下次访问时展示配额预警通知
13. THE 用户面板 SHALL 展示当前用户最近 50 条 API 请求记录，每条记录包含请求时间、使用模型、响应状态码和请求延迟（毫秒）
14. WHEN 部署模式为分布式模式时，THE KiroGate SHALL 使用 Redis 原子计数器（带 TTL）跟踪每个用户和每个 API Key 的请求计数，确保跨节点配额和速率限制准确

### 需求 14：管理端管理

**用户故事：** 作为管理员，我希望在管理面板中查看集群整体状态、管理 Token 风控、配置用户配额、查看审计日志，以便全面掌控分布式系统的运行状况。

#### 验收标准

1. THE 管理面板 SHALL 展示集群概览信息，包括在线节点数量、各节点健康状态、各节点的当前连接数和最近 1 分钟请求数
2. THE 管理面板 SHALL 展示全局 Token 池状态，每个 Token 显示当前 Risk_Score、RPM 使用量/上限、RPH 使用量/上限、并发数/上限、连续失败次数和当前状态（正常、冷却中、暂停）
3. WHEN Token 的 Risk_Score 超过预设阈值（默认 0.7）时，THE 管理面板 SHALL 以醒目样式标记该 Token 为高风险
4. THE 管理面板 SHALL 提供手动暂停和恢复单个 Token 的操作按钮
5. THE 管理面板 SHALL 提供批量暂停所有高风险 Token 的操作按钮
6. THE 管理面板 SHALL 提供用户配额配置功能，管理员可为单个用户设置自定义的每日配额和每月配额，覆盖默认值
7. THE 管理面板 SHALL 展示集群实时指标，包括聚合的总请求数、总成功率、平均延迟、P95 延迟和 P99 延迟，数据来源为所有节点的汇总
8. THE 管理面板 SHALL 展示 Token 风险评分趋势图，显示各 Token 最近 24 小时的 Risk_Score 变化
9. THE 管理面板 SHALL 记录所有管理员操作到审计日志，每条记录包含操作管理员用户名、操作类型、操作目标、操作详情和操作时间
10. THE 管理面板 SHALL 展示审计日志列表，支持按操作类型和时间范围筛选
11. THE 管理面板 SHALL 提供批量用户管理功能，支持批量审批待审核用户和批量封禁违规用户
12. THE 管理面板 SHALL 提供系统配置热重载功能，管理员修改 Token 防风控阈值、用户默认配额等配置后，所有节点 SHALL 在 10 秒内应用新配置，无需重启服务
13. WHEN 管理员触发配置热重载时，THE KiroGate SHALL 通过 Redis Pub/Sub 通知所有节点更新本地配置缓存
14. THE 管理面板 SHALL 提供数据库迁移工具，支持从 SQLite 导出数据并导入到 PostgreSQL，迁移过程中显示进度和迁移结果
15. IF 数据库迁移过程中发生错误，THEN THE 管理面板 SHALL 回滚已导入的数据并显示详细的错误信息，包含失败的数据表名和错误原因
