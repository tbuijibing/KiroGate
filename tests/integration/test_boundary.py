# -*- coding: utf-8 -*-

"""
边界条件和错误处理测�?

测试系统在异常情况下的行�?
"""

import pytest
from unittest.mock import patch, AsyncMock

from geek_gateway.user_manager import UserManager


class TestBoundaryConditions:
    """边界条件测试类�?""

    @pytest.mark.asyncio
    async def test_create_user_no_identity_raises_error(self, test_db):
        """测试无身份标识创建用户抛?ValueError�?""
        with pytest.raises(ValueError) as exc_info:
            await test_db.create_user(
                username="noidentity",
                # 没有提供 linuxdo_id, github_id ?email
            )
        
        assert "必须提供" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_short_password_registration_error(self, test_db):
        """测试密码长度小于 8 位返回错误�?""
        manager = UserManager()
        
        with patch("geek_gateway.user_manager.user_db", test_db):
            with patch("geek_gateway.metrics.metrics.is_self_use_enabled", AsyncMock(return_value=False)):
                user, error = await manager.register_with_email(
                    email="short@example.com",
                    password="short",  # 只有 5 个字�?
                    username="shortpwd"
                )
                
                assert user is None
                assert error is not None
                assert "8" in error or "�? in error

    @pytest.mark.asyncio
    async def test_invalid_email_format_error(self, test_db):
        """测试无效邮箱格式返回错误�?""
        manager = UserManager()
        
        with patch("geek_gateway.user_manager.user_db", test_db):
            with patch("geek_gateway.metrics.metrics.is_self_use_enabled", AsyncMock(return_value=False)):
                # 测试没有 @ 的邮?
                user, error = await manager.register_with_email(
                    email="invalidemail",
                    password="validpassword123",
                    username="invalid"
                )
                
                assert user is None
                assert error is not None
                assert "邮箱" in error or "格式" in error


    @pytest.mark.asyncio
    async def test_empty_email_error(self, test_db):
        """测试空邮箱返回错误�?""
        manager = UserManager()
        
        with patch("geek_gateway.user_manager.user_db", test_db):
            with patch("geek_gateway.metrics.metrics.is_self_use_enabled", AsyncMock(return_value=False)):
                user, error = await manager.register_with_email(
                    email="",
                    password="validpassword123",
                    username="empty"
                )
                
                assert user is None
                assert error is not None

    @pytest.mark.asyncio
    async def test_empty_password_login_error(self, test_db):
        """测试空密码登录返回错误�?""
        manager = UserManager()
        
        with patch("geek_gateway.user_manager.user_db", test_db):
            user, error = await manager.login_with_email(
                email="test@example.com",
                password=""
            )
            
            assert user is None
            assert error is not None
            assert "�? in error or "不能" in error

    @pytest.mark.asyncio
    async def test_invalid_token_status_rejected(self, test_db, test_user):
        """测试无效�?Token 状态被拒绝�?""
        # 捐赠 Token
        await test_db.donate_token(
            user_id=test_user.id,
            refresh_token="boundary-test-token",
        )
        
        tokens = await test_db.get_user_tokens(test_user.id)
        token_id = tokens[0].id
        
        # 尝试设置无效�?
        result = await test_db.set_token_status(token_id, "invalid_status")
        
        assert result is False

    @pytest.mark.asyncio
    async def test_invalid_visibility_rejected(self, test_db, test_user):
        """测试无效的可见性被拒绝�?""
        # 捐赠 Token
        await test_db.donate_token(
            user_id=test_user.id,
            refresh_token="visibility-test-token",
        )
        
        tokens = await test_db.get_user_tokens(test_user.id)
        token_id = tokens[0].id
        
        # 尝试设置无效可见?
        result = await test_db.set_token_visibility(token_id, "invalid_visibility")
        
        assert result is False

    @pytest.mark.asyncio
    async def test_invalid_approval_status_rejected(self, test_db, test_user):
        """测试无效的审核状态被拒绝�?""
        with pytest.raises(ValueError) as exc_info:
            await test_db.set_user_approval_status(test_user.id, "invalid_status")
        
        assert "无效" in str(exc_info.value)
