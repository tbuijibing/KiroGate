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
Kiro <thinking> 标签增量解析�?

?Kiro API 返回?<thinking>...</thinking> 标签内容解析?
Anthropic 官方 Extended Thinking 格式的事�?

参考实现：
- proxycast (Rust): https://github.com/hank9999/proxycast
- kiro.rs (Rust): https://github.com/hank9999/kiro.rs
"""

from dataclasses import dataclass
from enum import Enum, auto
from typing import List, Optional
from loguru import logger


class SegmentType(Enum):
    """文本片段类型"""
    THINKING = auto()  # thinking 内容
    TEXT = auto()      # 普通文本内?


@dataclass
class TextSegment:
    """文本片段"""
    type: SegmentType
    content: str


class ParseState(Enum):
    """解析状�?""
    INITIAL = auto()           # 初始状态，等待检测是否以 <thinking> 开?
    IN_THINKING = auto()       # ?thinking 块内
    AFTER_THINKING = auto()    # thinking 块结束后，处理普通文?
    PASSTHROUGH = auto()       # 直通模式（响应不以 <thinking> 开头）


class KiroThinkingTagParser:
    """
    Kiro <thinking> 标签增量解析�?

    设计原则?
    1. 只解析第一?<thinking>...</thinking> ?
    2. 仅当响应?<thinking> 开头时才启用解?
    3. 处理?chunk 的标签切?
    4. 跳过被引号包裹的假标?

    使用方式?
        parser = KiroThinkingTagParser()
        for chunk in stream:
            segments = parser.push_and_parse(chunk)
            for segment in segments:
                if segment.type == SegmentType.THINKING:
                    # �?thinking_delta 事件
                elif segment.type == SegmentType.TEXT:
                    # �?text_delta 事件
        # 流结束时刷新缓冲?
        final_segments = parser.flush()
    """

    OPEN_TAG = "<thinking>"
    CLOSE_TAG = "</thinking>"
    # 引号字符，用于检测假标签
    QUOTE_CHARS = ("`", '"', "'", """, """, "'", "'", "�?, "�?, "�?, "�?)

    def __init__(self):
        self.buffer = ""
        self.state = ParseState.INITIAL
        self.thinking_extracted = False  # 是否已提取过 thinking �?

    def push_and_parse(self, incoming: str) -> List[TextSegment]:
        """
        增量解析输入文本?

        Args:
            incoming: 新输入的文本

        Returns:
            解析出的文本片段列表
        """
        if not incoming:
            return []

        self.buffer += incoming
        segments: List[TextSegment] = []

        while True:
            if self.state == ParseState.INITIAL:
                # 初始状态：检测是否以 <thinking> 开?
                result = self._handle_initial_state()
                if result is None:
                    break  # 需要更多数?
                # 状态已更新，继续循?
                continue

            elif self.state == ParseState.IN_THINKING:
                # ?thinking 块内：查?</thinking>
                segment = self._handle_in_thinking_state()
                if segment is None:
                    break  # 需要更多数?
                if segment.content:
                    segments.append(segment)
                # 状态已更新，继续循?
                continue

            elif self.state == ParseState.AFTER_THINKING:
                # thinking 块结束后：输出剩余文?
                if self.buffer:
                    segments.append(TextSegment(SegmentType.TEXT, self.buffer))
                    self.buffer = ""
                break

            elif self.state == ParseState.PASSTHROUGH:
                # 直通模式：直接输出所有内?
                if self.buffer:
                    segments.append(TextSegment(SegmentType.TEXT, self.buffer))
                    self.buffer = ""
                break

        return segments

    def flush(self) -> List[TextSegment]:
        """
        流结束时刷新缓冲�?

        Returns:
            剩余的文本片段列?
        """
        segments: List[TextSegment] = []

        if self.state == ParseState.INITIAL:
            # 从未收到足够数据来判断，当作普通文?
            if self.buffer:
                segments.append(TextSegment(SegmentType.TEXT, self.buffer))
                self.buffer = ""

        elif self.state == ParseState.IN_THINKING:
            # thinking 块未正常关闭，输出剩余内容作?thinking
            if self.buffer:
                logger.warning(f"Thinking block not properly closed, flushing {len(self.buffer)} chars as thinking")
                segments.append(TextSegment(SegmentType.THINKING, self.buffer))
                self.buffer = ""

        elif self.state in (ParseState.AFTER_THINKING, ParseState.PASSTHROUGH):
            # 输出剩余文本
            if self.buffer:
                segments.append(TextSegment(SegmentType.TEXT, self.buffer))
                self.buffer = ""

        return segments

    def _handle_initial_state(self) -> Optional[bool]:
        """
        处理初始状�?

        Returns:
            None 表示需要更多数据，True 表示状态已更新
        """
        # 跳过开头的空白字符
        stripped = self.buffer.lstrip()
        whitespace_len = len(self.buffer) - len(stripped)

        # 检查是否有足够数据来判?
        if len(stripped) < len(self.OPEN_TAG):
            # 检查是否是 <thinking> 的前缀
            if stripped and self.OPEN_TAG.startswith(stripped):
                return None  # 可能?<thinking>，等待更多数?
            elif stripped:
                # 不是 <thinking> 开头，进入直通模?
                self.state = ParseState.PASSTHROUGH
                return True
            else:
                return None  # 只有空白，等待更多数?

        # 检查是否以 <thinking> 开?
        if stripped.startswith(self.OPEN_TAG):
            # 移除开头的空白?<thinking> 标签
            self.buffer = stripped[len(self.OPEN_TAG):]
            self.state = ParseState.IN_THINKING
            logger.debug("Detected <thinking> tag at start, entering thinking mode")
            return True
        else:
            # 不是?<thinking> 开头，进入直通模?
            self.state = ParseState.PASSTHROUGH
            return True

    def _handle_in_thinking_state(self) -> Optional[TextSegment]:
        """
        处理 thinking 块内的状�?

        Returns:
            None 表示需要更多数据，TextSegment 表示解析出的片段
        """
        # 查找真正?</thinking> 标签
        close_pos = self._find_real_close_tag()

        if close_pos is None:
            # 没找到关闭标?
            # 保留可能是标签一部分的尾部数?
            safe_len = len(self.buffer) - len(self.CLOSE_TAG) + 1
            if safe_len > 0:
                thinking_content = self.buffer[:safe_len]
                self.buffer = self.buffer[safe_len:]
                return TextSegment(SegmentType.THINKING, thinking_content)
            return None

        # 找到关闭标签
        thinking_content = self.buffer[:close_pos]
        # 跳过 </thinking> 标签
        after_tag = self.buffer[close_pos + len(self.CLOSE_TAG):]
        # 跳过标签后的换行符（通常?\n\n?
        after_tag = after_tag.lstrip('\n')

        self.buffer = after_tag
        self.state = ParseState.AFTER_THINKING
        self.thinking_extracted = True

        logger.debug(f"Extracted thinking block: {len(thinking_content)} chars")
        return TextSegment(SegmentType.THINKING, thinking_content)

    def _find_real_close_tag(self) -> Optional[int]:
        """
        查找真正?</thinking> 关闭标签?

        跳过被引号包裹的假标�?
        真正的结束标签通常后面跟着换行�?

        Returns:
            关闭标签的位置，?None 如果未找?
        """
        search_start = 0

        while True:
            pos = self.buffer.find(self.CLOSE_TAG, search_start)
            if pos == -1:
                return None

            # 检查是否被引号包裹
            if self._is_quoted_tag(pos):
                search_start = pos + 1
                continue

            # 检查标签后是否有换行符（真正的结束标签特征?
            after_pos = pos + len(self.CLOSE_TAG)
            if after_pos < len(self.buffer):
                # 有后续字符，检查是否是换行
                if self.buffer[after_pos] in '\n\r':
                    return pos
                # 不是换行，可能是假标签，但也可能是流的边?
                # 保守起见，如果后面还有很多内容，认为是假标签
                if len(self.buffer) - after_pos > 10:
                    search_start = pos + 1
                    continue
                return pos
            else:
                # 标签在缓冲区末尾，可能是真正的结?
                return pos

    def _is_quoted_tag(self, tag_pos: int) -> bool:
        """
        检查标签是否被引号包裹?

        Args:
            tag_pos: 标签在缓冲区中的位置

        Returns:
            是否被引号包?
        """
        if tag_pos == 0:
            return False

        # 检查标签前的字?
        prev_char = self.buffer[tag_pos - 1]
        if prev_char in self.QUOTE_CHARS:
            return True

        # 检查是否在代码块内（简单检测）
        # 统计标签前的反引号数?
        before_text = self.buffer[:tag_pos]
        backtick_count = before_text.count('`')
        if backtick_count % 2 == 1:
            # 奇数个反引号，可能在代码块内
            return True

        return False

    @property
    def is_thinking_mode(self) -> bool:
        """是否处于 thinking 模式（响应以 <thinking> 开头）"""
        return self.state in (ParseState.IN_THINKING, ParseState.AFTER_THINKING)

    @property
    def has_extracted_thinking(self) -> bool:
        """是否已提取过 thinking �?""
        return self.thinking_extracted
