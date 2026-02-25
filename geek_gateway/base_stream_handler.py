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
流式响应基础处理�?

提取 OpenAI ?Anthropic 流式处理的公共逻辑?
减少代码重复，提高可维护�?
"""

import asyncio
import json
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, AsyncGenerator, Dict, List, Optional, Any

import httpx
from loguru import logger

from geek_gateway.parsers import AwsEventStreamParser, parse_bracket_tool_calls, deduplicate_tool_calls
from geek_gateway.config import settings
from geek_gateway.tokenizer import count_tokens, count_message_tokens, count_tools_tokens

if TYPE_CHECKING:
    from geek_gateway.auth import GeekAuthManager
    from geek_gateway.cache import ModelInfoCache

# 导入 debug_logger
try:
    from geek_gateway.debug_logger import debug_logger
except ImportError:
    debug_logger = None


class FirstTokenTimeoutError(Exception):
    """首个 token 超时异常�?""
    pass


class BaseStreamHandler(ABC):
    """
    流式响应基础处理�?

    封装流式处理的公共逻辑?
    - 首个 token 超时处理
    - AWS Event Stream 解析
    - Token 计数
    - Tool calls 处理
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        response: httpx.Response,
        model: str,
        model_cache: "ModelInfoCache",
        auth_manager: "GeekAuthManager",
        first_token_timeout: float = settings.first_token_timeout,
        request_messages: Optional[List] = None,
        request_tools: Optional[List] = None
    ):
        """
        初始化基础流处理器?

        Args:
            client: HTTP 客户?
            response: HTTP 响应
            model: 模型名称
            model_cache: 模型缓存
            auth_manager: 认证管理?
            first_token_timeout: 首个 token 超时时间（秒?
            request_messages: 请求消息（用?token 计数?
            request_tools: 请求工具（用?token 计数?
        """
        self.client = client
        self.response = response
        self.model = model
        self.model_cache = model_cache
        self.auth_manager = auth_manager
        self.first_token_timeout = first_token_timeout
        self.request_messages = request_messages
        self.request_tools = request_tools

        # 初始化解析器和状?
        self.parser = AwsEventStreamParser()
        self.completion_id = self._generate_completion_id()
        self.created_time = int(time.time())
        self.full_content = ""
        self.metering_data = None
        self.context_usage_percentage = None

    @abstractmethod
    def _generate_completion_id(self) -> str:
        """生成完成 ID�?""
        pass

    @abstractmethod
    def _format_content_chunk(self, content: str, first_chunk: bool) -> Dict[str, Any]:
        """
        格式化内容块?

        Args:
            content: 内容文本
            first_chunk: 是否为首个块

        Returns:
            格式化的块数?
        """
        pass

    @abstractmethod
    def _format_tool_calls_chunk(self, tool_calls: List[Dict], index: int) -> Dict[str, Any]:
        """
        格式?tool calls �?

        Args:
            tool_calls: tool calls 列表
            index: 块索?

        Returns:
            格式化的块数?
        """
        pass

    @abstractmethod
    def _format_final_chunk(
        self,
        finish_reason: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int
    ) -> Dict[str, Any]:
        """
        格式化最终块?

        Args:
            finish_reason: 完成原因
            prompt_tokens: 输入 token ?
            completion_tokens: 输出 token ?
            total_tokens: ?token ?

        Returns:
            格式化的最终块数据
        """
        pass

    @abstractmethod
    def _serialize_chunk(self, chunk: Dict[str, Any]) -> str:
        """
        序列化块为字符串?

        Args:
            chunk: 块数?

        Returns:
            序列化后的字符串
        """
        pass

    async def _read_first_chunk_with_timeout(self) -> Optional[bytes]:
        """
        读取首个字节块，带超�?

        Returns:
            首个字节块，如果为空响应则返?None

        Raises:
            FirstTokenTimeoutError: 超时异常
        """
        byte_iterator = self.response.aiter_bytes()

        try:
            first_byte_chunk = await asyncio.wait_for(
                byte_iterator.__anext__(),
                timeout=self.first_token_timeout
            )
            return first_byte_chunk
        except asyncio.TimeoutError:
            logger.warning(f"First token timeout after {self.first_token_timeout}s")
            raise FirstTokenTimeoutError(f"No response within {self.first_token_timeout} seconds")
        except StopAsyncIteration:
            # 空响?- 这是正常?
            logger.debug("Empty response from Kiro API")
            return None

    def _process_events(self, events: List[Dict], first_chunk: bool) -> Optional[str]:
        """
        处理解析的事�?

        Args:
            events: 事件列表
            first_chunk: 是否为首个块

        Returns:
            内容文本（如果有?
        """
        content = None

        for event in events:
            if event["type"] == "content":
                content = event["data"]
                self.full_content += content
            elif event["type"] == "usage":
                self.metering_data = event["data"]
            elif event["type"] == "context_usage":
                self.context_usage_percentage = event["data"]

        return content

    def _calculate_tokens(self) -> tuple[int, int, int]:
        """
        计算 token 数量?

        Returns:
            (prompt_tokens, completion_tokens, total_tokens)
        """
        # 计算 completion_tokens（输出）
        completion_tokens = count_tokens(self.full_content)

        # 根据上下文使用百分比计算?token ?
        total_tokens_from_api = 0
        if self.context_usage_percentage is not None and self.context_usage_percentage > 0:
            max_input_tokens = self.model_cache.get_max_input_tokens(self.model)
            total_tokens_from_api = int((self.context_usage_percentage / 100) * max_input_tokens)

        if total_tokens_from_api > 0:
            # 使用 API 数据
            prompt_tokens = max(0, total_tokens_from_api - completion_tokens)
            total_tokens = total_tokens_from_api
            logger.debug(
                f"[Usage] {self.model}: "
                f"prompt_tokens={prompt_tokens} (subtraction), "
                f"completion_tokens={completion_tokens} (tiktoken), "
                f"total_tokens={total_tokens} (API Kiro)"
            )
        else:
            # 使用 tiktoken 计算
            prompt_tokens = 0
            if self.request_messages:
                prompt_tokens += count_message_tokens(self.request_messages, apply_claude_correction=False)
            if self.request_tools:
                prompt_tokens += count_tools_tokens(self.request_tools, apply_claude_correction=False)
            total_tokens = prompt_tokens + completion_tokens
            logger.debug(
                f"[Usage] {self.model}: "
                f"prompt_tokens={prompt_tokens} (tiktoken), "
                f"completion_tokens={completion_tokens} (tiktoken), "
                f"total_tokens={total_tokens} (tiktoken)"
            )

        return prompt_tokens, completion_tokens, total_tokens

    async def stream(self) -> AsyncGenerator[str, None]:
        """
        执行流式处理?

        Yields:
            序列化的块字符串
        """
        try:
            # 读取首个?
            first_byte_chunk = await self._read_first_chunk_with_timeout()
            if first_byte_chunk is None:
                # 空响?
                yield self._serialize_chunk({"type": "done"})
                return

            # 处理首个?
            if debug_logger:
                debug_logger.log_raw_chunk(first_byte_chunk)

            events = self.parser.feed(first_byte_chunk)
            first_content = self._process_events(events, first_chunk=True)

            first_chunk_sent = False
            if first_content:
                chunk = self._format_content_chunk(first_content, first_chunk=True)
                yield self._serialize_chunk(chunk)
                first_chunk_sent = True

            # 继续读取剩余?
            async for chunk in self.response.aiter_bytes():
                if debug_logger:
                    debug_logger.log_raw_chunk(chunk)

                events = self.parser.feed(chunk)
                content = self._process_events(events, first_chunk=False)

                if content:
                    chunk = self._format_content_chunk(content, first_chunk=not first_chunk_sent)
                    yield self._serialize_chunk(chunk)
                    first_chunk_sent = True

            # 处理 tool calls
            bracket_tool_calls = parse_bracket_tool_calls(self.full_content)
            all_tool_calls = self.parser.get_tool_calls() + bracket_tool_calls
            all_tool_calls = deduplicate_tool_calls(all_tool_calls)

            if all_tool_calls:
                logger.debug(f"Processing {len(all_tool_calls)} tool calls for streaming response")
                for idx, tc in enumerate(all_tool_calls):
                    chunk = self._format_tool_calls_chunk([tc], idx)
                    yield self._serialize_chunk(chunk)

            # 发送最终块
            finish_reason = "tool_calls" if all_tool_calls else "stop"
            prompt_tokens, completion_tokens, total_tokens = self._calculate_tokens()

            final_chunk = self._format_final_chunk(
                finish_reason,
                prompt_tokens,
                completion_tokens,
                total_tokens
            )

            if self.metering_data:
                final_chunk["usage"]["credits_used"] = self.metering_data

            yield self._serialize_chunk(final_chunk)
            yield self._serialize_chunk({"type": "done"})

        except FirstTokenTimeoutError:
            # 向上传递超时异常以进行重试
            raise
        except Exception as e:
            logger.error(f"Error during streaming: {e}", exc_info=True)
        finally:
            await self.response.aclose()
            logger.debug("Streaming completed")