#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修复 database.py 中被错误替换的 SQL 占位符"""

with open('geek_gateway/database.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 修复 SQL 占位符
content = content.replace('= 。', '= ?')
content = content.replace('= 。"', '= ?"')

with open('geek_gateway/database.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed database.py')
