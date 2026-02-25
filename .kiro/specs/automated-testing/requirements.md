# Requirements Document

## Introduction

本文档定义了 GeekGate 项目自动化测试系统的需求规格。GeekGate 是一个 Python 项目，提供 OpenAI/Anthropic 兼容的 Kiro API 网关服务。自动化测试系统将覆盖单元测试、集成测试和 API 端点测试，使用 pytest 作为测试框架。

## Glossary

- **Test_Runner**: pytest 测试执行器，负责发现和运行测试用�?
- **Unit_Test**: 单元测试模块，测试单个函数或类的独立功能
- **Integration_Test**: 集成测试模块，测试多个组件之间的交互
- **API_Test**: API 端点测试模块，测�?HTTP 接口的请求和响应
- **Test_Fixture**: pytest 测试夹具，提供测试所需的预置数据和环境
- **Mock_Object**: 模拟对象，用于隔离被测代码的外部依赖
- **Test_Database**: 测试数据库，使用内存 SQLite 进行隔离测试
- **Test_Client**: FastAPI 测试客户端，用于模拟 HTTP 请求
- **Coverage_Reporter**: 代码覆盖率报告器，统计测试覆盖的代码行数

## Requirements

### Requirement 1: 测试框架配置

**User Story:** As a 开发�? I want 配置 pytest 测试框架, so that 可以统一管理和运行所有测试用�?

#### Acceptance Criteria

1. THE Test_Runner SHALL 支持通过 `pytest` 命令发现并运�?`tests/` 目录下的所有测试文�?
2. THE Test_Runner SHALL 支持通过 `-v` 参数显示详细的测试执行信�?
3. THE Test_Runner SHALL 支持通过 `-k` 参数按名称过滤测试用�?
4. THE Test_Runner SHALL 支持通过 `--cov` 参数生成代码覆盖率报�?
5. WHEN 测试执行完成, THE Coverage_Reporter SHALL 生成 HTML 格式的覆盖率报告�?`htmlcov/` 目录

### Requirement 2: 数据库单元测�?

**User Story:** As a 开发�? I want 测试数据库操作模�? so that 确保用户、Token、API Key 等数据的 CRUD 操作正确

#### Acceptance Criteria

1. THE Unit_Test SHALL 使用内存 SQLite 作为 Test_Database 进行隔离测试
2. WHEN 调用 `create_user` 方法, THE Test_Database SHALL 创建用户记录并返回包含正确字段的 User 对象
3. WHEN 调用 `get_user` 方法并传入有效用�?ID, THE Test_Database SHALL 返回对应�?User 对象
4. WHEN 调用 `get_user` 方法并传入无效用�?ID, THE Test_Database SHALL 返回 None
5. WHEN 调用 `create_api_key` 方法, THE Test_Database SHALL 生成唯一�?API Key 并存储哈希�?
6. WHEN 调用 `verify_api_key` 方法并传入有�?API Key, THE Test_Database SHALL 返回对应的用�?ID �?APIKey 对象
7. WHEN 调用 `verify_api_key` 方法并传入无�?API Key, THE Test_Database SHALL 返回 None
8. WHEN 调用 `donate_token` 方法, THE Test_Database SHALL 加密存储 refresh token 并返�?DonatedToken 对象
9. FOR ALL 有效�?DonatedToken 对象, 解密后的 token �?SHALL 与原始捐赠的 token 值相�?(round-trip property)

### Requirement 3: 用户管理单元测试

**User Story:** As a 开发�? I want 测试用户管理模块, so that 确保会话管理、密码验证和 OAuth 流程正确

#### Acceptance Criteria

1. WHEN 调用 `create_session` 方法, THE UserSessionManager SHALL 生成包含用户 ID 和会话版本的签名 token
2. WHEN 调用 `verify_session` 方法并传入有�?token, THE UserSessionManager SHALL 返回对应的用�?ID
3. WHEN 调用 `verify_session` 方法并传入过�?token, THE UserSessionManager SHALL 返回 None
4. WHEN 调用 `verify_session` 方法并传入会话版本不匹配�?token, THE UserSessionManager SHALL 返回 None
5. WHEN 调用 `_hash_password` 方法, THE UserManager SHALL 生成 PBKDF2 格式的密码哈�?
6. WHEN 调用 `_verify_password` 方法并传入正确密�? THE UserManager SHALL 返回 True
7. WHEN 调用 `_verify_password` 方法并传入错误密�? THE UserManager SHALL 返回 False
8. FOR ALL 有效密码字符�? `_verify_password(_hash_password(password), password)` SHALL 返回 True (round-trip property)

### Requirement 4: 认证模块单元测试

**User Story:** As a 开发�? I want 测试认证模块, so that 确保 Token 刷新和过期检测逻辑正确

#### Acceptance Criteria

1. WHEN Token 距离过期时间小于 TOKEN_REFRESH_THRESHOLD, THE GeekAuthManager.is_token_expiring_soon SHALL 返回 True
2. WHEN Token 距离过期时间大于 TOKEN_REFRESH_THRESHOLD, THE GeekAuthManager.is_token_expiring_soon SHALL 返回 False
3. WHEN 未设置过期时�? THE GeekAuthManager.is_token_expiring_soon SHALL 返回 True
4. WHEN 提供 client_id �?client_secret, THE GeekAuthManager SHALL 检测为 IDC 认证类型
5. WHEN 未提�?client_id �?client_secret, THE GeekAuthManager SHALL 检测为 SOCIAL 认证类型

### Requirement 5: 配置模块单元测试

**User Story:** As a 开发�? I want 测试配置模块, so that 确保环境变量加载和模型映射正�?

#### Acceptance Criteria

1. WHEN 调用 `get_internal_model_id` 并传入有效外部模型名, THE config SHALL 返回对应的内部模�?ID
2. WHEN 调用 `get_internal_model_id` 并传入无效模型名, THE config SHALL 抛出 ValueError 异常
3. WHEN 调用 `get_adaptive_timeout` 并传入慢模型名称, THE config SHALL 返回基础超时乘以 SLOW_MODEL_TIMEOUT_MULTIPLIER
4. WHEN 调用 `get_adaptive_timeout` 并传入普通模型名�? THE config SHALL 返回基础超时�?
5. THE Settings SHALL 验证 log_level 为有效的日志级别�?
6. THE Settings SHALL 验证 debug_mode �?off、errors �?all 之一

### Requirement 6: 数据库集成测�?

**User Story:** As a 开发�? I want 测试数据库模块的完整工作�? so that 确保多个操作组合后数据一致�?

#### Acceptance Criteria

1. WHEN 创建用户后查询该用户, THE Integration_Test SHALL 验证返回的用户数据与创建时一�?
2. WHEN 用户创建 API Key 后使用该 Key 验证, THE Integration_Test SHALL 验证返回正确的用户关�?
3. WHEN 用户捐赠 Token 后查询用户的 Token 列表, THE Integration_Test SHALL 验证 Token 出现在列表中
4. WHEN 删除用户后查询该用户�?API Key, THE Integration_Test SHALL 验证级联删除生效
5. WHEN 更新用户封禁状态后查询用户, THE Integration_Test SHALL 验证状态已更新
6. WHEN 增加会话版本后使用旧版本 token 验证, THE Integration_Test SHALL 验证�?token 失效

### Requirement 7: 用户认证集成测试

**User Story:** As a 开发�? I want 测试用户认证的完整流�? so that 确保注册、登录、登出流程正�?

#### Acceptance Criteria

1. WHEN 用户通过邮箱注册后使用相同凭证登�? THE Integration_Test SHALL 验证登录成功并返回有效会�?
2. WHEN 用户登出后使用原会话 token 访问, THE Integration_Test SHALL 验证会话已失�?
3. WHEN 管理员撤销用户会话后用户尝试访�? THE Integration_Test SHALL 验证所有会话失�?
4. WHEN 用户被封禁后尝试登录, THE Integration_Test SHALL 验证登录被拒�?
5. IF 邮箱已被注册, THEN THE Integration_Test SHALL 验证重复注册返回错误

### Requirement 8: API 健康检查端点测�?

**User Story:** As a 开发�? I want 测试健康检�?API 端点, so that 确保服务状态监控正�?

#### Acceptance Criteria

1. WHEN 发�?GET 请求�?`/health`, THE API_Test SHALL 验证返回 200 状态码�?JSON 响应
2. WHEN 服务正常运行, THE API_Test SHALL 验证响应包含 status �?"healthy"
3. WHEN 发�?GET 请求�?`/api`, THE API_Test SHALL 验证返回包含版本信息�?JSON 响应
4. WHEN 发�?GET 请求�?`/metrics`, THE API_Test SHALL 验证返回包含指标数据�?JSON 响应

### Requirement 9: API 认证端点测试

**User Story:** As a 开发�? I want 测试需要认证的 API 端点, so that 确保认证机制正确保护接口

#### Acceptance Criteria

1. WHEN 发送请求到 `/v1/models` 且不�?Authorization �? THE API_Test SHALL 验证返回 401 状态码
2. WHEN 发送请求到 `/v1/models` 且带有效 API Key, THE API_Test SHALL 验证返回 200 状态码和模型列�?
3. WHEN 发送请求到 `/v1/models` 且带无效 API Key, THE API_Test SHALL 验证返回 401 状态码
4. WHEN 发送请求到 `/v1/chat/completions` 且不�?Authorization �? THE API_Test SHALL 验证返回 401 状态码
5. WHEN 发送请求到 `/v1/messages` 且不�?x-api-key �? THE API_Test SHALL 验证返回 401 状态码

### Requirement 10: API 用户端点测试

**User Story:** As a 开发�? I want 测试用户相关 API 端点, so that 确保用户操作接口正确

#### Acceptance Criteria

1. WHEN 发�?POST 请求�?`/user/register` 且带有效邮箱和密�? THE API_Test SHALL 验证返回成功响应
2. WHEN 发�?POST 请求�?`/user/login` 且带正确凭证, THE API_Test SHALL 验证返回会话 cookie
3. WHEN 发�?POST 请求�?`/user/login` 且带错误凭证, THE API_Test SHALL 验证返回错误响应
4. WHEN 发�?GET 请求�?`/user/me` 且带有效会话, THE API_Test SHALL 验证返回当前用户信息
5. WHEN 发�?GET 请求�?`/user/me` 且不带会�? THE API_Test SHALL 验证返回 401 状态码
6. WHEN 发�?POST 请求�?`/user/logout` 且带有效会话, THE API_Test SHALL 验证会话被清�?

### Requirement 11: 测试夹具和模拟对�?

**User Story:** As a 开发�? I want 使用测试夹具和模拟对�? so that 测试可以隔离外部依赖并快速执�?

#### Acceptance Criteria

1. THE Test_Fixture SHALL 提供预配置的内存数据库实�?
2. THE Test_Fixture SHALL 提供预创建的测试用户�?API Key
3. THE Test_Fixture SHALL 提供 FastAPI TestClient 实例
4. THE Mock_Object SHALL 模拟 OAuth2 外部服务响应
5. THE Mock_Object SHALL 模拟 Kiro API Token 刷新响应
6. WHEN 测试完成, THE Test_Fixture SHALL 清理所有测试数�?

### Requirement 12: 边界条件和错误处理测�?

**User Story:** As a 开发�? I want 测试边界条件和错误处�? so that 确保系统在异常情况下行为正确

#### Acceptance Criteria

1. WHEN 创建用户时不提供任何身份标识, THE Unit_Test SHALL 验证抛出 ValueError 异常
2. WHEN 密码长度小于 8 �? THE Unit_Test SHALL 验证注册返回错误
3. WHEN 邮箱格式无效, THE Unit_Test SHALL 验证注册返回错误
4. IF 数据库连接失�? THEN THE Integration_Test SHALL 验证返回适当的错误响�?
5. IF API 请求超时, THEN THE API_Test SHALL 验证返回 504 或适当的超时错�?
6. WHEN 并发创建相同邮箱的用�? THE Integration_Test SHALL 验证只有一个成功创�?
