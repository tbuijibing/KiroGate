#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修复 routes.py 中被截断的中文字符"""

replacements = [
    # 注释
    ('# 预创建速率限制装饰器（避免重复创建?', '# 预创建速率限制装饰器（避免重复创建）'),
    ('# 检查是否正在关?', '# 检查是否正在关闭'),
    ('label = "维护?', 'label = "维护中"'),
    ('# 尝试多个 idp 列表（按常见程度排序?', '# 尝试多个 idp 列表（按常见程度排序）'),
    ('# 如果获取失败，检查错误信息判断是否封?', '# 如果获取失败，检查错误信息判断是否封禁'),
    ('# 解析 Credits 使用?', '# 解析 Credits 使用量'),
    ('# 规范化订阅类?', '# 规范化订阅类型'),
    ('"status": user_status,  # Active, Suspended ?', '"status": user_status,  # Active, Suspended 等'),
    ('return False, "文件不是有效的 SQLite 数据?', 'return False, "文件不是有效的 SQLite 数据库"'),
    ('missing_list = "、".join(sorted(missing))', 'missing_list = "、".join(sorted(missing))'),
    ('await auth_manager.force_refresh()  # 管理员手动刷?', 'await auth_manager.force_refresh()  # 管理员手动刷新'),
    ('labels = "、".join(DB_LABELS.get(key, key) for key in missing)', 'labels = "、".join(DB_LABELS.get(key, key) for key in missing)'),
    ('invalid_labels = "、".join(DB_LABELS.get(key, key) for key in invalid)', 'invalid_labels = "、".join(DB_LABELS.get(key, key) for key in invalid)'),
    ('imported_labels = "、".join(DB_LABELS.get(key, key) for key in imported)', 'imported_labels = "、".join(DB_LABELS.get(key, key) for key in imported)'),
    ('"message": f"导入完成：{imported_labels} 已更新。请重启服务以加载最新数据?', '"message": f"导入完成：{imported_labels} 已更新。请重启服务以加载最新数据"'),
    ('imported_labels = "、".join(label_map[key] for key in imported)', 'imported_labels = "、".join(label_map[key] for key in imported)'),
    ('从导入数据中提取 token 凭证?', '从导入数据中提取 token 凭证。'),
    ('# 直接字段（支持驼峰和蛇形命名?', '# 直接字段（支持驼峰和蛇形命名）'),
    ('# 嵌套的 credentials 或 credentials_kiro_rs 等', '# 嵌套的 credentials 或 credentials_kiro_rs 等'),
    ('# 如果指定了 override 参数（IDC 模式），将其应用到所有凭?', '# 如果指定了 override 参数（IDC 模式），将其应用到所有凭证'),
    ('message = f"{message}，缺少必填 {missing_required}条', 'message = f"{message}，缺少必填 {missing_required}条"'),
    ('f"导入完成：成功 {imported}，已存在 {skipped}，无效 {invalid}，失败 {failed}条', 
     'f"导入完成：成功 {imported}，已存在 {skipped}，无效 {invalid}，失败 {failed}条"'),
    ('message = f"{message} 缺少必填 {missing_required}条', 'message = f"{message} 缺少必填 {missing_required}条"'),
    ('sample_messages.append(f"必填示例：{\'、\'.join(missing_samples)}")', 
     'sample_messages.append(f"必填示例：{\'、\'.join(missing_samples)}")'),
    ('sample_messages.append(f"错误示例：{\'、\'.join(error_samples)}")', 
     'sample_messages.append(f"错误示例：{\'、\'.join(error_samples)}")'),
    ('return JSONResponse(status_code=404, content={"error": "Token 不存?})', 
     'return JSONResponse(status_code=404, content={"error": "Token 不存在"})'),
    ('# 获取解密后的完整凭证（包含 IDC 的 client_id/client_secret?', 
     '# 获取解密后的完整凭证（包含 IDC 的 client_id/client_secret）'),
    ('FROM activity_logs WHERE user_id = ?', 'FROM activity_logs WHERE user_id = ?'),
    ('# 管理面板 - 集群概览和 Token 池状态 API（分布式部署?', 
     '# 管理面板 - 集群概览和 Token 池状态 API（分布式部署）'),
    ('计算 Token 风险评分 (0.0 - 1.0)?', '计算 Token 风险评分 (0.0 - 1.0)。'),
    ('因子?', '因子：'),
    ('集群概览 API?', '集群概览 API。'),
    ('返回在线节点、全局 Token 池状态、集群实时聚合指标?', '返回在线节点、全局 Token 池状态、集群实时聚合指标。'),
    ('单节点模式下返回当前节点信息?', '单节点模式下返回当前节点信息。'),
    ('获取集群实时聚合指标：总请求数、成功率、平均延迟、P95/P99 延迟?', 
     '获取集群实时聚合指标：总请求数、成功率、平均延迟、P95/P99 延迟。'),
    ('全局 Token 池状态 API?', '全局 Token 池状态 API。'),
    ('返回每个 Token 的 Risk_Score、RPM/RPH 使用量、并发数、连续失败次数、状态?', 
     '返回每个 Token 的 Risk_Score、RPM/RPH 使用量、并发数、连续失败次数、状态。'),
    ('# 判断实际状?', '# 判断实际状态'),
    ('return JSONResponse(status_code=404, content={"error": "用户不存?})', 
     'return JSONResponse(status_code=404, content={"error": "用户不存在"})'),
    ('# 确保用户配额行存?', '# 确保用户配额行存在'),
    ('记录管理员操作审计日志?', '记录管理员操作审计日志。'),
    ('admin_action: 操作类型 (token_pause, user_ban, quota_update, config_reload 等', 
     'admin_action: 操作类型 (token_pause, user_ban, quota_update, config_reload 等)'),
    ('target_type: 目标类型 (token, user, config 等', 'target_type: 目标类型 (token, user, config 等)'),
    ('热重载配置项?', '热重载配置项。'),
    ('将配置存储到 Redis Hash 并通过 Pub/Sub 通知所有节点更新?', 
     '将配置存储到 Redis Hash 并通过 Pub/Sub 通知所有节点更新。'),
    ('单节点模式下直接更新内存配置?', '单节点模式下直接更新内存配置。'),
    ('# 优先从 Redis 读取（分布式模式?', '# 优先从 Redis 读取（分布式模式）'),
]

with open('geek_gateway/routes.py', 'r', encoding='utf-8') as f:
    content = f.read()

for old, new in replacements:
    if old in content:
        content = content.replace(old, new)
        print(f'Fixed: {old[:60]}...')

with open('geek_gateway/routes.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('\nDone!')
