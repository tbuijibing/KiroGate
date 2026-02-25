#!/usr/bin/env python
# -*- coding: utf-8 -*-
import re

with open('geek_gateway/config.py', 'rb') as f:
    content = f.read()

# Find all triple quotes
triple_quotes = list(re.finditer(b'"""', content))
print(f'Found {len(triple_quotes)} triple quotes')

# Check around line 672
lines = content.split(b'\r\n')
for i, line in enumerate(lines[669:685], start=670):
    # Check for triple quotes
    if b'"""' in line:
        print(f'Line {i}: {line}')

# Check if quotes are balanced
if len(triple_quotes) % 2 != 0:
    print('WARNING: Unbalanced triple quotes!')
