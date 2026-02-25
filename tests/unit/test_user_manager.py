# -*- coding: utf-8 -*-

"""
用户管理模块单元测试?

测试 UserSessionManager ?UserManager �?
"""

import pytest
from unittest.mock import patch, AsyncMock
import time

from itsdangerous import SignatureExpired

from geek_gateway.user_manager import UserSessionManager, UserManager


class TestUserSessionManager:
    """会话管理测试类�?""

    def test_create_session_generates_token(self):
        """测试 create_session 生成签名 token�?""
        manager = UserSessionManager()
        
        token = manager.create_session(user_id=1, session_version=1)
        
        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 0

    @pytest.mark.asyncio
    async def test_verify_session_valid_token(self, test_db, test_user):
        """测试验证有效?session token�?""
        manager = UserSessionManager()
        
        token = manager.create_session(user_id=test_user.id, session_version=test_user.session_version)
        
        with patch("geek_gateway.user_manager.user_db", test_db):
            user_id = await manager.verify_session(token)
        
        assert user_id == test_user.id

    @pytest.mark.asyncio
    async def test_verify_session_expired_token(self, test_db, test_user):
        """测试验证过期?session token 返回 None�?""
        manager = UserSessionManager()
        
        token = manager.create_session(user_id=test_user.id, session_version=test_user.session_version)
        
        # Mock loads 方法抛出 SignatureExpired 异常模拟过期 token
        with patch("geek_gateway.user_manager.user_db", test_db):
            with patch.object(manager._serializer, "loads", side_effect=SignatureExpired("Signature expired")):
                user_id = await manager.verify_session(token)
        
        assert user_id is None

    @pytest.mark.asyncio
    async def test_verify_session_version_mismatch(self, test_db, test_user):
        """测试会话版本不匹配时返回 None�?""
        manager = UserSessionManager()
        
        # 使用旧版本创?token
        old_version = test_user.session_version
        token = manager.create_session(user_id=test_user.id, session_version=old_version)
        
        # 增加数据库中的会话版?
        await test_db.increment_session_version(test_user.id)
        
        with patch("geek_gateway.user_manager.user_db", test_db):
            user_id = await manager.verify_session(token)
        
        assert user_id is None

    def test_create_oauth_state(self):
        """测试创建 OAuth state�?""
        manager = UserSessionManager()
        
        state = manager.create_oauth_state()
        
        assert state is not None
        assert isinstance(state, str)
        assert len(state) > 0

    def test_verify_oauth_state_valid(self):
        """测试验证有效?OAuth state�?""
        manager = UserSessionManager()
        
        state = manager.create_oauth_state()
        result = manager.verify_oauth_state(state)
        
        assert result is True

    def test_verify_oauth_state_invalid(self):
        """测试验证无效?OAuth state�?""
        manager = UserSessionManager()
        
        result = manager.verify_oauth_state("invalid-state")
        
        assert result is False

    def test_verify_oauth_state_used_twice(self):
        """测试 OAuth state 只能使用一次�?""
        manager = UserSessionManager()
        
        state = manager.create_oauth_state()
        first_result = manager.verify_oauth_state(state)
        second_result = manager.verify_oauth_state(state)
        
        assert first_result is True
        assert second_result is False


class TestUserManager:
    """用户管理测试类�?""

    def test_hash_password_generates_pbkdf2_format(self):
        """测试 _hash_password 生成 PBKDF2 格式哈希�?""
        manager = UserManager()
        
        password = "testpassword123"
        hashed = manager._hash_password(password)
        
        assert hashed is not None
        assert hashed.startswith("pbkdf2_sha256$")
        parts = hashed.split("$")
        assert len(parts) == 4
        assert parts[0] == "pbkdf2_sha256"
        assert int(parts[1]) > 0  # iterations

    def test_verify_password_correct(self):
        """测试 _verify_password 正确密码返回 True�?""
        manager = UserManager()
        
        password = "correctpassword"
        hashed = manager._hash_password(password)
        
        result = manager._verify_password(password, hashed)
        
        assert result is True

    def test_verify_password_incorrect(self):
        """测试 _verify_password 错误密码返回 False�?""
        manager = UserManager()
        
        password = "correctpassword"
        hashed = manager._hash_password(password)
        
        result = manager._verify_password("wrongpassword", hashed)
        
        assert result is False

    def test_verify_password_invalid_hash_format(self):
        """测试 _verify_password 无效哈希格式返回 False�?""
        manager = UserManager()
        
        result = manager._verify_password("anypassword", "invalid-hash-format")
        
        assert result is False

    def test_password_hash_roundtrip(self):
        """测试密码哈希 round-trip�?""
        manager = UserManager()
        
        passwords = ["simple", "Complex123!", "中文密码", "a" * 100]
        
        for password in passwords:
            hashed = manager._hash_password(password)
            assert manager._verify_password(password, hashed) is True
            assert manager._verify_password(password + "x", hashed) is False
