#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""扫描所有 Python 文件中被截断的中文字符"""

import os
from pathlib import Path

def scan_file(filepath):
    """扫描单个文件"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f'Error reading {filepath}: {e}')
        return []
    
    issues = []
    for i, line in enumerate(lines, start=1):
        stripped = line.rstrip('\r\n')
        # Check for truncated strings
        if '?' in stripped:
            # Check if it looks like a truncated Chinese character
            if (stripped.endswith('?') or 
                '?"' in stripped or 
                "?'" in stripped or 
                '?)' in stripped or
                '?,' in stripped or
                '?:' in stripped):
                issues.append((i, stripped))
    return issues

# Scan all Python files in geek_gateway
for root, dirs, files in os.walk('geek_gateway'):
    # Skip __pycache__
    dirs[:] = [d for d in dirs if d != '__pycache__']
    
    for file in files:
        if file.endswith('.py'):
            filepath = os.path.join(root, file)
            issues = scan_file(filepath)
            if issues:
                print(f'\n=== {filepath} ===')
                for line_num, content in issues:
                    print(f'  Line {line_num}: {content[:80]}...' if len(content) > 80 else f'  Line {line_num}: {content}')
