#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修复所有 Python 文件中被截断的中文字符"""

import os
import re

# 通用替换规则（问号结尾的截断字符）
common_replacements = {
    # 常见的截断模式
    '?""': '。"""',  # docstring 结尾
    "?''": "。'''",  # docstring 结尾
    '?)': '。)',     # 括号内结尾
    '?:': '。:',     # 冒号前结尾
    '?,': '。,',     # 逗号前结尾
}

# 特定文件的替换规则
file_specific_replacements = {
    'geek_gateway/auth.py': [
        ('# 更新内部状?', '# 更新内部状态'),
    ],
    'geek_gateway/auth_cache.py': [
        ('logger.warning("Redis 不可用，降级为仅本地热缓?)', 'logger.warning("Redis 不可用，降级为仅本地热缓存")'),
    ],
    'geek_gateway/http_client.py': [
        ('detail=f"模型在 {max_retries} 次尝试后仍未在 {timeout}s 内响应，请稍后再试?', 
         'detail=f"模型在 {max_retries} 次尝试后仍未在 {timeout}s 内响应，请稍后再试"'),
    ],
    'geek_gateway/streaming.py': [
        (f'raise StreamReadTimeoutError(f"流式读取在 {{timeout}}s 后超?)', 
         f'raise StreamReadTimeoutError(f"流式读取在 {{timeout}}s 后超时")'),
        ('detail=f"模型在 {max_retries} 次尝试后仍未在 {first_token_timeout}s 内响应，请稍后再试?',
         'detail=f"模型在 {max_retries} 次尝试后仍未在 {first_token_timeout}s 内响应，请稍后再试"'),
    ],
    'geek_gateway/database.py': [
        ('raise ValueError("无效的审核状?)', 'raise ValueError("无效的审核状态")'),
        ('return False, "Token 已存?', 'return False, "Token 已存在"'),
    ],
    'geek_gateway/config_reloader.py': [
        ('logger.info("Redis 不可用，配置热重载仅支持单节点模?)', 
         'logger.info("Redis 不可用，配置热重载仅支持单节点模式")'),
    ],
    'geek_gateway/heartbeat.py': [
        ('logger.info("节点心跳上报已停?)', 'logger.info("节点心跳上报已停止")'),
    ],
    'geek_gateway/redis_manager.py': [
        ('logger.info("Redis URL 未配置，跳过 Redis 初始?)', 
         'logger.info("Redis URL 未配置，跳过 Redis 初始化")'),
        ('logger.warning("redis 包未安装，Redis 功能不可?)', 
         'logger.warning("redis 包未安装，Redis 功能不可用")'),
        ('logger.warning(f"Redis 连接失败: {e}，将以降级模式运?)', 
         'logger.warning(f"Redis 连接失败: {e}，将以降级模式运行")'),
        ('logger.info("Redis 连接已关?)', 'logger.info("Redis 连接已关闭")'),
    ],
    'geek_gateway/token_allocator.py': [
        ('logger.info("TokenAllocator: 已关?)', 'logger.info("TokenAllocator: 已关闭")'),
        ('logger.warning("TokenAllocator: Redis 不可用，降级为本地分?)', 
         'logger.warning("TokenAllocator: Redis 不可用，降级为本地分配")'),
        ('logger.warning(f"Token {token_id}: 连续失败 {consecutive_fails} 次，已暂?)', 
         'logger.warning(f"Token {token_id}: 连续失败 {consecutive_fails} 次，已暂停")'),
    ],
    'geek_gateway/user_manager.py': [
        ('return None, "授权码交换失?', 'return None, "授权码交换失败"'),
        ('return None, "响应中缺少访问令?', 'return None, "响应中缺少访问令牌"'),
        ('return None, "自用模式下暂不开放注?', 'return None, "自用模式下暂不开放注册"'),
        ('return None, "邮箱格式不正?', 'return None, "邮箱格式不正确"'),
        ('return None, "密码至少 8 ?', 'return None, "密码至少 8 位"'),
        ('return None, "邮箱已注?', 'return None, "邮箱已注册"'),
        ('return None, "注册成功，等待审?', 'return None, "注册成功，等待审核"'),
        ('return None, "邮箱或密码不能为?', 'return None, "邮箱或密码不能为空"'),
        ('return None, "邮箱或密码错?', 'return None, "邮箱或密码错误"'),
    ],
    'geek_gateway/config.py': [
        ('# 检查默认密钥- 这些是严重安全风?', '# 检查默认密钥 - 这些是严重安全风险'),
    ],
    'geek_gateway/metrics.py': [
        ('logger.info("Metrics: Redis 状态加载完?)', 'logger.info("Metrics: Redis 状态加载完成")'),
    ],
    'geek_gateway/routes.py': [
        ('raise HTTPException(status_code=403, detail="跨站请求被拒?)', 
         'raise HTTPException(status_code=403, detail="跨站请求被拒绝")'),
        ('logger.warning(f"[{get_timestamp()}] 缺少或无效的 Authorization 头格?)', 
         'logger.warning(f"[{get_timestamp()}] 缺少或无效的 Authorization 头格式")'),
        ('raise HTTPException(status_code=401, detail="API Key 无效或缺?)', 
         'raise HTTPException(status_code=401, detail="API Key 无效或缺失")'),
        ('return False, "文件不是有效的 SQLite 数据?', 'return False, "文件不是有效的 SQLite 数据库"'),
        ('return False, "数据库读取失?', 'return False, "数据库读取失败"'),
        ('if err_users == "数据库读取失? or err_metrics == "数据库读取失?:', 
         'if err_users == "数据库读取失败" or err_metrics == "数据库读取失败":'),
        ('error_message = "数据库读取失?', 'error_message = "数据库读取失败"'),
        ('"message": "解析完成，请选择需要导入的数据库?', '"message": "解析完成，请选择需要导入的数据库"'),
        ('return None, "请仅选择一种导入方?', 'return None, "请仅选择一种导入方式"'),
        ('return None, "文件过大，请拆分后导?', 'return None, "文件过大，请拆分后导入"'),
        ('return None, "JSON 内容过大，请拆分后导?', 'return None, "JSON 内容过大，请拆分后导入"'),
        ('return None, "导入文本过大，请拆分后导?', 'return None, "导入文本过大，请拆分后导入"'),
        ('record_missing(path, "类型不支?)', 'record_missing(path, "类型不支持")'),
        ('record_missing(item_path, "类型不支?)', 'record_missing(item_path, "类型不支持")'),
        ('if message == "Token 已存?:', 'if message == "Token 已存在":'),
    ],
}

def fix_file(filepath):
    """修复单个文件"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f'Error reading {filepath}: {e}')
        return False
    
    original = content
    
    # 应用文件特定的替换
    rel_path = filepath.replace('\\', '/')
    if rel_path in file_specific_replacements:
        for old, new in file_specific_replacements[rel_path]:
            if old in content:
                content = content.replace(old, new)
                print(f'  Fixed: {old[:60]}...')
    
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
            print(f'\nProcessing {filepath}...')
            if fix_file(filepath):
                fixed_count += 1

print(f'\n\nFixed {fixed_count} files.')
