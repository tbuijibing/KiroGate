# -*- coding: utf-8 -*-

"""
认证模块单元测试?

测试 GeekAuthManager 类的 Token 过期检测和认证类型检�?
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from geek_gateway.auth import GeekAuthManager, AuthType, TOKEN_REFRESH_THRESHOLD


class TestGeekAuthManager:
    """认证管理测试类�?""

    def test_is_token_expiring_soon_when_expiring(self):
        """测试 Token 即将过期时返?True�?""
        manager = MagicMock(spec=GeekAuthManager)
        
        # 设置过期时间为当前时?+ 60 秒（小于 TOKEN_REFRESH_THRESHOLD?
        now = datetime.now(timezone.utc)
        manager._expires_at = now + timedelta(seconds=60)
        
        # 调用实际方法
        result = GeekAuthManager.is_token_expiring_soon(manager)
        
        assert result is True

    def test_is_token_expiring_soon_when_not_expiring(self):
        """测试 Token 未过期时返回 False�?""
        manager = MagicMock(spec=GeekAuthManager)
        
        # 设置过期时间为当前时?+ 1 小时（大?TOKEN_REFRESH_THRESHOLD?
        now = datetime.now(timezone.utc)
        manager._expires_at = now + timedelta(hours=1)
        
        result = GeekAuthManager.is_token_expiring_soon(manager)
        
        assert result is False

    def test_is_token_expiring_soon_when_no_expiration(self):
        """测试未设置过期时间时返回 True�?""
        manager = MagicMock(spec=GeekAuthManager)
        manager._expires_at = None
        
        result = GeekAuthManager.is_token_expiring_soon(manager)
        
        assert result is True

    def test_is_token_expiring_soon_at_threshold_boundary(self):
        """测试 Token 恰好在阈值边界时返回 True�?""
        manager = MagicMock(spec=GeekAuthManager)
        
        # 设置过期时间恰好等于�?
        now = datetime.now(timezone.utc)
        manager._expires_at = now + timedelta(seconds=TOKEN_REFRESH_THRESHOLD)
        
        result = GeekAuthManager.is_token_expiring_soon(manager)
        
        assert result is True

    def test_is_token_expiring_soon_just_after_threshold(self):
        """测试 Token 刚好超过阈值时返回 False�?""
        manager = MagicMock(spec=GeekAuthManager)
        
        # 设置过期时间比阈值多 1 ?
        now = datetime.now(timezone.utc)
        manager._expires_at = now + timedelta(seconds=TOKEN_REFRESH_THRESHOLD + 1)
        
        result = GeekAuthManager.is_token_expiring_soon(manager)
        
        assert result is False

    def test_detect_auth_type_idc(self):
        """测试检?IDC 认证类型�?""
        manager = MagicMock(spec=GeekAuthManager)
        manager._client_id = "test-client-id"
        manager._client_secret = "test-client-secret"
        manager._auth_type = None
        
        GeekAuthManager._detect_auth_type(manager)
        
        assert manager._auth_type == AuthType.IDC

    def test_detect_auth_type_social_no_credentials(self):
        """测试无凭证时检测为 SOCIAL 认证类型�?""
        manager = MagicMock(spec=GeekAuthManager)
        manager._client_id = None
        manager._client_secret = None
        manager._auth_type = None
        
        GeekAuthManager._detect_auth_type(manager)
        
        assert manager._auth_type == AuthType.SOCIAL

    def test_detect_auth_type_social_partial_credentials(self):
        """测试只有部分凭证时检测为 SOCIAL 认证类型�?""
        # 只有 client_id
        manager1 = MagicMock(spec=GeekAuthManager)
        manager1._client_id = "test-client-id"
        manager1._client_secret = None
        manager1._auth_type = None
        
        GeekAuthManager._detect_auth_type(manager1)
        assert manager1._auth_type == AuthType.SOCIAL
        
        # 只有 client_secret
        manager2 = MagicMock(spec=GeekAuthManager)
        manager2._client_id = None
        manager2._client_secret = "test-client-secret"
        manager2._auth_type = None
        
        GeekAuthManager._detect_auth_type(manager2)
        assert manager2._auth_type == AuthType.SOCIAL

    def test_detect_auth_type_social_empty_credentials(self):
        """测试空字符串凭证时检测为 SOCIAL 认证类型�?""
        manager = MagicMock(spec=GeekAuthManager)
        manager._client_id = ""
        manager._client_secret = ""
        manager._auth_type = None
        
        GeekAuthManager._detect_auth_type(manager)
        
        assert manager._auth_type == AuthType.SOCIAL
