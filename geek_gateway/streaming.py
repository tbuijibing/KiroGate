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
Streaming response processing logic, converts Kiro stream to OpenAI/Anthropic format.

Contains generators for:
- Converting AWS SSE to OpenAI SSE
- Forming streaming chunks
- Processing tool calls in stream
- Adaptive timeout handling for slow models
"""

import asyncio
import json
import time
from typing import TYPE_CHECKING, AsyncGenerator, Callable, Awaitable, Optional, Dict, Any, List

import httpx
from fastapi import HTTPException
from loguru import logger

from geek_gateway.parsers import AwsEventStreamParser, parse_bracket_tool_calls, deduplicate_tool_calls
from geek_gateway.utils import generate_completion_id
from geek_gateway.config import settings, get_adaptive_timeout
from geek_gateway.tokenizer import count_tokens, count_message_tokens, count_tools_tokens
from geek_gateway.thinking_parser import KiroThinkingTagParser, SegmentType, TextSegment

if TYPE_CHECKING:
    from geek_gateway.auth import GeekAuthManager
    from geek_gateway.cache import ModelInfoCache

try:
    from geek_gateway.debug_logger import debug_logger
except ImportError:
    debug_logger = None


class FirstTokenTimeoutError(Exception):
    """Exception raised when first token timeout occurs."""
    pass


class StreamReadTimeoutError(Exception):
    """Exception raised when stream read timeout occurs."""
    pass


async def _read_chunk_with_timeout(
    byte_iterator,
    timeout: float
) -> bytes:
    """
    Read a chunk from byte iterator with timeout.

    Args:
        byte_iterator: Async byte iterator
        timeout: Timeout in seconds

    Returns:
        Bytes chunk

    Raises:
        StreamReadTimeoutError: If timeout occurs
        StopAsyncIteration: If iterator is exhausted
    """
    try:
        return await asyncio.wait_for(
            byte_iterator.__anext__(),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        raise StreamReadTimeoutError(f"流式读取�?{timeout}s 后超�?)


def _calculate_usage_tokens(
    full_content: str,
    context_usage_percentage: Optional[float],
    model_cache: "ModelInfoCache",
    model: str,
    request_messages: Optional[list],
    request_tools: Optional[list]
) -> Dict[str, Any]:
    """
    Calculate token usage from response.

    Args:
        full_content: Full response content
        context_usage_percentage: Context usage percentage from API
        model_cache: Model cache for token limits
        model: Model name
        request_messages: Request messages for fallback counting
        request_tools: Request tools for fallback counting

    Returns:
        Dict with prompt_tokens, completion_tokens, total_tokens and source info
    """
    completion_tokens = count_tokens(full_content)

    total_tokens_from_api = 0
    if context_usage_percentage is not None and context_usage_percentage > 0:
        max_input_tokens = model_cache.get_max_input_tokens(model)
        total_tokens_from_api = int((context_usage_percentage / 100) * max_input_tokens)

    if total_tokens_from_api > 0:
        prompt_tokens = max(0, total_tokens_from_api - completion_tokens)
        total_tokens = total_tokens_from_api
        prompt_source = "subtraction"
        total_source = "API Kiro"
    else:
        prompt_tokens = 0
        if request_messages:
            prompt_tokens += count_message_tokens(request_messages, apply_claude_correction=False)
        if request_tools:
            prompt_tokens += count_tools_tokens(request_tools, apply_claude_correction=False)
        total_tokens = prompt_tokens + completion_tokens
        prompt_source = "tiktoken"
        total_source = "tiktoken"

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "prompt_source": prompt_source,
        "total_source": total_source
    }


def _format_tool_calls_for_streaming(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Format tool calls for streaming response with required index field.

    Args:
        tool_calls: List of tool calls

    Returns:
        List of indexed tool calls for streaming
    """
    indexed_tool_calls = []
    for idx, tc in enumerate(tool_calls):
        func = tc.get("function") or {}
        tool_name = func.get("name") or ""
        tool_args = func.get("arguments") or "{}"

        logger.debug(f"Tool call [{idx}] '{tool_name}': id={tc.get('id')}, args_length={len(tool_args)}")

        indexed_tc = {
            "index": idx,
            "id": tc.get("id"),
            "type": tc.get("type", "function"),
            "function": {
                "name": tool_name,
                "arguments": tool_args
            }
        }
        indexed_tool_calls.append(indexed_tc)

    return indexed_tool_calls


def _format_tool_calls_for_non_streaming(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Format tool calls for non-streaming response (without index field).

    Args:
        tool_calls: List of tool calls

    Returns:
        List of cleaned tool calls for non-streaming
    """
    cleaned_tool_calls = []
    for tc in tool_calls:
        func = tc.get("function") or {}
        cleaned_tc = {
            "id": tc.get("id"),
            "type": tc.get("type", "function"),
            "function": {
                "name": func.get("name", ""),
                "arguments": func.get("arguments", "{}")
            }
        }
        cleaned_tool_calls.append(cleaned_tc)

    return cleaned_tool_calls


async def stream_kiro_to_openai_internal(
    client: httpx.AsyncClient,
    response: httpx.Response,
    model: str,
    model_cache: "ModelInfoCache",
    auth_manager: "GeekAuthManager",
    first_token_timeout: float = settings.first_token_timeout,
    stream_read_timeout: float = settings.stream_read_timeout,
    request_messages: Optional[list] = None,
    request_tools: Optional[list] = None
) -> AsyncGenerator[str, None]:
    """
    Internal generator for converting Kiro stream to OpenAI format.

    Parses AWS SSE stream and converts events to OpenAI chat.completion.chunk.
    Supports tool calls and usage calculation.

    IMPORTANT: This function raises FirstTokenTimeoutError if first token
    is not received within first_token_timeout seconds.

    Args:
        client: HTTP client (for connection management)
        response: HTTP response with data stream
        model: Model name to include in response
        model_cache: Model cache for token limits
        auth_manager: Authentication manager
        first_token_timeout: First token timeout (seconds)
        stream_read_timeout: Stream read timeout for subsequent chunks (seconds)
        request_messages: Original request messages (for fallback token counting)
        request_tools: Original request tools (for fallback token counting)

    Yields:
        Strings in SSE format: "data: {...}\\n\\n" or "data: [DONE]\\n\\n"

    Raises:
        FirstTokenTimeoutError: If first token not received within timeout
        StreamReadTimeoutError: If stream read times out
    """
    completion_id = generate_completion_id()
    created_time = int(time.time())
    first_chunk = True

    parser = AwsEventStreamParser()
    metering_data = None
    context_usage_percentage = None
    content_parts: list[str] = []  # 使用 list 替代字符串拼接，提升性能

    # 根据模型自适应调整超时时间
    adaptive_first_token_timeout = get_adaptive_timeout(model, first_token_timeout)
    adaptive_stream_read_timeout = get_adaptive_timeout(model, stream_read_timeout)

    try:
        byte_iterator = response.aiter_bytes()

        # Wait for first chunk with adaptive timeout
        try:
            first_byte_chunk = await asyncio.wait_for(
                byte_iterator.__anext__(),
                timeout=adaptive_first_token_timeout
            )
        except asyncio.TimeoutError:
            logger.warning(f"First token timeout after {adaptive_first_token_timeout}s (model: {model})")
            raise FirstTokenTimeoutError(f"。{adaptive_first_token_timeout}s 内未收到响应")
        except StopAsyncIteration:
            logger.debug("Empty response from Kiro API")
            yield "data: [DONE]\n\n"
            return

        # Process first chunk
        if debug_logger:
            debug_logger.log_raw_chunk(first_byte_chunk)

        events = parser.feed(first_byte_chunk)
        for event in events:
            if event["type"] == "content":
                content = event["data"]
                content_parts.append(content)

                delta = {"content": content}
                if first_chunk:
                    delta["role"] = "assistant"
                    first_chunk = False

                openai_chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}]
                }

                chunk_text = f"data: {json.dumps(openai_chunk, ensure_ascii=False)}\n\n"

                if debug_logger:
                    debug_logger.log_modified_chunk(chunk_text.encode('utf-8'))

                yield chunk_text

            elif event["type"] == "usage":
                metering_data = event["data"]

            elif event["type"] == "context_usage":
                context_usage_percentage = event["data"]

        # Continue reading remaining chunks with adaptive timeout
        # 对于慢模型和大文档，可能需要更长时间等待每?chunk
        consecutive_timeouts = 0
        max_consecutive_timeouts = 3  # 允许连续超时次数
        while True:
            try:
                chunk = await _read_chunk_with_timeout(byte_iterator, adaptive_stream_read_timeout)
                consecutive_timeouts = 0  # 重置超时计数?
            except StopAsyncIteration:
                break
            except StreamReadTimeoutError as e:
                consecutive_timeouts += 1
                if consecutive_timeouts <= max_consecutive_timeouts:
                    logger.warning(
                        f"Stream read timeout {consecutive_timeouts}/{max_consecutive_timeouts} "
                        f"after {adaptive_stream_read_timeout}s (model: {model}). "
                        f"Model may be processing large content - continuing to wait..."
                    )
                    # 继续等待下一?chunk
                    continue
                else:
                    logger.error(f"Stream read timeout after {max_consecutive_timeouts} consecutive timeouts (model: {model}): {e}")
                    raise

            if debug_logger:
                debug_logger.log_raw_chunk(chunk)

            events = parser.feed(chunk)

            for event in events:
                if event["type"] == "content":
                    content = event["data"]
                    content_parts.append(content)

                    delta = {"content": content}
                    if first_chunk:
                        delta["role"] = "assistant"
                        first_chunk = False

                    openai_chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created_time,
                        "model": model,
                        "choices": [{"index": 0, "delta": delta, "finish_reason": None}]
                    }

                    chunk_text = f"data: {json.dumps(openai_chunk, ensure_ascii=False)}\n\n"

                    if debug_logger:
                        debug_logger.log_modified_chunk(chunk_text.encode('utf-8'))

                    yield chunk_text

                elif event["type"] == "usage":
                    metering_data = event["data"]

                elif event["type"] == "context_usage":
                    context_usage_percentage = event["data"]

        # 合并 content 部分（比字符串拼接更高效?
        full_content = ''.join(content_parts)

        # Check bracket-style tool calls in full content
        bracket_tool_calls = parse_bracket_tool_calls(full_content)
        all_tool_calls = parser.get_tool_calls() + bracket_tool_calls
        all_tool_calls = deduplicate_tool_calls(all_tool_calls)

        finish_reason = "tool_calls" if all_tool_calls else "stop"

        # Calculate usage tokens using helper function
        usage_info = _calculate_usage_tokens(
            full_content, context_usage_percentage, model_cache, model,
            request_messages, request_tools
        )

        # Send tool calls if any
        if all_tool_calls:
            logger.debug(f"Processing {len(all_tool_calls)} tool calls for streaming response")
            indexed_tool_calls = _format_tool_calls_for_streaming(all_tool_calls)

            tool_calls_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_time,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"tool_calls": indexed_tool_calls},
                    "finish_reason": None
                }]
            }
            yield f"data: {json.dumps(tool_calls_chunk, ensure_ascii=False)}\n\n"

        # Final chunk with usage
        final_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created_time,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
            "usage": {
                "prompt_tokens": usage_info["prompt_tokens"],
                "completion_tokens": usage_info["completion_tokens"],
                "total_tokens": usage_info["total_tokens"],
            }
        }

        if metering_data:
            final_chunk["usage"]["credits_used"] = metering_data

        logger.debug(
            f"[Usage] {model}: "
            f"prompt_tokens={usage_info['prompt_tokens']} ({usage_info['prompt_source']}), "
            f"completion_tokens={usage_info['completion_tokens']} (tiktoken), "
            f"total_tokens={usage_info['total_tokens']} ({usage_info['total_source']})"
        )

        yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    except FirstTokenTimeoutError:
        raise
    except StreamReadTimeoutError:
        raise
    except Exception as e:
        logger.error(f"Error during streaming: {e}", exc_info=True)
    finally:
        await response.aclose()
        logger.debug("Streaming completed")


async def stream_kiro_to_openai(
    client: httpx.AsyncClient,
    response: httpx.Response,
    model: str,
    model_cache: "ModelInfoCache",
    auth_manager: "GeekAuthManager",
    request_messages: Optional[list] = None,
    request_tools: Optional[list] = None
) -> AsyncGenerator[str, None]:
    """
    Генератор для преобразования потока Kiro в OpenAI формат.
    
    Это wrapper над stream_kiro_to_openai_internal, который НЕ делает retry.
    Retry логика реализована в stream_with_first_token_retry.
    
    Args:
        client: HTTP клиент (для управления соединением)
        response: HTTP ответ с потоком данных
        model: Имя модели для включения в ответ
        model_cache: Кэш моделей для получения лимитов токенов
        auth_manager: Менеджер аутентификации
        request_messages: Исходные сообщения запроса (для fallback подсчёта токенов)
        request_tools: Исходные инструменты запроса (для fallback подсчёта токенов)
    
    Yields:
        Строки в формате SSE: "data: {...}\\n\\n" или "data: [DONE]\\n\\n"
    """
    async for chunk in stream_kiro_to_openai_internal(
        client, response, model, model_cache, auth_manager,
        request_messages=request_messages,
        request_tools=request_tools
    ):
        yield chunk


async def stream_with_first_token_retry(
    make_request: Callable[[], Awaitable[httpx.Response]],
    client: httpx.AsyncClient,
    model: str,
    model_cache: "ModelInfoCache",
    auth_manager: "GeekAuthManager",
    max_retries: int = settings.first_token_max_retries,
    first_token_timeout: float = settings.first_token_timeout,
    request_messages: Optional[list] = None,
    request_tools: Optional[list] = None
) -> AsyncGenerator[str, None]:
    """
    Streaming с автоматическим retry при таймауте первого токена.
    
    Если модель не отвечает в течение first_token_timeout секунд,
    запрос отменяется и делается новый. Максимум max_retries попыток.
    
    Это seamless для пользователя - он просто видит задержку,
    но в итоге получает ответ (или ошибку после всех попыток).
    
    Args:
        make_request: Функция для создания нового HTTP запроса
        client: HTTP клиент
        model: Имя модели
        model_cache: Кэш моделей
        auth_manager: Менеджер аутентификации
        max_retries: Максимальное количество попыток
        first_token_timeout: Таймаут ожидания первого токена (секунды)
        request_messages: Исходные сообщения запроса (для fallback подсчёта токенов)
        request_tools: Исходные инструменты запроса (для fallback подсчёта токенов)
    
    Yields:
        Строки в формате SSE
    
    Raises:
        HTTPException: После исчерпания всех попыток
    
    Example:
        >>> async def make_req():
        ...     return await http_client.request_with_retry("POST", url, payload, stream=True)
        >>> async for chunk in stream_with_first_token_retry(make_req, client, model, cache, auth):
        ...     print(chunk)
    """
    last_error: Optional[Exception] = None
    
    for attempt in range(max_retries):
        response: Optional[httpx.Response] = None
        try:
            # Делаем запрос
            if attempt > 0:
                logger.warning(f"Retry attempt {attempt + 1}/{max_retries} after first token timeout")
            
            response = await make_request()
            
            if response.status_code != 200:
                # Ошибка от API - закрываем response и выбрасываем исключение
                try:
                    error_content = await response.aread()
                    error_text = error_content.decode('utf-8', errors='replace')
                except Exception:
                    error_text = "未知错误"
                
                try:
                    await response.aclose()
                except Exception:
                    pass
                
                logger.error(f"Error from Kiro API: {response.status_code} - {error_text}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"上游 API 错误: {error_text}"
                )
            
            # Пытаемся стримить с таймаутом на первый токен
            async for chunk in stream_kiro_to_openai_internal(
                client,
                response,
                model,
                model_cache,
                auth_manager,
                first_token_timeout=first_token_timeout,
                request_messages=request_messages,
                request_tools=request_tools
            ):
                yield chunk
            
            # Успешно завершили - выходим
            return
            
        except FirstTokenTimeoutError as e:
            last_error = e
            logger.warning(f"First token timeout on attempt {attempt + 1}/{max_retries}")
            
            # Закрываем текущий response если он открыт
            if response:
                try:
                    await response.aclose()
                except Exception:
                    pass
            
            # Продолжаем к следующей попытке
            continue
            
        except Exception as e:
            # Другие ошибки - не retry, пробрасываем
            logger.error(f"Unexpected error during streaming: {e}", exc_info=True)
            if response:
                try:
                    await response.aclose()
                except Exception:
                    pass
            raise
    
    # Все попытки исчерпаны - выбрасываем HTTP ошибку
    logger.error(f"All {max_retries} attempts failed due to first token timeout")
    raise HTTPException(
        status_code=504,
        detail=f"模型�?{max_retries} 次尝试后仍未�?{first_token_timeout}s 内响应，请稍后再�?
    )


async def collect_stream_response(
    client: httpx.AsyncClient,
    response: httpx.Response,
    model: str,
    model_cache: "ModelInfoCache",
    auth_manager: "GeekAuthManager",
    request_messages: Optional[list] = None,
    request_tools: Optional[list] = None
) -> dict:
    """
    Собирает полный ответ из streaming потока.
    
    Используется для non-streaming режима - собирает все chunks
    и формирует единый ответ.
    
    Args:
        client: HTTP клиент
        response: HTTP ответ с потоком
        model: Имя модели
        model_cache: Кэш моделей
        auth_manager: Менеджер аутентификации
        request_messages: Исходные сообщения запроса (для fallback подсчёта токенов)
        request_tools: Исходные инструменты запроса (для fallback подсчёта токенов)
    
    Returns:
        Словарь с полным ответом в формате OpenAI chat.completion
    """
    content_parts: list[str] = []  # 使用 list 替代字符串拼接，提升性能
    final_usage = None
    tool_calls = []
    completion_id = generate_completion_id()

    async for chunk_str in stream_kiro_to_openai(
        client,
        response,
        model,
        model_cache,
        auth_manager,
        request_messages=request_messages,
        request_tools=request_tools
    ):
        if not chunk_str.startswith("data:"):
            continue
        
        data_str = chunk_str[len("data:"):].strip()
        if not data_str or data_str == "[DONE]":
            continue
        
        try:
            chunk_data = json.loads(data_str)
            
            # Извлекаем данные из chunk
            delta = chunk_data.get("choices", [{}])[0].get("delta", {})
            if "content" in delta:
                content_parts.append(delta["content"])
            if "tool_calls" in delta:
                tool_calls.extend(delta["tool_calls"])
            
            # Сохраняем usage из последнего chunk
            if "usage" in chunk_data:
                final_usage = chunk_data["usage"]
                
        except (json.JSONDecodeError, IndexError):
            continue

    # 合并 content 部分（比字符串拼接更高效?
    full_content = ''.join(content_parts)

    # Формируем финальный ответ
    message = {"role": "assistant", "content": full_content}
    if tool_calls:
        # Для non-streaming ответа удаляем поле index из tool_calls,
        # так как оно требуется только для streaming chunks
        cleaned_tool_calls = []
        for tc in tool_calls:
            # Извлекаем function с защитой от None
            func = tc.get("function") or {}
            cleaned_tc = {
                "id": tc.get("id"),
                "type": tc.get("type", "function"),
                "function": {
                    "name": func.get("name", ""),
                    "arguments": func.get("arguments", "{}")
                }
            }
            cleaned_tool_calls.append(cleaned_tc)
        message["tool_calls"] = cleaned_tool_calls
    
    finish_reason = "tool_calls" if tool_calls else "stop"
    
    # Формируем usage для ответа
    usage = final_usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    
    # Логируем информацию о токенах для отладки (non-streaming использует те же логи из streaming)
    
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason
        }],
        "usage": usage
    }


# ==================================================================================================
# Anthropic Streaming Functions
# ==================================================================================================

def generate_anthropic_message_id() -> str:
    """Генерирует ID сообщения в формате Anthropic."""
    import uuid
    return f"msg_{uuid.uuid4().hex[:24]}"


async def stream_kiro_to_anthropic(
    client: httpx.AsyncClient,
    response: httpx.Response,
    model: str,
    model_cache: "ModelInfoCache",
    auth_manager: "GeekAuthManager",
    request_messages: Optional[list] = None,
    request_tools: Optional[list] = None,
    thinking_enabled: bool = False,
    stream_read_timeout: float = settings.stream_read_timeout
) -> AsyncGenerator[str, None]:
    """
    Преобразует поток Kiro в формат Anthropic SSE.

    Anthropic использует другой формат событий:
    - message_start: начало сообщения
    - content_block_start: начало блока контента
    - content_block_delta: дельта контента (text_delta, thinking_delta или input_json_delta)
    - content_block_stop: конец блока контента
    - message_delta: финальная информация (stop_reason, usage)
    - message_stop: конец сообщения

    ?thinking_enabled=True 时，会解?Kiro 返回?<thinking>...</thinking> 标签?
    并转换为 Anthropic 官方?thinking_delta 事件格式?

    Args:
        client: HTTP клиент
        response: HTTP ответ с потоком
        model: Имя модели
        model_cache: Кэш моделей
        auth_manager: Менеджер аутентификации
        request_messages: Сообщения запроса (для подсчёта токенов)
        request_tools: Инструменты запроса (для подсчёта токенов)
        thinking_enabled: Включен ли режим thinking
        stream_read_timeout: Stream read timeout for each chunk (seconds)

    Yields:
        Строки в формате Anthropic SSE
    """
    message_id = generate_anthropic_message_id()
    parser = AwsEventStreamParser()
    metering_data = None
    context_usage_percentage = None
    content_parts: list[str] = []  # 用于 token 计算的完整内?
    thinking_parts: list[str] = []  # thinking 内容（用?token 计算?
    text_parts: list[str] = []  # 普通文本内容（用于 token 计算?
    content_block_index = 0
    thinking_block_started = False
    text_block_started = False

    # Thinking 解析器（仅在 thinking_enabled 时使用）
    thinking_parser = KiroThinkingTagParser() if thinking_enabled else None

    # 根据模型自适应调整超时时间
    adaptive_stream_read_timeout = get_adaptive_timeout(model, stream_read_timeout)

    # Pre-calculate input_tokens (can be determined before stream starts)
    # This ensures message_start event contains real input_tokens value
    pre_calculated_input_tokens = 0
    if request_messages:
        pre_calculated_input_tokens += count_message_tokens(request_messages, apply_claude_correction=False)
    if request_tools:
        pre_calculated_input_tokens += count_tools_tokens(request_tools, apply_claude_correction=False)

    async def emit_thinking_segment(content: str) -> AsyncGenerator[str, None]:
        """�?thinking 内容的事�?""
        nonlocal content_block_index, thinking_block_started, thinking_parts

        if not content:
            return

        thinking_parts.append(content)

        # 如果 thinking block 还没开始，先发?content_block_start
        if not thinking_block_started:
            block_start = {
                "type": "content_block_start",
                "index": content_block_index,
                "content_block": {"type": "thinking", "thinking": ""}
            }
            yield f"event: content_block_start\ndata: {json.dumps(block_start, ensure_ascii=False)}\n\n"
            thinking_block_started = True

        # �?thinking_delta
        delta = {
            "type": "content_block_delta",
            "index": content_block_index,
            "delta": {"type": "thinking_delta", "thinking": content}
        }
        yield f"event: content_block_delta\ndata: {json.dumps(delta, ensure_ascii=False)}\n\n"

        if debug_logger:
            debug_logger.log_modified_chunk(f"event: content_block_delta\ndata: {json.dumps(delta)}\n\n".encode('utf-8'))

    async def close_thinking_block() -> AsyncGenerator[str, None]:
        """关闭 thinking block"""
        nonlocal content_block_index, thinking_block_started

        if thinking_block_started:
            block_stop = {
                "type": "content_block_stop",
                "index": content_block_index
            }
            yield f"event: content_block_stop\ndata: {json.dumps(block_stop, ensure_ascii=False)}\n\n"
            content_block_index += 1
            thinking_block_started = False

    async def emit_text_segment(content: str) -> AsyncGenerator[str, None]:
        """发送普通文本内容的事件"""
        nonlocal content_block_index, text_block_started, text_parts

        if not content:
            return

        text_parts.append(content)

        # 如果 text block 还没开始，先发?content_block_start
        if not text_block_started:
            block_start = {
                "type": "content_block_start",
                "index": content_block_index,
                "content_block": {"type": "text", "text": ""}
            }
            yield f"event: content_block_start\ndata: {json.dumps(block_start, ensure_ascii=False)}\n\n"
            text_block_started = True

        # �?text_delta
        delta = {
            "type": "content_block_delta",
            "index": content_block_index,
            "delta": {"type": "text_delta", "text": content}
        }
        yield f"event: content_block_delta\ndata: {json.dumps(delta, ensure_ascii=False)}\n\n"

        if debug_logger:
            debug_logger.log_modified_chunk(f"event: content_block_delta\ndata: {json.dumps(delta)}\n\n".encode('utf-8'))

    async def close_text_block() -> AsyncGenerator[str, None]:
        """关闭 text block"""
        nonlocal content_block_index, text_block_started

        if text_block_started:
            block_stop = {
                "type": "content_block_stop",
                "index": content_block_index
            }
            yield f"event: content_block_stop\ndata: {json.dumps(block_stop, ensure_ascii=False)}\n\n"
            content_block_index += 1
            text_block_started = False

    try:
        # message_start
        message_start = {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": model,
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": pre_calculated_input_tokens,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0
                }
            }
        }

        yield f"event: message_start\ndata: {json.dumps(message_start, ensure_ascii=False)}\n\n"

        # Read chunks with adaptive timeout
        byte_iterator = response.aiter_bytes()
        consecutive_timeouts = 0
        max_consecutive_timeouts = 3

        while True:
            try:
                chunk = await _read_chunk_with_timeout(byte_iterator, adaptive_stream_read_timeout)
                consecutive_timeouts = 0
            except StopAsyncIteration:
                break
            except StreamReadTimeoutError as e:
                consecutive_timeouts += 1
                if consecutive_timeouts <= max_consecutive_timeouts:
                    logger.warning(
                        f"Anthropic stream timeout {consecutive_timeouts}/{max_consecutive_timeouts} "
                        f"after {adaptive_stream_read_timeout}s (model: {model}). "
                        f"Model may be processing large content - continuing to wait..."
                    )
                    continue
                else:
                    logger.error(f"Anthropic stream read timeout after {max_consecutive_timeouts} consecutive timeouts (model: {model}): {e}")
                    raise

            if debug_logger:
                debug_logger.log_raw_chunk(chunk)

            events = parser.feed(chunk)

            for event in events:
                if event["type"] == "content":
                    content = event["data"]
                    content_parts.append(content)

                    if thinking_enabled and thinking_parser:
                        # 使用 thinking 解析器处理内?
                        segments = thinking_parser.push_and_parse(content)

                        for segment in segments:
                            if segment.type == SegmentType.THINKING:
                                # 如果之前?text block 打开，先关闭?
                                async for event_str in close_text_block():
                                    yield event_str
                                # �?thinking 内容
                                async for event_str in emit_thinking_segment(segment.content):
                                    yield event_str
                            elif segment.type == SegmentType.TEXT:
                                # 如果之前?thinking block 打开，先关闭?
                                async for event_str in close_thinking_block():
                                    yield event_str
                                # 发送普通文?
                                async for event_str in emit_text_segment(segment.content):
                                    yield event_str
                    else:
                        # 不启?thinking 解析，直接作为文本处?
                        async for event_str in emit_text_segment(content):
                            yield event_str

                elif event["type"] == "usage":
                    metering_data = event["data"]

                elif event["type"] == "context_usage":
                    context_usage_percentage = event["data"]

        # 流结束，刷新 thinking 解析器缓冲区
        if thinking_enabled and thinking_parser:
            final_segments = thinking_parser.flush()
            for segment in final_segments:
                if segment.type == SegmentType.THINKING:
                    async for event_str in close_text_block():
                        yield event_str
                    async for event_str in emit_thinking_segment(segment.content):
                        yield event_str
                elif segment.type == SegmentType.TEXT:
                    async for event_str in close_thinking_block():
                        yield event_str
                    async for event_str in emit_text_segment(segment.content):
                        yield event_str

        # 关闭所有打开?blocks
        async for event_str in close_thinking_block():
            yield event_str
        async for event_str in close_text_block():
            yield event_str

        # 合并 content 部分（用?token 计算?
        full_content = ''.join(content_parts)

        # 处理 tool calls
        bracket_tool_calls = parse_bracket_tool_calls(full_content)
        all_tool_calls = parser.get_tool_calls() + bracket_tool_calls
        all_tool_calls = deduplicate_tool_calls(all_tool_calls)

        # �?tool_use blocks
        for tc in all_tool_calls:
            func = tc.get("function") or {}
            tool_name = func.get("name") or ""
            tool_args_str = func.get("arguments") or "{}"
            tool_id = tc.get("id") or f"toolu_{generate_completion_id()[8:]}"

            try:
                tool_input = json.loads(tool_args_str)
            except json.JSONDecodeError:
                tool_input = {}

            # content_block_start for tool_use
            tool_block_start = {
                "type": "content_block_start",
                "index": content_block_index,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": tool_name,
                    "input": {}
                }
            }
            yield f"event: content_block_start\ndata: {json.dumps(tool_block_start, ensure_ascii=False)}\n\n"

            # input_json_delta
            if tool_input:
                input_delta = {
                    "type": "content_block_delta",
                    "index": content_block_index,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(tool_input, ensure_ascii=False)
                    }
                }
                yield f"event: content_block_delta\ndata: {json.dumps(input_delta, ensure_ascii=False)}\n\n"

            # content_block_stop
            tool_block_stop = {
                "type": "content_block_stop",
                "index": content_block_index
            }
            yield f"event: content_block_stop\ndata: {json.dumps(tool_block_stop, ensure_ascii=False)}\n\n"

            content_block_index += 1

        # 确定 stop_reason
        stop_reason = "tool_use" if all_tool_calls else "end_turn"

        # 计算 token 使用?
        usage_info = _calculate_usage_tokens(
            full_content, context_usage_percentage, model_cache, model,
            request_messages, request_tools
        )
        input_tokens = usage_info["prompt_tokens"]
        completion_tokens = usage_info["completion_tokens"]

        # �?message_delta
        message_delta = {
            "type": "message_delta",
            "delta": {
                "stop_reason": stop_reason,
                "stop_sequence": None
            },
            "usage": {
                "output_tokens": completion_tokens
            }
        }
        yield f"event: message_delta\ndata: {json.dumps(message_delta, ensure_ascii=False)}\n\n"

        # �?message_stop
        yield f"event: message_stop\ndata: {{\"type\": \"message_stop\"}}\n\n"

        if thinking_enabled and thinking_parser and thinking_parser.has_extracted_thinking:
            logger.debug(
                f"[Anthropic Usage with Thinking] {model}: input_tokens={input_tokens}, "
                f"output_tokens={completion_tokens}, thinking_chars={len(''.join(thinking_parts))}"
            )
        else:
            logger.debug(
                f"[Anthropic Usage] {model}: input_tokens={input_tokens}, output_tokens={completion_tokens}"
            )

    except Exception as e:
        # 确保错误信息不为?
        error_msg = str(e) if str(e) else f"{type(e).__name__}: {repr(e)}"
        logger.error(f"Error during Anthropic streaming: {error_msg}", exc_info=True)
        # �?error event
        error_event = {
            "type": "error",
            "error": {
                "type": "api_error",
                "message": error_msg
            }
        }
        yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"
    finally:
        await response.aclose()
        logger.debug("Anthropic streaming completed")


async def collect_anthropic_response(
    client: httpx.AsyncClient,
    response: httpx.Response,
    model: str,
    model_cache: "ModelInfoCache",
    auth_manager: "GeekAuthManager",
    request_messages: Optional[list] = None,
    request_tools: Optional[list] = None,
    thinking_enabled: bool = False,
    stream_read_timeout: float = settings.stream_read_timeout
) -> dict:
    """
    Собирает полный ответ из streaming потока и преобразует в формат Anthropic.

    ?thinking_enabled=True 时，会解?Kiro 返回?<thinking>...</thinking> 标签?
    并在响应中添?thinking content block?

    Args:
        client: HTTP клиент
        response: HTTP ответ с потоком
        model: Имя модели
        model_cache: Кэш моделей
        auth_manager: Менеджер аутентификации
        request_messages: Сообщения запроса
        request_tools: Инструменты запроса
        thinking_enabled: Включен ли режим thinking
        stream_read_timeout: Stream read timeout for each chunk (seconds)

    Returns:
        Словарь с ответом в формате Anthropic Messages API
    """
    message_id = generate_anthropic_message_id()
    parser = AwsEventStreamParser()
    metering_data = None
    context_usage_percentage = None
    content_parts: list[str] = []

    # Thinking 解析器（仅在 thinking_enabled 时使用）
    thinking_parser = KiroThinkingTagParser() if thinking_enabled else None

    # 根据模型自适应调整超时时间
    adaptive_stream_read_timeout = get_adaptive_timeout(model, stream_read_timeout)

    try:
        # Read chunks with adaptive timeout
        byte_iterator = response.aiter_bytes()
        consecutive_timeouts = 0
        max_consecutive_timeouts = 3
        while True:
            try:
                chunk = await _read_chunk_with_timeout(byte_iterator, adaptive_stream_read_timeout)
                consecutive_timeouts = 0
            except StopAsyncIteration:
                break
            except StreamReadTimeoutError as e:
                consecutive_timeouts += 1
                if consecutive_timeouts <= max_consecutive_timeouts:
                    logger.warning(
                        f"Anthropic collect timeout {consecutive_timeouts}/{max_consecutive_timeouts} "
                        f"after {adaptive_stream_read_timeout}s (model: {model}). "
                        f"Model may be processing large content - continuing to wait..."
                    )
                    continue
                else:
                    logger.error(f"Anthropic collect stream read timeout after {max_consecutive_timeouts} consecutive timeouts (model: {model}): {e}")
                    raise

            if debug_logger:
                debug_logger.log_raw_chunk(chunk)

            events = parser.feed(chunk)

            for event in events:
                if event["type"] == "content":
                    content_parts.append(event["data"])
                elif event["type"] == "usage":
                    metering_data = event["data"]
                elif event["type"] == "context_usage":
                    context_usage_percentage = event["data"]

    finally:
        await response.aclose()

    # 合并 content 部分
    full_content = ''.join(content_parts)

    # 处理 thinking 内容
    thinking_content = ""
    text_content = full_content

    if thinking_enabled and thinking_parser:
        # 使用解析器处理完整内?
        segments = thinking_parser.push_and_parse(full_content)
        final_segments = thinking_parser.flush()
        all_segments = segments + final_segments

        thinking_parts = []
        text_parts = []

        for segment in all_segments:
            if segment.type == SegmentType.THINKING:
                thinking_parts.append(segment.content)
            elif segment.type == SegmentType.TEXT:
                text_parts.append(segment.content)

        thinking_content = ''.join(thinking_parts)
        text_content = ''.join(text_parts)

    # 处理 tool calls
    bracket_tool_calls = parse_bracket_tool_calls(full_content)
    all_tool_calls = parser.get_tool_calls() + bracket_tool_calls
    all_tool_calls = deduplicate_tool_calls(all_tool_calls)

    # 构建 content blocks
    content_blocks = []

    # 添加 thinking block（如果有?
    if thinking_content:
        content_blocks.append({
            "type": "thinking",
            "thinking": thinking_content
        })

    # 添加 text block（如果有?
    if text_content:
        content_blocks.append({
            "type": "text",
            "text": text_content
        })

    # 添加 tool_use blocks
    for tc in all_tool_calls:
        func = tc.get("function") or {}
        tool_name = func.get("name") or ""
        tool_args_str = func.get("arguments") or "{}"
        tool_id = tc.get("id") or f"toolu_{generate_completion_id()[8:]}"

        try:
            tool_input = json.loads(tool_args_str)
        except json.JSONDecodeError:
            tool_input = {}

        content_blocks.append({
            "type": "tool_use",
            "id": tool_id,
            "name": tool_name,
            "input": tool_input
        })

    # 确定 stop_reason
    stop_reason = "tool_use" if all_tool_calls else "end_turn"

    # 计算 token 使用?
    usage_info = _calculate_usage_tokens(
        full_content, context_usage_percentage, model_cache, model,
        request_messages, request_tools
    )
    input_tokens = usage_info["prompt_tokens"]
    completion_tokens = usage_info["completion_tokens"]

    if thinking_content:
        logger.debug(
            f"[Anthropic Usage with Thinking] {model}: input_tokens={input_tokens}, "
            f"output_tokens={completion_tokens}, thinking_chars={len(thinking_content)}"
        )
    else:
        logger.debug(
            f"[Anthropic Usage] {model}: input_tokens={input_tokens}, output_tokens={completion_tokens}"
        )

    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": completion_tokens
        }
    }
