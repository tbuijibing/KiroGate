#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修复测试文件中被截断的 docstring"""

import os
import re

def fix_file(filepath):
    """修复单个文件中缺少引号的 docstring"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f'Error reading {filepath}: {e}')
        return False
    
    original = content
    
    # 修复 """...。"" 格式（缺少一个结尾引号）
    content = re.sub(r'"""([^"]+)。""(\s*\n)', r'"""\1。"""\2', content)
    content = re.sub(r'"""([^"]+)\?""(\s*\n)', r'"""\1。"""\2', content)
    
    # 修复其他被截断的字符串
    content = content.replace('?"""', '。"""')
    
    if content != original:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    return False

# 处理所有测试文件
fixed_count = 0
for root, dirs, files in os.walk('tests'):
    dirs[:] = [d for d in dirs if d != '__pycache__']
    
    for file in files:
        if file.endswith('.py'):
            filepath = os.path.join(root, file)
            if fix_file(filepath):
                print(f'Fixed: {filepath}')
                fixed_count += 1

print(f'\nFixed {fixed_count} files.')
