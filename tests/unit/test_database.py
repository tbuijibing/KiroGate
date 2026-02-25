# -*- coding: utf-8 -*-

"""
数据库模块单元测�?

测试 UserDatabase 类的 CRUD 操作?
"""

import pytest


class TestUserDatabase:
    """数据库模块测试类�?""

    @pytest.mark.asyncio
    async def test_create_user_returns_valid_user(self, test_db):
        """测试 create_user 返回正确?User 对象�?""
        user = await test_db.create_user(
            username="newuser",
            email="newuser@example.com",
            password_hash="pbkdf2_sha256$120000$salt$hash",
        )
        
        assert user is not None
        assert user.id is not None
        assert user.username == "newuser"
        assert user.email == "newuser@example.com"
        assert user.is_admin is False
        assert user.is_banned is False
        assert user.approval_status == "approved"

    @pytest.mark.asyncio
    async def test_get_user_valid_id(self, test_db, test_user):
        """测试通过有效 ID 查询用户�?""
        user = await test_db.get_user(test_user.id)
        
        assert user is not None
        assert user.id == test_user.id
        assert user.username == test_user.username

    @pytest.mark.asyncio
    async def test_get_user_invalid_id(self, test_db):
        """测试通过无效 ID 查询用户返回 None�?""
        user = await test_db.get_user(99999)
        
        assert user is None

    @pytest.mark.asyncio
    async def test_generate_api_key_creates_unique_key(self, test_db, test_user):
        """测试 generate_api_key 生成唯一?API Key�?""
        plain_key1, api_key1 = await test_db.generate_api_key(test_user.id, "Key 1")
        plain_key2, api_key2 = await test_db.generate_api_key(test_user.id, "Key 2")
        
        assert plain_key1 != plain_key2
        assert api_key1.id != api_key2.id
        assert plain_key1.startswith("sk-")
        assert plain_key2.startswith("sk-")


    @pytest.mark.asyncio
    async def test_verify_api_key_valid(self, test_db, test_api_key, test_user):
        """测试验证有效?API Key�?""
        plain_key, api_key = test_api_key
        
        result = await test_db.verify_api_key(plain_key)
        
        assert result is not None
        user_id, key_id = result
        assert user_id == test_user.id
        assert key_id == api_key.id

    @pytest.mark.asyncio
    async def test_verify_api_key_invalid(self, test_db):
        """测试验证无效?API Key 返回 None�?""
        result = await test_db.verify_api_key("sk-invalid-key-12345")
        
        assert result is None

    @pytest.mark.asyncio
    async def test_donate_token_stores_and_returns(self, test_db, test_user):
        """测试 donate_token 存储并返?Token�?""
        refresh_token = "test-refresh-token-12345"
        
        success, message = await test_db.donate_token(
            user_id=test_user.id,
            refresh_token=refresh_token,
            visibility="private",
        )
        
        assert success is True
        assert "成功" in message
        
        # 验证 Token 已存?
        tokens = await test_db.get_user_tokens(test_user.id)
        assert len(tokens) == 1
        assert tokens[0].user_id == test_user.id
        assert tokens[0].visibility == "private"
        assert tokens[0].status == "active"

    @pytest.mark.asyncio
    async def test_donate_token_duplicate_rejected(self, test_db, test_user):
        """测试重复捐赠相同 Token 被拒绝�?""
        refresh_token = "duplicate-token-12345"
        
        success1, _ = await test_db.donate_token(
            user_id=test_user.id,
            refresh_token=refresh_token,
        )
        success2, message2 = await test_db.donate_token(
            user_id=test_user.id,
            refresh_token=refresh_token,
        )
        
        assert success1 is True
        assert success2 is False
        assert "已存�? in message2

    @pytest.mark.asyncio
    async def test_token_encryption_roundtrip(self, test_db, test_user):
        """测试 Token 加密解密 round-trip�?""
        original_token = "my-secret-refresh-token-xyz"
        
        await test_db.donate_token(
            user_id=test_user.id,
            refresh_token=original_token,
        )
        
        tokens = await test_db.get_user_tokens(test_user.id)
        token_id = tokens[0].id
        
        decrypted = await test_db.get_decrypted_token(token_id)
        
        assert decrypted == original_token
