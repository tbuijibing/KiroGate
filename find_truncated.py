#!/usr/bin/env python
# -*- coding: utf-8 -*-

with open('geek_gateway/config.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find lines ending with ? that might be truncated
for i, line in enumerate(lines, start=1):
    stripped = line.rstrip('\r\n')
    # Check for truncated strings (ending with ? inside a string)
    if '?' in stripped and not stripped.endswith('"""'):
        # Check if it looks like a truncated Chinese character
        if stripped.endswith('?') or '?"' in stripped or "?'" in stripped:
            print(f'Line {i}: {stripped}')
