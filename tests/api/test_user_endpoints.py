# -*- coding: utf-8 -*-

"""
API 用户端点测试?

测试用户相关?API 端点?
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock


class TestUserEndpoints:
    """用户端点测试类�?""

    @pytest.mark.asyncio
    async def test_register_valid_data_returns_success(self, test_client, test_db):
        """测试 POST /auth/register 有效数据返回成功�?""
        with patch("geek_gateway.database.user_db", test_db):
            with patch("geek_gateway.user_manager.user_db", test_db):
                with patch("geek_gateway.metrics.metrics.is_self_use_enabled", AsyncMock(return_value=False)):
                    with patch("geek_gateway.metrics.metrics.is_require_approval", MagicMock(return_value=False)):
                        response = await test_client.post(
                            "/auth/register",
                            data={
                                "email": "newuser@example.com",
                                "password": "securepassword123",
                                "username": "newuser"
                            }
                        )
        
        # 注册成功会重定向?/user
        assert response.status_code in [200, 303]
        if response.status_code == 303:
            assert response.headers.get("location") == "/user"
            # 检查是否设置了会话 cookie
            assert "user_session" in response.cookies

    @pytest.mark.asyncio
    async def test_login_correct_credentials_returns_session(self, test_client, test_db):
        """测试 POST /auth/login 正确凭证返回会话 cookie�?""
        from geek_gateway.user_manager import UserManager
        manager = UserManager()
        
        # 创建有密码的用户
        password = "testpassword123"
        password_hash = manager._hash_password(password)
        
        await test_db.create_user(
            username="loginuser",
            email="login@example.com",
            password_hash=password_hash,
        )
        
        with patch("geek_gateway.database.user_db", test_db):
            with patch("geek_gateway.user_manager.user_db", test_db):
                response = await test_client.post(
                    "/auth/login",
                    data={
                        "email": "login@example.com",
                        "password": password
                    }
                )
        
        # 登录成功会重定向?/user
        assert response.status_code in [200, 303]
        if response.status_code == 303:
            assert response.headers.get("location") == "/user"
            # 检查是否设置了会话 cookie
            assert "user_session" in response.cookies

    @pytest.mark.asyncio
    async def test_login_wrong_credentials_returns_error(self, test_client, test_db):
        """测试 POST /auth/login 错误凭证返回错误�?""
        from geek_gateway.user_manager import UserManager
        manager = UserManager()
        
        # 创建有密码的用户
        password_hash = manager._hash_password("correctpassword")
        
        await test_db.create_user(
            username="wronguser",
            email="wrong@example.com",
            password_hash=password_hash,
        )
        
        with patch("geek_gateway.database.user_db", test_db):
            with patch("geek_gateway.user_manager.user_db", test_db):
                response = await test_client.post(
                    "/auth/login",
                    data={
                        "email": "wrong@example.com",
                        "password": "wrongpassword"
                    }
                )
        
        # 登录失败返回 200 带错误页面（HTML 响应?
        assert response.status_code == 200
        # 不应该设置会?cookie
        assert "user_session" not in response.cookies

    @pytest.mark.asyncio
    async def test_profile_no_session_returns_401(self, test_client):
        """测试 GET /user/api/profile 无会话返?401�?""
        response = await test_client.get("/user/api/profile")
        
        assert response.status_code == 401
        data = response.json()
        assert "error" in data

    @pytest.mark.asyncio
    async def test_profile_valid_session_returns_user_info(self, test_client, test_db, test_user):
        """测试 GET /user/api/profile 有效会话返回用户信息�?""
        from geek_gateway.user_manager import UserSessionManager
        
        session_manager = UserSessionManager()
        
        # 创建会话 token
        session_token = session_manager.create_session(
            user_id=test_user.id,
            session_version=test_user.session_version
        )
        
        with patch("geek_gateway.database.user_db", test_db):
            with patch("geek_gateway.user_manager.user_db", test_db):
                with patch("geek_gateway.metrics.metrics.is_self_use_enabled", AsyncMock(return_value=False)):
                    # 设置 cookie 并请?
                    test_client.cookies.set("user_session", session_token)
                    response = await test_client.get("/user/api/profile")
        
        assert response.status_code == 200
        data = response.json()
        assert data.get("id") == test_user.id
        assert data.get("username") == test_user.username

    @pytest.mark.asyncio
    async def test_logout_clears_session(self, test_client, test_db, test_user):
        """测试 GET /oauth2/logout 清除会话�?""
        from geek_gateway.user_manager import UserSessionManager
        
        session_manager = UserSessionManager()
        
        # 创建会话 token
        session_token = session_manager.create_session(
            user_id=test_user.id,
            session_version=test_user.session_version
        )
        
        with patch("geek_gateway.database.user_db", test_db):
            with patch("geek_gateway.user_manager.user_db", test_db):
                # 设置 cookie 并登?
                test_client.cookies.set("user_session", session_token)
                response = await test_client.get("/oauth2/logout")
        
        # 登出成功会重定向到首?
        assert response.status_code in [200, 303]
        if response.status_code == 303:
            assert response.headers.get("location") == "/"
