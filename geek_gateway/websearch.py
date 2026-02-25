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
WebSearch 工具处理模块?

实现 Anthropic WebSearch 请求?Kiro MCP 的转换和响应生成?
�?kiro.rs 项目的实�?
"""

import json
import time
import uuid
import random
import string
from typing import Any, AsyncGenerator, Dict, Optional, Tuple

import httpx
from fastapi import Request
from fastapi.responses import StreamingResponse
from loguru import logger

from geek_gateway.auth import GeekAuthManager
from geek_gateway.models import AnthropicMessagesRequest
from geek_gateway.tokenizer import count_message_tokens, count_tools_tokens
from geek_gateway.utils import get_kiro_headers


def has_web_search_tool(request: AnthropicMessagesRequest) -> bool:
    """
    检查请求是否为?WebSearch 请求?

    条件：tools 有且只有一个，且为 web_search 工具

    支持的格式：
    1. {"type": "web_search_20250305", "name": "web_search"}
    2. {"name": "web_search", ...}

    Args:
        request: Anthropic 消息请求

    Returns:
        是否为纯 WebSearch 请求
    """
    if not request.tools:
        return False

    if len(request.tools) != 1:
        return False

    tool = request.tools[0]
    tool_dict = tool.model_dump() if hasattr(tool, 'model_dump') else tool

    # 检?tool name ?type
    tool_name = tool_dict.get("name", "")
    tool_type = tool_dict.get("type", "")

    # 支持多种格式
    is_web_search = (
        tool_name == "web_search" or
        tool_type.startswith("web_search") or
        "web_search" in tool_type
    )

    return is_web_search


def extract_search_query(request: AnthropicMessagesRequest) -> Optional[str]:
    """
    从消息中提取搜索查询?

    读取 messages 的第一条消息的第一个内容块?
    并去�?Perform a web search for the query: " 前缀?

    Args:
        request: Anthropic 消息请求

    Returns:
        搜索查询字符串，如果无法提取则返?None
    """
    if not request.messages:
        return None

    first_msg = request.messages[0]
    msg_dict = first_msg.model_dump() if hasattr(first_msg, 'model_dump') else first_msg
    content = msg_dict.get("content", "")

    # 提取文本内容
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        # 获取第一个文本块
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                break
        else:
            return None
    else:
        return None

    # 去除前缀 "Perform a web search for the query: "
    prefix = "Perform a web search for the query: "
    if text.startswith(prefix):
        query = text[len(prefix):]
    else:
        query = text

    return query.strip() if query.strip() else None


def _generate_random_id(length: int, charset: str = None) -> str:
    """生成随机 ID�?""
    if charset is None:
        charset = string.ascii_letters + string.digits
    return ''.join(random.choice(charset) for _ in range(length))


def _generate_random_id_22() -> str:
    """生成 22 位大小写字母和数字的随机字符串�?""
    return _generate_random_id(22)


def _generate_random_id_8() -> str:
    """生成 8 位小写字母和数字的随机字符串�?""
    charset = string.ascii_lowercase + string.digits
    return _generate_random_id(8, charset)


def create_mcp_request(query: str) -> Tuple[str, Dict[str, Any]]:
    """
    创建 MCP 请求?

    ID 格式: web_search_tooluse_{22位随机}_{毫秒时间戳}_{8位随机}

    Args:
        query: 搜索查询

    Returns:
        (tool_use_id, mcp_request_dict)
    """
    random_22 = _generate_random_id_22()
    timestamp = int(time.time() * 1000)
    random_8 = _generate_random_id_8()

    request_id = f"web_search_tooluse_{random_22}_{timestamp}_{random_8}"

    # tool_use_id 使用 srvtoolu_ 前缀 + UUID
    tool_use_id = f"srvtoolu_{uuid.uuid4().hex[:32]}"

    mcp_request = {
        "id": request_id,
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "web_search",
            "arguments": {
                "query": query
            }
        }
    }

    return tool_use_id, mcp_request


def parse_search_results(mcp_response: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    解析 MCP 响应中的搜索结果?

    Args:
        mcp_response: MCP 响应

    Returns:
        搜索结果，如果解析失败则返回 None
    """
    if "error" in mcp_response and mcp_response["error"]:
        return None

    result = mcp_response.get("result")
    if not result:
        return None

    content_list = result.get("content", [])
    if not content_list:
        return None

    first_content = content_list[0]
    if first_content.get("type") != "text":
        return None

    try:
        return json.loads(first_content.get("text", "{}"))
    except json.JSONDecodeError:
        return None


def generate_search_summary(query: str, results: Optional[Dict[str, Any]]) -> str:
    """
    生成搜索结果摘要?

    Args:
        query: 搜索查询
        results: 搜索结果

    Returns:
        摘要文本
    """
    summary = f'Here are the search results for "{query}":\n\n'

    if results and "results" in results:
        for i, result in enumerate(results["results"], 1):
            title = result.get("title", "Untitled")
            url = result.get("url", "")
            snippet = result.get("snippet", "")

            summary += f"{i}. **{title}**\n"
            if snippet:
                # 截断过长的摘?
                if len(snippet) > 200:
                    snippet = snippet[:200] + "..."
                summary += f"   {snippet}\n"
            summary += f"   Source: {url}\n\n"
    else:
        summary += "No results found.\n"

    summary += "\nPlease note that these are web search results and may not be fully accurate or up-to-date."

    return summary


async def generate_websearch_sse_events(
    model: str,
    query: str,
    tool_use_id: str,
    search_results: Optional[Dict[str, Any]],
    input_tokens: int
) -> AsyncGenerator[str, None]:
    """
    生成 WebSearch SSE 响应�?

    Args:
        model: 模型名称
        query: 搜索查询
        tool_use_id: 工具使用 ID
        search_results: 搜索结果
        input_tokens: 输入 token ?

    Yields:
        SSE 事件字符?
    """
    message_id = f"msg_{uuid.uuid4().hex[:24]}"

    # 1. message_start
    yield _format_sse_event("message_start", {
        "type": "message_start",
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0
            }
        }
    })

    # 2. content_block_start (server_tool_use)
    yield _format_sse_event("content_block_start", {
        "type": "content_block_start",
        "index": 0,
        "content_block": {
            "id": tool_use_id,
            "type": "server_tool_use",
            "name": "web_search",
            "input": {}
        }
    })

    # 3. content_block_delta (input_json_delta)
    input_json = json.dumps({"query": query})
    yield _format_sse_event("content_block_delta", {
        "type": "content_block_delta",
        "index": 0,
        "delta": {
            "type": "input_json_delta",
            "partial_json": input_json
        }
    })

    # 4. content_block_stop (server_tool_use)
    yield _format_sse_event("content_block_stop", {
        "type": "content_block_stop",
        "index": 0
    })

    # 5. content_block_start (web_search_tool_result)
    search_content = []
    if search_results and "results" in search_results:
        for r in search_results["results"]:
            search_content.append({
                "type": "web_search_result",
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "encrypted_content": r.get("snippet", ""),
                "page_age": None
            })

    yield _format_sse_event("content_block_start", {
        "type": "content_block_start",
        "index": 1,
        "content_block": {
            "type": "web_search_tool_result",
            "tool_use_id": tool_use_id,
            "content": search_content
        }
    })

    # 6. content_block_stop (web_search_tool_result)
    yield _format_sse_event("content_block_stop", {
        "type": "content_block_stop",
        "index": 1
    })

    # 7. content_block_start (text)
    yield _format_sse_event("content_block_start", {
        "type": "content_block_start",
        "index": 2,
        "content_block": {
            "type": "text",
            "text": ""
        }
    })

    # 8. content_block_delta (text_delta) - 生成搜索结果摘要
    summary = generate_search_summary(query, search_results)

    # 分块发送文?
    chunk_size = 100
    for i in range(0, len(summary), chunk_size):
        chunk = summary[i:i + chunk_size]
        yield _format_sse_event("content_block_delta", {
            "type": "content_block_delta",
            "index": 2,
            "delta": {
                "type": "text_delta",
                "text": chunk
            }
        })

    # 9. content_block_stop (text)
    yield _format_sse_event("content_block_stop", {
        "type": "content_block_stop",
        "index": 2
    })

    # 10. message_delta
    output_tokens = (len(summary) + 3) // 4  # 简单估?
    yield _format_sse_event("message_delta", {
        "type": "message_delta",
        "delta": {
            "stop_reason": "end_turn",
            "stop_sequence": None
        },
        "usage": {
            "output_tokens": output_tokens
        }
    })

    # 11. message_stop
    yield _format_sse_event("message_stop", {
        "type": "message_stop"
    })


def _format_sse_event(event_type: str, data: Dict[str, Any]) -> str:
    """
    格式?SSE 事件?

    Args:
        event_type: 事件类型
        data: 事件数据

    Returns:
        格式化的 SSE 字符?
    """
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def call_mcp_api(
    auth_manager: GeekAuthManager,
    mcp_request: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    调用 Kiro MCP API?

    Args:
        auth_manager: 认证管理?
        mcp_request: MCP 请求

    Returns:
        MCP 响应，如果失败则返回 None
    """
    try:
        token = await auth_manager.get_access_token()
        headers = get_kiro_headers(auth_manager, token)

        # MCP API URL
        mcp_url = f"{auth_manager.q_host}/mcp"

        request_body = json.dumps(mcp_request)
        logger.debug(f"MCP request: {request_body}")

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                mcp_url,
                content=request_body,
                headers=headers
            )

            if response.status_code != 200:
                logger.warning(f"MCP API 调用失败: HTTP {response.status_code} - {response.text}")
                return None

            result = response.json()
            logger.debug(f"MCP response: {result}")

            if result.get("error"):
                error = result["error"]
                logger.warning(
                    f"MCP error: {error.get('code', -1)} - {error.get('message', 'Unknown error')}"
                )
                return None

            return result

    except Exception as e:
        logger.warning(f"MCP API 调用失败: {e}")
        return None


async def handle_websearch_request(
    request: Request,  # noqa: ARG001
    request_data: AnthropicMessagesRequest,
    auth_manager: GeekAuthManager
) -> StreamingResponse:
    """
    处理 WebSearch 请求?

    Args:
        request: FastAPI Request
        request_data: Anthropic 消息请求
        auth_manager: 认证管理?

    Returns:
        StreamingResponse
    """
    # 1. 提取搜索查询
    query = extract_search_query(request_data)
    if not query:
        logger.warning("无法从消息中提取搜索查询")
        # 返回错误事件?
        async def error_stream():
            yield _format_sse_event("error", {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "无法从消息中提取搜索查询"
                }
            })
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    logger.info(f"处理 WebSearch 请求: query={query}")

    # 2. 创建 MCP 请求
    tool_use_id, mcp_request = create_mcp_request(query)

    # 3. 调用 Kiro MCP API
    mcp_response = await call_mcp_api(auth_manager, mcp_request)
    search_results = parse_search_results(mcp_response) if mcp_response else None

    # 4. 估算输入 tokens
    try:
        messages_list = [msg.model_dump() for msg in request_data.messages]
        tools_list = [tool.model_dump() for tool in request_data.tools] if request_data.tools else None
        input_tokens = count_message_tokens(messages_list)
        if tools_list:
            input_tokens += count_tools_tokens(tools_list)
    except Exception:
        input_tokens = 100  # 默认?

    # 5. 生成 SSE 响应
    async def sse_stream():
        async for event in generate_websearch_sse_events(
            request_data.model,
            query,
            tool_use_id,
            search_results,
            input_tokens
        ):
            yield event

    return StreamingResponse(sse_stream(), media_type="text/event-stream")
