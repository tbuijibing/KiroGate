#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修复只有两个引号的 docstring"""

import os
import re

def fix_file(filepath):
    """修复单个文件中的双引号 docstring"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f'Error reading {filepath}: {e}')
        return False
    
    original = content
    
    # 修复只有两个引号的 docstring（中文内容）
    # 匹配 ""中文内容"" 格式，转换为 """中文内容"""
    # 注意：需要确保不是在字符串内部
    
    # 模式1: 行首或缩进后的 ""...""
    content = re.sub(
        r'^(\s*)""([^"]+)""$',
        r'\1"""\2"""',
        content,
        flags=re.MULTILINE
    )
    
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
