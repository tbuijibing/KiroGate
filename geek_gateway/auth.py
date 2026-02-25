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
Kiro API Authentication Manager.

Manages access token lifecycle:
- Load credentials from .env or JSON file
- Auto-refresh token on expiration
- Thread-safe refresh using asyncio.Lock
- Support for both Social (Kiro Desktop) and IDC (AWS SSO OIDC) authentication
"""

import asyncio
import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from geek_gateway.config import (
    TOKEN_REFRESH_THRESHOLD,
    get_kiro_refresh_url,
    get_kiro_api_host,
    get_kiro_q_host,
    get_aws_sso_oidc_url,
)
from geek_gateway.utils import get_machine_fingerprint


class AuthType(Enum):
    """
    认证类型枚举�?

    SOCIAL: Kiro IDE 社交账号登录 (Google/GitHub)
        - 端点: https://prod.{region}.auth.desktop.kiro.dev/refreshToken
        - 请求: {"refreshToken": "..."}

    IDC: AWS IAM Identity Center (Builder ID)
        - 端点: https://oidc.{region}.amazonaws.com/token
        - 请求: grant_type=refresh_token&client_id=...&client_secret=...&refresh_token=...
    """
    SOCIAL = "social"
    IDC = "idc"


class GeekAuthManager:
    """
    Manages token lifecycle for Kiro API access.

    Supports:
    - Loading credentials from .env or JSON file
    - Auto-refresh token on expiration
    - Checking expiration time (expiresAt)
    - Saving updated tokens to file
    - Both Social (Kiro Desktop) and IDC (AWS SSO OIDC) authentication

    Attributes:
        profile_arn: AWS CodeWhisperer profile ARN
        region: AWS region
        api_host: API host for current region
        q_host: Q API host for current region
        fingerprint: Unique machine fingerprint
        auth_type: Authentication type (SOCIAL or IDC)

    Example:
        >>> # Social 登录 (默认)
        >>> auth_manager = GeekAuthManager(
        ...     refresh_token="your_refresh_token",
        ...     region="us-east-1"
        ... )
        >>> token = await auth_manager.get_access_token()

        >>> # IDC 登录 (AWS Builder ID)
        >>> auth_manager = GeekAuthManager(
        ...     refresh_token="your_refresh_token",
        ...     client_id="your_client_id",
        ...     client_secret="your_client_secret",
        ...     region="us-east-1"
        ... )
        >>> token = await auth_manager.get_access_token()
    """

    def __init__(
        self,
        refresh_token: Optional[str] = None,
        profile_arn: Optional[str] = None,
        region: str = "us-east-1",
        creds_file: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ):
        """
        Initialize authentication manager.

        Args:
            refresh_token: Refresh token for obtaining access token
            profile_arn: AWS CodeWhisperer profile ARN
            region: AWS region (default us-east-1)
            creds_file: Path to JSON credentials file (optional)
            client_id: OAuth client ID (for IDC mode, optional)
            client_secret: OAuth client secret (for IDC mode, optional)
        """
        self._refresh_token = refresh_token
        self._profile_arn = profile_arn
        self._region = region
        self._creds_file = creds_file

        # IDC (AWS SSO OIDC) 特有字段
        self._client_id: Optional[str] = client_id
        self._client_secret: Optional[str] = client_secret
        # AWS SSO OIDC 默认 scopes
        self._scopes: list = [
            "codewhisperer:completions",
            "codewhisperer:analysis",
            "codewhisperer:conversations",
            "codewhisperer:transformations",
            "codewhisperer:taskassist",
        ]

        self._access_token: Optional[str] = None
        self._expires_at: Optional[datetime] = None
        self._lock = asyncio.Lock()

        # 认证类型，加载凭证后确定
        self._auth_type: AuthType = AuthType.SOCIAL

        # Dynamic URLs based on region
        self._refresh_url = get_kiro_refresh_url(region)
        self._api_host = get_kiro_api_host(region)
        self._q_host = get_kiro_q_host(region)

        # Fingerprint for User-Agent
        self._fingerprint = get_machine_fingerprint()

        # Load credentials from file if specified
        if creds_file:
            self._load_credentials_from_file(creds_file)

        # 根据凭证确定认证类型
        self._detect_auth_type()

    def _detect_auth_type(self) -> None:
        """
        根据凭证检测认证类型�?

        如果�?client_id �?client_secret，则�?IDC 模式�?
        否则�?Social 模式�?
        """
        if self._client_id and self._client_secret:
            self._auth_type = AuthType.IDC
            logger.info("检测到认证类型: IDC (AWS SSO OIDC)")
        else:
            self._auth_type = AuthType.SOCIAL
            logger.debug("使用认证类型: Social (Kiro Desktop)")

    @staticmethod
    def _is_url(path: str) -> bool:
        """Check if path is a URL."""
        return path.startswith(('http://', 'https://'))

    def _load_credentials_from_file(self, file_path: str) -> None:
        """
        Load credentials from JSON file or remote URL.

        Supported fields in JSON:
        - refreshToken: Refresh token
        - accessToken: Access token (if already available)
        - profileArn: Profile ARN
        - region: AWS region
        - expiresAt: Token expiration time (ISO 8601)
        - clientId: OAuth client ID (for IDC mode)
        - clientSecret: OAuth client secret (for IDC mode)

        Args:
            file_path: Path to JSON file or remote URL (http/https)
        """
        try:
            if self._is_url(file_path):
                # Fetch from remote URL
                response = httpx.get(file_path, timeout=10.0, follow_redirects=True)
                response.raise_for_status()
                data = response.json()
                logger.info(f"Credentials loaded from URL: {file_path}")
            else:
                # Load from local file
                path = Path(file_path).expanduser()
                if not path.exists():
                    logger.warning(f"Credentials file not found: {file_path}")
                    return

                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                logger.info(f"Credentials loaded from file: {file_path}")

            if 'refreshToken' in data:
                self._refresh_token = data['refreshToken']
            if 'accessToken' in data:
                self._access_token = data['accessToken']
            if 'profileArn' in data:
                self._profile_arn = data['profileArn']
            if 'region' in data:
                self._region = data['region']
                # Update URLs for new region
                self._refresh_url = get_kiro_refresh_url(self._region)
                self._api_host = get_kiro_api_host(self._region)
                self._q_host = get_kiro_q_host(self._region)

            # IDC (AWS SSO OIDC) 特有字段
            if 'clientId' in data:
                self._client_id = data['clientId']
            if 'clientSecret' in data:
                self._client_secret = data['clientSecret']

            # Parse expiresAt
            if 'expiresAt' in data:
                try:
                    expires_str = data['expiresAt']
                    if expires_str.endswith('Z'):
                        self._expires_at = datetime.fromisoformat(expires_str.replace('Z', '+00:00'))
                    else:
                        self._expires_at = datetime.fromisoformat(expires_str)
                except Exception as e:
                    logger.warning(f"Failed to parse expiresAt: {e}")

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error loading credentials from URL: {e}")
        except httpx.RequestError as e:
            logger.error(f"Request error loading credentials from URL: {e}")
        except Exception as e:
            logger.error(f"Error loading credentials: {e}")

    def _save_credentials_to_file(
        self,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        profile_arn: Optional[str] = None
    ) -> None:
        """
        Save updated credentials to JSON file.

        Updates existing file, preserving other fields.

        Args:
            access_token: New access token (uses current if None)
            refresh_token: New refresh token (uses current if None)
            profile_arn: New profile ARN (uses current if None)
        """
        if not self._creds_file:
            return

        try:
            path = Path(self._creds_file).expanduser()

            # Read existing data
            existing_data = {}
            if path.exists():
                with open(path, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)

            # Update data with provided values or current values
            existing_data['accessToken'] = access_token if access_token is not None else self._access_token
            existing_data['refreshToken'] = refresh_token if refresh_token is not None else self._refresh_token
            if self._expires_at:
                existing_data['expiresAt'] = self._expires_at.isoformat()
            if profile_arn is not None:
                existing_data['profileArn'] = profile_arn
            elif self._profile_arn:
                existing_data['profileArn'] = self._profile_arn

            # Save
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, indent=2, ensure_ascii=False)

            logger.debug(f"Credentials saved to {self._creds_file}")

        except Exception as e:
            logger.error(f"Error saving credentials: {e}")

    def is_token_expiring_soon(self) -> bool:
        """
        Check if token is expiring soon.

        Returns:
            True if token expires within TOKEN_REFRESH_THRESHOLD seconds
            or if expiration info is missing
        """
        if not self._expires_at:
            return True

        now = datetime.now(timezone.utc)
        threshold = now.timestamp() + TOKEN_REFRESH_THRESHOLD

        return self._expires_at.timestamp() <= threshold

    async def _refresh_token_request(self) -> None:
        """
        Execute token refresh request with exponential backoff retry.

        根据认证类型路由到对应的刷新方法�?
        - SOCIAL: 使用 Kiro Desktop Auth 端点
        - IDC: 使用 AWS SSO OIDC 端点

        Raises:
            ValueError: If refresh token is not set or response lacks accessToken
            httpx.HTTPError: On HTTP request error after all retries
        """
        if self._auth_type == AuthType.IDC:
            await self._refresh_token_idc()
        else:
            await self._refresh_token_social()

    async def _refresh_token_social(self) -> None:
        """
        使用 Social (Kiro Desktop Auth) 端点刷新 Token�?

        端点: https://prod.{region}.auth.desktop.kiro.dev/refreshToken
        方法: POST
        Content-Type: application/json
        请求? {"refreshToken": "..."}
        """
        if not self._refresh_token:
            raise ValueError("Refresh token is not set")

        logger.info("通过 Social (Kiro Desktop Auth) 刷新 Token...")

        payload = {'refreshToken': self._refresh_token}
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"GeekGateway-{self._fingerprint[:16]}",
        }

        data = await self._execute_refresh_request(self._refresh_url, json_data=payload, headers=headers)
        self._process_refresh_response(data)

    async def _refresh_token_idc(self) -> None:
        """
        使用 IDC (AWS SSO OIDC) 端点刷新 Token�?

        端点: https://oidc.{region}.amazonaws.com/token
        方法: POST
        Content-Type: application/json
        请求? {"clientId": "...", "clientSecret": "...", "grantType": "refresh_token", "refreshToken": "..."}

        注意: AWS SSO OIDC 使用 JSON 格式�?camelCase 字段�?
        """
        if not self._refresh_token:
            raise ValueError("Refresh token is not set")
        if not self._client_id:
            raise ValueError("Client ID is not set (required for IDC mode)")
        if not self._client_secret:
            raise ValueError("Client secret is not set (required for IDC mode)")

        logger.info("通过 IDC (AWS SSO OIDC) 刷新 Token...")

        url = get_aws_sso_oidc_url(self._region)
        # AWS SSO OIDC 使用 JSON 格式�?camelCase 字段�?
        json_data = {
            "clientId": self._client_id,
            "clientSecret": self._client_secret,
            "grantType": "refresh_token",
            "refreshToken": self._refresh_token,
        }

        headers = {
            "Content-Type": "application/json",
        }

        data = await self._execute_refresh_request(url, json_data=json_data, headers=headers)
        self._process_refresh_response(data)

    async def _execute_refresh_request(
        self,
        url: str,
        json_data: Optional[dict] = None,
        form_data: Optional[dict] = None,
        headers: Optional[dict] = None
    ) -> dict:
        """
        执行刷新请求，带指数退避重试�?

        Args:
            url: 请求 URL
            json_data: JSON 请求?(用于 Social 模式)
            form_data: Form 请求?(用于 IDC 模式)
            headers: 请求�?

        Returns:
            响应 JSON 数据
        """
        max_retries = 3
        base_delay = 1.0
        last_error = None

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    if json_data:
                        response = await client.post(url, json=json_data, headers=headers)
                    else:
                        response = await client.post(url, data=form_data, headers=headers)
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code in (429, 500, 502, 503, 504):
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Token 刷新失败 (尝试 {attempt + 1}/{max_retries}): "
                        f"HTTP {e.response.status_code}, {delay}s 后重�?
                    )
                    await asyncio.sleep(delay)
                else:
                    raise
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_error = e
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"Token 刷新失败 (尝试 {attempt + 1}/{max_retries}): "
                    f"{type(e).__name__}, {delay}s 后重�?
                )
                await asyncio.sleep(delay)

        logger.error(f"Token 刷新。{max_retries} 次尝试后失败")
        raise last_error

    def _process_refresh_response(self, data: dict) -> None:
        """
        处理刷新响应，更新内部状态�?

        Args:
            data: 响应 JSON 数据
        """
        new_access_token = data.get("accessToken")
        new_refresh_token = data.get("refreshToken")
        expires_in = data.get("expiresIn", 3600)
        new_profile_arn = data.get("profileArn")

        if not new_access_token:
            raise ValueError(f"响应中没。accessToken: {data}")

        # 计算过期时间（减?60 秒缓冲）
        now = datetime.now(timezone.utc).replace(microsecond=0)
        new_expires_at = datetime.fromtimestamp(
            now.timestamp() + expires_in - 60,
            tz=timezone.utc
        )

        # 先保存到文件
        self._save_credentials_to_file(new_access_token, new_refresh_token, new_profile_arn)

        # 更新内部状�?
        self._access_token = new_access_token
        if new_refresh_token:
            self._refresh_token = new_refresh_token
        if new_profile_arn:
            self._profile_arn = new_profile_arn
        self._expires_at = new_expires_at

        logger.info(f"Token 刷新成功，过期时�?{self._expires_at.isoformat()}")

    async def get_access_token(self) -> str:
        """
        Return valid access_token, refreshing if necessary.

        Thread-safe method using asyncio.Lock.
        Automatically refreshes token if expired or expiring soon.

        Returns:
            Valid access token

        Raises:
            ValueError: If unable to obtain access token
        """
        async with self._lock:
            if not self._access_token or self.is_token_expiring_soon():
                await self._refresh_token_request()

            if not self._access_token:
                raise ValueError("Failed to obtain access token")

            return self._access_token

    async def force_refresh(self) -> str:
        """
        Force token refresh.

        Used when receiving 403 error from API.

        Returns:
            New access token
        """
        async with self._lock:
            await self._refresh_token_request()
            return self._access_token

    @property
    def profile_arn(self) -> Optional[str]:
        """AWS CodeWhisperer profile ARN."""
        return self._profile_arn

    @property
    def region(self) -> str:
        """AWS region."""
        return self._region

    @property
    def api_host(self) -> str:
        """API host for current region."""
        return self._api_host

    @property
    def q_host(self) -> str:
        """Q API host for current region."""
        return self._q_host

    @property
    def fingerprint(self) -> str:
        """Unique machine fingerprint."""
        return self._fingerprint

    @property
    def auth_type(self) -> AuthType:
        """Authentication type (SOCIAL or IDC)."""
        return self._auth_type
