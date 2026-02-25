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
OpenAI 兼容 API ?Pydantic 模型?

定义请求和响应的数据模式，提供验证和序列化功�?
"""

import time
from typing import Any, Dict, List, Optional, Union
from typing_extensions import Annotated
from pydantic import BaseModel, Field


# ==================================================================================================
# /v1/models 端点模型
# ==================================================================================================

class OpenAIModel(BaseModel):
    """
    OpenAI 格式?AI 模型描述?

    用于 /v1/models 端点的响�?
    """
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "anthropic"
    description: Optional[str] = None


class ModelList(BaseModel):
    """
    OpenAI 格式的模型列�?

    GET /v1/models 端点的响�?
    """
    object: str = "list"
    data: List[OpenAIModel]


# ==================================================================================================
# /v1/chat/completions 端点模型
# ==================================================================================================

class ChatMessage(BaseModel):
    """
    OpenAI 格式的聊天消�?

    支持多种角色（user、assistant、system、tool）和多种内容格式（字符串、列表、对象）?

    Attributes:
        role: 发送者角色（user、assistant、system、tool?
        content: 消息内容（可以是字符串、列表或 None?
        name: 可选的发送者名?
        tool_calls: 工具调用列表（用?assistant?
        tool_call_id: 工具调用 ID（用?tool?
    """
    role: str
    content: Optional[Union[str, List[Any], Any]] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Any]] = None
    tool_call_id: Optional[str] = None
    
    model_config = {"extra": "allow"}


class ToolFunction(BaseModel):
    """
    工具函数描述?

    Attributes:
        name: 函数名称
        description: 函数描述
        parameters: 函数参数?JSON Schema
    """
    name: str
    description: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None


class Tool(BaseModel):
    """
    OpenAI 格式的工�?

    Attributes:
        type: 工具类型（通常�?function"?
        function: 函数描述
    """
    type: str = "function"
    function: ToolFunction


class ChatCompletionRequest(BaseModel):
    """
    OpenAI Chat Completions API 格式的请�?

    支持所有标?OpenAI API 字段，包括：
    - 基本参数（model、messages、stream?
    - 生成参数（temperature、top_p、max_tokens?
    - 工具调用（function calling?
    - 兼容性参数（接受但忽略）

    Attributes:
        model: 生成模型 ID
        messages: 聊天消息列表
        stream: 是否使用流式响应（默?False?
        temperature: 生成温度?-2?
        top_p: Top-p 采样
        n: 响应变体数量
        max_tokens: 响应最?token ?
        max_completion_tokens: max_tokens 的替代字?
        stop: 停止序列
        presence_penalty: 主题重复惩罚
        frequency_penalty: 词汇重复惩罚
        tools: 可用工具列表
        tool_choice: 工具选择策略
    """
    model: str
    messages: Annotated[List[ChatMessage], Field(min_length=1)]
    stream: bool = False

    # 生成参数
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    n: Optional[int] = 1
    max_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    stop: Optional[Union[str, List[str]]] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None

    # 工具调用
    tools: Optional[List[Tool]] = None
    tool_choice: Optional[Union[str, Dict]] = None

    # 兼容性字段（忽略?
    stream_options: Optional[Dict[str, Any]] = None
    logit_bias: Optional[Dict[str, float]] = None
    logprobs: Optional[bool] = None
    top_logprobs: Optional[int] = None
    user: Optional[str] = None
    seed: Optional[int] = None
    parallel_tool_calls: Optional[bool] = None

    model_config = {"extra": "allow"}


# ==================================================================================================
# 响应模型
# ==================================================================================================

class ChatCompletionChoice(BaseModel):
    """
    Chat Completion 的单个响应选项?

    Attributes:
        index: 选项索引
        message: 响应消息
        finish_reason: 完成原因（stop、tool_calls、length?
    """
    index: int = 0
    message: Dict[str, Any]
    finish_reason: Optional[str] = None


class ChatCompletionUsage(BaseModel):
    """
    Token 使用信息?

    Attributes:
        prompt_tokens: 请求 token ?
        completion_tokens: 响应 token ?
        total_tokens: ?token ?
        credits_used: 使用的积分（Kiro 特有?
    """
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    credits_used: Optional[float] = None


class ChatCompletionResponse(BaseModel):
    """
    Chat Completion 完整响应（非流式�?

    Attributes:
        id: 响应唯一 ID
        object: 对象类型?chat.completion"?
        created: 创建时间?
        model: 使用的模?
        choices: 响应选项列表
        usage: Token 使用信息
    """
    id: str
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[ChatCompletionChoice]
    usage: ChatCompletionUsage


class ChatCompletionChunkDelta(BaseModel):
    """
    流式 chunk 的增量变�?

    Attributes:
        role: 角色（仅在第一?chunk 中）
        content: 新内?
        tool_calls: 新的工具调用
    """
    role: Optional[str] = None
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


class ChatCompletionChunkChoice(BaseModel):
    """
    流式 chunk 中的单个选项?

    Attributes:
        index: 选项索引
        delta: 增量变化
        finish_reason: 完成原因（仅在最后一?chunk 中）
    """
    index: int = 0
    delta: ChatCompletionChunkDelta
    finish_reason: Optional[str] = None


class ChatCompletionChunk(BaseModel):
    """
    OpenAI 格式的流?chunk?

    Attributes:
        id: 响应唯一 ID
        object: 对象类型?chat.completion.chunk"?
        created: 创建时间?
        model: 使用的模?
        choices: 选项列表
        usage: 使用信息（仅在最后一?chunk 中）
    """
    id: str
    object: str = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[ChatCompletionChunkChoice]
    usage: Optional[ChatCompletionUsage] = None


# ==================================================================================================
# Anthropic Messages API 模型 (/v1/messages)
# ==================================================================================================

class AnthropicContentBlock(BaseModel):
    """
    Anthropic Messages API 的内容块?

    支持多种内容类型：text、image、tool_use、tool_result、thinking?

    Attributes:
        type: 内容类型
        text: 文本内容（type="text" 时）
        source: 图片来源（type="image" 时）
        id: tool_use ID（type="tool_use" 时）
        name: 工具名称（type="tool_use" 时）
        input: 工具输入数据（type="tool_use" 时）
        tool_use_id: 关联?tool_use ID（type="tool_result" 时）
        content: 工具结果（type="tool_result" 时）
        is_error: 错误标志（type="tool_result" 时）
        thinking: thinking 内容（type="thinking" 时）
    """
    type: str  # "text", "image", "tool_use", "tool_result", "thinking"
    text: Optional[str] = None
    # image fields
    source: Optional[Dict[str, Any]] = None  # {"type": "base64"/"url", "media_type": "...", "data"/"url": "..."}
    # tool_use fields
    id: Optional[str] = None
    name: Optional[str] = None
    input: Optional[Dict[str, Any]] = None
    # tool_result fields
    tool_use_id: Optional[str] = None
    content: Optional[Union[str, List[Any]]] = None
    is_error: Optional[bool] = None
    # thinking fields
    thinking: Optional[str] = None

    model_config = {"extra": "allow"}


class AnthropicMessage(BaseModel):
    """
    Anthropic 格式的消�?

    Attributes:
        role: 角色（user ?assistant?
        content: 内容（字符串或内容块列表?
    """
    role: str  # "user" or "assistant"
    content: Union[str, List[AnthropicContentBlock], List[Dict[str, Any]]]

    model_config = {"extra": "allow"}


class AnthropicTool(BaseModel):
    """
    Anthropic 格式的工�?

    支持两种格式:
    1. 标准工具: name + description + input_schema
    2. 内置工具: type (?web_search_20250305) + name

    Attributes:
        name: 工具名称
        description: 工具描述（可选）
        input_schema: 输入参数?JSON Schema（可选，标准工具必填?
        type: 工具类型（可选，用于内置工具?web_search?
    """
    name: str
    description: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None
    type: Optional[str] = None

    model_config = {"extra": "allow"}


class AnthropicMessagesRequest(BaseModel):
    """
    Anthropic Messages API 请求?

    Attributes:
        model: 模型 ID
        messages: 消息列表
        max_tokens: 最?token 数（必填?
        system: 系统提示?
        tools: 工具列表
        tool_choice: 工具选择策略
        temperature: 生成温度
        top_p: Top-p 采样
        top_k: Top-k 采样
        stop_sequences: 停止序列
        stream: 是否使用流式响应
        metadata: 请求元数?
        thinking: Extended thinking 设置
    """
    model: str
    messages: Annotated[List[AnthropicMessage], Field(min_length=1)]
    max_tokens: int  # Required in Anthropic API
    system: Optional[Union[str, List[Dict[str, Any]]]] = None
    tools: Optional[List[AnthropicTool]] = None
    tool_choice: Optional[Dict[str, Any]] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    stop_sequences: Optional[List[str]] = None
    stream: bool = False
    metadata: Optional[Dict[str, Any]] = None
    # Extended Thinking support
    thinking: Optional[Dict[str, Any]] = None  # {"type": "enabled", "budget_tokens": 1024}

    model_config = {"extra": "allow"}


class AnthropicUsage(BaseModel):
    """
    Anthropic 格式?token 使用信息?

    Attributes:
        input_tokens: 输入 token ?
        output_tokens: 输出 token ?
    """
    input_tokens: int = 0
    output_tokens: int = 0


class AnthropicResponseContentBlock(BaseModel):
    """
    Anthropic 响应中的内容�?

    Attributes:
        type: 内容类型（text、tool_use、thinking?
        text: 文本内容
        id: tool_use ID
        name: 工具名称
        input: 工具输入数据
        thinking: thinking 内容
    """
    type: str  # "text", "tool_use", "thinking"
    text: Optional[str] = None
    id: Optional[str] = None
    name: Optional[str] = None
    input: Optional[Dict[str, Any]] = None
    thinking: Optional[str] = None


class AnthropicMessagesResponse(BaseModel):
    """
    Anthropic Messages API 响应?

    Attributes:
        id: 响应唯一 ID
        type: 对象类型（始终为 "message"?
        role: 角色（始终为 "assistant"?
        content: 内容块列?
        model: 使用的模?
        stop_reason: 停止原因
        stop_sequence: 触发的停止序?
        usage: Token 使用信息
    """
    id: str
    type: str = "message"
    role: str = "assistant"
    content: List[AnthropicResponseContentBlock]
    model: str
    stop_reason: Optional[str] = None  # "end_turn", "max_tokens", "tool_use", "stop_sequence"
    stop_sequence: Optional[str] = None
    usage: AnthropicUsage