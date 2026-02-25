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
�?Token 计数模块?

Использует tiktoken (библиотека OpenAI на Rust) для приблизительного
подсчёта токенов. Кодировка cl100k_base близка к токенизации Claude.

Примечание: Это приблизительный подсчёт, так как точный токенизатор
Claude не является публичным. Anthropic не публикует свой токенизатор,
поэтому используется tiktoken с коэффициентом коррекции.

Коэффициент коррекции CLAUDE_CORRECTION_FACTOR = 1.15 основан на
эмпирических наблюдениях: Claude токенизирует текст примерно на 15%
больше чем GPT-4 (cl100k_base). Это связано с различиями в BPE словарях.
"""

from typing import List, Dict, Any, Optional
from loguru import logger

# Ленивая загрузка tiktoken для ускорения импорта
_encoding = None

# Коэффициент коррекции для Claude моделей
# Claude токенизирует текст примерно на 15% больше чем GPT-4 (cl100k_base)
# Это эмпирическое значение, основанное на сравнении с context_usage от API
CLAUDE_CORRECTION_FACTOR = 1.15


def _get_encoding():
    """
    Ленивая инициализация токенизатора.
    
    Использует cl100k_base - кодировку для GPT-4/ChatGPT,
    которая достаточно близка к токенизации Claude.
    
    Returns:
        tiktoken.Encoding или None если tiktoken недоступен
    """
    global _encoding
    if _encoding is None:
        try:
            import tiktoken
            _encoding = tiktoken.get_encoding("cl100k_base")
            logger.debug("[Tokenizer] Initialized tiktoken with cl100k_base encoding")
        except ImportError:
            logger.warning(
                "[Tokenizer] tiktoken not installed. "
                "Token counting will use fallback estimation. "
                "Install with: pip install tiktoken"
            )
            _encoding = False  # Маркер что импорт не удался
        except Exception as e:
            logger.error(f"[Tokenizer] Failed to initialize tiktoken: {e}")
            _encoding = False
    return _encoding if _encoding else None


def count_tokens(text: str, apply_claude_correction: bool = True) -> int:
    """
    Подсчитывает количество токенов в тексте.
    
    Args:
        text: Текст для подсчёта токенов
        apply_claude_correction: Применять коэффициент коррекции для Claude (по умолчанию True)
    
    Returns:
        Количество токенов (приблизительное, с коррекцией для Claude)
    """
    if not text:
        return 0
    
    encoding = _get_encoding()
    if encoding:
        try:
            base_tokens = len(encoding.encode(text))
            if apply_claude_correction:
                return int(base_tokens * CLAUDE_CORRECTION_FACTOR)
            return base_tokens
        except Exception as e:
            logger.warning(f"[Tokenizer] Error encoding text: {e}")
    
    # Fallback: грубая оценка ~4 символа на токен для английского,
    # ~2-3 символа для других языков (берём среднее ~3.5)
    # Для Claude добавляем коррекцию
    base_estimate = len(text) // 4 + 1
    if apply_claude_correction:
        return int(base_estimate * CLAUDE_CORRECTION_FACTOR)
    return base_estimate


def count_message_tokens(messages: List[Dict[str, Any]], apply_claude_correction: bool = True) -> int:
    """
    Подсчитывает токены в списке сообщений чата.
    
    Учитывает структуру сообщений OpenAI/Claude:
    - role: ~1 токен
    - content: токены текста
    - Служебные токены между сообщениями: ~3-4 токена
    
    Args:
        messages: Список сообщений в формате OpenAI
        apply_claude_correction: Применять коэффициент коррекции для Claude
    
    Returns:
        Приблизительное количество токенов (с коррекцией для Claude)
    """
    if not messages:
        return 0
    
    total_tokens = 0
    
    for message in messages:
        # Базовые токены на сообщение (role, разделители)
        total_tokens += 4  # ~4 токена на служебную информацию
        
        # Токены роли (без коррекции, это короткие строки)
        role = message.get("role", "")
        total_tokens += count_tokens(role, apply_claude_correction=False)
        
        # Токены контента
        content = message.get("content")
        if content:
            if isinstance(content, str):
                total_tokens += count_tokens(content, apply_claude_correction=False)
            elif isinstance(content, list):
                # Мультимодальный контент (текст + изображения)
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            total_tokens += count_tokens(item.get("text", ""), apply_claude_correction=False)
                        elif item.get("type") == "image_url":
                            # Изображения занимают ~85-170 токенов в зависимости от размера
                            total_tokens += 100  # Средняя оценка
        
        # Токены tool_calls (если есть)
        tool_calls = message.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                total_tokens += 4  # Служебные токены
                func = tc.get("function", {})
                total_tokens += count_tokens(func.get("name", ""), apply_claude_correction=False)
                total_tokens += count_tokens(func.get("arguments", ""), apply_claude_correction=False)
        
        # Токены tool_call_id (для ответов от инструментов)
        if message.get("tool_call_id"):
            total_tokens += count_tokens(message["tool_call_id"], apply_claude_correction=False)
    
    # Финальные служебные токены
    total_tokens += 3
    
    # Применяем коррекцию к общему количеству
    if apply_claude_correction:
        return int(total_tokens * CLAUDE_CORRECTION_FACTOR)
    return total_tokens


def count_tools_tokens(tools: Optional[List[Dict[str, Any]]], apply_claude_correction: bool = True) -> int:
    """
    Подсчитывает токены в определениях инструментов.
    
    Args:
        tools: Список инструментов в формате OpenAI
        apply_claude_correction: Применять коэффициент коррекции для Claude
    
    Returns:
        Приблизительное количество токенов (с коррекцией для Claude)
    """
    if not tools:
        return 0
    
    total_tokens = 0
    
    for tool in tools:
        total_tokens += 4  # Служебные токены
        
        if tool.get("type") == "function":
            func = tool.get("function", {})
            
            # Имя функции
            total_tokens += count_tokens(func.get("name", ""), apply_claude_correction=False)
            
            # Описание функции
            total_tokens += count_tokens(func.get("description", ""), apply_claude_correction=False)
            
            # Параметры (JSON schema)
            params = func.get("parameters")
            if params:
                import json
                params_str = json.dumps(params, ensure_ascii=False)
                total_tokens += count_tokens(params_str, apply_claude_correction=False)
    
    # Применяем коррекцию к общему количеству
    if apply_claude_correction:
        return int(total_tokens * CLAUDE_CORRECTION_FACTOR)
    return total_tokens


def estimate_request_tokens(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    system_prompt: Optional[str] = None
) -> Dict[str, int]:
    """
    Оценивает общее количество токенов в запросе.
    
    Args:
        messages: Список сообщений
        tools: Список инструментов (опционально)
        system_prompt: Системный промпт (опционально, если не в messages)
    
    Returns:
        Словарь с детализацией токенов:
        - messages_tokens: токены сообщений
        - tools_tokens: токены инструментов
        - system_tokens: токены системного промпта
        - total_tokens: общее количество
    """
    messages_tokens = count_message_tokens(messages)
    tools_tokens = count_tools_tokens(tools)
    system_tokens = count_tokens(system_prompt) if system_prompt else 0
    
    return {
        "messages_tokens": messages_tokens,
        "tools_tokens": tools_tokens,
        "system_tokens": system_tokens,
        "total_tokens": messages_tokens + tools_tokens + system_tokens
    }