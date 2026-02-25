# -*- coding: utf-8 -*-

"""
GeekGate 测试全局 fixtures?

提供测试所需的数据库、用户、API Key ?TestClient ?fixtures?
"""

import os
import sys

# 设置测试环境变量（必须在导入应用模块之前设置?
# 使用内存数据库进行测?
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["TOKEN_ENCRYPT_KEY"] = "test-encryption-key-for-testing"
os.environ["USER_SESSION_SECRET"] = "test-session-secret-for-testing"
os.environ["PROXY_API_KEY"] = "test-proxy-api-key"
os.environ["REFRESH_TOKEN"] = "test-refresh-token"

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, MagicMock

from geek_gateway.database import UserDatabase, User, APIKey


@pytest_asyncio.fixture
async def test_db():
    """提供隔离的内?SQLite 数据库实�?
    
    每个测试都会获得一个全新的数据库实例，确保测试之间相互隔离?
    测试完成后自动关闭数据库连接?
    
    Yields:
        UserDatabase: 已初始化的数据库实例
    """
    db = UserDatabase()
    await db.initialize()
    yield db
    await db.close()


@pytest_asyncio.fixture
async def test_user(test_db) -> User:
    """提供预创建的测试用户?
    
    创建一个标准测试用户，用于需要用户上下文的测�?
    
    Args:
        test_db: 测试数据?fixture
        
    Returns:
        User: 预创建的测试用户对象
    """
    user = await test_db.create_user(
        username="testuser",
        email="test@example.com",
        password_hash="pbkdf2_sha256$120000$testsalt$testhash",
    )
    return user


@pytest_asyncio.fixture
async def test_api_key(test_db, test_user) -> tuple[str, APIKey]:
    """提供预创建的 API Key?
    
    为测试用户创建一?API Key，返回原?key ?APIKey 对象?
    原始 key 用于 API 认证，APIKey 对象用于验证数据库记�?
    
    Args:
        test_db: 测试数据?fixture
        test_user: 测试用户 fixture
        
    Returns:
        tuple[str, APIKey]: (原始 API Key 字符? APIKey 数据库对?
    """
    plain_key, api_key = await test_db.generate_api_key(test_user.id, "Test Key")
    return plain_key, api_key


@pytest_asyncio.fixture
async def test_client(test_db):
    """提供配置好的 FastAPI TestClient?
    
    创建一?AsyncClient 实例，用于测?API 端点?
    通过 patch 将全局数据库替换为测试数据库，确保测试隔离?
    同时设置 app.state 中的 auth_manager ?model_cache?
    
    Args:
        test_db: 测试数据?fixture
        
    Yields:
        AsyncClient: 配置好的异步 HTTP 客户?
    """
    # 延迟导入 app，确保环境变量已设置
    from main import app
    from geek_gateway.auth import GeekAuthManager
    from geek_gateway.cache import ModelInfoCache
    from geek_gateway.metrics import metrics
    
    # 创建模拟?auth_manager
    mock_auth_manager = MagicMock(spec=GeekAuthManager)
    mock_auth_manager._access_token = "test-access-token"
    mock_auth_manager.is_token_expiring_soon = MagicMock(return_value=False)
    
    # 创建模拟?model_cache
    mock_model_cache = MagicMock(spec=ModelInfoCache)
    mock_model_cache.size = 0
    mock_model_cache.last_update_time = None
    
    # 初始?metrics
    await metrics.initialize()
    
    # Patch the global user_db in database module
    with patch("geek_gateway.database.user_db", test_db):
        # 设置 app.state
        app.state.auth_manager = mock_auth_manager
        app.state.model_cache = mock_model_cache
        app.state.is_shutting_down = False
        
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
def mock_oauth_response() -> dict:
    """提供模拟?OAuth2 响应数据?
    
    用于测试 OAuth2 登录流程，无需实际调用外部服务?
    
    Returns:
        dict: 模拟?OAuth2 token 响应
    """
    return {
        "access_token": "mock-access-token-12345",
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": "mock-refresh-token-67890",
    }


@pytest.fixture
def mock_oauth_user_info() -> dict:
    """提供模拟?OAuth2 用户信息?
    
    Returns:
        dict: 模拟的用户信息响?
    """
    return {
        "id": "12345",
        "username": "mockuser",
        "avatar_url": "https://example.com/avatar.png",
        "trust_level": 2,
    }


@pytest.fixture
def mock_kiro_token_response() -> dict:
    """提供模拟?Kiro Token 刷新响应?
    
    用于测试 Token 刷新逻辑，无需实际调用 Kiro API?
    
    Returns:
        dict: 模拟?Kiro Token 响应
    """
    return {
        "accessToken": "mock-kiro-access-token",
        "refreshToken": "mock-kiro-refresh-token",
        "expiresIn": 3600,
        "profileArn": "arn:aws:iam::123456789:user/mock-user",
    }


@pytest.fixture
def mock_oauth_error_response() -> dict:
    """提供模拟?OAuth2 错误响应?
    
    Returns:
        dict: 模拟?OAuth2 错误响应
    """
    return {
        "error": "invalid_grant",
        "error_description": "Authorization code expired",
    }


@pytest.fixture
def mock_kiro_token_error_response() -> dict:
    """提供模拟?Kiro Token 刷新错误响应?
    
    Returns:
        dict: 模拟?Kiro Token 错误响应
    """
    return {
        "error": "invalid_token",
        "message": "Refresh token is invalid or expired",
    }
