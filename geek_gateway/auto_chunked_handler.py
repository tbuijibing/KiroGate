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
自动分片处理�?

当检测到长文档时，自动将其分片处理，流式返回结果?
对客户端完全透明，无需客户端做任何修改?
"""

import asyncio
import json
import time
import copy
from typing import AsyncGenerator, List, Optional, Union, Any

from loguru import logger

from geek_gateway.chunked_processor import ChunkedDocumentProcessor, CHARS_PER_TOKEN_ESTIMATE
from geek_gateway.config import settings, AUTO_CHUNK_THRESHOLD, CHUNK_MAX_CHARS, CHUNK_OVERLAP_CHARS


class AutoChunkedProcessor:
    """
    自动分片处理�?

    检测长文档并自动分片处理，对客户端透明?
    """

    def __init__(
        self,
        threshold: int = None,
        max_chars: int = None,
        overlap_chars: int = None
    ):
        """
        初始化自动分片处理器?

        Args:
            threshold: 触发自动分片的阈值（字符数），默认使用配�?
            max_chars: 每个分片的最大字符数，默认使用配�?
            overlap_chars: 分片之间的重叠字符数，默认使用配�?
        """
        self.threshold = threshold if threshold is not None else AUTO_CHUNK_THRESHOLD
        self.max_chars = max_chars if max_chars is not None else CHUNK_MAX_CHARS
        self.overlap_chars = overlap_chars if overlap_chars is not None else CHUNK_OVERLAP_CHARS
        self.processor = ChunkedDocumentProcessor(
            max_tokens_per_chunk=self.max_chars // CHARS_PER_TOKEN_ESTIMATE,
            overlap_tokens=self.overlap_chars // CHARS_PER_TOKEN_ESTIMATE
        )

    def extract_long_content(self, messages: List[Any]) -> tuple[Optional[str], int, str]:
        """
        从消息列表中提取长文档内�?

        Args:
            messages: 消息列表

        Returns:
            (长文档内? 消息索引, 内容类型) ?(None, -1, "")
            内容类型: "string" �?list"
        """
        for i, msg in enumerate(messages):
            # 获取 content
            if hasattr(msg, 'content'):
                content = msg.content
            elif isinstance(msg, dict):
                content = msg.get("content", "")
            else:
                continue

            # 检查字符串类型
            if isinstance(content, str) and len(content) > self.threshold:
                return content, i, "string"

            # 检查列表类型（多模态内容）
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if len(text) > self.threshold:
                            return text, i, "list"

        return None, -1, ""

    def needs_chunking(self, messages: List[Any]) -> bool:
        """
        检查消息是否需要分片处�?

        Args:
            messages: 消息列表

        Returns:
            是否需要分?
        """
        content, _, _ = self.extract_long_content(messages)
        return content is not None

    def create_chunked_messages(
        self,
        messages: List[Any],
        long_content: str,
        msg_index: int,
        content_type: str,
        chunk: str,
        chunk_index: int,
        total_chunks: int
    ) -> List[Any]:
        """
        创建包含分片内容的消息列�?

        Args:
            messages: 原始消息列表
            long_content: 原始长文档内?
            msg_index: 长文档所在的消息索引
            content_type: 内容类型 ("string" �?list")
            chunk: 当前分片内容
            chunk_index: 当前分片索引
            total_chunks: 总分片数

        Returns:
            修改后的消息列表
        """
        # 深拷贝消息列?
        new_messages = copy.deepcopy(messages)

        # 添加分片上下文信?
        if total_chunks > 1:
            chunk_info = f"\n\n[这是长文档的。{chunk_index + 1}/{total_chunks} 部分]"
            if chunk_index == 0:
                chunk_info += "\n[请处理这部分内容，后续会继续提供剩余部分]"
            elif chunk_index == total_chunks - 1:
                chunk_info += "\n[这是最后一部分，请总结完成处理]"
            else:
                chunk_info += "\n[请继续处理这部分内容]"

            chunk_with_info = chunk + chunk_info
        else:
            chunk_with_info = chunk

        # 替换消息中的长文档内?
        target_msg = new_messages[msg_index]

        if isinstance(target_msg, dict):
            if content_type == "string":
                target_msg["content"] = chunk_with_info
            else:  # list
                for block in target_msg.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        if block.get("text") == long_content:
                            block["text"] = chunk_with_info
                            break
        else:
            # Pydantic 模型
            if content_type == "string":
                target_msg.content = chunk_with_info
            else:  # list
                for block in target_msg.content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        if block.get("text") == long_content:
                            block["text"] = chunk_with_info
                            break

        return new_messages

    def split_for_processing(self, long_content: str) -> List[str]:
        """
        将长文档分割成多个分�?

        Args:
            long_content: 长文档内?

        Returns:
            分片列表
        """
        return self.processor.split_text(long_content)


# 全局实例
auto_chunked_processor = AutoChunkedProcessor()


async def process_with_auto_chunking(
    messages: List[Any],
    process_func,
    stream: bool = True,
    **kwargs
) -> AsyncGenerator[str, None]:
    """
    自动分片处理长文�?

    如果检测到长文档，自动分片处理并流式返回结�?
    对客户端完全透明?

    Args:
        messages: 消息列表
        process_func: 处理单个请求的异步函?
        stream: 是否流式返回
        **kwargs: 传递给 process_func 的其他参?

    Yields:
        SSE 格式的响应数?
    """
    processor = auto_chunked_processor

    # 检查是否需要分?
    long_content, msg_index, content_type = processor.extract_long_content(messages)

    if long_content is None:
        # 不需要分片，直接处理
        async for chunk in process_func(messages=messages, stream=stream, **kwargs):
            yield chunk
        return

    # 需要分片处?
    chunks = processor.split_for_processing(long_content)
    total_chunks = len(chunks)

    logger.info(f"Auto-chunking enabled: splitting into {total_chunks} chunks")

    # 用于收集所有分片的响应（非流式模式?
    all_responses = []

    for i, chunk in enumerate(chunks):
        logger.info(f"Processing chunk {i + 1}/{total_chunks} ({len(chunk)} chars)")

        # 创建包含当前分片的消?
        chunked_messages = processor.create_chunked_messages(
            messages=messages,
            long_content=long_content,
            msg_index=msg_index,
            content_type=content_type,
            chunk=chunk,
            chunk_index=i,
            total_chunks=total_chunks
        )

        if stream:
            # 流式模式：直接转发每个分片的响应
            if i > 0:
                # 在分片之间添加分隔符
                separator = f"\n\n--- [继续处理。{i + 1}/{total_chunks} 部分] ---\n\n"
                yield f"data: {json.dumps({'choices': [{'delta': {'content': separator}}]})}\n\n"

            async for response_chunk in process_func(messages=chunked_messages, stream=True, **kwargs):
                yield response_chunk
        else:
            # 非流式模式：收集响应后合?
            response_content = ""
            async for response_chunk in process_func(messages=chunked_messages, stream=True, **kwargs):
                # 解析响应提取内容
                if response_chunk.startswith("data: "):
                    data_str = response_chunk[6:].strip()
                    if data_str and data_str != "[DONE]":
                        try:
                            data = json.loads(data_str)
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            if "content" in delta:
                                response_content += delta["content"]
                        except json.JSONDecodeError:
                            pass

            all_responses.append(response_content)

    if not stream and all_responses:
        # 非流式模式：合并所有响应并返回
        merged_content = "\n\n".join(all_responses)
        final_response = {
            "id": f"chatcmpl-chunked-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": kwargs.get("model", "unknown"),
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": merged_content
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }
        }
        yield json.dumps(final_response)

    logger.info(f"Auto-chunking completed: processed {total_chunks} chunks")
