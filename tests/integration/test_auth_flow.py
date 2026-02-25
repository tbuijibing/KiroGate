# -*- coding: utf-8 -*-

"""
用户认证集成测试?

测试用户认证的完整流程，包括注册、登录、登�?
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from geek_gateway.user_manager import UserManager, UserSessionManager


class TestAuthenticationFlow:
    """认证流程测试类�?""

    @pytest.mark.asyncio
    async def test_email_registration_and_login_flow(self, test_db):
        """测试邮箱注册后登录流程�?""
        manager = UserManager()
        
        email = "newuser@example.com"
        password = "securepassword123"
        
        with patch("geek_gateway.user_manager.user_db", test_db):
            # is_self_use_enabled 是异步方法，使用 AsyncMock
            with patch("geek_gateway.metrics.metrics.is_self_use_enabled", AsyncMock(return_value=False)):
                # is_require_approval 在代码中被同步调用（虽然定义为异步），使?MagicMock
                with patch("geek_gateway.metrics.metrics.is_require_approval", MagicMock(return_value=False)):
                    # 注册
                    user, session_or_error = await manager.register_with_email(
                        email=email,
                        password=password,
                        username="newuser"
                    )
                    
                    assert user is not None
                    assert session_or_error is not None  # session token
                    
                    # 登录
                    login_user, login_session = await manager.login_with_email(
                        email=email,
                        password=password
                    )
                    
                    assert login_user is not None
                    assert login_user.id == user.id
                    assert login_session is not None

    @pytest.mark.asyncio
    async def test_logout_invalidates_session(self, test_db, test_user):
        """测试登出后会话失效�?""
        manager = UserManager()
        session_manager = UserSessionManager()
        
        # 创建会话
        session_token = session_manager.create_session(
            user_id=test_user.id,
            session_version=test_user.session_version
        )
        
        with patch("geek_gateway.user_manager.user_db", test_db):
            # 验证会话有效
            user_id = await session_manager.verify_session(session_token)
            assert user_id == test_user.id
            
            # 登出
            await manager.logout(test_user.id)
            
            # 验证会话失效
            user_id = await session_manager.verify_session(session_token)
            assert user_id is None

    @pytest.mark.asyncio
    async def test_admin_revoke_sessions(self, test_db, test_user):
        """测试管理员撤销会话�?""
        manager = UserManager()
        session_manager = UserSessionManager()
        
        # 创建多个会话
        session1 = session_manager.create_session(
            user_id=test_user.id,
            session_version=test_user.session_version
        )
        session2 = session_manager.create_session(
            user_id=test_user.id,
            session_version=test_user.session_version
        )
        
        with patch("geek_gateway.user_manager.user_db", test_db):
            # 验证会话有效
            assert await session_manager.verify_session(session1) == test_user.id
            assert await session_manager.verify_session(session2) == test_user.id
            
            # 管理员撤销所有会?
            new_version = await manager.revoke_user_sessions(test_user.id)
            
            assert new_version > test_user.session_version
            
            # 验证所有会话失?
            assert await session_manager.verify_session(session1) is None
            assert await session_manager.verify_session(session2) is None

    @pytest.mark.asyncio
    async def test_duplicate_email_registration_error(self, test_db, test_user):
        """测试重复邮箱注册返回错误�?""
        manager = UserManager()
        
        with patch("geek_gateway.user_manager.user_db", test_db):
            with patch("geek_gateway.metrics.metrics.is_self_use_enabled", AsyncMock(return_value=False)):
                # 尝试使用已存在的邮箱注册
                user, error = await manager.register_with_email(
                    email=test_user.email,
                    password="newpassword123",
                    username="duplicate"
                )
                
                assert user is None
                assert error is not None
                assert "已注�? in error

    @pytest.mark.asyncio
    async def test_banned_user_login_rejected(self, test_db):
        """测试封禁用户登录被拒绝�?""
        manager = UserManager()
        
        # 先设置密�?
        password = "testpassword123"
        password_hash = manager._hash_password(password)
        
        # 创建有密码的用户
        user = await test_db.create_user(
            username="banneduser",
            email="banned@example.com",
            password_hash=password_hash,
        )
        
        # 封禁用户
        await test_db.set_user_banned(user.id, True)
        
        with patch("geek_gateway.user_manager.user_db", test_db):
            # 尝试登录
            login_user, error = await manager.login_with_email(
                email="banned@example.com",
                password=password
            )
            
            assert login_user is None
            assert error is not None
            assert "封禁" in error

    @pytest.mark.asyncio
    async def test_wrong_password_login_rejected(self, test_db):
        """测试错误密码登录被拒绝�?""
        manager = UserManager()
        
        # 创建有密码的用户
        password = "correctpassword"
        password_hash = manager._hash_password(password)
        
        await test_db.create_user(
            username="passworduser",
            email="password@example.com",
            password_hash=password_hash,
        )
        
        with patch("geek_gateway.user_manager.user_db", test_db):
            # 尝试用错误密码登?
            user, error = await manager.login_with_email(
                email="password@example.com",
                password="wrongpassword"
            )
            
            assert user is None
            assert error is not None
            assert "错误" in error
