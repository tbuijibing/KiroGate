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
AWS Event Stream 格式解析�?

Содержит классы и функции для:
- Парсинга бинарного AWS SSE потока
- Извлечения JSON событий
- Обработки tool calls
- Дедупликации контента
"""

import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from geek_gateway.utils import generate_tool_call_id


def find_matching_brace(text: str, start_pos: int) -> int:
    """
    Находит позицию закрывающей скобки с учётом вложенности и строк.
    
    Использует bracket counting для корректного парсинга вложенных JSON.
    Учитывает строки в кавычках и escape-последовательности.
    
    Args:
        text: Текст для поиска
        start_pos: Позиция открывающей скобки '{'
    
    Returns:
        Позиция закрывающей скобки или -1 если не найдена
    
    Example:
        >>> find_matching_brace('{"a": {"b": 1}}', 0)
        14
        >>> find_matching_brace('{"a": "{}"}', 0)
        10
    """
    if start_pos >= len(text) or text[start_pos] != '{':
        return -1
    
    brace_count = 0
    in_string = False
    escape_next = False
    
    for i in range(start_pos, len(text)):
        char = text[i]
        
        if escape_next:
            escape_next = False
            continue
        
        if char == '\\' and in_string:
            escape_next = True
            continue
        
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        
        if not in_string:
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    return i
    
    return -1


def parse_bracket_tool_calls(response_text: str) -> List[Dict[str, Any]]:
    """
    Парсит tool calls в формате [Called func_name with args: {...}].
    
    Некоторые модели возвращают tool calls в текстовом формате вместо
    структурированного JSON. Эта функция извлекает их.
    
    Args:
        response_text: Текст ответа модели
    
    Returns:
        Список tool calls в формате OpenAI
    
    Example:
        >>> text = "[Called get_weather with args: {\"city\": \"London\"}]"
        >>> calls = parse_bracket_tool_calls(text)
        >>> calls[0]["function"]["name"]
        'get_weather'
    """
    if not response_text or "[Called" not in response_text:
        return []
    
    tool_calls = []
    pattern = r'\[Called\s+(\w+)\s+with\s+args:\s*'
    
    for match in re.finditer(pattern, response_text, re.IGNORECASE):
        func_name = match.group(1)
        args_start = match.end()
        
        # Ищем начало JSON
        json_start = response_text.find('{', args_start)
        if json_start == -1:
            continue
        
        # Ищем конец JSON с учётом вложенности
        json_end = find_matching_brace(response_text, json_start)
        if json_end == -1:
            continue
        
        json_str = response_text[json_start:json_end + 1]
        
        try:
            args = json.loads(json_str)
            tool_call_id = generate_tool_call_id()
            # index будет добавлен позже при формировании финального ответа
            tool_calls.append({
                "id": tool_call_id,
                "type": "function",
                "function": {
                    "name": func_name,
                    "arguments": json.dumps(args)
                }
            })
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse tool call arguments: {json_str[:100]}")
    
    return tool_calls


def deduplicate_tool_calls(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Удаляет дубликаты tool calls.
    
    Дедупликация происходит по двум критериям:
    1. По id - если есть несколько tool calls с одинаковым id, оставляем тот у которого
       больше аргументов (не пустой "{}")
    2. По name+arguments - удаляем полные дубликаты
    
    Args:
        tool_calls: Список tool calls
    
    Returns:
        Список уникальных tool calls
    """
    # Сначала дедупликация по id - оставляем tool call с непустыми аргументами
    by_id: Dict[str, Dict[str, Any]] = {}
    for tc in tool_calls:
        tc_id = tc.get("id", "")
        if not tc_id:
            # Без id - добавляем как есть (будет дедуплицировано по name+args)
            continue
        
        existing = by_id.get(tc_id)
        if existing is None:
            by_id[tc_id] = tc
        else:
            # Есть дубликат по id - оставляем тот у которого больше аргументов
            existing_args = existing.get("function", {}).get("arguments", "{}")
            current_args = tc.get("function", {}).get("arguments", "{}")
            
            # Предпочитаем непустые аргументы
            if current_args != "{}" and (existing_args == "{}" or len(current_args) > len(existing_args)):
                logger.debug(f"Replacing tool call {tc_id} with better arguments: {len(existing_args)} -> {len(current_args)}")
                by_id[tc_id] = tc
    
    # Собираем tool calls: сначала те что с id, потом без id
    result_with_id = list(by_id.values())
    result_without_id = [tc for tc in tool_calls if not tc.get("id")]
    
    # Теперь дедупликация по name+arguments для всех
    seen = set()
    unique = []
    
    for tc in result_with_id + result_without_id:
        # Защита от None в function
        func = tc.get("function") or {}
        func_name = func.get("name") or ""
        func_args = func.get("arguments") or "{}"
        key = f"{func_name}-{func_args}"
        if key not in seen:
            seen.add(key)
            unique.append(tc)
    
    if len(tool_calls) != len(unique):
        logger.debug(f"Deduplicated tool calls: {len(tool_calls)} -> {len(unique)}")
    
    return unique


class AwsEventStreamParser:
    """
    Парсер для AWS Event Stream формата.
    
    AWS возвращает события в бинарном формате с разделителями :message-type...event.
    Этот класс извлекает JSON события из потока и преобразует их в удобный формат.
    
    Поддерживаемые типы событий:
    - content: Текстовый контент ответа
    - tool_start: Начало tool call (name, toolUseId)
    - tool_input: Продолжение input для tool call
    - tool_stop: Завершение tool call
    - usage: Информация о потреблении кредитов
    - context_usage: Процент использования контекста
    
    Attributes:
        buffer: Буфер для накопления данных
        last_content: Последний обработанный контент (для дедупликации)
        current_tool_call: Текущий незавершённый tool call
        tool_calls: Список завершённых tool calls
    
    Example:
        >>> parser = AwsEventStreamParser()
        >>> events = parser.feed(chunk)
        >>> for event in events:
        ...     if event["type"] == "content":
        ...         print(event["data"])
    """

    # 事件类型映射（pattern -> event_type?
    _PATTERN_TYPE_MAP = {
        '{"content":': 'content',
        '{"name":': 'tool_start',
        '{"input":': 'tool_input',
        '{"stop":': 'tool_stop',
        '{"followupPrompt":': 'followup',
        '{"usage":': 'usage',
        '{"contextUsagePercentage":': 'context_usage',
    }

    # 预编译的正则表达式（性能优化：单次匹配所有模式）
    _PATTERN_REGEX = re.compile(
        r'\{"(?:content|name|input|stop|followupPrompt|usage|contextUsagePercentage)":'
    )

    def __init__(self):
        """Инициализирует парсер."""
        self.buffer = ""
        self.last_content: Optional[str] = None  # Для дедупликации повторяющегося контента
        self.current_tool_call: Optional[Dict[str, Any]] = None
        self.tool_calls: List[Dict[str, Any]] = []
    
    def feed(self, chunk: bytes) -> List[Dict[str, Any]]:
        """
        Добавляет chunk в буфер и возвращает распарсенные события.
        
        Args:
            chunk: Байты данных из потока
        
        Returns:
            Список событий в формате {"type": str, "data": Any}
        """
        try:
            self.buffer += chunk.decode('utf-8', errors='ignore')
        except Exception:
            return []
        
        events = []

        while True:
            # 使用预编译正则快速定位下一个事件（性能优化?
            match = self._PATTERN_REGEX.search(self.buffer)
            if not match:
                break

            earliest_pos = match.start()
            # 从匹配位置提取完整的 pattern 前缀来确定事件类?
            # 找到 ":" 后的位置来截取完整的 pattern
            colon_pos = self.buffer.find(':', earliest_pos)
            if colon_pos == -1:
                break
            pattern_prefix = self.buffer[earliest_pos:colon_pos + 1]
            earliest_type = self._PATTERN_TYPE_MAP.get(pattern_prefix)

            if earliest_type is None:
                # 未知模式，跳过这个位?
                self.buffer = self.buffer[earliest_pos + 1:]
                continue

            # Ищем конец JSON
            json_end = find_matching_brace(self.buffer, earliest_pos)
            if json_end == -1:
                # JSON не полный, ждём больше данных
                break
            
            json_str = self.buffer[earliest_pos:json_end + 1]
            self.buffer = self.buffer[json_end + 1:]
            
            try:
                data = json.loads(json_str)
                event = self._process_event(data, earliest_type)
                if event:
                    events.append(event)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse JSON: {json_str[:100]}")
        
        return events
    
    def _process_event(self, data: dict, event_type: str) -> Optional[Dict[str, Any]]:
        """
        Обрабатывает распарсенное событие.
        
        Args:
            data: Распарсенный JSON
            event_type: Тип события
        
        Returns:
            Обработанное событие или None
        """
        if event_type == 'content':
            return self._process_content_event(data)
        elif event_type == 'tool_start':
            return self._process_tool_start_event(data)
        elif event_type == 'tool_input':
            return self._process_tool_input_event(data)
        elif event_type == 'tool_stop':
            return self._process_tool_stop_event(data)
        elif event_type == 'usage':
            return {"type": "usage", "data": data.get('usage', 0)}
        elif event_type == 'context_usage':
            return {"type": "context_usage", "data": data.get('contextUsagePercentage', 0)}
        
        return None
    
    def _process_content_event(self, data: dict) -> Optional[Dict[str, Any]]:
        """Обрабатывает событие с контентом."""
        content = data.get('content', '')
        
        # Пропускаем followupPrompt
        if data.get('followupPrompt'):
            return None
        
        # Дедупликация повторяющегося контента
        if content == self.last_content:
            return None
        
        self.last_content = content
        
        return {"type": "content", "data": content}
    
    def _process_tool_start_event(self, data: dict) -> Optional[Dict[str, Any]]:
        """Обрабатывает начало tool call."""
        # Завершаем предыдущий tool call если есть
        if self.current_tool_call:
            self._finalize_tool_call()
        
        # input может быть строкой или объектом
        input_data = data.get('input', '')
        if isinstance(input_data, dict):
            input_str = json.dumps(input_data)
        else:
            input_str = str(input_data) if input_data else ''
        
        self.current_tool_call = {
            "id": data.get('toolUseId', generate_tool_call_id()),
            "type": "function",
            "function": {
                "name": data.get('name', ''),
                "arguments": input_str
            }
        }
        
        if data.get('stop'):
            self._finalize_tool_call()
        
        return None
    
    def _process_tool_input_event(self, data: dict) -> Optional[Dict[str, Any]]:
        """Обрабатывает продолжение input для tool call."""
        if self.current_tool_call:
            # input может быть строкой или объектом
            input_data = data.get('input', '')
            if isinstance(input_data, dict):
                input_str = json.dumps(input_data)
            else:
                input_str = str(input_data) if input_data else ''
            self.current_tool_call['function']['arguments'] += input_str
        return None
    
    def _process_tool_stop_event(self, data: dict) -> Optional[Dict[str, Any]]:
        """Обрабатывает завершение tool call."""
        if self.current_tool_call and data.get('stop'):
            self._finalize_tool_call()
        return None
    
    def _finalize_tool_call(self) -> None:
        """Завершает текущий tool call и добавляет в список."""
        if not self.current_tool_call:
            return
        
        # Пытаемся распарсить и нормализовать arguments как JSON
        args = self.current_tool_call['function']['arguments']
        tool_name = self.current_tool_call['function'].get('name', 'unknown')
        
        logger.debug(f"Finalizing tool call '{tool_name}' with raw arguments: {repr(args)[:200]}")
        
        if isinstance(args, str):
            if args.strip():
                try:
                    parsed = json.loads(args)
                    # Убеждаемся что результат - строка JSON
                    self.current_tool_call['function']['arguments'] = json.dumps(parsed)
                    logger.debug(f"Tool '{tool_name}' arguments parsed successfully: {list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}")
                except json.JSONDecodeError as e:
                    # Если не удалось распарсить, оставляем пустой объект
                    logger.warning(f"Failed to parse tool '{tool_name}' arguments: {e}. Raw: {args[:200]}")
                    self.current_tool_call['function']['arguments'] = "{}"
            else:
                # Пустая строка - используем пустой объект
                # Это нормальное поведение для дубликатов tool calls от Kiro
                logger.debug(f"Tool '{tool_name}' has empty arguments string (will be deduplicated)")
                self.current_tool_call['function']['arguments'] = "{}"
        elif isinstance(args, dict):
            # Если уже объект - сериализуем в строку
            self.current_tool_call['function']['arguments'] = json.dumps(args)
            logger.debug(f"Tool '{tool_name}' arguments already dict with keys: {list(args.keys())}")
        else:
            # Неизвестный тип - пустой объект
            logger.warning(f"Tool '{tool_name}' has unexpected arguments type: {type(args)}")
            self.current_tool_call['function']['arguments'] = "{}"
        
        self.tool_calls.append(self.current_tool_call)
        self.current_tool_call = None
    
    def get_tool_calls(self) -> List[Dict[str, Any]]:
        """
        Возвращает все собранные tool calls.
        
        Завершает текущий tool call если он не завершён.
        Удаляет дубликаты.
        
        Returns:
            Список уникальных tool calls
        """
        if self.current_tool_call:
            self._finalize_tool_call()
        return deduplicate_tool_calls(self.tool_calls)
    
    def reset(self) -> None:
        """Сбрасывает состояние парсера."""
        self.buffer = ""
        self.last_content = None
        self.current_tool_call = None
        self.tool_calls = []