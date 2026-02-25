#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修复所有被截断的字符串"""

import os
import re

def fix_file(filepath):
    """修复单个文件"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f'Error reading {filepath}: {e}')
        return False
    
    original = content
    
    # 修复常见的截断模式
    replacements = [
        # 未授权
        ('"未授?}', '"未授权"}'),
        ('"未授?)', '"未授权")'),
        # 访问被拒绝
        ('"访问被拒?}', '"访问被拒绝"}'),
        ('"访问被拒?)', '"访问被拒绝")'),
        # 账号审核
        ('"账号审核?', '"账号审核中"'),
        # 数据库
        ('"文件不是有效?SQLite 数据?}', '"文件不是有效的 SQLite 数据库"}'),
        ('"数据库文件无?}', '"数据库文件无效"}'),
        ('"请选择要导入的数据?}', '"请选择要导入的数据库"}'),
        ('"未选择可导入的数据?}', '"未选择可导入的数据库"}'),
        ('"导入会话已过期，请重新上?}', '"导入会话已过期，请重新上传"}'),
        # 统计数据
        ('"统计数据?,', '"统计数据",'),
        # 模型缓存
        ('"模型缓存已刷?}', '"模型缓存已刷新"}'),
        # 其他
        ('labels = "?.join', 'labels = "、".join'),
        ('invalid_labels = "?.join', 'invalid_labels = "、".join'),
        ('imported_labels = "?.join', 'imported_labels = "、".join'),
        # 日志消息
        ('使用自定。Refresh Token', '使用自定义 Refresh Token'),
        ('已同?', '已同步 '),
        ('评分。Redis', '评分到 Redis'),
        ('评分同步。Redis', '评分同步到 Redis'),
    ]
    
    for old, new in replacements:
        if old in content:
            content = content.replace(old, new)
    
    if content != original:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    return False

# 处理所有 Python 文件
fixed_count = 0
for root, dirs, files in os.walk('geek_gateway'):
    dirs[:] = [d for d in dirs if d != '__pycache__']
    
    for file in files:
        if file.endswith('.py'):
            filepath = os.path.join(root, file)
            if fix_file(filepath):
                print(f'Fixed: {filepath}')
                fixed_count += 1

print(f'\nFixed {fixed_count} files.')
