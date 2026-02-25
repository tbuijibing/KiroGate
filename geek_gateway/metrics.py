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
Prometheus metrics module.

Provides structured application metrics collection and export.
Supports dual-mode operation:
- Single-node: in-memory dicts + SQLite persistence (original behavior)
- Distributed: Redis atomic counters, hashes, sorted sets, lists, sets, strings
"""

import asyncio
import json
import os
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple

from loguru import logger

from geek_gateway.config import APP_VERSION, settings

METRICS_DB_FILE = os.getenv("METRICS_DB_FILE", "data/metrics.db")

# Redis key prefix
_PREFIX = "GeekGate:metrics"


@dataclass
class MetricsBucket:
    """Metrics bucket for histogram data."""
    le: float  # Upper bound
    count: int = 0


class PrometheusMetrics:
    """
    Prometheus-style metrics collector.

    Collects the following metrics:
    - Total requests (by endpoint, status code, model)
    - Request latency histogram
    - Token usage (input/output)
    - Retry count
    - Active connections
    - Error count

    In distributed mode, uses Redis for cross-node metric aggregation.
    In single-node mode, uses in-memory dicts + SQLite persistence.
    """

    # Latency histogram bucket boundaries (seconds)
    LATENCY_BUCKETS = [0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, float('inf')]
    MAX_RECENT_REQUESTS = 50
    MAX_RESPONSE_TIMES = 100

    def __init__(self):
        """Initialize metrics collector."""
        self._lock = asyncio.Lock()
        self._db_path = METRICS_DB_FILE

        # Counters (used in single-node mode, and as local pending in distributed mode)
        self._request_total: Dict[str, int] = defaultdict(int)
        self._error_total: Dict[str, int] = defaultdict(int)
        self._retry_total: Dict[str, int] = defaultdict(int)

        # Token counters
        self._input_tokens_total: Dict[str, int] = defaultdict(int)
        self._output_tokens_total: Dict[str, int] = defaultdict(int)

        # Histograms
        self._latency_histogram: Dict[str, List[int]] = defaultdict(
            lambda: [0] * len(self.LATENCY_BUCKETS)
        )
        self._latency_sum: Dict[str, float] = defaultdict(float)
        self._latency_count: Dict[str, int] = defaultdict(int)

        # Gauges
        self._active_connections = 0
        self._cache_size = 0
        self._token_valid = False

        # Start time
        self._start_time = time.time()

        # Deno-compatible fields
        self._stream_requests = 0
        self._non_stream_requests = 0
        self._response_times: List[float] = []
        self._recent_requests: List[Dict] = []
        self._api_type_usage: Dict[str, int] = defaultdict(int)
        self._hourly_requests: Dict[int, int] = defaultdict(int)

        # IP statistics and blacklist
        self._ip_requests: Dict[str, int] = defaultdict(int)
        self._ip_last_seen: Dict[str, int] = {}
        self._ip_blacklist: Dict[str, Dict] = {}
        self._site_enabled: bool = True
        self._self_use_enabled: bool = False
        self._require_approval: bool = True
        self._proxy_api_key: str = settings.proxy_api_key

        # Distributed mode: pending local increments for Redis sync
        self._pending_requests: int = 0
        self._pending_errors: int = 0
        self._pending_retries: int = 0
        self._pending_by_endpoint: Dict[str, int] = defaultdict(int)
        self._pending_by_status: Dict[str, int] = defaultdict(int)
        self._pending_by_model: Dict[str, int] = defaultdict(int)
        self._pending_by_error_type: Dict[str, int] = defaultdict(int)
        self._pending_input_tokens: Dict[str, int] = defaultdict(int)
        self._pending_output_tokens: Dict[str, int] = defaultdict(int)
        self._pending_stream: int = 0
        self._pending_non_stream: int = 0
        self._pending_api_type: Dict[str, int] = defaultdict(int)

        self._initialized = False

        # In single-node mode, init DB synchronously for backward compat
        if not settings.is_distributed:
            self._init_db()
            self._load_from_db()

    # ==================== Initialization ====================

    async def initialize(self) -> None:
        """
        Initialize metrics system.

        In distributed mode, loads state from Redis.
        In single-node mode, DB is already initialized in __init__.
        """
        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    # Load global switches from Redis
                    site_val = await client.get(f"{_PREFIX}:site_enabled")
                    if site_val is not None:
                        self._site_enabled = site_val == "true"

                    self_use_val = await client.get(f"{_PREFIX}:self_use_enabled")
                    if self_use_val is not None:
                        self._self_use_enabled = self_use_val == "true"

                    approval_val = await client.get(f"{_PREFIX}:require_approval")
                    if approval_val is not None:
                        self._require_approval = approval_val == "true"

                    proxy_key_val = await client.get(f"{_PREFIX}:proxy_api_key")
                    if proxy_key_val:
                        self._proxy_api_key = proxy_key_val

                    logger.info("Metrics: Redis 状态加载完�?)
                except Exception as e:
                    logger.warning(f"Metrics: Redis 状态加载失�?{e}")
        self._initialized = True

    async def flush(self) -> None:
        """
        Flush pending local counters to Redis.

        Called during graceful shutdown or periodic sync.
        In distributed mode, syncs accumulated local counters via INCRBY.
        """
        if not settings.is_distributed:
            return

        client = await self._get_redis()
        if not client:
            return

        async with self._lock:
            try:
                pipe = client.pipeline()

                if self._pending_requests:
                    pipe.incrby(f"{_PREFIX}:total_requests", self._pending_requests)
                if self._pending_errors:
                    pipe.incrby(f"{_PREFIX}:total_errors", self._pending_errors)
                if self._pending_retries:
                    pipe.incrby(f"{_PREFIX}:total_retries", self._pending_retries)

                for field, val in self._pending_by_endpoint.items():
                    if val:
                        pipe.hincrby(f"{_PREFIX}:by_endpoint", field, val)
                for field, val in self._pending_by_status.items():
                    if val:
                        pipe.hincrby(f"{_PREFIX}:by_status", field, val)
                for field, val in self._pending_by_model.items():
                    if val:
                        pipe.hincrby(f"{_PREFIX}:by_model", field, val)
                for field, val in self._pending_by_error_type.items():
                    if val:
                        pipe.hincrby(f"{_PREFIX}:by_error_type", field, val)
                for field, val in self._pending_input_tokens.items():
                    if val:
                        pipe.hincrby(f"{_PREFIX}:tokens:input_tokens", field, val)
                for field, val in self._pending_output_tokens.items():
                    if val:
                        pipe.hincrby(f"{_PREFIX}:tokens:output_tokens", field, val)

                if self._pending_stream:
                    pipe.incrby(f"{_PREFIX}:stream_requests", self._pending_stream)
                if self._pending_non_stream:
                    pipe.incrby(f"{_PREFIX}:non_stream_requests", self._pending_non_stream)
                for field, val in self._pending_api_type.items():
                    if val:
                        pipe.hincrby(f"{_PREFIX}:api_type_usage", field, val)

                await pipe.execute()

                # Clear pending counters
                self._pending_requests = 0
                self._pending_errors = 0
                self._pending_retries = 0
                self._pending_by_endpoint.clear()
                self._pending_by_status.clear()
                self._pending_by_model.clear()
                self._pending_by_error_type.clear()
                self._pending_input_tokens.clear()
                self._pending_output_tokens.clear()
                self._pending_stream = 0
                self._pending_non_stream = 0
                self._pending_api_type.clear()

                logger.debug("Metrics: 本地待同步计数已刷新。Redis")
            except Exception as e:
                logger.warning(f"Metrics: flush 。Redis 失败: {e}")

    # ==================== Redis Helper ====================

    async def _get_redis(self):
        """Get Redis client, returns None if unavailable."""
        try:
            from geek_gateway.redis_manager import redis_manager
            return await redis_manager.get_client()
        except Exception:
            return None

    # ==================== SQLite Methods (single-node) ====================

    def _init_db(self) -> None:
        """Initialize SQLite database and create tables."""
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS counters (
                    key TEXT PRIMARY KEY,
                    value INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS hourly_requests (
                    hour_ts INTEGER PRIMARY KEY,
                    count INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS recent_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER,
                    api_type TEXT,
                    path TEXT,
                    status INTEGER,
                    duration REAL,
                    model TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_recent_ts ON recent_requests(timestamp);
                CREATE TABLE IF NOT EXISTS ip_stats (
                    ip TEXT PRIMARY KEY,
                    count INTEGER DEFAULT 0,
                    last_seen INTEGER
                );
                CREATE TABLE IF NOT EXISTS ip_blacklist (
                    ip TEXT PRIMARY KEY,
                    banned_at INTEGER,
                    reason TEXT
                );
                CREATE TABLE IF NOT EXISTS site_config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
            ''')
            conn.commit()

    def _load_from_db(self) -> None:
        """Load metrics from SQLite database."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                # Load counters
                cursor = conn.execute("SELECT key, value FROM counters")
                for key, value in cursor:
                    if key.startswith("req:"):
                        self._request_total[key[4:]] = value
                    elif key.startswith("err:"):
                        self._error_total[key[4:]] = value
                    elif key.startswith("retry:"):
                        self._retry_total[key[6:]] = value
                    elif key.startswith("api:"):
                        self._api_type_usage[key[4:]] = value
                    elif key.startswith("in_tok:"):
                        self._input_tokens_total[key[7:]] = value
                    elif key.startswith("out_tok:"):
                        self._output_tokens_total[key[8:]] = value
                    elif key == "stream_requests":
                        self._stream_requests = value
                    elif key == "non_stream_requests":
                        self._non_stream_requests = value

                # Load hourly requests
                cursor = conn.execute("SELECT hour_ts, count FROM hourly_requests")
                for hour_ts, count in cursor:
                    self._hourly_requests[hour_ts] = count

                # Load recent requests (last 50)
                cursor = conn.execute(
                    "SELECT timestamp, api_type, path, status, duration, model "
                    "FROM recent_requests ORDER BY id DESC LIMIT 50"
                )
                rows = cursor.fetchall()
                self._recent_requests = [
                    {"timestamp": r[0], "apiType": r[1], "path": r[2],
                     "status": r[3], "duration": r[4], "model": r[5]}
                    for r in reversed(rows)
                ]

                # Load IP stats
                cursor = conn.execute("SELECT ip, count, last_seen FROM ip_stats")
                for ip, count, last_seen in cursor:
                    self._ip_requests[ip] = count
                    self._ip_last_seen[ip] = last_seen

                # Load IP blacklist
                cursor = conn.execute("SELECT ip, banned_at, reason FROM ip_blacklist")
                for ip, banned_at, reason in cursor:
                    self._ip_blacklist[ip] = {"banned_at": banned_at, "reason": reason}

                # Load site config
                cursor = conn.execute("SELECT key, value FROM site_config WHERE key = 'site_enabled'")
                row = cursor.fetchone()
                if row:
                    self._site_enabled = row[1] == "true"

                cursor = conn.execute("SELECT key, value FROM site_config WHERE key = 'self_use_enabled'")
                row = cursor.fetchone()
                if row:
                    self._self_use_enabled = row[1] == "true"

                cursor = conn.execute("SELECT key, value FROM site_config WHERE key = 'require_approval'")
                row = cursor.fetchone()
                if row:
                    self._require_approval = row[1] == "true"

                cursor = conn.execute("SELECT key, value FROM site_config WHERE key = 'proxy_api_key'")
                row = cursor.fetchone()
                if row and row[1]:
                    self._proxy_api_key = row[1]

                logger.info(f"Loaded metrics from {self._db_path}")
        except Exception as e:
            logger.warning(f"Failed to load metrics from DB: {e}")

    def _save_counter(self, key: str, value: int) -> None:
        """Save a single counter to database."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO counters (key, value) VALUES (?, ?)",
                    (key, value)
                )
                conn.commit()
        except Exception as e:
            logger.debug(f"Failed to save counter: {e}")

    def _save_hourly(self, hour_ts: int, count: int) -> None:
        """Save hourly request count."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO hourly_requests (hour_ts, count) VALUES (?, ?)",
                    (hour_ts, count)
                )
                # Clean old data (> 24h)
                cutoff = hour_ts - 24 * 3600000
                conn.execute("DELETE FROM hourly_requests WHERE hour_ts < �?, (cutoff,))
                conn.commit()
        except Exception as e:
            logger.debug(f"Failed to save hourly: {e}")

    def _save_recent_request(self, req: Dict) -> None:
        """Save a recent request to database."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO recent_requests (timestamp, api_type, path, status, duration, model) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (req["timestamp"], req["apiType"], req["path"],
                     req["status"], req["duration"], req["model"])
                )
                # Keep only last 100 records
                conn.execute(
                    "DELETE FROM recent_requests WHERE id NOT IN "
                    "(SELECT id FROM recent_requests ORDER BY id DESC LIMIT 100)"
                )
                conn.commit()
        except Exception as e:
            logger.debug(f"Failed to save recent request: {e}")

    # ==================== Core Metrics Methods ====================

    async def inc_request(self, endpoint: str, status_code: int, model: str = "unknown") -> None:
        """
        Increment request count.

        Args:
            endpoint: API endpoint
            status_code: HTTP status code
            model: Model name
        """
        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    pipe = client.pipeline()
                    pipe.incr(f"{_PREFIX}:total_requests")
                    pipe.hincrby(f"{_PREFIX}:by_endpoint", endpoint, 1)
                    pipe.hincrby(f"{_PREFIX}:by_status", str(status_code), 1)
                    pipe.hincrby(f"{_PREFIX}:by_model", model, 1)
                    await pipe.execute()
                except Exception as e:
                    logger.debug(f"Metrics Redis inc_request failed: {e}")
                    # Degrade to local pending
                    async with self._lock:
                        self._pending_requests += 1
                        self._pending_by_endpoint[endpoint] += 1
                        self._pending_by_status[str(status_code)] += 1
                        self._pending_by_model[model] += 1
            else:
                # Redis unavailable, accumulate locally
                async with self._lock:
                    self._pending_requests += 1
                    self._pending_by_endpoint[endpoint] += 1
                    self._pending_by_status[str(status_code)] += 1
                    self._pending_by_model[model] += 1
        else:
            async with self._lock:
                key = f"{endpoint}:{status_code}:{model}"
                self._request_total[key] += 1
                self._save_counter(f"req:{key}", self._request_total[key])

    def _split_request_key(self, key: str) -> Tuple[str, str, str]:
        """Split request key safely, allowing ':' in endpoints."""
        parts = key.rsplit(":", 2)
        if len(parts) == 3:
            endpoint, status, model = parts
        elif len(parts) == 2:
            endpoint, status = parts
            model = "unknown"
        else:
            endpoint, status, model = key, "unknown", "unknown"
        return endpoint, status, model

    def _is_success_status(self, key: str) -> bool:
        """Check if request key has a successful HTTP status."""
        _endpoint, status_str, _model = self._split_request_key(key)
        try:
            status = int(status_str)
        except ValueError:
            return False
        return 200 <= status < 400

    async def inc_error(self, error_type: str) -> None:
        """
        Increment error count.

        Args:
            error_type: Error type
        """
        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    pipe = client.pipeline()
                    pipe.incr(f"{_PREFIX}:total_errors")
                    pipe.hincrby(f"{_PREFIX}:by_error_type", error_type, 1)
                    await pipe.execute()
                except Exception as e:
                    logger.debug(f"Metrics Redis inc_error failed: {e}")
                    async with self._lock:
                        self._pending_errors += 1
                        self._pending_by_error_type[error_type] += 1
            else:
                async with self._lock:
                    self._pending_errors += 1
                    self._pending_by_error_type[error_type] += 1
        else:
            async with self._lock:
                self._error_total[error_type] += 1
                self._save_counter(f"err:{error_type}", self._error_total[error_type])

    async def inc_retry(self, endpoint: str) -> None:
        """
        Increment retry count.

        Args:
            endpoint: API endpoint
        """
        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    await client.incr(f"{_PREFIX}:total_retries")
                except Exception as e:
                    logger.debug(f"Metrics Redis inc_retry failed: {e}")
                    async with self._lock:
                        self._pending_retries += 1
            else:
                async with self._lock:
                    self._pending_retries += 1
        else:
            async with self._lock:
                self._retry_total[endpoint] += 1
                self._save_counter(f"retry:{endpoint}", self._retry_total[endpoint])

    async def observe_latency(self, endpoint: str, latency: float) -> None:
        """
        Record request latency.

        Args:
            endpoint: API endpoint
            latency: Latency in seconds
        """
        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    # Store latency sample in sorted set with timestamp as score
                    now = time.time()
                    member = f"{now}:{latency}"
                    await client.zadd(f"{_PREFIX}:latency:{endpoint}", {member: now})
                    # Keep only last 1000 samples per endpoint
                    await client.zremrangebyrank(f"{_PREFIX}:latency:{endpoint}", 0, -1001)
                except Exception as e:
                    logger.debug(f"Metrics Redis observe_latency failed: {e}")
            # Also update local histogram for Prometheus export
            async with self._lock:
                for i, le in enumerate(self.LATENCY_BUCKETS):
                    if latency <= le:
                        self._latency_histogram[endpoint][i] += 1
                self._latency_sum[endpoint] += latency
                self._latency_count[endpoint] += 1
        else:
            async with self._lock:
                for i, le in enumerate(self.LATENCY_BUCKETS):
                    if latency <= le:
                        self._latency_histogram[endpoint][i] += 1
                self._latency_sum[endpoint] += latency
                self._latency_count[endpoint] += 1

    async def add_tokens(self, model: str, input_tokens: int, output_tokens: int) -> None:
        """
        Add token usage.

        Args:
            model: Model name
            input_tokens: Input token count
            output_tokens: Output token count
        """
        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    pipe = client.pipeline()
                    pipe.hincrby(f"{_PREFIX}:tokens:input_tokens", model, input_tokens)
                    pipe.hincrby(f"{_PREFIX}:tokens:output_tokens", model, output_tokens)
                    await pipe.execute()
                except Exception as e:
                    logger.debug(f"Metrics Redis add_tokens failed: {e}")
                    async with self._lock:
                        self._pending_input_tokens[model] += input_tokens
                        self._pending_output_tokens[model] += output_tokens
            else:
                async with self._lock:
                    self._pending_input_tokens[model] += input_tokens
                    self._pending_output_tokens[model] += output_tokens
        else:
            async with self._lock:
                self._input_tokens_total[model] += input_tokens
                self._output_tokens_total[model] += output_tokens
                self._save_counter(f"in_tok:{model}", self._input_tokens_total[model])
                self._save_counter(f"out_tok:{model}", self._output_tokens_total[model])

    async def set_active_connections(self, count: int) -> None:
        """Set active connection count."""
        async with self._lock:
            self._active_connections = count

    async def inc_active_connections(self) -> None:
        """Increment active connection count."""
        async with self._lock:
            self._active_connections += 1

    async def dec_active_connections(self) -> None:
        """Decrement active connection count."""
        async with self._lock:
            self._active_connections = max(0, self._active_connections - 1)

    async def set_cache_size(self, size: int) -> None:
        """Set cache size."""
        async with self._lock:
            self._cache_size = size

    async def set_token_valid(self, valid: bool) -> None:
        """Set token validity status."""
        async with self._lock:
            self._token_valid = valid

    async def record_request(
        self,
        endpoint: str,
        status_code: int,
        duration_ms: float,
        model: str = "unknown",
        is_stream: bool = False,
        api_type: str = "openai"
    ) -> None:
        """
        Record a complete request with all Deno-compatible fields.

        Args:
            endpoint: API endpoint
            status_code: HTTP status code
            duration_ms: Duration in milliseconds
            model: Model name
            is_stream: Whether streaming request
            api_type: API type (openai/anthropic)
        """
        now = int(time.time() * 1000)
        req = {
            "timestamp": now,
            "apiType": api_type,
            "path": endpoint,
            "status": status_code,
            "duration": duration_ms,
            "model": model
        }

        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    pipe = client.pipeline()
                    # Stream/non-stream counters
                    if is_stream:
                        pipe.incr(f"{_PREFIX}:stream_requests")
                    else:
                        pipe.incr(f"{_PREFIX}:non_stream_requests")
                    # API type usage
                    pipe.hincrby(f"{_PREFIX}:api_type_usage", api_type, 1)
                    # Recent requests list (LPUSH + LTRIM to keep 100)
                    pipe.lpush(f"{_PREFIX}:recent_requests", json.dumps(req))
                    pipe.ltrim(f"{_PREFIX}:recent_requests", 0, 99)
                    # Hourly requests
                    hour_ts = (now // 3600000) * 3600000
                    pipe.hincrby(f"{_PREFIX}:hourly_requests", str(hour_ts), 1)
                    await pipe.execute()
                except Exception as e:
                    logger.debug(f"Metrics Redis record_request failed: {e}")
                    # Degrade to local pending
                    async with self._lock:
                        if is_stream:
                            self._pending_stream += 1
                        else:
                            self._pending_non_stream += 1
                        self._pending_api_type[api_type] += 1
            else:
                async with self._lock:
                    if is_stream:
                        self._pending_stream += 1
                    else:
                        self._pending_non_stream += 1
                    self._pending_api_type[api_type] += 1

            # Always keep local response_times and recent_requests for this node
            async with self._lock:
                self._response_times.append(duration_ms)
                if len(self._response_times) > self.MAX_RESPONSE_TIMES:
                    self._response_times.pop(0)
                self._recent_requests.append(req)
                if len(self._recent_requests) > self.MAX_RECENT_REQUESTS:
                    self._recent_requests.pop(0)
        else:
            async with self._lock:
                # Increment stream/non-stream counters
                if is_stream:
                    self._stream_requests += 1
                    self._save_counter("stream_requests", self._stream_requests)
                else:
                    self._non_stream_requests += 1
                    self._save_counter("non_stream_requests", self._non_stream_requests)

                # Track API type usage
                self._api_type_usage[api_type] += 1
                self._save_counter(f"api:{api_type}", self._api_type_usage[api_type])

                # Add to response times (keep last N)
                self._response_times.append(duration_ms)
                if len(self._response_times) > self.MAX_RESPONSE_TIMES:
                    self._response_times.pop(0)

                # Add to recent requests (keep last N)
                self._recent_requests.append(req)
                if len(self._recent_requests) > self.MAX_RECENT_REQUESTS:
                    self._recent_requests.pop(0)
                self._save_recent_request(req)

                # Track hourly requests
                hour_ts = (now // 3600000) * 3600000
                self._hourly_requests[hour_ts] += 1
                self._save_hourly(hour_ts, self._hourly_requests[hour_ts])
                # Clean up old hourly data (keep only last 24 hours)
                cutoff = hour_ts - 24 * 3600000
                self._hourly_requests = defaultdict(
                    int,
                    {k: v for k, v in self._hourly_requests.items() if k >= cutoff}
                )

    # ==================== Read Methods ====================

    async def get_deno_compatible_metrics(self) -> Dict:
        """
        Get metrics in Deno-compatible format for dashboard.

        Returns:
            Deno-compatible metrics dictionary
        """
        if settings.is_distributed:
            return await self._get_deno_metrics_distributed()

        async with self._lock:
            return self._get_deno_metrics_local()

    def _get_deno_metrics_local(self) -> Dict:
        """Get Deno-compatible metrics from local memory (single-node)."""
        total_requests = sum(self._request_total.values())
        success_requests = 0
        failed_requests = 0

        for key, count in self._request_total.items():
            _endpoint, status_str, _model = self._split_request_key(key)
            try:
                status = int(status_str)
            except ValueError:
                failed_requests += count
                continue
            if 200 <= status < 400:
                success_requests += count
            else:
                failed_requests += count

        avg_response_time = 0.0
        if self._response_times:
            avg_response_time = sum(self._response_times) / len(self._response_times)

        model_usage = {}
        for req in self._recent_requests:
            model = req.get("model", "unknown")
            if model and model != "unknown":
                model_usage[model] = model_usage.get(model, 0) + 1
        if not model_usage:
            for key, count in self._request_total.items():
                _endpoint, _status, model = self._split_request_key(key)
                if model != "unknown":
                    model_usage[model] = model_usage.get(model, 0) + count

        now = int(time.time() * 1000)
        current_hour = (now // 3600000) * 3600000
        hourly_data = []
        for i in range(24):
            hour_ts = current_hour - (23 - i) * 3600000
            hourly_data.append({
                "hour": hour_ts,
                "count": self._hourly_requests.get(hour_ts, 0)
            })

        return {
            "totalRequests": total_requests,
            "successRequests": success_requests,
            "failedRequests": failed_requests,
            "avgResponseTime": avg_response_time,
            "responseTimes": list(self._response_times),
            "streamRequests": self._stream_requests,
            "nonStreamRequests": self._non_stream_requests,
            "modelUsage": model_usage,
            "apiTypeUsage": dict(self._api_type_usage),
            "recentRequests": list(self._recent_requests),
            "startTime": int(self._start_time * 1000),
            "hourlyRequests": hourly_data
        }

    async def _get_deno_metrics_distributed(self) -> Dict:
        """Get Deno-compatible metrics from Redis (distributed mode)."""
        client = await self._get_redis()
        if not client:
            # Fallback to local data
            async with self._lock:
                return self._get_deno_metrics_local()

        try:
            pipe = client.pipeline()
            pipe.get(f"{_PREFIX}:total_requests")
            pipe.hgetall(f"{_PREFIX}:by_status")
            pipe.hgetall(f"{_PREFIX}:by_model")
            pipe.get(f"{_PREFIX}:stream_requests")
            pipe.get(f"{_PREFIX}:non_stream_requests")
            pipe.hgetall(f"{_PREFIX}:api_type_usage")
            pipe.lrange(f"{_PREFIX}:recent_requests", 0, 49)
            pipe.hgetall(f"{_PREFIX}:hourly_requests")
            results = await pipe.execute()

            total_requests = int(results[0] or 0)
            by_status = results[1] or {}
            by_model = results[2] or {}
            stream_requests = int(results[3] or 0)
            non_stream_requests = int(results[4] or 0)
            api_type_usage = {k: int(v) for k, v in (results[5] or {}).items()}
            recent_raw = results[6] or []
            hourly_raw = results[7] or {}

            # Calculate success/failed from by_status
            success_requests = 0
            failed_requests = 0
            for status_str, count_str in by_status.items():
                count = int(count_str)
                try:
                    status = int(status_str)
                    if 200 <= status < 400:
                        success_requests += count
                    else:
                        failed_requests += count
                except ValueError:
                    failed_requests += count

            # Parse recent requests
            recent_requests = []
            for raw in recent_raw:
                try:
                    recent_requests.append(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    pass

            # Model usage from by_model hash
            model_usage = {k: int(v) for k, v in by_model.items() if k != "unknown"}

            # Response times from local node
            async with self._lock:
                avg_response_time = 0.0
                if self._response_times:
                    avg_response_time = sum(self._response_times) / len(self._response_times)
                response_times = list(self._response_times)

            # Hourly data
            now = int(time.time() * 1000)
            current_hour = (now // 3600000) * 3600000
            hourly_data = []
            for i in range(24):
                hour_ts = current_hour - (23 - i) * 3600000
                hourly_data.append({
                    "hour": hour_ts,
                    "count": int(hourly_raw.get(str(hour_ts), 0))
                })

            return {
                "totalRequests": total_requests,
                "successRequests": success_requests,
                "failedRequests": failed_requests,
                "avgResponseTime": avg_response_time,
                "responseTimes": response_times,
                "streamRequests": stream_requests,
                "nonStreamRequests": non_stream_requests,
                "modelUsage": model_usage,
                "apiTypeUsage": api_type_usage,
                "recentRequests": recent_requests,
                "startTime": int(self._start_time * 1000),
                "hourlyRequests": hourly_data
            }
        except Exception as e:
            logger.warning(f"Metrics: Redis get_deno_metrics failed: {e}")
            async with self._lock:
                return self._get_deno_metrics_local()

    async def get_metrics(self) -> Dict:
        """
        Get all metrics.

        Returns:
            Metrics dictionary
        """
        if settings.is_distributed:
            return await self._get_metrics_distributed()

        async with self._lock:
            return self._get_metrics_local()

    def _get_metrics_local(self) -> Dict:
        """Get metrics from local memory (single-node)."""
        latency_stats = {}
        for endpoint, counts in self._latency_histogram.items():
            total_count = self._latency_count[endpoint]
            if total_count > 0:
                avg = self._latency_sum[endpoint] / total_count
                p50 = self._calculate_percentile(counts, total_count, 0.50)
                p95 = self._calculate_percentile(counts, total_count, 0.95)
                p99 = self._calculate_percentile(counts, total_count, 0.99)
                latency_stats[endpoint] = {
                    "avg": round(avg, 4),
                    "p50": round(p50, 4),
                    "p95": round(p95, 4),
                    "p99": round(p99, 4),
                    "count": total_count
                }

        return {
            "version": APP_VERSION,
            "uptime_seconds": round(time.time() - self._start_time, 2),
            "requests": {
                "total": dict(self._request_total),
                "by_endpoint": self._aggregate_by_endpoint(),
                "by_status": self._aggregate_by_status(),
                "by_model": self._aggregate_by_model()
            },
            "errors": dict(self._error_total),
            "retries": dict(self._retry_total),
            "latency": latency_stats,
            "tokens": {
                "input": dict(self._input_tokens_total),
                "output": dict(self._output_tokens_total),
                "total_input": sum(self._input_tokens_total.values()),
                "total_output": sum(self._output_tokens_total.values())
            },
            "gauges": {
                "active_connections": self._active_connections,
                "cache_size": self._cache_size,
                "token_valid": self._token_valid
            }
        }

    async def _get_metrics_distributed(self) -> Dict:
        """Get metrics from Redis (distributed mode), aggregating all nodes."""
        client = await self._get_redis()
        if not client:
            async with self._lock:
                return self._get_metrics_local()

        try:
            pipe = client.pipeline()
            pipe.get(f"{_PREFIX}:total_requests")
            pipe.get(f"{_PREFIX}:total_errors")
            pipe.get(f"{_PREFIX}:total_retries")
            pipe.hgetall(f"{_PREFIX}:by_endpoint")
            pipe.hgetall(f"{_PREFIX}:by_status")
            pipe.hgetall(f"{_PREFIX}:by_model")
            pipe.hgetall(f"{_PREFIX}:by_error_type")
            pipe.hgetall(f"{_PREFIX}:tokens:input_tokens")
            pipe.hgetall(f"{_PREFIX}:tokens:output_tokens")
            results = await pipe.execute()

            total_requests = int(results[0] or 0)
            total_errors = int(results[1] or 0)
            total_retries = int(results[2] or 0)
            by_endpoint = {k: int(v) for k, v in (results[3] or {}).items()}
            by_status = {k: int(v) for k, v in (results[4] or {}).items()}
            by_model = {k: int(v) for k, v in (results[5] or {}).items()}
            by_error_type = {k: int(v) for k, v in (results[6] or {}).items()}
            input_tokens = {k: int(v) for k, v in (results[7] or {}).items()}
            output_tokens = {k: int(v) for k, v in (results[8] or {}).items()}

            # Latency stats from local histogram (per-node)
            async with self._lock:
                latency_stats = {}
                for endpoint, counts in self._latency_histogram.items():
                    total_count = self._latency_count[endpoint]
                    if total_count > 0:
                        avg = self._latency_sum[endpoint] / total_count
                        p50 = self._calculate_percentile(counts, total_count, 0.50)
                        p95 = self._calculate_percentile(counts, total_count, 0.95)
                        p99 = self._calculate_percentile(counts, total_count, 0.99)
                        latency_stats[endpoint] = {
                            "avg": round(avg, 4),
                            "p50": round(p50, 4),
                            "p95": round(p95, 4),
                            "p99": round(p99, 4),
                            "count": total_count
                        }

            # Also try to compute latency from Redis sorted sets for endpoints
            for ep in by_endpoint:
                try:
                    samples = await client.zrange(
                        f"{_PREFIX}:latency:{ep}", 0, -1, withscores=False
                    )
                    if samples:
                        latencies = []
                        for s in samples:
                            try:
                                latencies.append(float(s.split(":")[1]))
                            except (IndexError, ValueError):
                                pass
                        if latencies:
                            latencies.sort()
                            n = len(latencies)
                            latency_stats[ep] = {
                                "avg": round(sum(latencies) / n, 4),
                                "p50": round(latencies[int(n * 0.50)], 4),
                                "p95": round(latencies[min(int(n * 0.95), n - 1)], 4),
                                "p99": round(latencies[min(int(n * 0.99), n - 1)], 4),
                                "count": n
                            }
                except Exception:
                    pass

            return {
                "version": APP_VERSION,
                "uptime_seconds": round(time.time() - self._start_time, 2),
                "requests": {
                    "total": total_requests,
                    "by_endpoint": by_endpoint,
                    "by_status": by_status,
                    "by_model": by_model
                },
                "errors": by_error_type,
                "retries": {"total": total_retries},
                "latency": latency_stats,
                "tokens": {
                    "input": input_tokens,
                    "output": output_tokens,
                    "total_input": sum(input_tokens.values()),
                    "total_output": sum(output_tokens.values())
                },
                "gauges": {
                    "active_connections": self._active_connections,
                    "cache_size": self._cache_size,
                    "token_valid": self._token_valid
                }
            }
        except Exception as e:
            logger.warning(f"Metrics: Redis get_metrics failed: {e}")
            async with self._lock:
                return self._get_metrics_local()

    def _calculate_percentile(self, bucket_counts: List[int], total: int, percentile: float) -> float:
        """
        Calculate percentile from histogram buckets.

        Args:
            bucket_counts: Bucket count list
            total: Total count
            percentile: Percentile (0-1)

        Returns:
            Estimated percentile value
        """
        if total == 0:
            return 0.0

        target = total * percentile
        cumulative = 0

        for i, count in enumerate(bucket_counts):
            cumulative += count
            if cumulative >= target:
                return self.LATENCY_BUCKETS[i] if self.LATENCY_BUCKETS[i] != float('inf') else 120.0

        return self.LATENCY_BUCKETS[-2]

    def _aggregate_by_endpoint(self) -> Dict[str, int]:
        """Aggregate request count by endpoint."""
        result = defaultdict(int)
        for key, count in self._request_total.items():
            endpoint, _status, _model = self._split_request_key(key)
            result[endpoint] += count
        return dict(result)

    def _aggregate_by_status(self) -> Dict[str, int]:
        """Aggregate request count by status code."""
        result = defaultdict(int)
        for key, count in self._request_total.items():
            _endpoint, status, _model = self._split_request_key(key)
            result[status] += count
        return dict(result)

    def _aggregate_by_model(self) -> Dict[str, int]:
        """Aggregate request count by model."""
        result = defaultdict(int)
        for key, count in self._request_total.items():
            _endpoint, _status, model = self._split_request_key(key)
            result[model] += count
        return dict(result)

    async def export_prometheus(self) -> str:
        """
        Export metrics in Prometheus format.

        In distributed mode, aggregates all nodes' metrics from Redis.

        Returns:
            Prometheus text format metrics
        """
        if settings.is_distributed:
            return await self._export_prometheus_distributed()

        async with self._lock:
            return self._export_prometheus_local()

    def _export_prometheus_local(self) -> str:
        """Export Prometheus metrics from local memory (single-node)."""
        lines = []

        # Info metric with version
        lines.append("# HELP GeekGate_info GeekGate version information")
        lines.append("# TYPE GeekGate_info gauge")
        lines.append(f'GeekGate_info{{version="{APP_VERSION}"}} 1')

        # Total requests
        lines.append("# HELP GeekGate_requests_total Total number of requests")
        lines.append("# TYPE GeekGate_requests_total counter")
        for key, count in self._request_total.items():
            endpoint, status, model = self._split_request_key(key)
            lines.append(
                f'GeekGate_requests_total{{endpoint="{endpoint}",status="{status}",model="{model}"}} {count}'
            )

        # Total errors
        lines.append("# HELP GeekGate_errors_total Total number of errors")
        lines.append("# TYPE GeekGate_errors_total counter")
        for error_type, count in self._error_total.items():
            lines.append(f'GeekGate_errors_total{{type="{error_type}"}} {count}')

        # Total retries
        lines.append("# HELP GeekGate_retries_total Total number of retries")
        lines.append("# TYPE GeekGate_retries_total counter")
        for endpoint, count in self._retry_total.items():
            lines.append(f'GeekGate_retries_total{{endpoint="{endpoint}"}} {count}')

        # Token usage
        lines.append("# HELP GeekGate_tokens_total Total tokens used")
        lines.append("# TYPE GeekGate_tokens_total counter")
        for model, tokens in self._input_tokens_total.items():
            lines.append(f'GeekGate_tokens_total{{model="{model}",type="input"}} {tokens}')
        for model, tokens in self._output_tokens_total.items():
            lines.append(f'GeekGate_tokens_total{{model="{model}",type="output"}} {tokens}')

        # Latency histogram
        lines.append("# HELP GeekGate_request_duration_seconds Request duration histogram")
        lines.append("# TYPE GeekGate_request_duration_seconds histogram")
        for endpoint, counts in self._latency_histogram.items():
            cumulative = 0
            for i, count in enumerate(counts):
                cumulative += count
                le = self.LATENCY_BUCKETS[i]
                le_str = "+Inf" if le == float('inf') else str(le)
                lines.append(
                    f'GeekGate_request_duration_seconds_bucket{{endpoint="{endpoint}",le="{le_str}"}} {cumulative}'
                )
            lines.append(
                f'GeekGate_request_duration_seconds_sum{{endpoint="{endpoint}"}} {self._latency_sum[endpoint]}'
            )
            lines.append(
                f'GeekGate_request_duration_seconds_count{{endpoint="{endpoint}"}} {self._latency_count[endpoint]}'
            )

        # Gauges
        lines.append("# HELP GeekGate_active_connections Current active connections")
        lines.append("# TYPE GeekGate_active_connections gauge")
        lines.append(f"GeekGate_active_connections {self._active_connections}")

        lines.append("# HELP GeekGate_cache_size Current cache size")
        lines.append("# TYPE GeekGate_cache_size gauge")
        lines.append(f"GeekGate_cache_size {self._cache_size}")

        lines.append("# HELP GeekGate_token_valid Token validity status")
        lines.append("# TYPE GeekGate_token_valid gauge")
        lines.append(f"GeekGate_token_valid {1 if self._token_valid else 0}")

        lines.append("# HELP GeekGate_uptime_seconds Uptime in seconds")
        lines.append("# TYPE GeekGate_uptime_seconds gauge")
        lines.append(f"GeekGate_uptime_seconds {round(time.time() - self._start_time, 2)}")

        return "\n".join(lines) + "\n"

    async def _export_prometheus_distributed(self) -> str:
        """Export Prometheus metrics from Redis (distributed mode, aggregates all nodes)."""
        client = await self._get_redis()
        if not client:
            async with self._lock:
                return self._export_prometheus_local()

        try:
            pipe = client.pipeline()
            pipe.hgetall(f"{_PREFIX}:by_endpoint")
            pipe.hgetall(f"{_PREFIX}:by_status")
            pipe.hgetall(f"{_PREFIX}:by_model")
            pipe.hgetall(f"{_PREFIX}:by_error_type")
            pipe.get(f"{_PREFIX}:total_retries")
            pipe.hgetall(f"{_PREFIX}:tokens:input_tokens")
            pipe.hgetall(f"{_PREFIX}:tokens:output_tokens")
            results = await pipe.execute()

            by_endpoint = results[0] or {}
            by_status = results[1] or {}
            by_model = results[2] or {}
            by_error_type = results[3] or {}
            total_retries = int(results[4] or 0)
            input_tokens = results[5] or {}
            output_tokens = results[6] or {}

            lines = []

            lines.append("# HELP GeekGate_info GeekGate version information")
            lines.append("# TYPE GeekGate_info gauge")
            lines.append(f'GeekGate_info{{version="{APP_VERSION}"}} 1')

            # Requests by endpoint+status+model (reconstruct from hashes)
            lines.append("# HELP GeekGate_requests_total Total number of requests")
            lines.append("# TYPE GeekGate_requests_total counter")
            for ep, count in by_endpoint.items():
                lines.append(
                    f'GeekGate_requests_total{{endpoint="{ep}"}} {count}'
                )

            # Errors
            lines.append("# HELP GeekGate_errors_total Total number of errors")
            lines.append("# TYPE GeekGate_errors_total counter")
            for error_type, count in by_error_type.items():
                lines.append(f'GeekGate_errors_total{{type="{error_type}"}} {int(count)}')

            # Retries
            lines.append("# HELP GeekGate_retries_total Total number of retries")
            lines.append("# TYPE GeekGate_retries_total counter")
            lines.append(f"GeekGate_retries_total {total_retries}")

            # Token usage
            lines.append("# HELP GeekGate_tokens_total Total tokens used")
            lines.append("# TYPE GeekGate_tokens_total counter")
            for model, tokens in input_tokens.items():
                lines.append(f'GeekGate_tokens_total{{model="{model}",type="input"}} {int(tokens)}')
            for model, tokens in output_tokens.items():
                lines.append(f'GeekGate_tokens_total{{model="{model}",type="output"}} {int(tokens)}')

            # Latency histogram from local node
            async with self._lock:
                lines.append("# HELP GeekGate_request_duration_seconds Request duration histogram")
                lines.append("# TYPE GeekGate_request_duration_seconds histogram")
                for endpoint, counts in self._latency_histogram.items():
                    cumulative = 0
                    for i, count in enumerate(counts):
                        cumulative += count
                        le = self.LATENCY_BUCKETS[i]
                        le_str = "+Inf" if le == float('inf') else str(le)
                        lines.append(
                            f'GeekGate_request_duration_seconds_bucket{{endpoint="{endpoint}",le="{le_str}"}} {cumulative}'
                        )
                    lines.append(
                        f'GeekGate_request_duration_seconds_sum{{endpoint="{endpoint}"}} {self._latency_sum[endpoint]}'
                    )
                    lines.append(
                        f'GeekGate_request_duration_seconds_count{{endpoint="{endpoint}"}} {self._latency_count[endpoint]}'
                    )

            # Gauges
            lines.append("# HELP GeekGate_active_connections Current active connections")
            lines.append("# TYPE GeekGate_active_connections gauge")
            lines.append(f"GeekGate_active_connections {self._active_connections}")

            lines.append("# HELP GeekGate_cache_size Current cache size")
            lines.append("# TYPE GeekGate_cache_size gauge")
            lines.append(f"GeekGate_cache_size {self._cache_size}")

            lines.append("# HELP GeekGate_token_valid Token validity status")
            lines.append("# TYPE GeekGate_token_valid gauge")
            lines.append(f"GeekGate_token_valid {1 if self._token_valid else 0}")

            lines.append("# HELP GeekGate_uptime_seconds Uptime in seconds")
            lines.append("# TYPE GeekGate_uptime_seconds gauge")
            lines.append(f"GeekGate_uptime_seconds {round(time.time() - self._start_time, 2)}")

            return "\n".join(lines) + "\n"
        except Exception as e:
            logger.warning(f"Metrics: Redis export_prometheus failed: {e}")
            async with self._lock:
                return self._export_prometheus_local()

    # ==================== IP Statistics & Admin Methods ====================

    async def record_ip(self, ip: str) -> None:
        """Record IP request."""
        if not ip:
            return

        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    now = int(time.time() * 1000)
                    pipe = client.pipeline()
                    pipe.hincrby(f"{_PREFIX}:ip_requests", ip, 1)
                    pipe.hset(f"{_PREFIX}:ip_last_seen", ip, str(now))
                    await pipe.execute()
                except Exception as e:
                    logger.debug(f"Metrics Redis record_ip failed: {e}")
                    # Fallback to local
                    async with self._lock:
                        self._ip_requests[ip] += 1
                        self._ip_last_seen[ip] = int(time.time() * 1000)
            else:
                async with self._lock:
                    self._ip_requests[ip] += 1
                    self._ip_last_seen[ip] = int(time.time() * 1000)
        else:
            async with self._lock:
                self._ip_requests[ip] += 1
                now = int(time.time() * 1000)
                self._ip_last_seen[ip] = now
                try:
                    with sqlite3.connect(self._db_path) as conn:
                        conn.execute(
                            "INSERT OR REPLACE INTO ip_stats (ip, count, last_seen) VALUES (?, ?, ?)",
                            (ip, self._ip_requests[ip], now)
                        )
                        conn.commit()
                except Exception as e:
                    logger.debug(f"Failed to save IP stats: {e}")

    async def get_ip_stats(
        self,
        limit: int = 100,
        offset: int = 0,
        search: str = "",
        sort_field: str = "count",
        sort_order: str = "desc"
    ) -> Tuple[List[Dict], int]:
        """Get IP statistics sorted by request count with pagination."""
        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    ip_counts = await client.hgetall(f"{_PREFIX}:ip_requests")
                    ip_last_seen = await client.hgetall(f"{_PREFIX}:ip_last_seen")
                    stats = [
                        {
                            "ip": ip,
                            "count": int(count),
                            "lastSeen": int(ip_last_seen.get(ip, 0))
                        }
                        for ip, count in ip_counts.items()
                    ]
                    if search:
                        stats = [item for item in stats if search in item["ip"]]
                    sort_map = {"count": "count", "last_seen": "lastSeen", "ip": "ip"}
                    key_name = sort_map.get(sort_field, "count")
                    reverse = sort_order.lower() != "asc"
                    stats.sort(key=lambda x: x.get(key_name, 0), reverse=reverse)
                    total = len(stats)
                    return stats[offset:offset + limit], total
                except Exception as e:
                    logger.debug(f"Metrics Redis get_ip_stats failed: {e}")

        # Fallback to local
        async with self._lock:
            stats = [
                {"ip": ip, "count": count, "lastSeen": self._ip_last_seen.get(ip, 0)}
                for ip, count in self._ip_requests.items()
            ]
            if search:
                stats = [item for item in stats if search in item["ip"]]
            sort_map = {"count": "count", "last_seen": "lastSeen", "ip": "ip"}
            key_name = sort_map.get(sort_field, "count")
            reverse = sort_order.lower() != "asc"
            stats.sort(key=lambda x: x.get(key_name, 0), reverse=reverse)
            total = len(stats)
            return stats[offset:offset + limit], total

    async def is_ip_banned(self, ip: str) -> bool:
        """Check if IP is banned."""
        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    return await client.sismember(f"{_PREFIX}:banned_ips", ip)
                except Exception as e:
                    logger.debug(f"Metrics Redis is_ip_banned failed: {e}")

        async with self._lock:
            return ip in self._ip_blacklist

    async def ban_ip(self, ip: str, reason: str = "") -> bool:
        """Ban an IP address."""
        if not ip:
            return False

        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    now = int(time.time() * 1000)
                    pipe = client.pipeline()
                    pipe.sadd(f"{_PREFIX}:banned_ips", ip)
                    # Store ban details in a hash
                    pipe.hset(f"{_PREFIX}:ban_details:{ip}", mapping={
                        "banned_at": str(now),
                        "reason": reason
                    })
                    await pipe.execute()
                    logger.info(f"Banned IP: {ip}, reason: {reason}")
                    return True
                except Exception as e:
                    logger.error(f"Metrics Redis ban_ip failed: {e}")
                    return False

        async with self._lock:
            now = int(time.time() * 1000)
            self._ip_blacklist[ip] = {"banned_at": now, "reason": reason}
            try:
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO ip_blacklist (ip, banned_at, reason) VALUES (?, ?, ?)",
                        (ip, now, reason)
                    )
                    conn.commit()
                logger.info(f"Banned IP: {ip}, reason: {reason}")
                return True
            except Exception as e:
                logger.error(f"Failed to ban IP: {e}")
                return False

    async def unban_ip(self, ip: str) -> bool:
        """Unban an IP address."""
        if not ip:
            return False

        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    pipe = client.pipeline()
                    pipe.srem(f"{_PREFIX}:banned_ips", ip)
                    pipe.delete(f"{_PREFIX}:ban_details:{ip}")
                    await pipe.execute()
                    logger.info(f"Unbanned IP: {ip}")
                    return True
                except Exception as e:
                    logger.error(f"Metrics Redis unban_ip failed: {e}")
                    return False

        async with self._lock:
            if ip in self._ip_blacklist:
                del self._ip_blacklist[ip]
            try:
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute("DELETE FROM ip_blacklist WHERE ip = ?", (ip,))
                    conn.commit()
                logger.info(f"Unbanned IP: {ip}")
                return True
            except Exception as e:
                logger.error(f"Failed to unban IP: {e}")
                return False

    async def get_blacklist(
        self,
        limit: int = 100,
        offset: int = 0,
        search: str = "",
        sort_field: str = "banned_at",
        sort_order: str = "desc"
    ) -> Tuple[List[Dict], int]:
        """Get IP blacklist with pagination."""
        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    banned_ips = await client.smembers(f"{_PREFIX}:banned_ips")
                    items = []
                    for ip in banned_ips:
                        details = await client.hgetall(f"{_PREFIX}:ban_details:{ip}")
                        items.append({
                            "ip": ip,
                            "bannedAt": int(details.get("banned_at", 0)),
                            "reason": details.get("reason", "")
                        })
                    if search:
                        items = [
                            item for item in items
                            if search in item["ip"] or search in (item["reason"] or "")
                        ]
                    sort_map = {"banned_at": "bannedAt", "ip": "ip"}
                    key_name = sort_map.get(sort_field, "bannedAt")
                    reverse = sort_order.lower() != "asc"
                    items.sort(key=lambda x: x.get(key_name, 0), reverse=reverse)
                    total = len(items)
                    return items[offset:offset + limit], total
                except Exception as e:
                    logger.debug(f"Metrics Redis get_blacklist failed: {e}")

        # Fallback to local
        async with self._lock:
            items = [
                {"ip": ip, "bannedAt": info["banned_at"], "reason": info["reason"]}
                for ip, info in self._ip_blacklist.items()
            ]
            if search:
                items = [
                    item for item in items
                    if search in item["ip"] or search in (item["reason"] or "")
                ]
            sort_map = {"banned_at": "bannedAt", "ip": "ip"}
            key_name = sort_map.get(sort_field, "bannedAt")
            reverse = sort_order.lower() != "asc"
            items.sort(key=lambda x: x.get(key_name, 0), reverse=reverse)
            total = len(items)
            return items[offset:offset + limit], total

    # ==================== Site Config Methods ====================

    async def is_site_enabled(self) -> bool:
        """Check if site is enabled."""
        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    val = await client.get(f"{_PREFIX}:site_enabled")
                    if val is not None:
                        return val == "true"
                except Exception as e:
                    logger.debug(f"Metrics Redis is_site_enabled failed: {e}")

        async with self._lock:
            return self._site_enabled

    async def set_site_enabled(self, enabled: bool) -> bool:
        """Enable or disable site."""
        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    await client.set(f"{_PREFIX}:site_enabled", "true" if enabled else "false")
                    async with self._lock:
                        self._site_enabled = enabled
                    logger.info(f"Site enabled: {enabled}")
                    return True
                except Exception as e:
                    logger.error(f"Metrics Redis set_site_enabled failed: {e}")
                    return False

        async with self._lock:
            self._site_enabled = enabled
            try:
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO site_config (key, value) VALUES (?, ?)",
                        ("site_enabled", "true" if enabled else "false")
                    )
                    conn.commit()
                logger.info(f"Site enabled: {enabled}")
                return True
            except Exception as e:
                logger.error(f"Failed to set site status: {e}")
                return False

    async def is_self_use_enabled(self) -> bool:
        """Check if self-use mode is enabled."""
        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    val = await client.get(f"{_PREFIX}:self_use_enabled")
                    if val is not None:
                        return val == "true"
                except Exception as e:
                    logger.debug(f"Metrics Redis is_self_use_enabled failed: {e}")

        async with self._lock:
            return self._self_use_enabled

    async def is_require_approval(self) -> bool:
        """Check if registration approval is required."""
        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    val = await client.get(f"{_PREFIX}:require_approval")
                    if val is not None:
                        return val == "true"
                except Exception as e:
                    logger.debug(f"Metrics Redis is_require_approval failed: {e}")

        async with self._lock:
            return self._require_approval

    async def set_self_use_enabled(self, enabled: bool) -> bool:
        """Enable or disable self-use mode."""
        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    await client.set(f"{_PREFIX}:self_use_enabled", "true" if enabled else "false")
                    async with self._lock:
                        self._self_use_enabled = enabled
                    logger.info(f"Self-use enabled: {enabled}")
                    return True
                except Exception as e:
                    logger.error(f"Metrics Redis set_self_use_enabled failed: {e}")
                    return False

        async with self._lock:
            self._self_use_enabled = enabled
            try:
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO site_config (key, value) VALUES (?, ?)",
                        ("self_use_enabled", "true" if enabled else "false")
                    )
                    conn.commit()
                logger.info(f"Self-use enabled: {enabled}")
                return True
            except Exception as e:
                logger.error(f"Failed to set self-use status: {e}")
                return False

    async def set_require_approval(self, enabled: bool) -> bool:
        """Enable or disable registration approval requirement."""
        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    await client.set(f"{_PREFIX}:require_approval", "true" if enabled else "false")
                    async with self._lock:
                        self._require_approval = enabled
                    logger.info(f"Require approval enabled: {enabled}")
                    return True
                except Exception as e:
                    logger.error(f"Metrics Redis set_require_approval failed: {e}")
                    return False

        async with self._lock:
            self._require_approval = enabled
            try:
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO site_config (key, value) VALUES (?, ?)",
                        ("require_approval", "true" if enabled else "false")
                    )
                    conn.commit()
                logger.info(f"Require approval enabled: {enabled}")
                return True
            except Exception as e:
                logger.error(f"Failed to set require approval: {e}")
                return False

    async def get_proxy_api_key(self) -> str:
        """Get current proxy API key."""
        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    val = await client.get(f"{_PREFIX}:proxy_api_key")
                    if val:
                        return val
                except Exception as e:
                    logger.debug(f"Metrics Redis get_proxy_api_key failed: {e}")

        async with self._lock:
            return self._proxy_api_key

    async def set_proxy_api_key(self, api_key: str) -> bool:
        """Update proxy API key."""
        api_key = api_key.strip()
        if not api_key:
            return False

        if settings.is_distributed:
            client = await self._get_redis()
            if client:
                try:
                    await client.set(f"{_PREFIX}:proxy_api_key", api_key)
                    async with self._lock:
                        self._proxy_api_key = api_key
                    logger.info("Proxy API key updated")
                    return True
                except Exception as e:
                    logger.error(f"Metrics Redis set_proxy_api_key failed: {e}")
                    return False

        async with self._lock:
            self._proxy_api_key = api_key
            try:
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO site_config (key, value) VALUES (?, ?)",
                        ("proxy_api_key", api_key)
                    )
                    conn.commit()
                logger.info("Proxy API key updated")
                return True
            except Exception as e:
                logger.error(f"Failed to set proxy API key: {e}")
                return False

    async def get_admin_stats(self) -> Dict:
        """Get statistics for admin dashboard."""
        if settings.is_distributed:
            return await self._get_admin_stats_distributed()

        async with self._lock:
            return self._get_admin_stats_local()

    def _get_admin_stats_local(self) -> Dict:
        """Get admin stats from local memory (single-node)."""
        total_requests = sum(self._request_total.values())
        success_requests = sum(
            c for k, c in self._request_total.items()
            if self._is_success_status(k)
        )
        return {
            "totalRequests": total_requests,
            "successRequests": success_requests,
            "failedRequests": total_requests - success_requests,
            "streamRequests": self._stream_requests,
            "nonStreamRequests": self._non_stream_requests,
            "activeConnections": self._active_connections,
            "tokenValid": self._token_valid,
            "siteEnabled": self._site_enabled,
            "selfUseEnabled": self._self_use_enabled,
            "requireApproval": self._require_approval,
            "uptimeSeconds": round(time.time() - self._start_time, 2),
            "totalIPs": len(self._ip_requests),
            "bannedIPs": len(self._ip_blacklist),
        }

    async def _get_admin_stats_distributed(self) -> Dict:
        """Get admin stats from Redis (distributed mode)."""
        client = await self._get_redis()
        if not client:
            async with self._lock:
                return self._get_admin_stats_local()

        try:
            pipe = client.pipeline()
            pipe.get(f"{_PREFIX}:total_requests")
            pipe.hgetall(f"{_PREFIX}:by_status")
            pipe.get(f"{_PREFIX}:stream_requests")
            pipe.get(f"{_PREFIX}:non_stream_requests")
            pipe.get(f"{_PREFIX}:site_enabled")
            pipe.get(f"{_PREFIX}:self_use_enabled")
            pipe.get(f"{_PREFIX}:require_approval")
            pipe.hlen(f"{_PREFIX}:ip_requests")
            pipe.scard(f"{_PREFIX}:banned_ips")
            results = await pipe.execute()

            total_requests = int(results[0] or 0)
            by_status = results[1] or {}
            stream_requests = int(results[2] or 0)
            non_stream_requests = int(results[3] or 0)
            site_enabled = (results[4] or "true") == "true"
            self_use_enabled = (results[5] or "false") == "true"
            require_approval = (results[6] or "true") == "true"
            total_ips = results[7] or 0
            banned_ips = results[8] or 0

            # Calculate success/failed from by_status
            success_requests = 0
            failed_requests = 0
            for status_str, count_str in by_status.items():
                count = int(count_str)
                try:
                    status = int(status_str)
                    if 200 <= status < 400:
                        success_requests += count
                    else:
                        failed_requests += count
                except ValueError:
                    failed_requests += count

            return {
                "totalRequests": total_requests,
                "successRequests": success_requests,
                "failedRequests": failed_requests,
                "streamRequests": stream_requests,
                "nonStreamRequests": non_stream_requests,
                "activeConnections": self._active_connections,
                "tokenValid": self._token_valid,
                "siteEnabled": site_enabled,
                "selfUseEnabled": self_use_enabled,
                "requireApproval": require_approval,
                "uptimeSeconds": round(time.time() - self._start_time, 2),
                "totalIPs": total_ips,
                "bannedIPs": banned_ips,
            }
        except Exception as e:
            logger.warning(f"Metrics: Redis get_admin_stats failed: {e}")
            async with self._lock:
                return self._get_admin_stats_local()


# Global metrics instance
metrics = PrometheusMetrics()
