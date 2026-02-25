#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修复 routes.py 中所有被截断的字符串"""

replacements = [
    ('"Token 不存?}', '"Token 不存在"}'),
    ('"未登?}', '"未登录"}'),
    ('"自用模式下不开放公开 Token ?}', '"自用模式下不开放公开 Token 池"}'),
    ('"可见性无?}', '"可见性无效"}'),
    ('"Token 验证失败：无法获取访问令?}', '"Token 验证失败：无法获取访问令牌"}'),
    ('"API Key 不存?}', '"API Key 不存在"}'),
]

with open('geek_gateway/routes.py', 'r', encoding='utf-8') as f:
    content = f.read()

for old, new in replacements:
    if old in content:
        content = content.replace(old, new)
        print(f'Fixed: {old[:50]}...')

with open('geek_gateway/routes.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('\nDone!')
