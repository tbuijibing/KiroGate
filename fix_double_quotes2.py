#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修复只有两个引号的 docstring"""

import os

def fix_file(filepath):
    """修复单个文件中的双引号 docstring"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f'Error reading {filepath}: {e}')
        return False
    
    modified = False
    new_lines = []
    
    for line in lines:
        stripped = line.rstrip('\r\n')
        # 检查是否是只有两个引号的 docstring
        # 格式: 空白 + "" + 中文内容 + ""
        if stripped.lstrip().startswith('""') and stripped.rstrip().endswith('""'):
            # 检查是否不是三引号
            content = stripped.lstrip()
            if not content.startswith('"""'):
                # 获取缩进
                indent = len(line) - len(line.lstrip())
                indent_str = line[:indent]
                # 提取内容
                inner = content[2:-2]  # 去掉前后的 ""
                if inner and not inner.startswith('"'):  # 确保不是 """
                    new_line = f'{indent_str}"""{inner}"""\n'
                    new_lines.append(new_line)
                    modified = True
                    print(f'  Fixed line: {stripped[:60]}...')
                    continue
        new_lines.append(line)
    
    if modified:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        return True
    return False

# 处理所有 Python 文件
fixed_count = 0
for root, dirs, files in os.walk('geek_gateway'):
    dirs[:] = [d for d in dirs if d != '__pycache__']
    
    for file in files:
        if file.endswith('.py'):
            filepath = os.path.join(root, file)
            print(f'Processing {filepath}...')
            if fix_file(filepath):
                fixed_count += 1

print(f'\nFixed {fixed_count} files.')
