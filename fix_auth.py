#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修复 auth.py 中被截断的中文字符"""

replacements = [
    ('认证类型枚举?', '认证类型枚举。'),
    ('SOCIAL: Kiro IDE 社交账号登录 (Google/GitHub?', 'SOCIAL: Kiro IDE 社交账号登录 (Google/GitHub)'),
    ('根据凭证检测认证类型?', '根据凭证检测认证类型。'),
    ('如果?client_id ?client_secret，则?IDC 模式?', '如果有 client_id 和 client_secret，则为 IDC 模式。'),
    ('否则?Social 模式?', '否则为 Social 模式。'),
    ('根据认证类型路由到对应的刷新方法?', '根据认证类型路由到对应的刷新方法。'),
    ('使用 Social (Kiro Desktop Auth) 端点刷新 Token?', '使用 Social (Kiro Desktop Auth) 端点刷新 Token。'),
    ('使用 IDC (AWS SSO OIDC) 端点刷新 Token?', '使用 IDC (AWS SSO OIDC) 端点刷新 Token。'),
    ('注意: AWS SSO OIDC 使用 JSON 格式?camelCase 字段?', '注意: AWS SSO OIDC 使用 JSON 格式和 camelCase 字段。'),
    ('# AWS SSO OIDC 使用 JSON 格式?camelCase 字段?', '# AWS SSO OIDC 使用 JSON 格式和 camelCase 字段。'),
    ('执行刷新请求，带指数退避重试?', '执行刷新请求，带指数退避重试。'),
    ('headers: 请求?', 'headers: 请求头'),
    ('f"HTTP {e.response.status_code}, {delay}s 后重?', 'f"HTTP {e.response.status_code}, {delay}s 后重试"'),
    ('f"{type(e).__name__}, {delay}s 后重?', 'f"{type(e).__name__}, {delay}s 后重试"'),
    ('处理刷新响应，更新内部状态?', '处理刷新响应，更新内部状态。'),
]

with open('geek_gateway/auth.py', 'r', encoding='utf-8') as f:
    content = f.read()

for old, new in replacements:
    if old in content:
        content = content.replace(old, new)
        print(f'Fixed: {old[:50]}...')

with open('geek_gateway/auth.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('\nDone!')
