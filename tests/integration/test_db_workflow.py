# -*- coding: utf-8 -*-

"""
数据库集成测�?

测试数据库模块的完整工作流和数据一致�?
"""

import pytest


class TestDatabaseWorkflow:
    """数据库工作流测试类�?""

    @pytest.mark.asyncio
    async def test_user_creation_and_query_consistency(self, test_db):
        """测试创建用户后查询数据一致性�?""
        # 创建用户
        user = await test_db.create_user(
            username="workflow_user",
            email="workflow@example.com",
            password_hash="pbkdf2_sha256$120000$salt$hash",
        )
        
        # 查询用户
        queried_user = await test_db.get_user(user.id)
        
        # 验证数据一�?
        assert queried_user is not None
        assert queried_user.id == user.id
        assert queried_user.username == user.username
        assert queried_user.email == user.email
        assert queried_user.created_at == user.created_at

    @pytest.mark.asyncio
    async def test_api_key_workflow(self, test_db, test_user):
        """测试 API Key 创建和验证工作流�?""
        # 创建 API Key
        plain_key, api_key = await test_db.generate_api_key(test_user.id, "Workflow Key")
        
        # 验证 API Key
        result = await test_db.verify_api_key(plain_key)
        
        assert result is not None
        user_id, key_id = result
        assert user_id == test_user.id
        assert key_id == api_key.id
        
        # 验证 API Key 列表
        keys = await test_db.get_user_api_keys(test_user.id)
        assert len(keys) >= 1
        assert any(k.id == api_key.id for k in keys)

    @pytest.mark.asyncio
    async def test_token_donation_workflow(self, test_db, test_user):
        """测试 Token 捐赠和查询工作流�?""
        # 捐赠 Token
        refresh_token = "workflow-refresh-token-12345"
        success, message = await test_db.donate_token(
            user_id=test_user.id,
            refresh_token=refresh_token,
            visibility="private",
        )
        
        assert success is True
        
        # 查询用户?Token 列表
        tokens = await test_db.get_user_tokens(test_user.id)
        
        assert len(tokens) == 1
        assert tokens[0].user_id == test_user.id
        assert tokens[0].visibility == "private"
        assert tokens[0].status == "active"
        
        # 验证解密后的 Token
        decrypted = await test_db.get_decrypted_token(tokens[0].id)
        assert decrypted == refresh_token

    @pytest.mark.asyncio
    async def test_user_ban_status_update(self, test_db, test_user):
        """测试用户封禁状态更新�?""
        # 初始状态应该是未封?
        assert test_user.is_banned is False
        
        # 封禁用户
        await test_db.set_user_banned(test_user.id, True)
        
        # 查询用户验证�?
        updated_user = await test_db.get_user(test_user.id)
        assert updated_user.is_banned is True
        
        # 解封用户
        await test_db.set_user_banned(test_user.id, False)
        
        # 再次验证
        final_user = await test_db.get_user(test_user.id)
        assert final_user.is_banned is False

    @pytest.mark.asyncio
    async def test_user_admin_status_update(self, test_db, test_user):
        """测试用户管理员状态更新�?""
        # 初始状态应该是非管理员
        assert test_user.is_admin is False
        
        # 设置为管理员
        await test_db.set_user_admin(test_user.id, True)
        
        # 查询用户验证�?
        updated_user = await test_db.get_user(test_user.id)
        assert updated_user.is_admin is True
        
        # 取消管理?
        await test_db.set_user_admin(test_user.id, False)
        
        # 再次验证
        final_user = await test_db.get_user(test_user.id)
        assert final_user.is_admin is False

    @pytest.mark.asyncio
    async def test_token_status_update_workflow(self, test_db, test_user):
        """测试 Token 状态更新工作流�?""
        # 捐赠 Token
        await test_db.donate_token(
            user_id=test_user.id,
            refresh_token="status-test-token",
        )
        
        tokens = await test_db.get_user_tokens(test_user.id)
        token_id = tokens[0].id
        
        # 初始状态应该是 active
        assert tokens[0].status == "active"
        
        # 更新?invalid
        await test_db.set_token_status(token_id, "invalid")
        
        updated_token = await test_db.get_token_by_id(token_id)
        assert updated_token.status == "invalid"
        
        # 更新?suspended
        await test_db.set_token_status(token_id, "suspended")
        
        final_token = await test_db.get_token_by_id(token_id)
        assert final_token.status == "suspended"

    @pytest.mark.asyncio
    async def test_api_key_usage_recording(self, test_db, test_api_key, test_user):
        """测试 API Key 使用记录�?""
        plain_key, api_key = test_api_key
        
        # 初始请求计数应该?0
        keys = await test_db.get_user_api_keys(test_user.id)
        initial_count = keys[0].request_count
        
        # 记录使用
        await test_db.record_api_key_usage(api_key.id)
        
        # 验证计数增加
        keys = await test_db.get_user_api_keys(test_user.id)
        assert keys[0].request_count == initial_count + 1
        assert keys[0].last_used is not None
