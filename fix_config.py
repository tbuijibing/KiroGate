#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修复 config.py 中被截断的中文字符"""

# 定义需要修复的替换对
replacements = [
    # 模块 docstring
    ('使用 Pydantic Settings 进行类型安全的环境变量加载?', '使用 Pydantic Settings 进行类型安全的环境变量加载。'),
    
    # _get_raw_env_value docstring
    ('?.env 文件读取原始变量值，不处理转义序列?', '从 .env 文件读取原始变量值，不处理转义序列。'),
    ('这对?Windows 路径很重要，因为反斜杠（?D:\\\\Projects\\\\file.json?', '这对于 Windows 路径很重要，因为反斜杠（如 D:\\\\Projects\\\\file.json）'),
    ('可能被错误地解释为转义序列（\\\\a -> bell, \\\\n -> newline 等）?', '可能被错误地解释为转义序列（\\\\a -> bell, \\\\n -> newline 等）。'),
    ('var_name: 环境变量?', 'var_name: 环境变量名'),
    ('env_file: .env 文件路径（默?".env"?', 'env_file: .env 文件路径（默认 ".env"）'),
    
    # Settings class docstring
    ('应用程序配置类?', '应用程序配置类。'),
    ('使用 Pydantic Settings 进行类型安全的环境变量加载和验证?', '使用 Pydantic Settings 进行类型安全的环境变量加载和验证。'),
    
    # 代理服务器设置
    ('# 代理服务器设?', '# 代理服务器设置'),
    
    # AWS 区域
    ('# AWS 区域（默?us-east-1?', '# AWS 区域（默认 us-east-1）'),
    
    # 代理 URL
    ('# 代理 URL（支?HTTP ?SOCKS5?', '# 代理 URL（支持 HTTP 和 SOCKS5）'),
    
    # 最大重试次数
    ('# 最大重试次?', '# 最大重试次数'),
    
    # 模型缓存 TTL
    ('# 模型缓存 TTL（秒?', '# 模型缓存 TTL（秒）'),
    
    # 默认最大输出 token
    ('# 默认最大输?token ?', '# 默认最大输出 token 数'),
    
    # Tool Description
    ('# Tool Description 处理（Kiro API 限制?', '# Tool Description 处理（Kiro API 限制）'),
    ('# Tool description 最大长度（字符?', '# Tool description 最大长度（字符）'),
    
    # 超时设置
    ('# 对于 Opus 等慢模型，建议设置为 120-180 ?', '# 对于 Opus 等慢模型，建议设置为 120-180 秒'),
    ('# 首个 token 超时时的最大重试次?', '# 首个 token 超时时的最大重试次数'),
    ('# 流式读取超时（秒? 读取流中每个 chunk 的最大等待时?', '# 流式读取超时（秒）- 读取流中每个 chunk 的最大等待时间'),
    ('# 对于慢模型会自动乘以倍数。建议设置为 180-300 ?', '# 对于慢模型会自动乘以倍数。建议设置为 180-300 秒'),
    ('# 非流式请求超时（秒）- 等待完整响应的最大时?', '# 非流式请求超时（秒）- 等待完整响应的最大时间'),
    ('# 对于复杂请求，建议设置为 600-1200 ?', '# 对于复杂请求，建议设置为 600-1200 秒'),
    
    # 速率限制
    ('# 速率限制：每分钟请求数（0 表示禁用?', '# 速率限制：每分钟请求数（0 表示禁用）'),
    
    # 慢模型配置
    ('# 慢模型配?', '# 慢模型配置'),
    ('# 建议设置?3.0-4.0，因为慢模型处理大文档时可能需要更长时?', '# 建议设置为 3.0-4.0，因为慢模型处理大文档时可能需要更长时间'),
    
    # 自动分片配置
    ('# 自动分片配置（长文档处理?', '# 自动分片配置（长文档处理）'),
    ('# 分片重叠字符?', '# 分片重叠字符数'),
    
    # Admin Session
    ('# Admin Session 签名密钥（请在生产环境中更改?', '# Admin Session 签名密钥（请在生产环境中更改）'),
    
    # 用户 Session
    ('# 用户 Session 有效期（秒），默??', '# 用户 Session 有效期（秒），默认 7 天'),
    
    # Token 加密密钥
    ('# Token 加密密钥?2字节?', '# Token 加密密钥（32字节）'),
    
    # Token 最低成功率阈值
    ('# Token 最低成功率阈?', '# Token 最低成功率阈值'),
    
    # 静态资源代理配置
    ('# 静态资源代理配?', '# 静态资源代理配置'),
    
    # 分布式部署配置
    ('# 分布式部署配?', '# 分布式部署配置'),
    
    # PostgreSQL 连接池大小
    ('# PostgreSQL 连接池大?', '# PostgreSQL 连接池大小'),
    
    # Token 防风控配置
    ('# Token 防风控配?', '# Token 防风控配置'),
    
    # 同一 Token 连续使用最大次数
    ('# 同一 Token 连续使用最大次?', '# 同一 Token 连续使用最大次数'),
    
    # 单个 API Key 默认每分钟请求限制
    ('# 单个 API Key 默认每分钟请求限?', '# 单个 API Key 默认每分钟请求限制'),
    
    # 检查默认密钥
    ('# 检查默认密?', '# 检查默认密钥'),
    ('insecure_defaults.append("ADMIN_PASSWORD 使用默认?\'admin123\'")', 'insecure_defaults.append("ADMIN_PASSWORD 使用默认值 \'admin123\'")'),
    ('# 检查默认密?- 这些是严重安全风?', '# 检查默认密钥 - 这些是严重安全风险'),
    
    # 安全警告
    ('logger.warning("安全警告: 检测到不安全的默认配置?)', 'logger.warning("安全警告: 检测到不安全的默认配置！")'),
    ('logger.warning("请在生产环境中修?.env 文件中的相关配置")', 'logger.warning("请在生产环境中修改 .env 文件中的相关配置")'),
    
    # 生产环境检查
    ('# 在生产环境中，如果使用默认的 session 密钥，拒绝启?', '# 在生产环境中，如果使用默认的 session 密钥，拒绝启动'),
    ('f"请设置以下环境变? {', 'f"请设置以下环境变量: {'),
    
    # 分布式模式安全检查
    ('# 分布式模式安全检?', '# 分布式模式安全检查'),
    
    # 检查是否是有效的内部模型ID
    ('# 检查是否是有效的内部模?ID（直接传递）', '# 检查是否是有效的内部模型 ID（直接传递）'),
]

# 读取文件
with open('geek_gateway/config.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 应用替换
for old, new in replacements:
    if old in content:
        content = content.replace(old, new)
        print(f'Fixed: {old[:50]}...')
    else:
        # 尝试查找类似的内容
        pass

# 写回文件
with open('geek_gateway/config.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('\nDone!')
