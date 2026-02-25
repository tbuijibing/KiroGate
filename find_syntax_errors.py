#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""查找所有 Python 文件中的语法错误"""

import py_compile
import os

for root, dirs, files in os.walk('geek_gateway'):
    dirs[:] = [d for d in dirs if d != '__pycache__']
    
    for file in files:
        if file.endswith('.py'):
            filepath = os.path.join(root, file)
            try:
                py_compile.compile(filepath, doraise=True)
            except py_compile.PyCompileError as e:
                print(f'\n=== {filepath} ===')
                print(str(e))
