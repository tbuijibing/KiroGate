# -*- coding: utf-8 -*-

# GeekGate
# Based on kiro-openai-gateway by Jwadow (https://github.com/Jwadow/kiro-openai-gateway)
# Original Copyright (C) 2025 Jwadow
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""
GeekGate FastAPI routes.

Contains all API endpoints:
- / and /health: Health check
- /v1/models: Model list
- /v1/chat/completions: OpenAI compatible chat completions
- /v1/messages: Anthropic compatible messages API
"""

import asyncio
import hashlib
import json
import re
import secrets
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, Security, Header, Form, Query, File, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from loguru import logger

from geek_gateway.middleware import get_timestamp
from geek_gateway.config import (
    PROXY_API_KEY,
    AVAILABLE_MODELS,
    APP_VERSION,
    RATE_LIMIT_PER_MINUTE,
)
from geek_gateway.models import (
    OpenAIModel,
    ModelList,
    ChatCompletionRequest,
    AnthropicMessagesRequest,
)
from geek_gateway.auth import GeekAuthManager
from geek_gateway.auth_cache import auth_cache
from geek_gateway.tokenizer import count_message_tokens, count_tools_tokens, count_tokens
from geek_gateway.cache import ModelInfoCache
from geek_gateway.request_handler import RequestHandler
from geek_gateway.utils import get_kiro_headers
from geek_gateway.config import settings
from geek_gateway.pages import (
    render_home_page,
    render_docs_page,
    render_playground_page,
    render_deploy_page,
    render_status_page,
    render_dashboard_page,
    render_swagger_page,
    render_register_page,
)

def _hash_rate_key(value: str) -> str:
    """Hash rate limit key to avoid leaking secrets."""
    return hashlib.sha256(value.encode()).hexdigest()


def rate_limit_key_func(request: Request) -> str:
    """Rate limit key by user/api key when possible, fallback to IP."""
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return f"user:{user_id}"

    auth_header = request.headers.get("authorization", "")
    if auth_header:
        token = auth_header[7:] if auth_header.lower().startswith("bearer ") else auth_header
        if token:
            return f"auth:{_hash_rate_key(token)}"

    x_api_key = request.headers.get("x-api-key", "")
    if x_api_key:
        return f"auth:{_hash_rate_key(x_api_key)}"

    return get_remote_address(request)


# Initialize rate limiter
limiter = Limiter(key_func=rate_limit_key_func)

# 预创建速率限制装饰器（避免重复创建�?
_rate_limit_decorator_cache = None


def rate_limit_decorator():
    """
    Conditional rate limit decorator (cached).

    Applies rate limit when RATE_LIMIT_PER_MINUTE > 0,
    disabled when RATE_LIMIT_PER_MINUTE = 0.
    """
    global _rate_limit_decorator_cache
    if _rate_limit_decorator_cache is None:
        if RATE_LIMIT_PER_MINUTE > 0:
            _rate_limit_decorator_cache = limiter.limit(f"{RATE_LIMIT_PER_MINUTE}/minute")
        else:
            _rate_limit_decorator_cache = lambda func: func
    return _rate_limit_decorator_cache


try:
    from geek_gateway.debug_logger import debug_logger
except ImportError:
    debug_logger = None


# --- Security scheme ---
api_key_header = APIKeyHeader(name="Authorization", auto_error=False)


def _mask_token(token: str) -> str:
    """
    Mask token for logging (show only first and last 4 chars).

    Args:
        token: Token to mask

    Returns:
        Masked token string
    """
    if len(token) <= 8:
        return "***"
    return f"{token[:4]}...{token[-4:]}"


def _get_import_key_from_request(request: Request) -> str | None:
    """Extract import key from Authorization or x-import-key header."""
    auth_header = request.headers.get("authorization", "")
    if auth_header:
        if auth_header.lower().startswith("bearer "):
            candidate = auth_header[7:].strip()
        else:
            candidate = auth_header.strip()
        if candidate:
            return candidate
    key = request.headers.get("x-import-key", "").strip()
    return key or None


def _get_proxy_api_key(request: Request | None = None) -> str:
    try:
        from geek_gateway.metrics import metrics
        proxy_key = metrics._proxy_api_key
        if proxy_key:
            return proxy_key
    except Exception:
        pass
    return PROXY_API_KEY


def _is_https_request(request: Request) -> bool:
    """Return True if request is HTTPS (including proxy headers)."""
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto:
        return forwarded_proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme.lower() == "https"


def _cookie_secure(request: Request) -> bool:
    """Resolve secure cookie flag based on settings or request."""
    if settings.cookie_secure is not None:
        return bool(settings.cookie_secure)
    return _is_https_request(request)


def _request_origin(request: Request) -> str:
    """Build origin string from request or proxy headers."""
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")
    proto = (forwarded_proto.split(",")[0].strip() if forwarded_proto else request.url.scheme).lower()
    host = (forwarded_host.split(",")[0].strip() if forwarded_host else request.headers.get("host", request.url.netloc))
    return f"{proto}://{host}"


def _origin_matches(origin_value: str, request: Request) -> bool:
    """Check if origin or referer matches current request origin."""
    try:
        parsed = urlparse(origin_value)
    except Exception:
        return False
    if not parsed.scheme or not parsed.netloc:
        return False
    origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    return origin == _request_origin(request).lower()


def require_same_origin(request: Request) -> None:
    """Basic CSRF protection for browser-based admin/user endpoints."""
    if not settings.csrf_enabled:
        return
    origin = request.headers.get("origin")
    if origin and _origin_matches(origin, request):
        return
    referer = request.headers.get("referer")
    if referer and _origin_matches(referer, request):
        return
    raise HTTPException(status_code=403, detail="跨站请求被拒�?)


async def _parse_auth_header(auth_header: str, request: Request = None) -> tuple[str, GeekAuthManager, int | None, int | None]:
    """
    Parse Authorization header and return proxy key, AuthManager, and optional user/key IDs.

    Supports three formats:
    1. Traditional: "Bearer {PROXY_API_KEY}" - uses global AuthManager
    2. Multi-tenant: "Bearer {PROXY_API_KEY}:{REFRESH_TOKEN}" - creates per-user AuthManager
    3. User API Key: "Bearer sk-xxx" - uses user's donated tokens

    Args:
        auth_header: Authorization header value
        request: Optional FastAPI Request for accessing app.state

    Returns:
        Tuple of (proxy_key, auth_manager, user_id, api_key_id)
        user_id and api_key_id are set when using sk-xxx format

    Raises:
        HTTPException: 401 if key is invalid or missing
    """
    if not auth_header or not auth_header.startswith("Bearer "):
        logger.warning(f"[{get_timestamp()}] 缺少或无效的 Authorization 头格�?)
        raise HTTPException(status_code=401, detail="API Key 无效或缺�?)

    token = auth_header[7:]  # Remove "Bearer "

    proxy_api_key = _get_proxy_api_key(request)

    # Check if token contains ':' (multi-tenant format)
    if ':' in token:
        parts = token.split(':', 1)  # Split only once
        proxy_key = parts[0]
        refresh_token = parts[1]

        # Verify proxy key
        if not secrets.compare_digest(proxy_key, proxy_api_key):
            logger.warning(f"[{get_timestamp()}] 多租户模式下 Proxy Key 无效: {_mask_token(proxy_key)}")
            raise HTTPException(status_code=401, detail="API Key 无效或缺�?)

        # Get or create AuthManager for this refresh token
        logger.debug(f"[{get_timestamp()}] 多租户模? 使用自定�?Refresh Token {_mask_token(refresh_token)}")
        auth_manager = await auth_cache.get_or_create(
            refresh_token=refresh_token,
            region=settings.region,
            profile_arn=settings.profile_arn
        )
        return proxy_key, auth_manager, None, None

    # Traditional mode: verify entire token as PROXY_API_KEY
    if secrets.compare_digest(token, proxy_api_key):
        logger.debug(f"[{get_timestamp()}] 传统模式: 使用全局 AuthManager")
        return token, None, None, None

    # Check if it's a user API key (sk-xxx format)
    if token.startswith("sk-"):
        from geek_gateway.database import user_db
        from geek_gateway.token_allocator import token_allocator, NoTokenAvailable

        result = await user_db.verify_api_key(token)
        if not result:
            logger.warning(f"[{get_timestamp()}] 用户 API Key 无效: {_mask_token(token)}")
            raise HTTPException(status_code=401, detail="API Key 无效或缺�?)

        user_id, api_key_id = result

        # Check if user is banned
        user = await user_db.get_user(user_id)
        if not user or user.is_banned:
            logger.warning(f"[{get_timestamp()}] 被封禁用户尝试使。API Key: 用户ID={user_id}")
            raise HTTPException(status_code=403, detail="用户已被封禁")

        # Check user quota (daily/monthly)
        from geek_gateway.quota_manager import quota_manager
        allowed, quota_info = await quota_manager.check_user_quota(user_id)
        if not allowed:
            retry_after = quota_info.get("retry_after", 60) if quota_info else 60
            reset_at = quota_info.get("reset_at") if quota_info else None
            reason = quota_info.get("reason", "quota_exceeded") if quota_info else "quota_exceeded"
            logger.warning(f"[{get_timestamp()}] 用户 {user_id} 配额超限: {reason}")
            detail = {
                "error": "配额超限",
                "reason": reason,
                "retry_after": retry_after,
            }
            if reset_at:
                detail["reset_at"] = reset_at
            raise HTTPException(
                status_code=429,
                detail=detail,
                headers={"Retry-After": str(retry_after)},
            )

        # Check API Key RPM limit
        rpm_allowed, rpm_retry_after = await quota_manager.check_api_key_rpm(api_key_id)
        if not rpm_allowed:
            retry_after = rpm_retry_after or 60
            logger.warning(f"[{get_timestamp()}] API Key {api_key_id} RPM 超限")
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "API Key 速率限制",
                    "reason": "api_key_rpm_exceeded",
                    "retry_after": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )

        # Get best token for this user
        try:
            donated_token, auth_manager = await token_allocator.get_best_token(user_id)
            logger.debug(f"[{get_timestamp()}] 用户 API Key 模式: 用户ID={user_id}, Token ID={donated_token.id}")

            # Store token_id in request state for usage tracking
            if request:
                request.state.donated_token_id = donated_token.id
                request.state.api_key_id = api_key_id
                request.state.user_id = user_id

            # Increment usage counters after successful token allocation
            await quota_manager.increment_user_usage(user_id)
            await quota_manager.increment_api_key_rpm(api_key_id)

            return token, auth_manager, user_id, api_key_id
        except NoTokenAvailable as e:
            logger.warning(f"[{get_timestamp()}] 用户可用 Token 不足: 用户ID={user_id}, 错误={e}")
            raise HTTPException(status_code=503, detail="该用户暂无可用的 Token")

    logger.warning(f"[{get_timestamp()}] 传统模式。API Key 无效")
    raise HTTPException(status_code=401, detail="API Key 无效或缺�?)


async def verify_api_key(
    request: Request,
    auth_header: str = Security(api_key_header)
) -> GeekAuthManager:
    """
    Verify API key in Authorization header and return appropriate AuthManager.

    Supports three formats:
    1. Traditional: "Bearer {PROXY_API_KEY}" - uses global AuthManager
    2. Multi-tenant: "Bearer {PROXY_API_KEY}:{REFRESH_TOKEN}" - creates per-user AuthManager
    3. User API Key: "Bearer sk-xxx" - uses user's donated tokens

    Args:
        request: FastAPI Request for accessing app.state
        auth_header: Authorization header value

    Returns:
        GeekAuthManager instance (global or per-user)

    Raises:
        HTTPException: 401 if key is invalid or missing
    """
    proxy_key, auth_manager, user_id, api_key_id = await _parse_auth_header(auth_header, request)

    # If auth_manager is None, use global AuthManager
    if auth_manager is None:
        auth_manager = request.app.state.auth_manager

    return auth_manager


async def verify_anthropic_api_key(
    request: Request,
    x_api_key: str = Header(None, alias="x-api-key"),
    auth_header: str = Security(api_key_header)
) -> GeekAuthManager:
    """
    Verify Anthropic or OpenAI format API key and return appropriate AuthManager.

    Anthropic uses x-api-key header, but we also support
    standard Authorization: Bearer format for compatibility.

    Supports three formats:
    1. Traditional: "{PROXY_API_KEY}" - uses global AuthManager
    2. Multi-tenant: "{PROXY_API_KEY}:{REFRESH_TOKEN}" - creates per-user AuthManager
    3. User API Key: "sk-xxx" - uses user's donated tokens

    Args:
        request: FastAPI Request for accessing app.state
        x_api_key: x-api-key header value (Anthropic format)
        auth_header: Authorization header value (OpenAI format)

    Returns:
        GeekAuthManager instance (global or per-user)

    Raises:
        HTTPException: 401 if key is invalid or missing
    """
    proxy_api_key = _get_proxy_api_key(request)

    # Try x-api-key first (Anthropic format)
    if x_api_key:
        # Check if x-api-key contains ':' (multi-tenant format)
        if ':' in x_api_key:
            parts = x_api_key.split(':', 1)
            proxy_key = parts[0]
            refresh_token = parts[1]

            # Verify proxy key
            if not secrets.compare_digest(proxy_key, proxy_api_key):
                logger.warning(f"[{get_timestamp()}] x-api-key 多租户模式下 Proxy Key 无效: {_mask_token(proxy_key)}")
                raise HTTPException(status_code=401, detail="API Key 无效或缺�?)

            # Get or create AuthManager for this refresh token
            logger.debug(f"[{get_timestamp()}] x-api-key 多租户模? 使用自定�?Refresh Token {_mask_token(refresh_token)}")
            auth_manager = await auth_cache.get_or_create(
                refresh_token=refresh_token,
                region=settings.region,
                profile_arn=settings.profile_arn
            )
            return auth_manager

        # Traditional mode: verify entire x-api-key as PROXY_API_KEY
        if secrets.compare_digest(x_api_key, proxy_api_key):
            logger.debug(f"[{get_timestamp()}] x-api-key 传统模式: 使用全局 AuthManager")
            return request.app.state.auth_manager

        # Check if it's a user API key (sk-xxx format)
        if x_api_key.startswith("sk-"):
            from geek_gateway.database import user_db
            from geek_gateway.token_allocator import token_allocator, NoTokenAvailable

            result = await user_db.verify_api_key(x_api_key)
            if not result:
                logger.warning(f"[{get_timestamp()}] x-api-key 中的用户 API Key 无效: {_mask_token(x_api_key)}")
                raise HTTPException(status_code=401, detail="API Key 无效或缺�?)

            user_id, api_key_id = result

            # Check if user is banned
            user = await user_db.get_user(user_id)
            if not user or user.is_banned:
                logger.warning(f"[{get_timestamp()}] 被封禁用户尝试使。API Key: 用户ID={user_id}")
                raise HTTPException(status_code=403, detail="用户已被封禁")

            # Check user quota (daily/monthly)
            from geek_gateway.quota_manager import quota_manager
            allowed, quota_info = await quota_manager.check_user_quota(user_id)
            if not allowed:
                retry_after = quota_info.get("retry_after", 60) if quota_info else 60
                reset_at = quota_info.get("reset_at") if quota_info else None
                reason = quota_info.get("reason", "quota_exceeded") if quota_info else "quota_exceeded"
                logger.warning(f"[{get_timestamp()}] 用户 {user_id} 配额超限: {reason}")
                detail = {
                    "error": "配额超限",
                    "reason": reason,
                    "retry_after": retry_after,
                }
                if reset_at:
                    detail["reset_at"] = reset_at
                raise HTTPException(
                    status_code=429,
                    detail=detail,
                    headers={"Retry-After": str(retry_after)},
                )

            # Check API Key RPM limit
            rpm_allowed, rpm_retry_after = await quota_manager.check_api_key_rpm(api_key_id)
            if not rpm_allowed:
                retry_after = rpm_retry_after or 60
                logger.warning(f"[{get_timestamp()}] API Key {api_key_id} RPM 超限")
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "API Key 速率限制",
                        "reason": "api_key_rpm_exceeded",
                        "retry_after": retry_after,
                    },
                    headers={"Retry-After": str(retry_after)},
                )

            try:
                donated_token, auth_manager = await token_allocator.get_best_token(user_id)
                logger.debug(f"[{get_timestamp()}] x-api-key 用户 API Key 模式: 用户ID={user_id}, Token ID={donated_token.id}")

                request.state.donated_token_id = donated_token.id
                request.state.api_key_id = api_key_id
                request.state.user_id = user_id

                # Increment usage counters after successful token allocation
                await quota_manager.increment_user_usage(user_id)
                await quota_manager.increment_api_key_rpm(api_key_id)

                return auth_manager
            except NoTokenAvailable as e:
                logger.warning(f"[{get_timestamp()}] 用户可用 Token 不足: 用户ID={user_id}, 错误={e}")
                raise HTTPException(status_code=503, detail="该用户暂无可用的 Token")

    # Try Authorization header (OpenAI format)
    if auth_header:
        return await verify_api_key(request, auth_header)

    logger.warning(f"[{get_timestamp()}] Anthropic 端点访问。API Key 无效")
    raise HTTPException(status_code=401, detail="API Key 无效或缺�?)


# --- Router ---
router = APIRouter()


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    """
    Home page with dashboard.

    Returns:
        HTML home page
    """
    return HTMLResponse(content=render_home_page())


@router.get("/api", response_class=JSONResponse)
async def api_root():
    """
    API health check endpoint (JSON).

    Returns:
        Application status and version info
    """
    return {
        "status": "ok",
        "message": "Kiro API Gateway is running",
        "version": APP_VERSION
    }


@router.get("/docs", response_class=HTMLResponse, include_in_schema=False)
async def docs_page():
    """
    API documentation page.

    Returns:
        HTML documentation page
    """
    return HTMLResponse(content=render_docs_page())


@router.get("/playground", response_class=HTMLResponse, include_in_schema=False)
async def playground_page():
    """
    API playground page.

    Returns:
        HTML playground page
    """
    return HTMLResponse(content=render_playground_page())


@router.get("/deploy", response_class=HTMLResponse, include_in_schema=False)
async def deploy_page():
    """
    Deployment guide page.

    Returns:
        HTML deployment guide page
    """
    return HTMLResponse(content=render_deploy_page())


@router.get("/status", response_class=HTMLResponse, include_in_schema=False)
async def status_page(request: Request):
    """
    Status page with system health info.

    Returns:
        HTML status page
    """
    from geek_gateway.metrics import metrics

    auth_manager: GeekAuthManager = request.app.state.auth_manager
    model_cache: ModelInfoCache = request.app.state.model_cache

    # Check if token is valid
    token_valid = False
    try:
        if auth_manager._access_token and not auth_manager.is_token_expiring_soon():
            token_valid = True
    except Exception:
        token_valid = False

    status_data = {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": APP_VERSION,
        "token_valid": token_valid,
        "cache_size": model_cache.size,
        "cache_last_update": model_cache.last_update_time
    }

    return HTMLResponse(content=render_status_page(status_data))


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_page():
    """
    Dashboard page with metrics and charts.

    Returns:
        HTML dashboard page
    """
    return HTMLResponse(content=render_dashboard_page())


@router.get("/swagger", response_class=HTMLResponse, include_in_schema=False)
async def swagger_page():
    """
    Swagger UI page for API documentation.

    Returns:
        HTML Swagger UI page
    """
    return HTMLResponse(content=render_swagger_page())


@router.get("/health")
async def health(request: Request):
    """
    Detailed health check.

    Returns:
        Status, timestamp, version and runtime info
        
    During shutdown, returns 503 Service Unavailable.
    
    In distributed mode, also returns PostgreSQL and Redis connection status.
    """
    # 检查是否正在关�?
    if hasattr(request.app.state, 'is_shutting_down') and request.app.state.is_shutting_down:
        return JSONResponse(
            status_code=503,
            content={
                "status": "shutting_down",
                "message": "Service is shutting down"
            }
        )
    
    from geek_gateway.metrics import metrics

    auth_manager: GeekAuthManager = request.app.state.auth_manager
    model_cache: ModelInfoCache = request.app.state.model_cache

    # Check if token is valid
    token_valid = False
    try:
        if auth_manager._access_token and not auth_manager.is_token_expiring_soon():
            token_valid = True
    except Exception:
        token_valid = False

    # Update metrics
    await metrics.set_cache_size(model_cache.size)
    await metrics.set_token_valid(token_valid)

    # Calculate uptime
    uptime = int(metrics._start_time and (datetime.now(timezone.utc).timestamp() - metrics._start_time) or 0)

    # Base response
    response = {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": APP_VERSION,
        "token_valid": token_valid,
        "cache_size": model_cache.size,
        "cache_last_update": model_cache.last_update_time,
        "uptime": uptime,
    }

    # Add distributed mode information
    if settings.is_distributed:
        response["mode"] = "distributed"
        response["node_id"] = settings.node_id

        # Check PostgreSQL connection status
        postgres_status = "disconnected"
        try:
            from geek_gateway.database import user_db
            if user_db._backend:
                # Try a simple query to check connection
                await user_db._backend.fetch_one("SELECT 1 as test")
                postgres_status = "connected"
        except Exception as e:
            logger.debug(f"PostgreSQL health check failed: {e}")
            postgres_status = "disconnected"
            # Return 503 if PostgreSQL is down
            response["status"] = "unhealthy"
            response["postgres"] = postgres_status
            return JSONResponse(status_code=503, content=response)

        response["postgres"] = postgres_status

        # Check Redis connection status
        redis_status = "disconnected"
        try:
            from geek_gateway.redis_manager import redis_manager
            if redis_manager.is_available:
                client = await redis_manager.get_client()
                if client:
                    await client.ping()
                    redis_status = "connected"
        except Exception as e:
            logger.debug(f"Redis health check failed: {e}")
            redis_status = "disconnected"

        response["redis"] = redis_status
    else:
        response["mode"] = "single_node"

    return response


@router.get("/api/site-mode", include_in_schema=False)
async def get_site_mode():
    """Get current site mode (normal/self-use/maintenance)."""
    from geek_gateway.metrics import metrics

    site_enabled = await metrics.is_site_enabled()
    self_use_enabled = await metrics.is_self_use_enabled()

    if not site_enabled:
        mode = "maintenance"
        label = "维护�?
    elif self_use_enabled:
        mode = "self_use"
        label = "自用模式"
    else:
        mode = "normal"
        label = "正常运行"

    return {
        "mode": mode,
        "label": label,
        "site_enabled": site_enabled,
        "self_use_enabled": self_use_enabled,
    }


@router.get("/metrics")
async def get_metrics():
    """
    Get application metrics in JSON format.

    Returns:
        Metrics data dictionary
    """
    from geek_gateway.metrics import metrics
    return await metrics.get_metrics()


@router.get("/api/metrics")
async def get_api_metrics():
    """
    Get application metrics in Deno-compatible format for dashboard.

    Returns:
        Deno-compatible metrics data dictionary
    """
    from geek_gateway.metrics import metrics
    return await metrics.get_deno_compatible_metrics()


# ============================================================================
# Kiro Portal API - 账号信息查询
# ============================================================================

import cbor2

KIRO_PORTAL_API_BASE = "https://app.kiro.dev/service/KiroWebPortalService/operation"


async def kiro_portal_api_request(operation: str, body: dict, access_token: str, idp: str = "BuilderId") -> dict:
    """调用 Kiro Portal API (使用 CBOR 格式)"""
    import uuid

    headers = {
        "accept": "application/cbor",
        "content-type": "application/cbor",
        "smithy-protocol": "rpc-v2-cbor",
        "amz-sdk-invocation-id": str(uuid.uuid4()),
        "amz-sdk-request": "attempt=1; max=1",
        "x-amz-user-agent": "aws-sdk-js/1.0.0 GeekGate/1.0.0",
        "authorization": f"Bearer {access_token}",
        "cookie": f"Idp={idp}; AccessToken={access_token}",
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{KIRO_PORTAL_API_BASE}/{operation}",
            headers=headers,
            content=cbor2.dumps(body),
            timeout=30.0
        )

        if not response.is_success:
            error_message = f"HTTP {response.status_code}"
            try:
                error_data = cbor2.loads(response.content)
                if error_data.get("__type") and error_data.get("message"):
                    error_type = error_data["__type"].split("#")[-1]
                    error_message = f"{error_type}: {error_data['message']}"
                elif error_data.get("message"):
                    error_message = error_data["message"]
            except Exception:
                pass
            raise HTTPException(status_code=response.status_code, detail=error_message)

        return cbor2.loads(response.content)


async def get_kiro_account_info(access_token: str, idp: str = "BuilderId") -> dict:
    """获取账号使用量和订阅信息

    Args:
        access_token: Kiro access token
        idp: 身份提供商，可�? BuilderId, Github, Google
             如果不确定，会自动尝试多?idp
    """
    import time
    import asyncio

    # 尝试?idp 列表（按常见程度排序?
    idp_list = [idp] if idp != "BuilderId" else ["Github", "Google", "BuilderId"]

    last_error = None
    usage_data = None
    for try_idp in idp_list:
        try:
            usage_data = await kiro_portal_api_request(
                "GetUserUsageAndLimits",
                {"isEmailRequired": True, "origin": "KIRO_IDE"},
                access_token,
                try_idp
            )
            # 成功了，使用这个 idp 继续
            idp = try_idp
            break
        except HTTPException as e:
            last_error = e
            # 如果是认证错误，尝试下一?idp
            if e.status_code in (401, 403) and try_idp != idp_list[-1]:
                logger.debug(f"idp={try_idp} failed, trying next...")
                continue
            raise
    else:
        # 所?idp 都失败了
        if last_error:
            raise last_error
        raise HTTPException(status_code=401, detail="Authentication failed with all idp options")

    # 获取用户状态（用于检测封禁）
    user_status = "Active"
    try:
        user_info = await kiro_portal_api_request(
            "GetUserInfo",
            {"origin": "KIRO_IDE"},
            access_token,
            idp
        )
        user_status = user_info.get("status", "Active")
    except Exception as e:
        logger.warning(f"Failed to get user info: {e}")
        # 如果获取失败，检查错误信息判断是否封�?
        error_msg = str(e)
        if "AccountSuspendedException" in error_msg or "423" in error_msg:
            user_status = "Suspended"

    # 解析 Credits 使用�?
    credit_usage = None
    for item in usage_data.get("usageBreakdownList", []):
        if item.get("resourceType") == "CREDIT":
            credit_usage = item
            break

    subscription_title = usage_data.get("subscriptionInfo", {}).get("subscriptionTitle", "Free")

    # 规范化订阅类�?
    subscription_type = "Free"
    upper_title = subscription_title.upper()
    if "PRO_PLUS" in upper_title or "PRO+" in upper_title:
        subscription_type = "Pro_Plus"
    elif "PRO" in upper_title:
        subscription_type = "Pro"
    elif "ENTERPRISE" in upper_title:
        subscription_type = "Enterprise"
    elif "TEAMS" in upper_title:
        subscription_type = "Teams"

    # 基础额度
    base_limit = credit_usage.get("usageLimitWithPrecision") or credit_usage.get("usageLimit", 0) if credit_usage else 0
    base_current = credit_usage.get("currentUsageWithPrecision") or credit_usage.get("currentUsage", 0) if credit_usage else 0

    # 试用额度
    free_trial_limit = 0
    free_trial_current = 0
    free_trial_expiry = None
    if credit_usage and credit_usage.get("freeTrialInfo", {}).get("freeTrialStatus") == "ACTIVE":
        ft_info = credit_usage["freeTrialInfo"]
        free_trial_limit = ft_info.get("usageLimitWithPrecision") or ft_info.get("usageLimit", 0)
        free_trial_current = ft_info.get("currentUsageWithPrecision") or ft_info.get("currentUsage", 0)
        free_trial_expiry = ft_info.get("freeTrialExpiry")

    # 奖励额度
    bonuses = []
    if credit_usage and credit_usage.get("bonuses"):
        for bonus in credit_usage["bonuses"]:
            if bonus.get("status") == "ACTIVE":
                bonuses.append({
                    "code": bonus.get("bonusCode", ""),
                    "name": bonus.get("displayName", ""),
                    "current": bonus.get("currentUsageWithPrecision") or bonus.get("currentUsage", 0),
                    "limit": bonus.get("usageLimitWithPrecision") or bonus.get("usageLimit", 0),
                    "expiresAt": bonus.get("expiresAt"),
                })

    total_limit = base_limit + free_trial_limit + sum(b["limit"] for b in bonuses)
    total_current = base_current + free_trial_current + sum(b["current"] for b in bonuses)

    # 计算剩余天数
    days_remaining = None
    expires_at = None
    next_reset_date = usage_data.get("nextDateReset")
    if next_reset_date:
        from datetime import datetime
        try:
            reset_time = datetime.fromisoformat(next_reset_date.replace("Z", "+00:00"))
            expires_at = int(reset_time.timestamp() * 1000)
            days_remaining = max(0, (reset_time.timestamp() - time.time()) / 86400)
            days_remaining = int(days_remaining) + 1
        except Exception:
            pass

    return {
        "email": usage_data.get("userInfo", {}).get("email"),
        "userId": usage_data.get("userInfo", {}).get("userId"),
        "status": user_status,  # Active, Suspended �?
        "subscription": {
            "type": subscription_type,
            "title": subscription_title,
            "rawType": usage_data.get("subscriptionInfo", {}).get("type"),
            "expiresAt": expires_at,
            "daysRemaining": days_remaining,
            "upgradeCapability": usage_data.get("subscriptionInfo", {}).get("upgradeCapability"),
            "overageCapability": usage_data.get("subscriptionInfo", {}).get("overageCapability"),
            "managementTarget": usage_data.get("subscriptionInfo", {}).get("subscriptionManagementTarget"),
        },
        "usage": {
            "current": total_current,
            "limit": total_limit,
            "percentUsed": (total_current / total_limit * 100) if total_limit > 0 else 0,
            "baseLimit": base_limit,
            "baseCurrent": base_current,
            "freeTrialLimit": free_trial_limit,
            "freeTrialCurrent": free_trial_current,
            "freeTrialExpiry": free_trial_expiry,
            "bonuses": bonuses,
            "nextResetDate": next_reset_date,
            "resourceDetail": {
                "resourceType": credit_usage.get("resourceType") if credit_usage else None,
                "displayName": credit_usage.get("displayName") if credit_usage else None,
                "displayNamePlural": credit_usage.get("displayNamePlural") if credit_usage else None,
                "currency": credit_usage.get("currency") if credit_usage else None,
                "unit": credit_usage.get("unit") if credit_usage else None,
                "overageRate": credit_usage.get("overageRate") if credit_usage else None,
                "overageCap": credit_usage.get("overageCap") if credit_usage else None,
                "overageEnabled": usage_data.get("overageConfiguration", {}).get("overageEnabled"),
            } if credit_usage else None,
        },
        "lastUpdated": int(time.time() * 1000),
    }


@router.get("/metrics/prometheus")
async def get_prometheus_metrics():
    """
    Get application metrics in Prometheus format.

    Returns:
        Prometheus text format metrics
    """
    from geek_gateway.metrics import metrics
    return Response(
        content=metrics.export_prometheus(),
        media_type="text/plain; charset=utf-8"
    )


@router.get("/v1/models", response_model=ModelList)
@rate_limit_decorator()
async def get_models(
    request: Request,
    auth_manager: GeekAuthManager = Depends(verify_api_key)
):
    """
    Return available models list.

    Uses static model list with optional dynamic updates from API.
    Results are cached to reduce API load.

    Args:
        request: FastAPI Request for accessing app.state
        auth_manager: GeekAuthManager instance (from verify_api_key)

    Returns:
        ModelList containing available models
    """
    logger.info(f"[{get_timestamp()}] 收到 /v1/models 请求")

    model_cache: ModelInfoCache = request.app.state.model_cache

    # Trigger background refresh if cache is empty or stale
    if model_cache.is_empty() or model_cache.is_stale():
        # Don't block - just trigger refresh in background
        try:
            import asyncio
            asyncio.create_task(model_cache.refresh())
        except Exception as e:
            logger.warning(f"[{get_timestamp()}] 触发模型缓存刷新失败: {e}")

    # Return static model list immediately
    openai_models = [
        OpenAIModel(
            id=model_id,
            owned_by="anthropic",
            description="Claude model via Kiro API"
        )
        for model_id in AVAILABLE_MODELS
    ]

    return ModelList(data=openai_models)


@router.post("/v1/chat/completions")
@rate_limit_decorator()
async def chat_completions(
    request: Request,
    request_data: ChatCompletionRequest,
    auth_manager: GeekAuthManager = Depends(verify_api_key)
):
    """
    Chat completions endpoint - OpenAI API compatible.

    Accepts OpenAI format requests and converts to Kiro API.
    Supports streaming and non-streaming modes.

    Args:
        request: FastAPI Request for accessing app.state
        request_data: OpenAI ChatCompletionRequest format
        auth_manager: GeekAuthManager instance (from verify_api_key)

    Returns:
        StreamingResponse for streaming mode
        JSONResponse for non-streaming mode

    Raises:
        HTTPException: On validation or API errors
    """
    logger.info(f"[{get_timestamp()}] 收到 /v1/chat/completions 请求 (模型={request_data.model}, 流式={request_data.stream})")

    # Store auth_manager and model in request state for RequestHandler and metrics
    request.state.auth_manager = auth_manager
    request.state.model = request_data.model

    return await RequestHandler.process_request(
        request,
        request_data,
        "/v1/chat/completions",
        convert_to_openai=False,
        response_format="openai"
    )


# ==================================================================================================
# Anthropic Messages API Endpoint (/v1/messages)
# ==================================================================================================

@router.post("/v1/messages")
@rate_limit_decorator()
async def anthropic_messages(
    request: Request,
    request_data: AnthropicMessagesRequest,
    auth_manager: GeekAuthManager = Depends(verify_anthropic_api_key)
):
    """
    Anthropic Messages API endpoint - Anthropic SDK compatible.

    Accepts Anthropic format requests and converts to Kiro API.
    Supports streaming and non-streaming modes.
    Also supports WebSearch tool requests via Kiro MCP API.

    Args:
        request: FastAPI Request for accessing app.state
        request_data: Anthropic MessagesRequest format
        auth_manager: GeekAuthManager instance (from verify_anthropic_api_key)

    Returns:
        StreamingResponse for streaming mode
        JSONResponse for non-streaming mode

    Raises:
        HTTPException: On validation or API errors
    """
    logger.info(f"[{get_timestamp()}] 收到 /v1/messages 请求 (模型={request_data.model}, 流式={request_data.stream})")

    # Store auth_manager and model in request state for RequestHandler and metrics
    request.state.auth_manager = auth_manager
    request.state.model = request_data.model

    # 检查是否为 WebSearch 请求
    try:
        from geek_gateway.websearch import has_web_search_tool, handle_websearch_request
        if has_web_search_tool(request_data):
            logger.info(f"[{get_timestamp()}] 检测到 WebSearch 工具，路由到 WebSearch 处理")
            return await handle_websearch_request(request, request_data, auth_manager)
    except ImportError:
        pass  # websearch 模块不可用，继续正常处理

    return await RequestHandler.process_request(
        request,
        request_data,
        "/v1/messages",
        convert_to_openai=True,
        response_format="anthropic"
    )


# ==================================================================================================
# Count Tokens API Endpoint (/v1/messages/count_tokens)
# ==================================================================================================

@router.post("/v1/messages/count_tokens")
async def count_tokens_endpoint(
    request: Request,
    request_data: AnthropicMessagesRequest,
):
    """
    Count tokens in a messages request without making an API call.
    
    Compatible with Anthropic's count_tokens API.
    Returns estimated token count for the given messages.
    
    Args:
        request: FastAPI Request
        request_data: Anthropic MessagesRequest format
    
    Returns:
        JSONResponse with input_tokens count
    """
    logger.info(f"[{get_timestamp()}] 收到 /v1/messages/count_tokens 请求")
    
    # Count message tokens
    messages_tokens = 0
    if request_data.messages:
        # Convert to list of dicts for tokenizer
        messages_list = [msg.model_dump() if hasattr(msg, 'model_dump') else msg for msg in request_data.messages]
        messages_tokens = count_message_tokens(messages_list)
    
    # Count system prompt tokens
    system_tokens = 0
    if request_data.system:
        if isinstance(request_data.system, str):
            system_tokens = count_tokens(request_data.system)
        elif isinstance(request_data.system, list):
            for item in request_data.system:
                if hasattr(item, 'text'):
                    system_tokens += count_tokens(item.text)
                elif isinstance(item, dict) and 'text' in item:
                    system_tokens += count_tokens(item['text'])
    
    # Count tools tokens
    tools_tokens = 0
    if request_data.tools:
        tools_list = [tool.model_dump() if hasattr(tool, 'model_dump') else tool for tool in request_data.tools]
        tools_tokens = count_tools_tokens(tools_list)
    
    total_tokens = messages_tokens + system_tokens + tools_tokens
    
    logger.info(f"[{get_timestamp()}] Token 统计: messages={messages_tokens}, system={system_tokens}, tools={tools_tokens}, total={total_tokens}")
    
    return JSONResponse(content={"input_tokens": total_tokens})


# --- Rate limit error handler ---
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Handle rate limit errors."""
    return JSONResponse(
        status_code=429,
        content={
            "error": {
                "message": "Rate limit exceeded. Please try again later.",
                "type": "rate_limit_exceeded",
                "code": 429
            }
        }
    )


USER_DB_REQUIRED_TABLES = {"users"}
METRICS_DB_REQUIRED_TABLES = {"counters"}
DB_LABELS = {
    "users": "用户数据",
    "metrics": "统计数据",
}
ADMIN_DB_IMPORT_TTL_SECONDS = 15 * 60
_ADMIN_DB_IMPORT_SESSIONS: dict[str, dict] = {}


def _resolve_db_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    project_root = Path(__file__).resolve().parents[1]
    return (project_root / path).resolve()


def _get_db_paths() -> dict[str, Path]:
    from geek_gateway.database import USER_DB_FILE
    from geek_gateway.metrics import METRICS_DB_FILE
    return {
        "users": _resolve_db_path(USER_DB_FILE),
        "metrics": _resolve_db_path(METRICS_DB_FILE),
    }


def _parse_db_types(db_types_value: str | None, db_type_value: str | None = None) -> list[str]:
    raw: list[str] = []
    if db_types_value:
        raw = [item.strip().lower() for item in db_types_value.split(",") if item.strip()]
    elif db_type_value:
        raw = [db_type_value.strip().lower()]
    if not raw or "all" in raw:
        return ["users", "metrics"]
    invalid = [item for item in raw if item not in DB_LABELS]
    if invalid:
        raise HTTPException(status_code=400, detail="导出类型无效")
    seen: set[str] = set()
    selected: list[str] = []
    for item in raw:
        if item in DB_LABELS and item not in seen:
            selected.append(item)
            seen.add(item)
    return selected


def _cleanup_db_import_sessions() -> None:
    now = datetime.now(timezone.utc).timestamp()
    expired_tokens = [
        token
        for token, session in _ADMIN_DB_IMPORT_SESSIONS.items()
        if session.get("expires_at", 0) <= now
    ]
    for token in expired_tokens:
        session = _ADMIN_DB_IMPORT_SESSIONS.pop(token, None)
        if session and session.get("dir"):
            shutil.rmtree(session["dir"], ignore_errors=True)


def _create_db_import_session(upload_dir: Path, upload_path: Path, available: set[str]) -> str:
    token = secrets.token_urlsafe(24)
    _ADMIN_DB_IMPORT_SESSIONS[token] = {
        "dir": upload_dir,
        "path": upload_path,
        "available": available,
        "expires_at": datetime.now(timezone.utc).timestamp() + ADMIN_DB_IMPORT_TTL_SECONDS,
    }
    return token


def _get_db_import_session(token: str) -> dict | None:
    _cleanup_db_import_sessions()
    session = _ADMIN_DB_IMPORT_SESSIONS.get(token)
    if not session:
        return None
    if session.get("expires_at", 0) <= datetime.now(timezone.utc).timestamp():
        _ADMIN_DB_IMPORT_SESSIONS.pop(token, None)
        if session.get("dir"):
            shutil.rmtree(session["dir"], ignore_errors=True)
        return None
    return session


def _remove_db_import_session(token: str) -> None:
    session = _ADMIN_DB_IMPORT_SESSIONS.pop(token, None)
    if session and session.get("dir"):
        shutil.rmtree(session["dir"], ignore_errors=True)


def _is_sqlite_file(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            header = handle.read(16)
        return header.startswith(b"SQLite format 3")
    except OSError:
        return False


def _validate_sqlite_db(path: Path, required_tables: set[str]) -> tuple[bool, str | None]:
    if not _is_sqlite_file(path):
        return False, "文件不是有效�?SQLite 数据�?
    try:
        with sqlite3.connect(path) as conn:
            rows = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
    except sqlite3.Error:
        return False, "数据库读取失�?
    missing = required_tables - rows
    if missing:
        missing_list = "�?.join(sorted(missing))
        return False, f"数据库缺少必要表：{missing_list}"
    return True, None


def _backup_sqlite_db(src: Path, dest: Path) -> None:
    if not src.exists():
        raise FileNotFoundError(f"数据库不存在: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    with sqlite3.connect(src) as conn:
        with sqlite3.connect(dest) as backup:
            conn.backup(backup)


def _stream_file(path: Path, chunk_size: int = 1024 * 1024):
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                yield chunk
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def _replace_db_file(target: Path, new_file: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup = target.with_name(f"{target.stem}.bak-{timestamp}{target.suffix}")
        shutil.copy2(target, backup)
    shutil.move(str(new_file), str(target))


# ==================== Admin Routes (Hidden from Swagger) ====================

def create_admin_session() -> str:
    """Create signed admin session token."""
    from itsdangerous import URLSafeTimedSerializer
    from geek_gateway.config import ADMIN_SECRET_KEY
    serializer = URLSafeTimedSerializer(ADMIN_SECRET_KEY)
    return serializer.dumps({"admin": True})


def verify_admin_session(token: str) -> bool:
    """Verify admin session token."""
    if not token:
        return False
    try:
        from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
        from geek_gateway.config import ADMIN_SECRET_KEY, ADMIN_SESSION_MAX_AGE
        serializer = URLSafeTimedSerializer(ADMIN_SECRET_KEY)
        serializer.loads(token, max_age=ADMIN_SESSION_MAX_AGE)
        return True
    except Exception:
        return False


@router.get("/admin/login", response_class=HTMLResponse, include_in_schema=False)
async def admin_login_page():
    """Admin login page."""
    from geek_gateway.pages import render_admin_login_page
    return HTMLResponse(content=render_admin_login_page())


@router.post("/admin/login", include_in_schema=False)
async def admin_login(request: Request, password: str = Form(...)):
    """Handle admin login."""
    from geek_gateway.config import ADMIN_PASSWORD
    if password == ADMIN_PASSWORD:
        response = RedirectResponse(url="/admin", status_code=303)
        response.set_cookie(
            key="admin_session",
            value=create_admin_session(),
            httponly=True,
            max_age=86400,
            samesite=settings.admin_cookie_samesite,
            secure=_cookie_secure(request)
        )
        return response
    from geek_gateway.pages import render_admin_login_page
    return HTMLResponse(content=render_admin_login_page(error="密码错误"))


@router.get("/admin/logout", include_in_schema=False)
async def admin_logout():
    """Admin logout."""
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(key="admin_session")
    return response


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def admin_page(request: Request):
    """Admin dashboard page."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return RedirectResponse(url="/admin/login", status_code=303)
    from geek_gateway.pages import render_admin_page
    return HTMLResponse(content=render_admin_page())


@router.get("/admin/api/stats", include_in_schema=False)
async def admin_get_stats(request: Request):
    """Get admin statistics."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})
    from geek_gateway.metrics import metrics

    stats = await metrics.get_admin_stats()
    # Add cached tokens count
    stats["cached_tokens"] = auth_cache.size
    # Map snake_case for frontend
    return {
        "total_requests": stats.get("totalRequests", 0),
        "success_requests": stats.get("successRequests", 0),
        "failed_requests": stats.get("failedRequests", 0),
        "active_connections": stats.get("activeConnections", 0),
        "token_valid": stats.get("tokenValid", False),
        "site_enabled": stats.get("siteEnabled", True),
        "self_use_enabled": stats.get("selfUseEnabled", False),
        "require_approval": stats.get("requireApproval", True),
        "banned_count": stats.get("bannedIPs", 0),
        "cached_tokens": stats.get("cached_tokens", 0),
        "cache_size": stats.get("cacheSize", 0),
        "avg_latency": stats.get("avgLatency", 0),
    }


@router.get("/admin/api/ip-stats", include_in_schema=False)
async def admin_get_ip_stats(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str = Query("", alias="search"),
    sort_field: str = Query("count"),
    sort_order: str = Query("desc")
):
    """Get IP statistics."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})
    from geek_gateway.metrics import metrics
    offset = (page - 1) * page_size
    search = search.strip()
    items, total = metrics.get_ip_stats(
        limit=page_size,
        offset=offset,
        search=search,
        sort_field=sort_field,
        sort_order=sort_order
    )
    items = [
        {
            "ip": item.get("ip"),
            "count": item.get("count", 0),
            "last_seen": item.get("last_seen", item.get("lastSeen", 0)),
        }
        for item in items
    ]
    return {
        "items": items,
        "pagination": {"page": page, "page_size": page_size, "total": total}
    }


@router.get("/admin/api/blacklist", include_in_schema=False)
async def admin_get_blacklist(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str = Query("", alias="search"),
    sort_field: str = Query("banned_at"),
    sort_order: str = Query("desc")
):
    """Get IP blacklist."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})
    from geek_gateway.metrics import metrics
    offset = (page - 1) * page_size
    search = search.strip()
    items, total = metrics.get_blacklist(
        limit=page_size,
        offset=offset,
        search=search,
        sort_field=sort_field,
        sort_order=sort_order
    )
    items = [
        {
            "ip": item.get("ip"),
            "banned_at": item.get("banned_at", item.get("bannedAt", 0)),
            "reason": item.get("reason"),
        }
        for item in items
    ]
    return {
        "items": items,
        "pagination": {"page": page, "page_size": page_size, "total": total}
    }


@router.post("/admin/api/ban-ip", include_in_schema=False)
async def admin_ban_ip(
    request: Request,
    ip: str = Form(...),
    reason: str = Form(""),
    _csrf: None = Depends(require_same_origin)
):
    """Ban an IP address."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})
    from geek_gateway.metrics import metrics
    success = metrics.ban_ip(ip, reason)
    return {"success": success}


@router.post("/admin/api/unban-ip", include_in_schema=False)
async def admin_unban_ip(
    request: Request,
    ip: str = Form(...),
    _csrf: None = Depends(require_same_origin)
):
    """Unban an IP address."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})
    from geek_gateway.metrics import metrics
    success = metrics.unban_ip(ip)
    return {"success": success}


@router.post("/admin/api/toggle-site", include_in_schema=False)
async def admin_toggle_site(
    request: Request,
    enabled: bool = Form(...),
    _csrf: None = Depends(require_same_origin)
):
    """Toggle site status."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})
    from geek_gateway.metrics import metrics
    success = await metrics.set_site_enabled(enabled)
    return {"success": success, "enabled": enabled}


@router.post("/admin/api/toggle-self-use", include_in_schema=False)
async def admin_toggle_self_use(
    request: Request,
    enabled: bool = Form(...),
    _csrf: None = Depends(require_same_origin)
):
    """Toggle self-use mode."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})
    from geek_gateway.metrics import metrics
    success = await metrics.set_self_use_enabled(enabled)
    return {"success": success, "enabled": enabled}


@router.post("/admin/api/toggle-approval", include_in_schema=False)
async def admin_toggle_approval(
    request: Request,
    enabled: bool = Form(...),
    _csrf: None = Depends(require_same_origin)
):
    """Toggle registration approval requirement."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})
    from geek_gateway.metrics import metrics
    success = await metrics.set_require_approval(enabled)
    return {"success": success, "enabled": enabled}

@router.get("/admin/api/proxy-key", include_in_schema=False)
async def admin_get_proxy_key(request: Request):
    """Get proxy API key."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})
    from geek_gateway.metrics import metrics
    return {"proxy_api_key": await metrics.get_proxy_api_key()}


@router.post("/admin/api/proxy-key", include_in_schema=False)
async def admin_set_proxy_key(
    request: Request,
    proxy_api_key: str = Form(...),
    _csrf: None = Depends(require_same_origin)
):
    """Update proxy API key."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})
    proxy_api_key = proxy_api_key.strip()
    if not proxy_api_key:
        return JSONResponse(status_code=400, content={"error": "API Key 不能为空"})
    from geek_gateway.metrics import metrics
    success = await metrics.set_proxy_api_key(proxy_api_key)
    if not success:
        return JSONResponse(status_code=500, content={"error": "更新失败"})
    return {"success": True}


@router.post("/admin/api/refresh-token", include_in_schema=False)
async def admin_refresh_token(
    request: Request,
    _csrf: None = Depends(require_same_origin)
):
    """Force refresh Kiro token."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})
    try:
        auth_manager = getattr(request.app.state, "auth_manager", None)
        if auth_manager:
            await auth_manager.force_refresh()  # 管理员手动刷�?
            return {"success": True, "message": "Token 刷新成功"}
        return {"success": False, "message": "认证管理器不可用"}
    except Exception as e:
        logger.error(f"[{get_timestamp()}] Token 刷新失败: {e}")
        return {"success": False, "message": f"刷新失败: {str(e)}"}


@router.post("/admin/api/clear-cache", include_in_schema=False)
async def admin_clear_cache(
    request: Request,
    _csrf: None = Depends(require_same_origin)
):
    """Clear model cache."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})
    try:
        from geek_gateway.cache import model_cache
        await model_cache.refresh()
        return {"success": True, "message": "模型缓存已刷�?}
    except Exception as e:
        return {"success": False, "message": f"模型缓存刷新失败: {str(e)}"}


@router.get("/admin/api/db/info", include_in_schema=False)
async def admin_db_info(request: Request):
    """Get sqlite database sizes."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})
    db_paths = _get_db_paths()
    items = []
    for key, path in db_paths.items():
        exists = path.exists()
        size_bytes = path.stat().st_size if exists else None
        items.append({
            "key": key,
            "label": DB_LABELS.get(key, key),
            "exists": exists,
            "size_bytes": size_bytes,
        })
    return {"items": items}


@router.get("/admin/api/db/export", include_in_schema=False)
async def admin_export_db(
    request: Request,
    db_type: str = Query("all"),
    db_types: str | None = Query(None)
):
    """Export sqlite databases."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    try:
        selected = _parse_db_types(db_types, db_type)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

    db_paths = _get_db_paths()
    missing = [key for key in selected if not db_paths[key].exists()]
    if missing:
        labels = "�?.join(DB_LABELS.get(key, key) for key in missing)
        return JSONResponse(status_code=404, content={"error": f"数据库不存在：{labels}"})

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tmp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmp_zip_path = Path(tmp_zip.name)
    tmp_zip.close()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        with zipfile.ZipFile(tmp_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for key in selected:
                backup_path = tmpdir_path / f"{key}.db"
                _backup_sqlite_db(db_paths[key], backup_path)
                zf.write(backup_path, arcname=f"{key}.db")

    label_suffix = "-".join(selected)
    filename = f"GeekGate-{label_suffix}-db-{timestamp}.zip"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(
        _stream_file(tmp_zip_path),
        media_type="application/zip",
        headers=headers
    )


@router.post("/admin/api/db/import/preview", include_in_schema=False)
async def admin_preview_db_import(
    request: Request,
    file: UploadFile | None = File(None),
    _csrf: None = Depends(require_same_origin)
):
    """Preview sqlite databases before import."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})
    if not file or not file.filename:
        return JSONResponse(status_code=400, content={"error": "请选择要导入的文件"})

    _cleanup_db_import_sessions()
    filename = Path(file.filename).name
    upload_dir = Path(tempfile.mkdtemp(prefix="GeekGate-db-import-"))
    upload_path = upload_dir / filename
    file.file.seek(0)
    with upload_path.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)

    items: list[dict] = []
    available: set[str] = set()
    if zipfile.is_zipfile(upload_path):
        name_map = {
            "users.db": "users",
            "metrics.db": "metrics",
        }
        seen: set[str] = set()
        with zipfile.ZipFile(upload_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                base = Path(info.filename).name
                key = name_map.get(base)
                if not key or key in seen:
                    continue
                extract_path = upload_dir / f"preview-{base}"
                with zf.open(info) as src, extract_path.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                required = (
                    USER_DB_REQUIRED_TABLES
                    if key == "users"
                    else METRICS_DB_REQUIRED_TABLES
                )
                ok, error = _validate_sqlite_db(extract_path, required)
                try:
                    extract_path.unlink()
                except OSError:
                    pass
                if not ok:
                    shutil.rmtree(upload_dir, ignore_errors=True)
                    return JSONResponse(
                        status_code=400,
                        content={"error": f"{base} 无效：{error}"}
                    )
                items.append({
                    "key": key,
                    "label": DB_LABELS.get(key, key),
                    "size_bytes": info.file_size,
                })
                available.add(key)
                seen.add(key)
        if not items:
            shutil.rmtree(upload_dir, ignore_errors=True)
            return JSONResponse(
                status_code=400,
                content={"error": "压缩包中未找?users.db ?metrics.db"}
            )
    else:
        if not _is_sqlite_file(upload_path):
            shutil.rmtree(upload_dir, ignore_errors=True)
            return JSONResponse(status_code=400, content={"error": "文件不是有效�?SQLite 数据�?})
        ok_users, err_users = _validate_sqlite_db(upload_path, USER_DB_REQUIRED_TABLES)
        ok_metrics, err_metrics = _validate_sqlite_db(upload_path, METRICS_DB_REQUIRED_TABLES)
        if ok_users:
            items.append({
                "key": "users",
                "label": DB_LABELS["users"],
                "size_bytes": upload_path.stat().st_size,
            })
            available.add("users")
        if ok_metrics:
            items.append({
                "key": "metrics",
                "label": DB_LABELS["metrics"],
                "size_bytes": upload_path.stat().st_size,
            })
            available.add("metrics")
        if not items:
            error_message = "数据库不符合本系统（缺少 users ?counters 表）"
            if err_users == "数据库读取失�? or err_metrics == "数据库读取失�?:
                error_message = "数据库读取失�?
            shutil.rmtree(upload_dir, ignore_errors=True)
            return JSONResponse(status_code=400, content={"error": error_message})

    token = _create_db_import_session(upload_dir, upload_path, available)
    return {
        "success": True,
        "token": token,
        "items": items,
        "expires_in": ADMIN_DB_IMPORT_TTL_SECONDS,
        "message": "解析完成，请选择需要导入的数据�?
    }


@router.post("/admin/api/db/import/confirm", include_in_schema=False)
async def admin_confirm_db_import(
    request: Request,
    token: str = Form(...),
    db_types: str = Form(""),
    _csrf: None = Depends(require_same_origin)
):
    """Confirm sqlite database import."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})
    token = (token or "").strip()
    if not token:
        return JSONResponse(status_code=400, content={"error": "导入会话无效"})

    session_data = _get_db_import_session(token)
    if not session_data:
        return JSONResponse(status_code=400, content={"error": "导入会话已过期，请重新上�?})

    if not db_types.strip():
        return JSONResponse(status_code=400, content={"error": "请选择要导入的数据�?})

    try:
        selected = _parse_db_types(db_types, None)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"error": "导入类型无效"})

    available = session_data.get("available", set())
    invalid = [key for key in selected if key not in available]
    if invalid:
        invalid_labels = "�?.join(DB_LABELS.get(key, key) for key in invalid)
        return JSONResponse(status_code=400, content={"error": f"所选数据库不存在于上传文件：{invalid_labels}"})
    selected = [key for key in selected if key in available]
    if not selected:
        return JSONResponse(status_code=400, content={"error": "未选择可导入的数据�?})

    upload_path = Path(session_data["path"])
    db_paths = _get_db_paths()
    imported: list[str] = []

    if zipfile.is_zipfile(upload_path):
        name_map = {
            "users": "users.db",
            "metrics": "metrics.db",
        }
        with zipfile.ZipFile(upload_path) as zf:
            for key in selected:
                target_name = name_map[key]
                match = None
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    if Path(info.filename).name == target_name:
                        match = info
                        break
                if not match:
                    _remove_db_import_session(token)
                    return JSONResponse(status_code=400, content={"error": f"{target_name} 未在压缩包中找到"})
                extract_path = Path(session_data["dir"]) / f"import-{target_name}"
                with zf.open(match) as src, extract_path.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                required = USER_DB_REQUIRED_TABLES if key == "users" else METRICS_DB_REQUIRED_TABLES
                ok, error = _validate_sqlite_db(extract_path, required)
                if not ok:
                    _remove_db_import_session(token)
                    return JSONResponse(status_code=400, content={"error": f"{target_name} 无效：{error}"})
                _replace_db_file(db_paths[key], extract_path)
                imported.append(key)
    else:
        for key in selected:
            temp_copy = Path(session_data["dir"]) / f"import-{key}.db"
            shutil.copy2(upload_path, temp_copy)
            required = USER_DB_REQUIRED_TABLES if key == "users" else METRICS_DB_REQUIRED_TABLES
            ok, error = _validate_sqlite_db(temp_copy, required)
            if not ok:
                _remove_db_import_session(token)
                return JSONResponse(status_code=400, content={"error": error or "数据库文件无�?})
            _replace_db_file(db_paths[key], temp_copy)
            imported.append(key)

    _remove_db_import_session(token)
    imported_labels = "�?.join(DB_LABELS.get(key, key) for key in imported)
    return {
        "success": True,
        "imported": imported,
        "message": f"导入完成：{imported_labels} 已更新。请重启服务以加载最新数�?
    }


@router.post("/admin/api/db/import", include_in_schema=False)
async def admin_import_db(
    request: Request,
    file: UploadFile | None = File(None),
    db_type: str = Form("all"),
    _csrf: None = Depends(require_same_origin)
):
    """Import sqlite databases."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    if not file or not file.filename:
        return JSONResponse(status_code=400, content={"error": "请选择要导入的文件"})

    db_type = db_type.strip().lower()
    if db_type not in {"all", "users", "metrics"}:
        return JSONResponse(status_code=400, content={"error": "导入类型无效"})

    filename = Path(file.filename).name
    db_paths = _get_db_paths()
    imported: list[str] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        upload_path = tmpdir_path / filename
        file.file.seek(0)
        with upload_path.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)

        if zipfile.is_zipfile(upload_path):
            name_map = {
                "users.db": "users",
                "metrics.db": "metrics",
            }
            with zipfile.ZipFile(upload_path) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    base = Path(info.filename).name
                    key = name_map.get(base)
                    if not key:
                        continue
                    extract_path = tmpdir_path / base
                    with zf.open(info) as src, extract_path.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    required = (
                        USER_DB_REQUIRED_TABLES
                        if key == "users"
                        else METRICS_DB_REQUIRED_TABLES
                    )
                    ok, error = _validate_sqlite_db(extract_path, required)
                    if not ok:
                        return JSONResponse(
                            status_code=400,
                            content={"error": f"{base} 无效：{error}"}
                        )
                    _replace_db_file(db_paths[key], extract_path)
                    imported.append(key)
            if not imported:
                return JSONResponse(
                    status_code=400,
                    content={"error": "压缩包中未找?users.db ?metrics.db"}
                )
        else:
            if db_type == "all":
                return JSONResponse(
                    status_code=400,
                    content={"error": "单文件导入请指定导入类型"}
                )
            required = (
                USER_DB_REQUIRED_TABLES
                if db_type == "users"
                else METRICS_DB_REQUIRED_TABLES
            )
            ok, error = _validate_sqlite_db(upload_path, required)
            if not ok:
                return JSONResponse(status_code=400, content={"error": error or "数据库文件无�?})
            _replace_db_file(db_paths[db_type], upload_path)
            imported.append(db_type)

    label_map = {
        "users": "用户数据",
        "metrics": "统计数据",
    }
    imported_labels = "�?.join(label_map[key] for key in imported)
    return {
        "success": True,
        "imported": imported,
        "message": f"导入完成：{imported_labels} 已更新。请重启服务以加载最新数�?
    }


@router.get("/admin/api/tokens", include_in_schema=False)
async def admin_get_tokens(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str = Query("", alias="search")
):
    """Get cached tokens list."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    tokens = []
    for token, manager in auth_cache.cache.items():
        masked = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else "***"
        tokens.append({
            "token_id": token[:8],  # Use first 8 chars as ID
            "masked_token": masked,
            "has_access_token": bool(manager._access_token)
        })
    if search:
        tokens = [
            t for t in tokens
            if search in t["token_id"] or search in t["masked_token"]
        ]
    total = len(tokens)
    offset = (page - 1) * page_size
    tokens = tokens[offset:offset + page_size]
    return {
        "tokens": tokens,
        "count": total,
        "pagination": {"page": page, "page_size": page_size, "total": total}
    }


@router.post("/admin/api/remove-token", include_in_schema=False)
async def admin_remove_token(
    request: Request,
    token_id: str = Form(...),
    _csrf: None = Depends(require_same_origin)
):
    """Remove a cached token."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    # Find token by ID (first 8 chars)
    for token in list(auth_cache.cache.keys()):
        if token[:8] == token_id:
            await auth_cache.remove(token)
            return {"success": True}
    return {"success": False, "message": "Token 不存�?}


@router.post("/admin/api/import-keys", include_in_schema=False)
async def admin_create_import_key(
    request: Request,
    user_id: int = Form(...),
    name: str = Form(""),
    _csrf: None = Depends(require_same_origin)
):
    """Create an admin-generated import key for a user."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    from geek_gateway.database import user_db
    user = await user_db.get_user(user_id)
    if not user:
        return JSONResponse(status_code=404, content={"error": "用户不存�?})
    if user.is_banned:
        return JSONResponse(status_code=403, content={"error": "用户已被封禁"})

    plain_key, import_key = await user_db.generate_import_key(user_id, name or None)
    return {
        "success": True,
        "key": plain_key,
        "key_prefix": import_key.key_prefix,
        "id": import_key.id,
        "user_id": user_id
    }


@router.post("/admin/api/import-keys/delete", include_in_schema=False)
async def admin_delete_import_key(
    request: Request,
    key_id: int = Form(...),
    _csrf: None = Depends(require_same_origin)
):
    """Delete an admin-generated import key."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    from geek_gateway.database import user_db
    success = await user_db.delete_import_key(key_id)
    return {"success": success}


@router.post("/admin/api/clear-tokens", include_in_schema=False)
async def admin_clear_tokens(
    request: Request,
    _csrf: None = Depends(require_same_origin)
):
    """Clear all cached tokens."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    await auth_cache.clear()
    return {"success": True}


@router.get("/admin/api/users", include_in_schema=False)
async def admin_get_users(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str = Query("", alias="search"),
    is_admin: bool | None = Query(None),
    is_banned: bool | None = Query(None),
    approval_status: str | None = Query(None),
    trust_level: int | None = Query(None),
    sort_field: str = Query("created_at"),
    sort_order: str = Query("desc"),
    include_details: bool = Query(True),
    details_limit: int | None = Query(None)
):
    """Get all registered users."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    from geek_gateway.database import user_db
    search = search.strip()
    offset = (page - 1) * page_size
    users = await user_db.get_all_users(
        limit=page_size,
        offset=offset,
        search=search,
        is_admin=is_admin,
        is_banned=is_banned,
        approval_status=approval_status,
        trust_level=trust_level,
        sort_field=sort_field,
        sort_order=sort_order
    )
    total = await user_db.get_user_count(
        search=search,
        is_admin=is_admin,
        is_banned=is_banned,
        approval_status=approval_status,
        trust_level=trust_level
    )

    async def _serialize_user(user):
        payload = {
            "id": user.id,
            "linuxdo_id": user.linuxdo_id,
            "github_id": user.github_id,
            "email": user.email,
            "username": user.username,
            "avatar_url": user.avatar_url,
            "trust_level": user.trust_level,
            "is_admin": user.is_admin,
            "is_banned": user.is_banned,
            "approval_status": user.approval_status,
            "created_at": user.created_at,
            "last_login": user.last_login,
            "token_count": (await user_db.get_token_count(user.id))["total"],
            "api_key_count": await user_db.get_api_key_count(user.id),
        }
        if include_details:
            limit = details_limit if details_limit and details_limit > 0 else None
            tokens = await user_db.get_user_tokens(user.id, limit=limit, offset=0)
            keys = await user_db.get_user_api_keys(user.id, limit=limit, offset=0)
            payload["tokens"] = [
                {
                    "id": t.id,
                    "token_hash": t.token_hash,
                    "visibility": t.visibility,
                    "status": t.status,
                    "success_count": t.success_count,
                    "fail_count": t.fail_count,
                    "success_rate": round(t.success_rate * 100, 1),
                    "last_used": t.last_used,
                    "last_check": t.last_check,
                    "created_at": t.created_at,
                }
                for t in tokens
            ]
            payload["api_keys"] = [
                {
                    "id": k.id,
                    "key_prefix": k.key_prefix,
                    "name": k.name,
                    "is_active": k.is_active,
                    "request_count": k.request_count,
                    "last_used": k.last_used,
                    "created_at": k.created_at,
                }
                for k in keys
            ]
        return payload

    serialized_users = await asyncio.gather(*[_serialize_user(u) for u in users])
    return {
        "users": list(serialized_users),
        "pagination": {"page": page, "page_size": page_size, "total": total}
    }


@router.post("/admin/api/users/ban", include_in_schema=False)
async def admin_ban_user(
    request: Request,
    user_id: int = Form(...),
    _csrf: None = Depends(require_same_origin)
):
    """Ban a user."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    from geek_gateway.database import user_db
    success = await user_db.set_user_banned(user_id, True)
    return {"success": success}


@router.post("/admin/api/users/unban", include_in_schema=False)
async def admin_unban_user(
    request: Request,
    user_id: int = Form(...),
    _csrf: None = Depends(require_same_origin)
):
    """Unban a user."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    from geek_gateway.database import user_db
    success = await user_db.set_user_banned(user_id, False)
    return {"success": success}


@router.post("/admin/api/users/approve", include_in_schema=False)
async def admin_approve_user(
    request: Request,
    user_id: int = Form(...),
    _csrf: None = Depends(require_same_origin)
):
    """Approve a user registration."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})
    from geek_gateway.database import user_db
    await user_db.set_user_approval_status(user_id, "approved")
    return {"success": True}


@router.post("/admin/api/users/reject", include_in_schema=False)
async def admin_reject_user(
    request: Request,
    user_id: int = Form(...),
    _csrf: None = Depends(require_same_origin)
):
    """Reject a user registration."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})
    from geek_gateway.database import user_db
    await user_db.set_user_approval_status(user_id, "rejected")
    return {"success": True}


@router.get("/admin/api/donated-tokens", include_in_schema=False)
async def admin_get_donated_tokens(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str = Query("", alias="search"),
    visibility: str | None = Query(None),
    status: str | None = Query(None),
    user_id: int | None = Query(None),
    sort_field: str = Query("created_at"),
    sort_order: str = Query("desc")
):
    """Get all donated tokens with statistics."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    from geek_gateway.database import user_db
    offset = (page - 1) * page_size
    tokens = await user_db.get_all_tokens_with_users(
        limit=page_size,
        offset=offset,
        search=search,
        visibility=visibility,
        status=status,
        user_id=user_id,
        sort_field=sort_field,
        sort_order=sort_order
    )
    total_filtered = await user_db.get_tokens_count(
        search=search,
        visibility=visibility,
        status=status,
        user_id=user_id
    )
    token_counts = await user_db.get_token_count()
    avg_success = await user_db.get_tokens_success_rate_avg()

    return {
        "total": token_counts["total"],
        "active": token_counts["active"],
        "public": token_counts["public"],
        "avg_success_rate": avg_success * 100,
        "tokens": tokens,
        "pagination": {"page": page, "page_size": page_size, "total": total_filtered}
    }


@router.post("/admin/api/donated-tokens/visibility", include_in_schema=False)
async def admin_toggle_token_visibility(
    request: Request,
    token_id: int = Form(...),
    visibility: str = Form(...),
    _csrf: None = Depends(require_same_origin)
):
    """Toggle token visibility."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    from geek_gateway.metrics import metrics
    if await metrics.is_self_use_enabled() and visibility == "public":
        return JSONResponse(status_code=403, content={"error": "自用模式下禁止公开 Token"})

    from geek_gateway.database import user_db
    success = await user_db.set_token_visibility(token_id, visibility)
    return {"success": success}


@router.post("/admin/api/donated-tokens/delete", include_in_schema=False)
async def admin_delete_donated_token(
    request: Request,
    token_id: int = Form(...),
    _csrf: None = Depends(require_same_origin)
):
    """Delete a donated token (admin override)."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    from geek_gateway.database import user_db
    success = await user_db.admin_delete_token(token_id)
    return {"success": success}


@router.get("/admin/api/announcement", include_in_schema=False)
async def admin_get_announcement(request: Request):
    """Get latest announcement for admin."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    from geek_gateway.database import user_db
    latest = await user_db.get_latest_announcement()
    active = await user_db.get_active_announcement()
    return {
        "announcement": latest,
        "is_active": bool(active),
        "active_id": active["id"] if active else None
    }


@router.post("/admin/api/announcement", include_in_schema=False)
async def admin_update_announcement(
    request: Request,
    content: str = Form(""),
    is_active: str = Form("false"),
    allow_guest: str = Form("false"),
    _csrf: None = Depends(require_same_origin)
):
    """Update announcement content and toggle."""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    content = content.strip()
    active = str(is_active).lower() in ("1", "true", "on", "yes")
    allow_guest_flag = str(allow_guest).lower() in ("1", "true", "on", "yes")
    from geek_gateway.database import user_db

    if active:
        if not content:
            return JSONResponse(status_code=400, content={"error": "公告内容不能为空"})
        await user_db.deactivate_announcements()
        announcement_id = await user_db.create_announcement(content, True, allow_guest_flag)
        return {"success": True, "id": announcement_id}

    await user_db.deactivate_announcements()
    if content:
        announcement_id = await user_db.create_announcement(content, False, allow_guest_flag)
        return {"success": True, "id": announcement_id, "active": False}
    return {"success": True, "active": False}


# ==================== OAuth2 Routes (Hidden from Swagger) ====================

@router.get("/oauth2/login", include_in_schema=False)
async def oauth2_login(request: Request):
    """Redirect to LinuxDo OAuth2 authorization."""
    from geek_gateway.user_manager import user_manager

    if not user_manager.oauth.is_configured:
        return HTMLResponse(
            content="<h1>OAuth2 未配?/h1><p>请在 .env 中配?OAUTH_CLIENT_ID ?OAUTH_CLIENT_SECRET</p>",
            status_code=500
        )

    state = user_manager.session.create_oauth_state()
    auth_url = user_manager.oauth.get_authorization_url(state)

    response = RedirectResponse(url=auth_url, status_code=302)
    response.set_cookie(
        key="oauth_state",
        value=state,
        httponly=True,
        max_age=600,  # 10 minutes
        samesite=settings.oauth_state_cookie_samesite,
        secure=_cookie_secure(request)
    )
    return response


@router.get("/oauth2/callback", include_in_schema=False)
async def oauth2_callback(request: Request, code: str = None, state: str = None):
    """Handle OAuth2 callback from LinuxDo."""
    from geek_gateway.user_manager import user_manager

    # Verify state
    cookie_state = request.cookies.get("oauth_state")
    if not state or state != cookie_state:
        return HTMLResponse(content="<h1>错误</h1><p>无效?state 参数</p>", status_code=400)

    if not code:
        return HTMLResponse(content="<h1>错误</h1><p>未收到授权码</p>", status_code=400)

    # Complete OAuth2 flow
    user, result = await user_manager.oauth_login(code)

    if not user:
        error_msg = result or "登录失败"
        return HTMLResponse(content=f"<h1>错误</h1><p>{error_msg}</p>", status_code=400)

    # Create session and redirect
    response = RedirectResponse(url="/user", status_code=303)
    response.set_cookie(
        key="user_session",
        value=result,  # session_token
        httponly=True,
        max_age=settings.user_session_max_age,
        samesite=settings.user_cookie_samesite,
        secure=_cookie_secure(request)
    )
    response.delete_cookie(key="oauth_state")
    return response


@router.get("/oauth2/logout", include_in_schema=False)
async def oauth2_logout(request: Request):
    """User logout - invalidates all sessions for the user."""
    from geek_gateway.user_manager import user_manager

    # Get current user before clearing cookie
    user = await get_current_user(request)
    if user:
        # Increment session version to invalidate all existing tokens
        await user_manager.logout(user.id)

    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(key="user_session")
    return response


# ==================== GitHub OAuth2 Routes ====================

@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request):
    """Login selection page with multiple OAuth2 providers."""
    user = await get_current_user(request)
    if user:
        redirect_url = f"{_request_origin(request)}/user"
        return RedirectResponse(url=redirect_url, status_code=303)
    from geek_gateway.pages import render_login_page
    return HTMLResponse(content=render_login_page())


@router.get("/register", response_class=HTMLResponse, include_in_schema=False)
async def register_page(request: Request):
    """Register page."""
    user = await get_current_user(request)
    if user:
        redirect_url = f"{_request_origin(request)}/user"
        return RedirectResponse(url=redirect_url, status_code=303)
    return HTMLResponse(content=render_register_page())


@router.post("/auth/login", include_in_schema=False)
async def password_login(request: Request, email: str = Form(...), password: str = Form(...)):
    """Handle email/password login."""
    from geek_gateway.user_manager import user_manager
    user, result = await user_manager.login_with_email(email=email, password=password)
    if not user:
        from geek_gateway.pages import render_login_page
        return HTMLResponse(content=render_login_page(error=result or "登录失败", email=email))
    response = RedirectResponse(url="/user", status_code=303)
    response.set_cookie(
        key="user_session",
        value=result,
        httponly=True,
        max_age=settings.user_session_max_age,
        samesite=settings.user_cookie_samesite,
        secure=_cookie_secure(request)
    )
    return response


@router.post("/auth/register", include_in_schema=False)
async def password_register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    username: str | None = Form(None)
):
    """Handle email/password registration."""
    from geek_gateway.user_manager import user_manager
    user, result = await user_manager.register_with_email(email=email, password=password, username=username)
    if not user:
        from geek_gateway.pages import render_register_page
        info = result if result == "注册成功，等待审�? else ""
        error = "" if info else (result or "注册失败")
        return HTMLResponse(
            content=render_register_page(error=error, info=info, email=email, username=username)
        )
    response = RedirectResponse(url="/user", status_code=303)
    response.set_cookie(
        key="user_session",
        value=result,
        httponly=True,
        max_age=settings.user_session_max_age,
        samesite=settings.user_cookie_samesite,
        secure=_cookie_secure(request)
    )
    return response


@router.get("/oauth2/github/login", include_in_schema=False)
async def github_oauth2_login(request: Request):
    """Redirect to GitHub OAuth2 authorization."""
    from geek_gateway.user_manager import user_manager

    if not user_manager.github.is_configured:
        return HTMLResponse(
            content="<h1>GitHub OAuth2 未配?/h1><p>请在 .env 中配?GITHUB_CLIENT_ID ?GITHUB_CLIENT_SECRET</p>",
            status_code=500
        )

    state = user_manager.session.create_oauth_state()
    auth_url = user_manager.github.get_authorization_url(state)

    response = RedirectResponse(url=auth_url, status_code=302)
    response.set_cookie(
        key="github_oauth_state",
        value=state,
        httponly=True,
        max_age=600,  # 10 minutes
        samesite=settings.oauth_state_cookie_samesite,
        secure=_cookie_secure(request)
    )
    return response


@router.get("/oauth2/github/callback", include_in_schema=False)
async def github_oauth2_callback(request: Request, code: str = None, state: str = None):
    """Handle OAuth2 callback from GitHub."""
    from geek_gateway.user_manager import user_manager

    # Verify state
    cookie_state = request.cookies.get("github_oauth_state")
    if not state or state != cookie_state:
        return HTMLResponse(content="<h1>错误</h1><p>无效?state 参数</p>", status_code=400)

    if not code:
        return HTMLResponse(content="<h1>错误</h1><p>未收到授权码</p>", status_code=400)

    # Complete GitHub OAuth2 flow
    user, result = await user_manager.github_login(code)

    if not user:
        error_msg = result or "登录失败"
        return HTMLResponse(content=f"<h1>错误</h1><p>{error_msg}</p>", status_code=400)

    # Create session and redirect
    response = RedirectResponse(url="/user", status_code=303)
    response.set_cookie(
        key="user_session",
        value=result,  # session_token
        httponly=True,
        max_age=settings.user_session_max_age,
        samesite=settings.user_cookie_samesite,
        secure=_cookie_secure(request)
    )
    response.delete_cookie(key="github_oauth_state")
    return response


# ==================== User Routes (Hidden from Swagger) ====================

async def get_current_user(request: Request):
    """Get current logged-in user from session."""
    from geek_gateway.user_manager import user_manager
    session_token = request.cookies.get("user_session")
    return await user_manager.get_current_user(session_token) if session_token else None


@router.get("/user", response_class=HTMLResponse, include_in_schema=False)
async def user_page(request: Request):
    """User dashboard page."""
    user = await get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    from geek_gateway.pages import render_user_page
    return HTMLResponse(content=render_user_page(user))


@router.get("/user/api/profile", include_in_schema=False)
async def user_get_profile(request: Request):
    """Get current user profile."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})
    from geek_gateway.database import user_db
    from geek_gateway.metrics import metrics
    token_counts = await user_db.get_token_count(user.id)
    api_key_count = await user_db.get_api_key_count(user.id)
    public_token_count = 0 if await metrics.is_self_use_enabled() else token_counts.get("public", 0)
    return {
        "id": user.id,
        "username": user.username,
        "avatar_url": user.avatar_url,
        "trust_level": user.trust_level,
        "is_admin": user.is_admin,
        "token_count": token_counts["total"],
        "public_token_count": public_token_count,
        "api_key_count": api_key_count,
    }


@router.get("/user/api/announcement", include_in_schema=False)
async def user_get_announcement(request: Request):
    """Get active announcement for current user."""
    from geek_gateway.database import user_db
    announcement = await user_db.get_active_announcement()
    if not announcement:
        return {"active": False}
    user = await get_current_user(request)
    allow_guest = bool(announcement.get("allow_guest"))
    if not user:
        if not allow_guest:
            return {"active": False}
        return {
            "active": True,
            "announcement": {
                "id": announcement["id"],
                "content": announcement["content"],
                "updated_at": announcement["updated_at"],
            },
            "can_mark": False,
            "viewer": "guest",
        }
    status = await user_db.get_announcement_status(user.id, announcement["id"])
    if status.get("is_read") or status.get("is_dismissed"):
        return {"active": False}
    return {
        "active": True,
        "announcement": {
            "id": announcement["id"],
            "content": announcement["content"],
            "updated_at": announcement["updated_at"],
        },
        "can_mark": True,
        "viewer": "user",
    }


@router.post("/user/api/announcement/read", include_in_schema=False)
async def user_mark_announcement_read(
    request: Request,
    announcement_id: int = Form(...),
    _csrf: None = Depends(require_same_origin)
):
    """Mark announcement as read."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})
    from geek_gateway.database import user_db
    active = await user_db.get_active_announcement()
    if not active or active["id"] != announcement_id:
        return JSONResponse(status_code=400, content={"error": "公告已更新，请刷新后再试"})
    await user_db.mark_announcement_read(user.id, announcement_id)
    return {"success": True}


@router.post("/user/api/announcement/dismiss", include_in_schema=False)
async def user_mark_announcement_dismissed(
    request: Request,
    announcement_id: int = Form(...),
    _csrf: None = Depends(require_same_origin)
):
    """Dismiss announcement for current user."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})
    from geek_gateway.database import user_db
    active = await user_db.get_active_announcement()
    if not active or active["id"] != announcement_id:
        return JSONResponse(status_code=400, content={"error": "公告已更新，请刷新后再试"})
    await user_db.mark_announcement_dismissed(user.id, announcement_id)
    return {"success": True}

@router.get("/user/api/tokens", include_in_schema=False)
async def user_get_tokens(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str = Query("", alias="search"),
    visibility: str | None = Query(None),
    status: str | None = Query(None),
    sort_field: str = Query("id"),
    sort_order: str = Query("desc")
):
    """Get user's tokens."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})
    from geek_gateway.database import user_db
    search = search.strip()
    offset = (page - 1) * page_size
    tokens = await user_db.get_user_tokens(
        user.id,
        limit=page_size,
        offset=offset,
        search=search,
        status=status,
        visibility=visibility,
        sort_field=sort_field,
        sort_order=sort_order
    )
    total = await user_db.get_user_tokens_count(
        user.id,
        search=search,
        status=status,
        visibility=visibility
    )
    return {
        "tokens": [
            {
                "id": t.id,
                "visibility": t.visibility,
                "status": t.status,
                "success_count": t.success_count,
                "fail_count": t.fail_count,
                "success_rate": round(t.success_rate * 100, 1),
                "last_used": t.last_used,
                "created_at": t.created_at,
                # 账号信息缓存
                "account_email": t.account_email,
                "account_status": t.account_status,
                "account_usage": t.account_usage,
                "account_limit": t.account_limit,
                "account_checked_at": t.account_checked_at,
            }
            for t in tokens
        ],
        "pagination": {"page": page, "page_size": page_size, "total": total}
    }


@router.get("/user/api/public-tokens", include_in_schema=False)
async def user_get_public_tokens(request: Request):
    """Get public tokens with contributor info for user page."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})
    from geek_gateway.metrics import metrics
    if await metrics.is_self_use_enabled():
        return JSONResponse(status_code=403, content={"error": "自用模式下不开放公开 Token �?})
    from geek_gateway.database import user_db
    tokens = await user_db.get_public_tokens_with_users()
    avg_rate = sum(t["success_rate"] for t in tokens) / len(tokens) if tokens else 0
    return {
        "tokens": [
            {
                "id": t["id"],
                "username": t["username"],
                "status": t["status"],
                "success_rate": round(t["success_rate"] * 100, 1),
                "use_count": t["success_count"] + t["fail_count"],
                "last_used": t["last_used"],
            }
            for t in tokens
        ],
        "count": len(tokens),
        "avg_success_rate": round(avg_rate * 100, 1),
    }


IMPORT_FILE_MAX_BYTES = 5 * 1024 * 1024
IMPORT_TEXT_MAX_BYTES = 200 * 1024
IMPORT_TOKEN_MAX_COUNT = 500
IMPORT_VALIDATE_CONCURRENCY = 3
IMPORT_ERROR_SAMPLE_LIMIT = 5


@dataclass
class TokenCredential:
    """Token 凭证数据结构，支?Social ?IDC 两种认证方式�?""
    refresh_token: str
    auth_type: str = "social"  # social ?idc
    client_id: Optional[str] = None
    client_secret: Optional[str] = None


def _split_tokens_text(text: str) -> list[str]:
    parts = re.split(r"[,\s;]+", text.strip())
    return [part for part in parts if part]


def _extract_refresh_tokens(payload: object) -> tuple[list[TokenCredential], int, list[str]]:
    """
    从导入数据中提取 token 凭证�?

    支持的格式：
    1. 纯文本列表：["token1", "token2"]
    2. 对象列表：[{"refreshToken": "...", "clientId": "...", "clientSecret": "..."}]
    3. 嵌套对象：{"accounts": [...], "credentials": {...}}

    Returns:
        (credentials, missing_required, missing_samples)
    """
    credentials: list[TokenCredential] = []
    missing_required = 0
    missing_samples: list[str] = []

    def record_missing(path: str, reason: str) -> None:
        nonlocal missing_required
        missing_required += 1
        if len(missing_samples) < IMPORT_ERROR_SAMPLE_LIMIT:
            missing_samples.append(f"{path}: {reason}")

    def add_credential(obj: dict | str, path: str) -> None:
        """从对象或字符串中提取凭证�?""
        if isinstance(obj, str):
            token = obj.strip()
            if token:
                credentials.append(TokenCredential(refresh_token=token))
                return
            record_missing(path, "refreshToken 为空")
            return

        if not isinstance(obj, dict):
            record_missing(path, "类型不支�?)
            return

        # 尝试从对象中提取 refreshToken
        refresh_token = None
        client_id = None
        client_secret = None

        # 直接字段（支持驼峰和蛇形命名�?
        if "refreshToken" in obj:
            refresh_token = obj.get("refreshToken")
        elif "refresh_token" in obj:
            refresh_token = obj.get("refresh_token")
        # 嵌套?credentials ?credentials_kiro_rs ?
        elif isinstance(obj.get("credentials"), dict):
            creds = obj["credentials"]
            refresh_token = creds.get("refreshToken") or creds.get("refresh_token")
            client_id = creds.get("clientId") or creds.get("client_id")
            client_secret = creds.get("clientSecret") or creds.get("client_secret")
        elif isinstance(obj.get("credentials_kiro_rs"), dict):
            creds = obj["credentials_kiro_rs"]
            refresh_token = creds.get("refreshToken") or creds.get("refresh_token")
            client_id = creds.get("clientId") or creds.get("client_id")
            client_secret = creds.get("clientSecret") or creds.get("client_secret")

        # 获取 clientId ?clientSecret（如果在顶层，支持两种命名）
        if client_id is None:
            client_id = obj.get("clientId") or obj.get("client_id")
        if client_secret is None:
            client_secret = obj.get("clientSecret") or obj.get("client_secret")

        if not refresh_token or not isinstance(refresh_token, str):
            record_missing(path, "缺少 refreshToken")
            return

        refresh_token = refresh_token.strip()
        if not refresh_token:
            record_missing(path, "refreshToken 为空")
            return

        # 判断认证类型
        auth_type = "social"
        if client_id and client_secret:
            auth_type = "idc"
            client_id = client_id.strip() if isinstance(client_id, str) else None
            client_secret = client_secret.strip() if isinstance(client_secret, str) else None

        credentials.append(TokenCredential(
            refresh_token=refresh_token,
            auth_type=auth_type,
            client_id=client_id if auth_type == "idc" else None,
            client_secret=client_secret if auth_type == "idc" else None,
        ))

    def _has_refresh_token(obj: dict) -> bool:
        """检查对象是否包?refreshToken（支持驼峰和蛇形命名�?""
        if "refreshToken" in obj or "refresh_token" in obj:
            return True
        creds = obj.get("credentials") or obj.get("credentials_kiro_rs")
        if isinstance(creds, dict) and ("refreshToken" in creds or "refresh_token" in creds):
            return True
        return False

    def handle_list(items: list, path: str, enforce_required: bool) -> None:
        for index, item in enumerate(items):
            item_path = f"{path}[{index}]"
            if isinstance(item, dict):
                if _has_refresh_token(item):
                    add_credential(item, item_path)
                else:
                    if enforce_required:
                        record_missing(item_path, "缺少 refreshToken")
                    handle_dict(item, item_path)
            elif isinstance(item, str):
                add_credential(item, item_path)
            elif isinstance(item, list):
                handle_list(item, item_path, enforce_required)
            else:
                if enforce_required:
                    record_missing(item_path, "类型不支�?)

    def handle_dict(obj: dict, path: str) -> None:
        # 检查顶层是否有 refreshToken（支持两种命名）
        if _has_refresh_token(obj):
            add_credential(obj, path if path else "root")

        for key, value in obj.items():
            if isinstance(value, dict):
                handle_dict(value, f"{path}.{key}" if path else key)
            elif isinstance(value, list):
                enforce = key in {"accounts", "tokens", "data"}
                handle_list(value, f"{path}.{key}" if path else key, enforce)

    if isinstance(payload, list):
        handle_list(payload, "root", True)
    elif isinstance(payload, dict):
        handle_dict(payload, "")
    elif isinstance(payload, str):
        add_credential(payload, "refreshToken")

    return credentials, missing_required, missing_samples


def _dedupe_credentials(credentials: list[TokenCredential]) -> list[TokenCredential]:
    """去重凭证列表（按 refresh_token 去重）�?""
    seen: set[str] = set()
    deduped: list[TokenCredential] = []
    for cred in credentials:
        if cred.refresh_token in seen:
            continue
        seen.add(cred.refresh_token)
        deduped.append(cred)
    return deduped


async def _read_import_payload(
    file: UploadFile | None,
    tokens_text: str | None,
    json_text: str | None,
) -> tuple[object | None, str | None, int | None]:
    """
    Read import payload from file upload or text input.

    Security: file_path parameter has been removed to prevent path traversal attacks.
    """
    input_count = 0
    if file and file.filename:
        input_count += 1
    if tokens_text and tokens_text.strip():
        input_count += 1
    if json_text and json_text.strip():
        input_count += 1

    if input_count == 0:
        return None, "请提供文件或文本", 400
    if input_count > 1:
        return None, "请仅选择一种导入方�?, 400

    if file and file.filename:
        content = await file.read()
        if not content:
            return None, "文件内容为空", 400
        if len(content) > IMPORT_FILE_MAX_BYTES:
            return None, "文件过大，请拆分后导�?, 400
        try:
            return json.loads(content), None, None
        except json.JSONDecodeError:
            return None, "JSON 格式无效", 400

    if json_text and json_text.strip():
        json_text = json_text.strip()
        if len(json_text.encode("utf-8")) > IMPORT_FILE_MAX_BYTES:
            return None, "JSON 内容过大，请拆分后导�?, 400
        try:
            return json.loads(json_text), None, None
        except json.JSONDecodeError:
            return None, "JSON 格式无效", 400

    if tokens_text is not None:
        tokens_text = tokens_text.strip()
        if not tokens_text:
            return None, "导入文本为空", 400
        if len(tokens_text.encode("utf-8")) > IMPORT_TEXT_MAX_BYTES:
            return None, "导入文本过大，请拆分后导�?, 400
        if tokens_text[0] in "[{\"":
            try:
                return json.loads(tokens_text), None, None
            except json.JSONDecodeError:
                return None, "JSON 格式无效", 400
        return _split_tokens_text(tokens_text), None, None

    return None, "导入内容无效", 400


async def _process_import_payload(
    user_id: int,
    visibility: str,
    anonymous: bool,
    payload: object,
    override_auth_type: str | None = None,
    override_client_id: str | None = None,
    override_client_secret: str | None = None,
) -> tuple[dict, int]:
    credentials, missing_required, missing_samples = _extract_refresh_tokens(payload)
    credentials = _dedupe_credentials(credentials)

    # 如果指定?override 参数（IDC 模式），将其应用到所有凭?
    if override_auth_type == "idc" and override_client_id and override_client_secret:
        credentials = [
            TokenCredential(
                refresh_token=cred.refresh_token,
                auth_type="idc",
                client_id=override_client_id,
                client_secret=override_client_secret,
            )
            for cred in credentials
        ]

    if not credentials:
        message = "未找到可导入�?Refresh Token"
        if missing_required:
            message = f"{message}，缺少必�?{missing_required} �?
        if missing_samples:
            message = f"{message} 必填示例：{'�?.join(missing_samples)}"
        return {
            "error": message,
            "missing_required": missing_required,
        }, 400
    if len(credentials) > IMPORT_TOKEN_MAX_COUNT:
        return {"error": f"导入数量过多（{len(credentials)}），请拆分后导入"}, 400

    from geek_gateway.database import user_db

    pending_credentials: list[TokenCredential] = []
    skipped = 0
    for cred in credentials:
        if await user_db.token_exists(cred.refresh_token):
            skipped += 1
        else:
            pending_credentials.append(cred)

    semaphore = asyncio.Semaphore(IMPORT_VALIDATE_CONCURRENCY)

    async def validate_credential(cred: TokenCredential) -> tuple[TokenCredential, bool, str | None]:
        async with semaphore:
            try:
                temp_manager = GeekAuthManager(
                    refresh_token=cred.refresh_token,
                    client_id=cred.client_id,
                    client_secret=cred.client_secret,
                    region=settings.region,
                    profile_arn=settings.profile_arn
                )
                access_token = await temp_manager.get_access_token()
                if not access_token:
                    return cred, False, "无法获取访问令牌"
                return cred, True, None
            except Exception as exc:
                return cred, False, str(exc)

    validation_results = await asyncio.gather(
        *(validate_credential(cred) for cred in pending_credentials)
    )

    imported = 0
    invalid = 0
    failed = 0
    error_samples: list[str] = []

    for cred, ok, error in validation_results:
        if not ok:
            invalid += 1
            if error and len(error_samples) < IMPORT_ERROR_SAMPLE_LIMIT:
                error_samples.append(f"{_mask_token(cred.refresh_token)}: {error}")
            continue

        success, message = await user_db.donate_token(
            user_id=user_id,
            refresh_token=cred.refresh_token,
            visibility=visibility,
            anonymous=anonymous,
            auth_type=cred.auth_type,
            client_id=cred.client_id,
            client_secret=cred.client_secret,
        )
        if success:
            imported += 1
        else:
            if message == "Token 已存�?:
                skipped += 1
            else:
                failed += 1
                if len(error_samples) < IMPORT_ERROR_SAMPLE_LIMIT:
                    error_samples.append(f"{_mask_token(cred.refresh_token)}: {message}")

    total = len(credentials)
    message = (
        f"导入完成：成�?{imported}，已存在 {skipped}，无�?{invalid}，失�?{failed} �?
    )
    if missing_required:
        message = f"{message} 缺少必填 {missing_required} �?
    sample_messages: list[str] = []
    if missing_samples:
        sample_messages.append(f"必填示例：{'�?.join(missing_samples)}")
    if error_samples:
        sample_messages.append(f"错误示例：{'�?.join(error_samples)}")
    if sample_messages:
        message = f"{message} {' '.join(sample_messages)}"

    return {
        "success": imported + skipped > 0,
        "message": message,
        "total": total,
        "imported": imported,
        "skipped": skipped,
        "invalid": invalid,
        "failed": failed,
        "missing_required": missing_required,
    }, 200


@router.post("/user/api/tokens", include_in_schema=False)
async def user_donate_token(
    request: Request,
    refresh_token: str = Form(...),
    auth_type: str = Form("social"),
    client_id: str = Form(""),
    client_secret: str = Form(""),
    visibility: str = Form("private"),
    anonymous: bool = Form(False),
    _csrf: None = Depends(require_same_origin)
):
    """Donate a new token."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})

    from geek_gateway.metrics import metrics
    if await metrics.is_self_use_enabled() and visibility == "public":
        return JSONResponse(status_code=403, content={"error": "自用模式下禁止公开 Token"})

    if visibility not in ("public", "private"):
        return JSONResponse(status_code=400, content={"error": "可见性无�?})

    if auth_type not in ("social", "idc"):
        return JSONResponse(status_code=400, content={"error": "认证类型无效"})

    # IDC 模式必须提供 client_id ?client_secret
    client_id = client_id.strip() if client_id else None
    client_secret = client_secret.strip() if client_secret else None
    if auth_type == "idc" and (not client_id or not client_secret):
        return JSONResponse(status_code=400, content={"error": "IDC 模式需要提?Client ID ?Client Secret"})

    from geek_gateway.database import user_db

    # Validate token before saving
    from geek_gateway.auth import GeekAuthManager
    from geek_gateway.config import settings as cfg
    try:
        temp_manager = GeekAuthManager(
            refresh_token=refresh_token,
            client_id=client_id if auth_type == "idc" else None,
            client_secret=client_secret if auth_type == "idc" else None,
            region=cfg.region,
            profile_arn=cfg.profile_arn
        )
        access_token = await temp_manager.get_access_token()
        if not access_token:
            return {"success": False, "message": "Token 验证失败：无法获取访问令�?}
    except Exception as e:
        return {"success": False, "message": f"Token 验证失败：{str(e)}"}

    # Save token
    success, message = await user_db.donate_token(
        user_id=user.id,
        refresh_token=refresh_token,
        visibility=visibility,
        anonymous=anonymous,
        auth_type=auth_type,
        client_id=client_id if auth_type == "idc" else None,
        client_secret=client_secret if auth_type == "idc" else None,
    )
    return {"success": success, "message": message}


@router.post("/user/api/tokens/import", include_in_schema=False)
async def user_import_tokens(
    request: Request,
    file: UploadFile | None = File(None),
    tokens_text: str | None = Form(None),
    json_text: str | None = Form(None),
    visibility: str = Form("private"),
    anonymous: bool = Form(False),
    auth_type: str = Form("social"),
    client_id: str | None = Form(None),
    client_secret: str | None = Form(None),
    _csrf: None = Depends(require_same_origin)
):
    """Import refresh tokens from a JSON file."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})

    from geek_gateway.metrics import metrics
    if await metrics.is_self_use_enabled() and visibility == "public":
        return JSONResponse(status_code=403, content={"error": "自用模式下禁止公开 Token"})

    if visibility not in ("public", "private"):
        return JSONResponse(status_code=400, content={"error": "可见性无�?})

    if auth_type not in ("social", "idc"):
        return JSONResponse(status_code=400, content={"error": "认证类型无效"})

    payload, error, status = await _read_import_payload(
        file=file,
        tokens_text=tokens_text,
        json_text=json_text
    )
    if error:
        return JSONResponse(status_code=status or 400, content={"error": error})

    result, status = await _process_import_payload(
        user_id=user.id,
        visibility=visibility,
        anonymous=anonymous,
        payload=payload,
        override_auth_type=auth_type if auth_type == "idc" else None,
        override_client_id=client_id.strip() if client_id else None,
        override_client_secret=client_secret.strip() if client_secret else None,
    )
    if status != 200:
        return JSONResponse(status_code=status, content=result)
    return result


@router.post("/api/tokens/import")
async def api_import_tokens(
    request: Request,
    file: UploadFile | None = File(None),
    tokens_text: str | None = Form(None),
    json_text: str | None = Form(None),
    visibility: str = Form("private"),
    anonymous: bool = Form(False)
):
    """Import refresh tokens using an admin-generated import key."""
    import_key = _get_import_key_from_request(request)
    if not import_key:
        return JSONResponse(status_code=401, content={"error": "Import Key 缺失"})

    from geek_gateway.database import user_db
    result = await user_db.verify_import_key(import_key)
    if not result:
        return JSONResponse(status_code=401, content={"error": "Import Key 无效"})

    user_id, import_key_obj = result
    user = await user_db.get_user(user_id)
    if not user:
        return JSONResponse(status_code=404, content={"error": "用户不存�?})
    if user.is_banned:
        return JSONResponse(status_code=403, content={"error": "用户已被封禁"})

    from geek_gateway.metrics import metrics
    if await metrics.is_self_use_enabled() and visibility == "public":
        return JSONResponse(status_code=403, content={"error": "自用模式下禁止公开 Token"})

    if visibility not in ("public", "private"):
        return JSONResponse(status_code=400, content={"error": "可见性无�?})

    payload, error, status = await _read_import_payload(
        file=file,
        tokens_text=tokens_text,
        json_text=json_text
    )
    if error:
        return JSONResponse(status_code=status or 400, content={"error": error})

    result, status = await _process_import_payload(
        user_id=user_id,
        visibility=visibility,
        anonymous=anonymous,
        payload=payload
    )
    if status != 200:
        return JSONResponse(status_code=status, content=result)
    await user_db.record_import_key_usage(import_key_obj.id)
    return result


@router.put("/user/api/tokens/{token_id}", include_in_schema=False)
async def user_update_token(
    request: Request,
    token_id: int,
    visibility: str = Form(...),
    _csrf: None = Depends(require_same_origin)
):
    """Update token visibility."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})

    from geek_gateway.metrics import metrics
    if await metrics.is_self_use_enabled() and visibility == "public":
        return JSONResponse(status_code=403, content={"error": "自用模式下禁止公开 Token"})

    from geek_gateway.database import user_db

    # Verify ownership
    token = await user_db.get_token_by_id(token_id)
    if not token or token.user_id != user.id:
        return JSONResponse(status_code=404, content={"error": "Token 不存�?})

    success = await user_db.set_token_visibility(token_id, visibility)
    return {"success": success}


@router.delete("/user/api/tokens/{token_id}", include_in_schema=False)
async def user_delete_token(
    request: Request,
    token_id: int,
    _csrf: None = Depends(require_same_origin)
):
    """Delete a token."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})

    from geek_gateway.database import user_db
    success = await user_db.delete_token(token_id, user.id)
    return {"success": success}


@router.get("/user/api/tokens/{token_id}/account-info", include_in_schema=False)
async def user_get_token_account_info(
    request: Request,
    token_id: int,
):
    """获取指定 Token 的账号信息（订阅、额度等�?""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})

    from geek_gateway.database import user_db

    # 验证 Token 所有权
    token = await user_db.get_token_by_id(token_id)
    if not token or token.user_id != user.id:
        return JSONResponse(status_code=404, content={"error": "Token 不存�?})

    # 获取解密后的完整凭证（包?IDC ?client_id/client_secret?
    credentials = await user_db.get_token_credentials(token_id)
    if not credentials or not credentials.get("refresh_token"):
        return JSONResponse(status_code=400, content={"error": "无法获取 Token"})

    # 使用 refresh_token 获取 access_token
    from geek_gateway.auth import GeekAuthManager
    auth_manager = GeekAuthManager(
        refresh_token=credentials["refresh_token"],
        client_id=credentials.get("client_id"),
        client_secret=credentials.get("client_secret"),
    )
    try:
        access_token = await auth_manager.get_access_token()
        if not access_token:
            return JSONResponse(status_code=400, content={"error": "Token 无效或已过期"})
    except Exception as e:
        logger.error(f"Failed to get access token for token {token_id}: {e}")
        return JSONResponse(status_code=400, content={"error": f"Token 验证失败: {str(e)}"})

    # 获取账号信息
    try:
        account_info = await get_kiro_account_info(access_token)
        # 更新缓存
        await user_db.update_token_account_info(
            token_id,
            email=account_info.get("email"),
            status=account_info.get("status"),
            usage=account_info.get("usage", {}).get("current"),
            limit=account_info.get("usage", {}).get("limit")
        )
        return account_info
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"error": e.detail})
    except Exception as e:
        logger.error(f"Failed to get account info for token {token_id}: {e}")
        return JSONResponse(status_code=500, content={"error": f"获取账号信息失败: {str(e)}"})


@router.get("/user/api/keys", include_in_schema=False)
async def user_get_keys(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str = Query("", alias="search"),
    is_active: bool | None = Query(None),
    sort_field: str = Query("created_at"),
    sort_order: str = Query("desc")
):
    """Get user's API keys."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})
    from geek_gateway.database import user_db
    search = search.strip()
    offset = (page - 1) * page_size
    keys = await user_db.get_user_api_keys(
        user.id,
        limit=page_size,
        offset=offset,
        search=search,
        is_active=is_active,
        sort_field=sort_field,
        sort_order=sort_order
    )
    total = await user_db.get_user_api_keys_count(user.id, search=search, is_active=is_active)
    return {
        "keys": [
            {
                "id": k.id,
                "key_prefix": k.key_prefix,
                "name": k.name,
                "is_active": k.is_active,
                "request_count": k.request_count,
                "last_used": k.last_used,
                "created_at": k.created_at,
            }
            for k in keys
        ],
        "pagination": {"page": page, "page_size": page_size, "total": total}
    }


@router.post("/user/api/keys", include_in_schema=False)
async def user_create_key(
    request: Request,
    name: str = Form(""),
    _csrf: None = Depends(require_same_origin)
):
    """Generate a new API key."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})

    from geek_gateway.database import user_db
    from geek_gateway.metrics import metrics

    # Check if user has any tokens (for info purposes only, not blocking)
    tokens = await user_db.get_user_tokens(user.id)
    active_tokens = [t for t in tokens if t.status == "active"]
    has_own_tokens = len(active_tokens) > 0
    if await metrics.is_self_use_enabled():
        active_private = [t for t in active_tokens if t.visibility == "private"]
        if not active_private:
            return JSONResponse(status_code=400, content={"error": "自用模式下请先添加私?Token"})

    plain_key, api_key = await user_db.generate_api_key(user.id, name or None)
    return {
        "success": True,
        "key": plain_key,  # Only returned once!
        "key_prefix": api_key.key_prefix,
        "id": api_key.id,
        "uses_public_pool": not has_own_tokens,
    }


@router.put("/user/api/keys/{key_id}", include_in_schema=False)
async def user_update_key(
    request: Request,
    key_id: int,
    is_active: bool = Form(...),
    _csrf: None = Depends(require_same_origin)
):
    """Enable or disable an API key."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})

    from geek_gateway.database import user_db
    success = await user_db.set_api_key_active(key_id, user_id=user.id, is_active=is_active)
    if not success:
        return JSONResponse(status_code=404, content={"error": "API Key 不存�?})
    return {"success": True}


@router.delete("/user/api/keys/{key_id}", include_in_schema=False)
async def user_delete_key(
    request: Request,
    key_id: int,
    _csrf: None = Depends(require_same_origin)
):
    """Delete an API key."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})

    from geek_gateway.database import user_db
    success = await user_db.delete_api_key(key_id, user.id)
    return {"success": success}


# ==================== Public Token Pool ====================

@router.get("/tokens", response_class=HTMLResponse, include_in_schema=False)
async def public_tokens_page(request: Request):
    """Public token pool page."""
    from geek_gateway.pages import render_tokens_page
    user = await get_current_user(request)
    return HTMLResponse(content=render_tokens_page(user))


@router.get("/api/public-tokens", include_in_schema=False)
async def get_public_tokens():
    """Get public tokens list (masked)."""
    from geek_gateway.metrics import metrics
    if await metrics.is_self_use_enabled():
        return JSONResponse(status_code=403, content={"error": "自用模式下不开放公开 Token �?})
    from geek_gateway.database import user_db
    tokens = await user_db.get_public_tokens_with_users()
    return {
        "tokens": [
            {
                "id": t["id"],
                "username": t["username"],
                "success_rate": round(t["success_rate"] * 100, 1),
                "last_used": t["last_used"],
            }
            for t in tokens
        ],
        "count": len(tokens)
    }


# ==================== User Panel Data API ====================


@router.get("/user/api/stats", include_in_schema=False)
async def user_get_stats(request: Request):
    """Get user usage statistics."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})

    from geek_gateway.database import user_db

    now = datetime.now(timezone.utc)
    today_start_ms = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    month_start_ms = int(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)

    total_row = await user_db._backend.fetch_one(
        "SELECT COUNT(*) as cnt FROM activity_logs WHERE user_id = ?",
        (user.id,),
    )
    total_requests = total_row["cnt"] if total_row else 0

    today_row = await user_db._backend.fetch_one(
        "SELECT COUNT(*) as cnt FROM activity_logs WHERE user_id = ? AND created_at >= ?",
        (user.id, today_start_ms),
    )
    today_requests = today_row["cnt"] if today_row else 0

    month_row = await user_db._backend.fetch_one(
        "SELECT COUNT(*) as cnt FROM activity_logs WHERE user_id = ? AND created_at >= ?",
        (user.id, month_start_ms),
    )
    month_requests = month_row["cnt"] if month_row else 0

    success_row = await user_db._backend.fetch_one(
        "SELECT COUNT(*) as cnt FROM activity_logs WHERE user_id = ? AND status_code >= 200 AND status_code < 300",
        (user.id,),
    )
    success_count = success_row["cnt"] if success_row else 0
    success_rate = round(success_count / total_requests * 100, 1) if total_requests > 0 else 100.0

    token_row = await user_db._backend.fetch_one(
        "SELECT COUNT(*) as cnt FROM tokens WHERE user_id = ?",
        (user.id,),
    )
    donated_token_count = token_row["cnt"] if token_row else 0

    return {
        "total_requests": total_requests,
        "today_requests": today_requests,
        "month_requests": month_requests,
        "success_rate": success_rate,
        "donated_token_count": donated_token_count,
    }


@router.get("/user/api/quota", include_in_schema=False)
async def user_get_quota(request: Request):
    """Get user quota usage info."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})

    from geek_gateway.quota_manager import quota_manager

    quota_info = await quota_manager.get_user_quota_info(user.id)
    return quota_info


@router.get("/user/api/token-health", include_in_schema=False)
async def user_get_token_health(request: Request):
    """Get health status of user's donated tokens."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})

    from geek_gateway.database import user_db

    rows = await user_db._backend.fetch_all(
        """SELECT id, status, success_count, fail_count, last_used,
                  risk_score, consecutive_fails
           FROM tokens WHERE user_id = ?""",
        (user.id,),
    )

    tokens = []
    for row in rows:
        total = row["success_count"] + row["fail_count"]
        sr = round(row["success_count"] / total * 100, 1) if total > 0 else 100.0
        tokens.append({
            "id": row["id"],
            "status": row["status"],
            "success_rate": sr,
            "last_used_at": row["last_used"],
            "risk_score": round(row["risk_score"], 3) if row["risk_score"] else 0.0,
            "consecutive_fails": row["consecutive_fails"] or 0,
        })

    return {"tokens": tokens}


@router.get("/user/api/activity", include_in_schema=False)
async def user_get_activity(request: Request):
    """Get recent API request records (last 50)."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})

    from geek_gateway.database import user_db

    rows = await user_db._backend.fetch_all(
        """SELECT model, status_code, latency_ms, created_at
           FROM activity_logs WHERE user_id = ?
           ORDER BY created_at DESC LIMIT 50""",
        (user.id,),
    )

    return {
        "records": [
            {
                "model": row["model"],
                "status_code": row["status_code"],
                "latency_ms": row["latency_ms"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    }


@router.get("/user/api/notifications", include_in_schema=False)
async def user_get_notifications(request: Request):
    """Get unread notifications."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})

    from geek_gateway.database import user_db

    rows = await user_db._backend.fetch_all(
        """SELECT id, type, message, is_read, created_at
           FROM user_notifications WHERE user_id = ? AND is_read = 0
           ORDER BY created_at DESC""",
        (user.id,),
    )

    return {
        "notifications": [
            {
                "id": row["id"],
                "type": row["type"],
                "message": row["message"],
                "is_read": bool(row["is_read"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    }


@router.post("/user/api/notifications/read", include_in_schema=False)
async def user_mark_notification_read(request: Request, notification_id: int = Form(...)):
    """Mark a notification as read."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})
    from geek_gateway.notification_manager import notification_manager

    await notification_manager.mark_read(user.id, notification_id)
    return {"success": True}


@router.post("/user/api/notifications/read-all", include_in_schema=False)
async def user_mark_all_notifications_read(request: Request):
    """Mark all notifications as read."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "未登�?})
    from geek_gateway.notification_manager import notification_manager

    await notification_manager.mark_all_read(user.id)
    return {"success": True}


# ============================================================
# 管理面板 - 集群概览?Token 池状?API（分布式部署?
# ============================================================


def _calculate_risk_score(risk_data: dict, token=None) -> float:
    """
    计算 Token 风险评分 (0.0 - 1.0)�?

    因子�?
    - RPM 使用?(权重 0.3)
    - RPH 使用?(权重 0.3)
    - 失败?(权重 0.2)
    - 连续失败次数 (权重 0.2)
    """
    from geek_gateway.config import settings

    rpm = risk_data.get("rpm", 0)
    rph = risk_data.get("rph", 0)
    consecutive_fails = risk_data.get("consecutive_fails", 0)

    rpm_ratio = min(1.0, rpm / max(1, settings.token_rpm_limit))
    rph_ratio = min(1.0, rph / max(1, settings.token_rph_limit))

    # 失败率：?token 对象获取
    fail_ratio = 0.0
    if token:
        total = token.success_count + token.fail_count
        if total > 0:
            fail_ratio = token.fail_count / total

    consec_ratio = min(1.0, consecutive_fails / 5)

    return round(0.3 * rpm_ratio + 0.3 * rph_ratio + 0.2 * fail_ratio + 0.2 * consec_ratio, 4)


@router.get("/admin/api/cluster", include_in_schema=False)
async def admin_get_cluster(request: Request):
    """
    集群概览 API�?

    返回在线节点、全局 Token 池状态、集群实时聚合指标�?
    单节点模式下返回当前节点信息�?
    """
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    from geek_gateway.config import settings
    from geek_gateway.metrics import metrics
    from geek_gateway.database import user_db
    from geek_gateway.token_allocator import token_allocator

    import time

    # --- 节点信息 ---
    nodes = []
    if settings.is_distributed:
        from geek_gateway.heartbeat import NodeHeartbeat
        nodes = await NodeHeartbeat.get_online_nodes()
    else:
        # 单节点模式：返回当前节点信息
        nodes = [{
            "node_id": settings.node_id,
            "status": "online",
            "uptime": int(time.time() - metrics._start_time),
            "connections": metrics._active_connections,
            "last_heartbeat": int(time.time()),
            "requests_1m": len(metrics._response_times) if hasattr(metrics, "_response_times") else 0,
        }]

    # --- Token 池概?---
    all_tokens = await user_db.get_all_active_tokens()
    now = int(time.time())
    token_pool_summary = {
        "total": len(all_tokens),
        "active": 0,
        "cooldown": 0,
        "suspended": 0,
    }
    for t in all_tokens:
        if t.status == "active" and t.cooldown_until <= now:
            token_pool_summary["active"] += 1
        elif t.cooldown_until > now:
            token_pool_summary["cooldown"] += 1
        else:
            token_pool_summary["suspended"] += 1

    # --- 集群聚合指标 ---
    cluster_metrics = await _get_cluster_aggregated_metrics()

    return {
        "nodes": nodes,
        "online_count": len(nodes),
        "token_pool": token_pool_summary,
        "cluster_metrics": cluster_metrics,
    }


async def _get_cluster_aggregated_metrics() -> dict:
    """
    获取集群实时聚合指标：总请求数、成功率、平均延迟、P95/P99 延迟�?
    """
    from geek_gateway.metrics import metrics

    try:
        full_metrics = await metrics.get_metrics()

        # 提取请求数据
        requests_data = full_metrics.get("requests", {})
        if isinstance(requests_data.get("total"), dict):
            total_requests = sum(requests_data["total"].values())
        else:
            total_requests = requests_data.get("total", 0)

        by_status = requests_data.get("by_status", {})
        success_count = sum(
            int(v) for k, v in by_status.items()
            if k.isdigit() and 200 <= int(k) < 400
        )
        success_rate = round(success_count / max(1, total_requests), 4)

        # 提取延迟数据
        latency_data = full_metrics.get("latency", {})
        avg_latency = 0.0
        p95_latency = 0.0
        p99_latency = 0.0
        total_latency_count = 0

        for _ep, stats in latency_data.items():
            count = stats.get("count", 0)
            if count > 0:
                avg_latency += stats.get("avg", 0) * count
                p95_latency = max(p95_latency, stats.get("p95", 0))
                p99_latency = max(p99_latency, stats.get("p99", 0))
                total_latency_count += count

        if total_latency_count > 0:
            avg_latency = round(avg_latency / total_latency_count, 4)

        return {
            "total_requests": total_requests,
            "success_rate": success_rate,
            "avg_latency": avg_latency,
            "p95_latency": round(p95_latency, 4),
            "p99_latency": round(p99_latency, 4),
        }
    except Exception as e:
        logger.warning(f"获取集群聚合指标失败: {e}")
        return {
            "total_requests": 0,
            "success_rate": 0.0,
            "avg_latency": 0.0,
            "p95_latency": 0.0,
            "p99_latency": 0.0,
        }


@router.get("/admin/api/tokens/pool", include_in_schema=False)
async def admin_get_token_pool(request: Request):
    """
    全局 Token 池状?API?

    返回每个 Token ?Risk_Score、RPM/RPH 使用量、并发数、连续失败次数、状�?
    """
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    import time
    from geek_gateway.config import settings
    from geek_gateway.database import user_db
    from geek_gateway.token_allocator import token_allocator

    # 获取所?Token（包括非 active 的）
    all_tokens = await user_db._backend.fetch_all(
        "SELECT * FROM tokens ORDER BY id",
        (),
    )

    now = int(time.time())
    pool = []

    for row in all_tokens:
        token_id = row["id"]
        status = row.get("status", "unknown")
        consecutive_fails = row.get("consecutive_fails", 0)
        cooldown_until = row.get("cooldown_until", 0)

        # 获取实时风控数据
        risk_data = await token_allocator.get_risk_data(token_id)
        risk_data["consecutive_fails"] = consecutive_fails

        success_count = row.get("success_count", 0)
        fail_count = row.get("fail_count", 0)

        # ?SimpleNamespace �?success/fail 计数?risk_score 计算
        from types import SimpleNamespace
        token_like = SimpleNamespace(success_count=success_count, fail_count=fail_count)
        risk_score = _calculate_risk_score(risk_data, token_like)

        # 判断实际状�?
        display_status = status
        if status == "active" and cooldown_until > now:
            display_status = "cooldown"
        if risk_data.get("in_cooldown", False):
            display_status = "cooldown"

        pool.append({
            "id": token_id,
            "status": display_status,
            "risk_score": risk_score,
            "rpm": risk_data.get("rpm", 0),
            "rpm_limit": settings.token_rpm_limit,
            "rph": risk_data.get("rph", 0),
            "rph_limit": settings.token_rph_limit,
            "concurrent": risk_data.get("concurrent", 0),
            "concurrent_limit": settings.token_max_concurrent,
            "consecutive_fails": consecutive_fails,
            "success_count": success_count,
            "fail_count": fail_count,
            "success_rate": round(success_count / max(1, success_count + fail_count), 4),
            "last_used": row.get("last_used"),
            "cooldown_until": cooldown_until if cooldown_until > now else None,
        })

    return {
        "tokens": pool,
        "total": len(pool),
        "limits": {
            "rpm": settings.token_rpm_limit,
            "rph": settings.token_rph_limit,
            "max_concurrent": settings.token_max_concurrent,
            "max_consecutive_uses": settings.token_max_consecutive_uses,
        },
    }


# ==================== Token 风控管理接口 ====================


@router.post("/admin/api/tokens/pause", include_in_schema=False)
async def admin_pause_token(
    request: Request,
    token_id: int = Form(...),
    _csrf: None = Depends(require_same_origin),
):
    """手动暂停单个 Token�?""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    from geek_gateway.database import user_db

    token = await user_db.get_token_by_id(token_id)
    if not token:
        return JSONResponse(status_code=404, content={"error": "Token 不存�?})

    await user_db.set_token_status(token_id, "suspended")
    await log_audit(request, "token_pause", "token", token_id, f"手动暂停 Token #{token_id}")
    return {"success": True, "token_id": token_id, "status": "suspended"}


@router.post("/admin/api/tokens/resume", include_in_schema=False)
async def admin_resume_token(
    request: Request,
    token_id: int = Form(...),
    _csrf: None = Depends(require_same_origin),
):
    """手动恢复单个 Token，重置冷却和连续失败计数�?""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    from geek_gateway.database import user_db

    token = await user_db.get_token_by_id(token_id)
    if not token:
        return JSONResponse(status_code=404, content={"error": "Token 不存�?})

    await user_db.set_token_status(token_id, "active")
    await user_db.update_token_risk_fields(
        token_id,
        consecutive_fails=0,
        cooldown_until=0,
    )
    await log_audit(request, "token_resume", "token", token_id, f"手动恢复 Token #{token_id}")
    return {"success": True, "token_id": token_id, "status": "active"}


@router.post("/admin/api/tokens/batch-pause-risky", include_in_schema=False)
async def admin_batch_pause_risky(
    request: Request,
    _csrf: None = Depends(require_same_origin),
):
    """批量暂停所有高风险 Token（Risk_Score > 0.7）�?""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    from types import SimpleNamespace
    from geek_gateway.database import user_db
    from geek_gateway.token_allocator import token_allocator

    all_tokens = await user_db.get_all_active_tokens()
    paused_count = 0

    for token in all_tokens:
        risk_data = await token_allocator.get_risk_data(token.id)
        risk_data["consecutive_fails"] = token.consecutive_fails
        risk_score = _calculate_risk_score(risk_data, token)

        if risk_score > 0.7:
            await user_db.set_token_status(token.id, "suspended")
            paused_count += 1

    await log_audit(request, "token_batch_pause", "token", "batch", f"批量暂停高风�?Token，共 {paused_count} �?)
    return {"success": True, "paused_count": paused_count}


# ==================== 用户配额配置与批量管理接�?====================


@router.post("/admin/api/users/quota", include_in_schema=False)
async def admin_set_user_quota(
    request: Request,
    user_id: int = Form(...),
    daily_quota: Optional[int] = Form(None),
    monthly_quota: Optional[int] = Form(None),
    _csrf: None = Depends(require_same_origin),
):
    """管理员为单个用户设置自定义每?每月配额�?""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    if daily_quota is None and monthly_quota is None:
        return JSONResponse(status_code=400, content={"error": "请至少提?daily_quota ?monthly_quota"})

    from geek_gateway.database import user_db
    from geek_gateway.quota_manager import quota_manager

    # 验证用户存在
    user = await user_db.get_user(user_id)
    if not user:
        return JSONResponse(status_code=404, content={"error": "用户不存�?})

    # 确保用户配额行存�?
    await quota_manager._ensure_user_quota_row(user_id)

    # 更新配额
    updates = []
    params = []
    if daily_quota is not None:
        updates.append("daily_quota = ?")
        params.append(daily_quota)
    if monthly_quota is not None:
        updates.append("monthly_quota = ?")
        params.append(monthly_quota)
    params.append(user_id)

    await user_db._backend.execute(
        f"UPDATE user_quotas SET {', '.join(updates)} WHERE user_id = ?",
        tuple(params),
    )

    details = f"设置用户 #{user_id} 配额: daily={daily_quota}, monthly={monthly_quota}"
    await log_audit(request, "quota_update", "user", user_id, details)

    return {
        "success": True,
        "user_id": user_id,
        "daily_quota": daily_quota,
        "monthly_quota": monthly_quota,
    }


@router.post("/admin/api/users/batch-approve", include_in_schema=False)
async def admin_batch_approve_users(
    request: Request,
    user_ids: str = Form(...),
    _csrf: None = Depends(require_same_origin),
):
    """批量审批待审核用户�?""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    from geek_gateway.database import user_db

    # 解析用户 ID 列表
    try:
        id_list = [int(uid.strip()) for uid in user_ids.split(",") if uid.strip()]
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "user_ids 格式无效"})

    if not id_list:
        return JSONResponse(status_code=400, content={"error": "user_ids 不能为空"})

    approved_count = 0
    for uid in id_list:
        try:
            await user_db.set_user_approval_status(uid, "approved")
            approved_count += 1
        except Exception:
            pass  # 跳过不存在或已审批的用户

    await log_audit(request, "user_batch_approve", "user", user_ids, f"批量审批 {approved_count} 个用�?)
    return {"success": True, "approved_count": approved_count}


@router.post("/admin/api/users/batch-ban", include_in_schema=False)
async def admin_batch_ban_users(
    request: Request,
    user_ids: str = Form(...),
    _csrf: None = Depends(require_same_origin),
):
    """批量封禁违规用户�?""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    from geek_gateway.database import user_db

    # 解析用户 ID 列表
    try:
        id_list = [int(uid.strip()) for uid in user_ids.split(",") if uid.strip()]
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "user_ids 格式无效"})

    if not id_list:
        return JSONResponse(status_code=400, content={"error": "user_ids 不能为空"})

    banned_count = 0
    for uid in id_list:
        try:
            await user_db.set_user_banned(uid, True)
            banned_count += 1
        except Exception:
            pass  # 跳过不存在的用户

    await log_audit(request, "user_batch_ban", "user", user_ids, f"批量封禁 {banned_count} 个用�?)
    return {"success": True, "banned_count": banned_count}


# ==================== 审计日志系统 ====================


async def log_audit(
    request: Request,
    admin_action: str,
    target_type: str,
    target_id,
    details: str = "",
) -> None:
    """
    记录管理员操作审计日志�?

    Args:
        request: 当前请求对象，用于获取管理员 IP
        admin_action: 操作类型 (token_pause, user_ban, quota_update, config_reload ?
        target_type: 目标类型 (token, user, config ?
        target_id: 目标 ID
        details: 操作详情（可选）
    """
    import time
    from geek_gateway.database import user_db

    admin_ip = request.client.host if request.client else "unknown"
    now = int(time.time() * 1000)

    try:
        await user_db._backend.execute(
            """INSERT INTO audit_logs (admin_username, action_type, target_type, target_id, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("admin", admin_action, target_type, str(target_id), details, now),
        )
    except Exception as e:
        logger.warning(f"审计日志写入失败: {e}")


@router.get("/admin/api/audit-logs", include_in_schema=False)
async def admin_get_audit_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    action: Optional[str] = Query(None),
    start_time: Optional[int] = Query(None),
    end_time: Optional[int] = Query(None),
):
    """查询审计日志，支持按操作类型和时间范围筛选�?""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    from geek_gateway.database import user_db

    where = []
    params = []

    if action:
        where.append("action_type = ?")
        params.append(action)
    if start_time is not None:
        where.append("created_at >= ?")
        params.append(start_time)
    if end_time is not None:
        where.append("created_at <= ?")
        params.append(end_time)

    where_clause = (" WHERE " + " AND ".join(where)) if where else ""

    # 获取总数
    count_row = await user_db._backend.fetch_one(
        f"SELECT COUNT(*) as cnt FROM audit_logs{where_clause}",
        tuple(params),
    )
    total = count_row["cnt"] if count_row else 0

    # 分页查询
    offset = (page - 1) * page_size
    query_params = list(params) + [page_size, offset]
    rows = await user_db._backend.fetch_all(
        f"SELECT * FROM audit_logs{where_clause} ORDER BY created_at DESC LIMIT ? OFFSET �?,
        tuple(query_params),
    )

    logs = []
    for row in rows:
        logs.append({
            "id": row["id"],
            "admin_username": row.get("admin_username", "admin"),
            "action_type": row["action_type"],
            "target_type": row.get("target_type"),
            "target_id": row.get("target_id"),
            "details": row.get("details"),
            "created_at": row["created_at"],
        })

    return {
        "logs": logs,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ==================== 配置热重载功?====================


@router.post("/admin/config/reload", include_in_schema=False)
async def admin_config_reload(
    request: Request,
    config_key: str = Form(...),
    config_value: str = Form(...),
    _csrf: None = Depends(require_same_origin),
):
    """
    热重载配置项�?

    将配置存储到 Redis Hash 并通过 Pub/Sub 通知所有节点更新�?
    单节点模式下直接更新内存配置�?
    """
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    from geek_gateway.config_reloader import HOT_RELOAD_KEYS, REDIS_CONFIG_HASH, REDIS_CONFIG_CHANNEL, _apply_config

    if config_key not in HOT_RELOAD_KEYS:
        return JSONResponse(
            status_code=400,
            content={"error": f"不支持热重载的配置项: {config_key}，支�?{', '.join(sorted(HOT_RELOAD_KEYS))}"},
        )

    # 验证值是否为有效整数
    try:
        int(config_value)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": f"配置值必须为整数: {config_value}"})

    from geek_gateway.redis_manager import redis_manager

    client = await redis_manager.get_client()
    if client:
        # 分布式模式：存储?Redis Hash 并发布通知
        try:
            await client.hset(REDIS_CONFIG_HASH, config_key, config_value)
            await client.publish(REDIS_CONFIG_CHANNEL, json.dumps([config_key]))
        except Exception as e:
            logger.warning(f"Redis 配置更新失败: {e}，仅更新本地配置")
            _apply_config(settings, config_key, config_value)
    else:
        # 单节点模式：直接更新内存配置
        _apply_config(settings, config_key, config_value)

    await log_audit(request, "config_reload", "config", config_key, f"{config_key}={config_value}")

    return {
        "success": True,
        "config_key": config_key,
        "config_value": config_value,
        "mode": "distributed" if client else "local",
    }


@router.get("/admin/api/config/hot-reload", include_in_schema=False)
async def admin_get_hot_reload_config(request: Request):
    """获取当前可热重载的配置值�?""
    session = request.cookies.get("admin_session")
    if not verify_admin_session(session):
        return JSONResponse(status_code=401, content={"error": "未授�?})

    from geek_gateway.config_reloader import HOT_RELOAD_KEYS, REDIS_CONFIG_HASH
    from geek_gateway.redis_manager import redis_manager

    # 优先?Redis 读取（分布式模式?
    config_values = {}
    client = await redis_manager.get_client()
    if client:
        try:
            redis_config = await client.hgetall(REDIS_CONFIG_HASH)
            for key in HOT_RELOAD_KEYS:
                if key in redis_config:
                    config_values[key] = int(redis_config[key])
                else:
                    config_values[key] = getattr(settings, key)
        except Exception:
            # Redis 读取失败，降级为本地配置
            for key in HOT_RELOAD_KEYS:
                config_values[key] = getattr(settings, key)
    else:
        # 单节点模式：从本?settings 读取
        for key in HOT_RELOAD_KEYS:
            config_values[key] = getattr(settings, key)

    return {"config": config_values}
