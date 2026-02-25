# -*- coding: utf-8 -*-

"""
API 认证端点测试?

测试需要认证的 API 端点的认证机�?
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock


class TestAuthEndpoints:
    """认证端点测试类�?""

    @pytest.mark.asyncio
    async def test_models_endpoint_no_auth_returns_401(self, test_client):
        """测试 /v1/models ?Authorization 返回 401�?""
        response = await test_client.get("/v1/models")
        
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_models_endpoint_valid_api_key_returns_200(self, test_client, test_db, test_user, test_api_key):
        """测试 /v1/models 有效 API Key 返回 200�?""
        plain_key, _ = test_api_key
        
        # 为用户捐赠一?Token，以?token_allocator 可以分配
        await test_db.donate_token(
            user_id=test_user.id,
            refresh_token="test-refresh-token-for-api-test",
            visibility="private",
        )
        
        # 模拟 token_allocator.get_best_token 返回一个有效的 Token ?AuthManager
        mock_token = MagicMock()
        mock_token.id = 1
        mock_auth_manager = MagicMock()
        mock_auth_manager._access_token = "test-access-token"
        mock_auth_manager.is_token_expiring_soon = MagicMock(return_value=False)
        
        with patch("geek_gateway.token_allocator.token_allocator") as mock_allocator:
            mock_allocator.get_best_token = AsyncMock(return_value=(mock_token, mock_auth_manager))
            
            response = await test_client.get(
                "/v1/models",
                headers={"Authorization": f"Bearer {plain_key}"}
            )
        
        assert response.status_code == 200
        data = response.json()
        assert "data" in data or "object" in data

    @pytest.mark.asyncio
    async def test_models_endpoint_invalid_api_key_returns_401(self, test_client):
        """测试 /v1/models 无效 API Key 返回 401�?""
        response = await test_client.get(
            "/v1/models",
            headers={"Authorization": "Bearer sk-invalid-key-12345"}
        )
        
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_chat_completions_no_auth_returns_401(self, test_client):
        """测试 /v1/chat/completions ?Authorization 返回 401�?""
        response = await test_client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-sonnet-4-5",
                "messages": [{"role": "user", "content": "Hello"}]
            }
        )
        
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_messages_no_api_key_returns_401(self, test_client):
        """测试 /v1/messages ?x-api-key 返回 401�?""
        response = await test_client.post(
            "/v1/messages",
            json={
                "model": "claude-sonnet-4-5",
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 100
            }
        )
        
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_chat_completions_invalid_api_key_returns_401(self, test_client):
        """测试 /v1/chat/completions 无效 API Key 返回 401�?""
        response = await test_client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-invalid-key"},
            json={
                "model": "claude-sonnet-4-5",
                "messages": [{"role": "user", "content": "Hello"}]
            }
        )
        
        assert response.status_code == 401
