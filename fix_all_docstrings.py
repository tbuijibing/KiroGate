#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修复所有 Python 文件中被截断的 docstring 和字符串"""

import re
import os

def fix_truncated_docstrings(content):
    """修复被截断的 docstring"""
    # 修复以问号加三引号结尾的 docstring
    content = content.replace('?"""', '。"""')
    content = content.replace("?'''", "。'''")
    return content

def fix_truncated_strings(content):
    """修复被截断的字符串"""
    # 常见的截断模式
    patterns = [
        # f-string 中的截断
        (r'f"([^"]*)\?([^"]*)"', r'f"\1。\2"'),
        (r"f'([^']*)\?([^']*)'", r"f'\1。\2'"),
        # 普通字符串中的截断
        (r'"([^"]*)\?"', r'"\1。"'),
        (r"'([^']*)\?'", r"'\1。'"),
    ]
    
    for pattern, replacement in patterns:
        content = re.sub(pattern, replacement, content)
    
    return content

def fix_file(filepath):
    """修复单个文件"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f'Error reading {filepath}: {e}')
        return False
    
    original = content
    
    # 应用修复
    content = fix_truncated_docstrings(content)
    content = fix_truncated_strings(content)
    
    # 如果有修改，写回文件
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
