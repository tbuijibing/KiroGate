#!/usr/bin/env python
# -*- coding: utf-8 -*-
import re

with open('geek_gateway/config.py', 'rb') as f:
    content = f.read()

lines = content.split(b'\r\n')

# Find all lines with triple quotes
for i, line in enumerate(lines, start=1):
    if b'"""' in line:
        count = line.count(b'"""')
        print(f'Line {i} ({count}x): {line.decode("utf-8", errors="replace")}')
