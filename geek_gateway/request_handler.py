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
请求处理公共函数?

提取 /v1/chat/completions ?/v1/messages 端点的公共逻辑?
减少代码重复，提高可维护�?
"""

import json
import time
from typing import Any, Callable, Dict, List, Optional, Union

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger

from geek_gateway.auth import GeekAuthManager
from geek_gateway.cache import ModelInfoCache
from geek_gateway.converters import build_kiro_payload, convert_anthropic_to_openai_request, is_thinking_enabled
from geek_gateway.config import settings
from geek_gateway.http_client import GeekHttpClient
from geek_gateway.models import (
    ChatCompletionRequest,
    AnthropicMessagesRequest,
)
from geek_gateway.streaming import (
    stream_kiro_to_openai,
    collect_stream_response,
    stream_kiro_to_anthropic,
    collect_anthropic_response,
)
from geek_gateway.utils import generate_conversation_id, get_kiro_headers
from geek_gateway.config import settings, AUTO_CHUNKING_ENABLED, AUTO_CHUNK_THRESHOLD
from geek_gateway.metrics import metrics


# 导入可选的自动分片处理?
try:
    from geek_gateway.auto_chunked_handler import process_with_auto_chunking
    auto_chunking_available = True
except ImportError:
    auto_chunking_available = False


# 导入 debug_logger
try:
    from geek_gateway.debug_logger import debug_logger
except ImportError:
    debug_logger = None


class RequestHandler:
    """
    请求处理器基类，封装公共逻辑?
    """

    @staticmethod
    def prepare_request_logging(request_data: Union[ChatCompletionRequest, AnthropicMessagesRequest]) -> None:
        """
        准备请求日志记录?

        Args:
            request_data: 请求数据
        """
        if debug_logger:
            debug_logger.prepare_new_request()

        try:
            request_body = json.dumps(request_data.model_dump(), ensure_ascii=False, indent=2).encode('utf-8')
            if debug_logger:
                debug_logger.log_request_body(request_body)
        except Exception as e:
            logger.warning(f"Failed to log request body: {e}")

    @staticmethod
    def log_kiro_request(kiro_payload: dict) -> None:
        """
        记录 Kiro 请求?

        Args:
            kiro_payload: Kiro 请求 payload
        """
        try:
            kiro_request_body = json.dumps(kiro_payload, ensure_ascii=False, indent=2).encode('utf-8')
            if debug_logger:
                debug_logger.log_kiro_request_body(kiro_request_body)
        except Exception as e:
            logger.warning(f"Failed to log Kiro request: {e}")

    @staticmethod
    async def handle_api_error(
        response,
        http_client: GeekHttpClient,
        endpoint_name: str,
        error_format: str = "openai",
        request: Optional[Request] = None
    ) -> JSONResponse:
        """
        处理 API 错误?

        Args:
            response: HTTP 响应
            http_client: HTTP 客户?
            endpoint_name: 端点名称（用于日志）
            error_format: 错误格式?openai" �?anthropic"?

        Returns:
            JSONResponse 错误响应
        """
        try:
            error_content = await response.aread()
        except Exception:
            error_content = "未知错误".encode("utf-8")
        finally:
            try:
                await response.aclose()
            except Exception:
                pass

        await http_client.close()
        error_text = error_content.decode('utf-8', errors='replace')
        logger.error(f"Error from Kiro API: {response.status_code} - {error_text}")

        # 尝试解析 JSON 错误响应
        error_message = error_text
        error_reason = None
        try:
            error_json = json.loads(error_text)
            if isinstance(error_json, dict):
                if "reason" in error_json:
                    error_reason = str(error_json["reason"])
                if "message" in error_json:
                    error_message = error_json["message"]
                elif "error" in error_json and isinstance(error_json["error"], dict):
                    if "message" in error_json["error"]:
                        error_message = error_json["error"]["message"]
                    if not error_reason and "reason" in error_json["error"]:
                        error_reason = str(error_json["error"]["reason"])
                if error_reason:
                    error_message = f"{error_message} (reason: {error_reason})"
        except (json.JSONDecodeError, KeyError):
            pass

        logger.warning(f"HTTP {response.status_code} - POST {endpoint_name} - {error_message[:100]}")

        if debug_logger:
            debug_logger.flush_on_error(response.status_code, error_message)

        if request and hasattr(request.state, "donated_token_id"):
            reason_text = error_reason or error_message
            if "MONTHLY_REQUEST_COUNT" in reason_text:
                try:
                    from geek_gateway.database import user_db
                    token_id = request.state.donated_token_id
                    user_db.set_token_status(token_id, "expired")
                    logger.warning(f"Token {token_id} marked expired due to monthly limit")
                except Exception as e:
                    logger.warning(f"Failed to mark token expired: {e}")

        # 根据格式返回错误
        if error_format == "anthropic":
            return JSONResponse(
                status_code=response.status_code,
                content={
                    "type": "error",
                    "error": {
                        "type": "api_error",
                        "message": error_message
                    }
                }
            )
        else:
            return JSONResponse(
                status_code=response.status_code,
                content={
                    "error": {
                        "message": error_message,
                        "type": "kiro_api_error",
                        "code": response.status_code
                    }
                }
            )

    @staticmethod
    def log_success(endpoint_name: str, is_streaming: bool = False) -> None:
        """
        记录成功日志?

        Args:
            endpoint_name: 端点名称
            is_streaming: 是否为流式响?
        """
        mode = "streaming" if is_streaming else "non-streaming"
        logger.info(f"HTTP 200 - POST {endpoint_name} ({mode}) - completed")

    @staticmethod
    def log_error(endpoint_name: str, error: Union[str, Exception], status_code: int = 500) -> None:
        """
        记录错误日志?

        Args:
            endpoint_name: 端点名称
            error: 错误信息
            status_code: HTTP 状态码
        """
        if isinstance(error, Exception):
            error_msg = str(error) if str(error) else f"{type(error).__name__}: {repr(error)}"
        else:
            error_msg = error
        logger.error(f"HTTP {status_code} - POST {endpoint_name} - {error_msg[:100]}")

    @staticmethod
    def handle_streaming_error(error: Exception, endpoint_name: str) -> str:
        """
        处理流式错误，确保错误信息不为空?

        Args:
            error: 异常
            endpoint_name: 端点名称

        Returns:
            错误信息字符?
        """
        error_msg = str(error) if str(error) else f"{type(error).__name__}: {repr(error)}"
        RequestHandler.log_error(endpoint_name, error_msg, 500)
        return error_msg

    @staticmethod
    def prepare_tokenizer_data(request_data: ChatCompletionRequest) -> tuple:
        """
        准备用于 token 计数的数�?

        Args:
            request_data: 请求数据

        Returns:
            (messages_for_tokenizer, tools_for_tokenizer)
        """
        messages_for_tokenizer = [msg.model_dump() for msg in request_data.messages]
        tools_for_tokenizer = [tool.model_dump() for tool in request_data.tools] if request_data.tools else None
        return messages_for_tokenizer, tools_for_tokenizer

    @staticmethod
    async def create_stream_response(
        http_client: GeekHttpClient,
        response,
        model: str,
        model_cache: ModelInfoCache,
        auth_manager: GeekAuthManager,
        stream_func: Callable,
        endpoint_name: str,
        messages_for_tokenizer: Optional[List] = None,
        tools_for_tokenizer: Optional[List] = None,
        **kwargs
    ) -> StreamingResponse:
        """
        创建流式响应?

        Args:
            http_client: HTTP 客户?
            response: Kiro API 响应
            model: 模型名称
            model_cache: 模型缓存
            auth_manager: 认证管理?
            stream_func: 流式处理函数
            endpoint_name: 端点名称
            messages_for_tokenizer: 消息数据（用?token 计数?
            tools_for_tokenizer: 工具数据（用?token 计数?
            **kwargs: 其他参数

        Returns:
            StreamingResponse
        """
        async def stream_wrapper():
            streaming_error = None
            try:
                async for chunk in stream_func(
                    http_client.client,
                    response,
                    model,
                    model_cache,
                    auth_manager,
                    request_messages=messages_for_tokenizer,
                    request_tools=tools_for_tokenizer,
                    **kwargs
                ):
                    yield chunk
            except Exception as e:
                streaming_error = e
                raise
            finally:
                await http_client.close()
                if streaming_error:
                    RequestHandler.handle_streaming_error(streaming_error, endpoint_name)
                else:
                    RequestHandler.log_success(endpoint_name, is_streaming=True)
                if debug_logger:
                    if streaming_error:
                        error_msg = RequestHandler.handle_streaming_error(streaming_error, endpoint_name)
                        debug_logger.flush_on_error(500, error_msg)
                    else:
                        debug_logger.discard_buffers()

        return StreamingResponse(stream_wrapper(), media_type="text/event-stream")

    @staticmethod
    def should_enable_auto_chunking(messages: List) -> bool:
        """
        检查是否应该启用自动分片功�?

        Args:
            messages: 消息列表

        Returns:
            是否启用自动分片
        """
        if not AUTO_CHUNKING_ENABLED or not auto_chunking_available:
            return False

        # 检查消息内容是否超过阈?
        total_chars = 0
        for msg in messages:
            if hasattr(msg, 'content'):
                content = msg.content
            elif isinstance(msg, dict):
                content = msg.get("content", "")
            else:
                continue

            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        total_chars += len(block.get("text", ""))

        return total_chars > AUTO_CHUNK_THRESHOLD

    @staticmethod
    async def create_non_stream_response(
        http_client: GeekHttpClient,
        response,
        model: str,
        model_cache: ModelInfoCache,
        auth_manager: GeekAuthManager,
        collect_func: Callable,
        endpoint_name: str,
        messages_for_tokenizer: Optional[List] = None,
        tools_for_tokenizer: Optional[List] = None,
        **kwargs
    ) -> JSONResponse:
        """
        创建非流式响�?

        Args:
            http_client: HTTP 客户?
            response: Kiro API 响应
            model: 模型名称
            model_cache: 模型缓存
            auth_manager: 认证管理?
            collect_func: 收集响应函数
            endpoint_name: 端点名称
            messages_for_tokenizer: 消息数据（用?token 计数?
            tools_for_tokenizer: 工具数据（用?token 计数?
            **kwargs: 其他参数

        Returns:
            JSONResponse
        """
        collected_response = await collect_func(
            http_client.client,
            response,
            model,
            model_cache,
            auth_manager,
            request_messages=messages_for_tokenizer,
            request_tools=tools_for_tokenizer,
            **kwargs
        )

        await http_client.close()
        RequestHandler.log_success(endpoint_name, is_streaming=False)

        if debug_logger:
            debug_logger.discard_buffers()

        return JSONResponse(content=collected_response)

    @staticmethod
    async def process_request(
        request: Request,
        request_data: Union[ChatCompletionRequest, AnthropicMessagesRequest],
        endpoint_name: str,
        convert_to_openai: bool = False,
        response_format: str = "openai"
    ) -> Union[StreamingResponse, JSONResponse]:
        """
        处理请求的核心逻辑?

        Args:
            request: FastAPI Request
            request_data: 请求数据
            endpoint_name: 端点名称
            convert_to_openai: 是否需要将 Anthropic 请求转换?OpenAI 格式
            response_format: 响应格式?openai" �?anthropic"?

        Returns:
            StreamingResponse ?JSONResponse
        """
        start_time = time.time()
        api_type = "anthropic" if response_format == "anthropic" else "openai"

        # Use auth_manager from request.state if available (multi-tenant mode)
        # Otherwise fall back to global auth_manager
        auth_manager: GeekAuthManager = getattr(request.state, 'auth_manager', None) or request.app.state.auth_manager
        model_cache: ModelInfoCache = request.app.state.model_cache

        # 准备日志
        RequestHandler.prepare_request_logging(request_data)

        # 如果需要，转换 Anthropic 请求?OpenAI 格式
        if convert_to_openai:
            try:
                openai_request = convert_anthropic_to_openai_request(request_data)
            except Exception as e:
                logger.error(f"Failed to convert Anthropic request: {e}")
                raise HTTPException(status_code=400, detail=f"请求格式无效: {str(e)}")
        else:
            openai_request = request_data

        # 生成会话 ID
        conversation_id = generate_conversation_id()

        # 获取 thinking 配置（从原始请求中获取，支持 Anthropic 格式?
        thinking_config = getattr(request_data, 'thinking', None)
        thinking_enabled = is_thinking_enabled(thinking_config)

        # 构建 Kiro payload
        try:
            kiro_payload = build_kiro_payload(
                openai_request,
                conversation_id,
                auth_manager.profile_arn or "",
                thinking_config=thinking_config
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        # 记录 Kiro 请求
        RequestHandler.log_kiro_request(kiro_payload)

        # 创建 HTTP 客户?
        http_client = GeekHttpClient(auth_manager)
        url = f"{auth_manager.api_host}/generateAssistantResponse"

        try:
            # 发送请求到 Kiro API
            response = await http_client.request_with_retry(
                "POST",
                url,
                kiro_payload,
                stream=True,
                model=request_data.model
            )

            if response.status_code != 200:
                duration_ms = (time.time() - start_time) * 1000
                metrics.record_request(
                    endpoint=endpoint_name,
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                    model=request_data.model,
                    is_stream=request_data.stream,
                    api_type=api_type
                )
                return await RequestHandler.handle_api_error(
                    response,
                    http_client,
                    endpoint_name,
                    response_format,
                    request=request
                )

            # 准备 token 计数数据
            messages_for_tokenizer, tools_for_tokenizer = RequestHandler.prepare_tokenizer_data(openai_request)

            # 记录成功请求
            duration_ms = (time.time() - start_time) * 1000
            metrics.record_request(
                endpoint=endpoint_name,
                status_code=200,
                duration_ms=duration_ms,
                model=request_data.model,
                is_stream=request_data.stream,
                api_type=api_type
            )

            # 根据请求类型和响应格式处?
            if request_data.stream:
                if response_format == "anthropic":
                    return await RequestHandler.create_stream_response(
                        http_client,
                        response,
                        request_data.model,
                        model_cache,
                        auth_manager,
                        stream_kiro_to_anthropic,
                        endpoint_name,
                        messages_for_tokenizer,
                        tools_for_tokenizer,
                        thinking_enabled=thinking_enabled
                    )
                else:
                    return await RequestHandler.create_stream_response(
                        http_client,
                        response,
                        request_data.model,
                        model_cache,
                        auth_manager,
                        stream_kiro_to_openai,
                        endpoint_name,
                        messages_for_tokenizer,
                        tools_for_tokenizer
                    )
            else:
                if response_format == "anthropic":
                    return await RequestHandler.create_non_stream_response(
                        http_client,
                        response,
                        request_data.model,
                        model_cache,
                        auth_manager,
                        collect_anthropic_response,
                        endpoint_name,
                        messages_for_tokenizer,
                        tools_for_tokenizer,
                        thinking_enabled=thinking_enabled
                    )
                else:
                    return await RequestHandler.create_non_stream_response(
                        http_client,
                        response,
                        request_data.model,
                        model_cache,
                        auth_manager,
                        collect_stream_response,
                        endpoint_name,
                        messages_for_tokenizer,
                        tools_for_tokenizer
                    )

        except HTTPException as e:
            await http_client.close()
            duration_ms = (time.time() - start_time) * 1000
            metrics.record_request(
                endpoint=endpoint_name,
                status_code=e.status_code,
                duration_ms=duration_ms,
                model=request_data.model,
                is_stream=request_data.stream,
                api_type=api_type
            )
            RequestHandler.log_error(endpoint_name, e.detail, e.status_code)
            if debug_logger:
                debug_logger.flush_on_error(e.status_code, str(e.detail))
            raise
        except Exception as e:
            await http_client.close()
            duration_ms = (time.time() - start_time) * 1000
            metrics.record_request(
                endpoint=endpoint_name,
                status_code=500,
                duration_ms=duration_ms,
                model=request_data.model,
                is_stream=request_data.stream,
                api_type=api_type
            )
            error_msg = str(e) if str(e) else f"{type(e).__name__}: {repr(e)}"
            logger.error(f"Internal error: {error_msg}", exc_info=True)
            RequestHandler.log_error(endpoint_name, error_msg, 500)
            if debug_logger:
                debug_logger.flush_on_error(500, error_msg)
            if settings.debug_mode == "off":
                detail = "服务器内部错�?
            else:
                detail = f"服务器内部错�? {error_msg}"
            raise HTTPException(status_code=500, detail=detail)
