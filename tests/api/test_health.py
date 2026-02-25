# -*- coding: utf-8 -*-

"""
API 健康检查端点测�?

测试服务状态监控相关的 API 端点?
"""

import pytest


class TestHealthEndpoints:
    """健康检查端点测试类�?""

    @pytest.mark.asyncio
    async def test_health_endpoint_returns_200(self, test_client):
        """测试 GET /health 返回 200 状态码�?""
        response = await test_client.get("/health")
        
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_endpoint_returns_json(self, test_client):
        """测试 GET /health 返回 JSON 响应�?""
        response = await test_client.get("/health")
        
        assert response.headers.get("content-type", "").startswith("application/json")
        data = response.json()
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_health_endpoint_status_healthy(self, test_client):
        """测试健康检查响应包?status ?healthy�?""
        response = await test_client.get("/health")
        data = response.json()
        
        assert "status" in data
        assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_api_endpoint_returns_version(self, test_client):
        """测试 GET /api 返回版本信息�?""
        response = await test_client.get("/api")
        
        assert response.status_code == 200
        data = response.json()
        assert "version" in data
        assert "status" in data
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_metrics_endpoint_returns_data(self, test_client):
        """测试 GET /metrics 返回指标数据�?""
        response = await test_client.get("/metrics")
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
