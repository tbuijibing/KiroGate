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
GeekGate - OpenAI & Anthropic 兼容�?Kiro API 代理�?

本包提供模块化架构，用于�?OpenAI/Anthropic API 请求代理�?Kiro (AWS CodeWhisperer)�?

支持两种 API 格式:
    - OpenAI API: /v1/chat/completions
    - Anthropic API: /v1/messages

模块:
    - config: 配置和常�?
    - models: OpenAI �?Anthropic API �?Pydantic 模型
    - auth: Kiro 认证管理�?
    - cache: 模型元数据缓�?
    - utils: 辅助工具函数
    - converters: OpenAI/Anthropic <-> Kiro 格式转换
    - parsers: AWS SSE 流解析器
    - streaming: 流式响应处理逻辑
    - http_client: 带重试逻辑�?HTTP 客户�?
    - routes: FastAPI 路由
    - exceptions: 异常处理�?
"""

# 版本�?config.py 导入 - 单一数据�?(Single Source of Truth)
from geek_gateway.config import APP_VERSION as __version__

__author__ = "Based on kiro-openai-gateway by Jwadow"

# Main components for convenient import
from geek_gateway.auth import GeekAuthManager
from geek_gateway.cache import ModelInfoCache
from geek_gateway.http_client import GeekHttpClient
from geek_gateway.routes import router

# Configuration
from geek_gateway.config import (
    PROXY_API_KEY,
    REGION,
    MODEL_MAPPING,
    AVAILABLE_MODELS,
    APP_VERSION,
    APP_TITLE,
    APP_DESCRIPTION,
)

# Models
from geek_gateway.models import (
    ChatCompletionRequest,
    ChatMessage,
    OpenAIModel,
    ModelList,
    # Anthropic models
    AnthropicMessagesRequest,
    AnthropicMessage,
    AnthropicTool,
    AnthropicContentBlock,
    AnthropicMessagesResponse,
    AnthropicUsage,
)

# Converters
from geek_gateway.converters import (
    build_kiro_payload,
    extract_text_content,
    merge_adjacent_messages,
    # Anthropic converters
    convert_anthropic_to_openai_request,
    convert_anthropic_tools_to_openai,
    convert_anthropic_messages_to_openai,
)

# Parsers
from geek_gateway.parsers import (
    AwsEventStreamParser,
    parse_bracket_tool_calls,
)

# Streaming
from geek_gateway.streaming import (
    stream_kiro_to_openai,
    collect_stream_response,
    # Anthropic streaming
    stream_kiro_to_anthropic,
    collect_anthropic_response,
)

# Exceptions
from geek_gateway.exceptions import (
    validation_exception_handler,
    sanitize_validation_errors,
)

__all__ = [
    # Version
    "__version__",

    # Main classes
    "GeekAuthManager",
    "ModelInfoCache",
    "GeekHttpClient",
    "router",

    # Configuration
    "PROXY_API_KEY",
    "REGION",
    "MODEL_MAPPING",
    "AVAILABLE_MODELS",
    "APP_VERSION",
    "APP_TITLE",
    "APP_DESCRIPTION",

    # OpenAI models
    "ChatCompletionRequest",
    "ChatMessage",
    "OpenAIModel",
    "ModelList",

    # Anthropic models
    "AnthropicMessagesRequest",
    "AnthropicMessage",
    "AnthropicTool",
    "AnthropicContentBlock",
    "AnthropicMessagesResponse",
    "AnthropicUsage",

    # Converters
    "build_kiro_payload",
    "extract_text_content",
    "merge_adjacent_messages",
    "convert_anthropic_to_openai_request",
    "convert_anthropic_tools_to_openai",
    "convert_anthropic_messages_to_openai",

    # Parsers
    "AwsEventStreamParser",
    "parse_bracket_tool_calls",

    # Streaming
    "stream_kiro_to_openai",
    "collect_stream_response",
    "stream_kiro_to_anthropic",
    "collect_anthropic_response",

    # Exceptions
    "validation_exception_handler",
    "sanitize_validation_errors",
]
