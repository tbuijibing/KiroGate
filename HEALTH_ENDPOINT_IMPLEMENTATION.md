# Health Endpoint Extension Implementation

## Task 17.2: 扩展 `/health` 端点

### Summary

Extended the `/health` endpoint to support distributed deployment mode with PostgreSQL and Redis connection status monitoring.

### Changes Made

#### File: `kiro_gateway/routes.py`

Modified the `health()` function to:

1. **Added uptime tracking**: Calculate and return uptime in seconds using `metrics._start_time`

2. **Added deployment mode detection**: 
   - Returns `"mode": "single_node"` when `settings.is_distributed` is False
   - Returns `"mode": "distributed"` when `settings.is_distributed` is True

3. **Added distributed mode fields**:
   - `node_id`: Returns the unique node identifier from `settings.node_id`
   - `postgres`: Connection status ("connected" or "disconnected")
   - `redis`: Connection status ("connected" or "disconnected")

4. **PostgreSQL health check**:
   - Executes `SELECT 1 as test` query to verify connection
   - Returns 503 status code if PostgreSQL is disconnected
   - Sets `status` to "unhealthy" when PostgreSQL is down

5. **Redis health check**:
   - Uses `redis_manager.is_available` and `client.ping()` to verify connection
   - Gracefully degrades if Redis is unavailable (does not return 503)

### Response Format

#### Single-Node Mode
```json
{
  "status": "healthy",
  "timestamp": "2025-01-15T10:30:00.000000+00:00",
  "version": "2.3.0",
  "token_valid": true,
  "cache_size": 42,
  "cache_last_update": 1705315800,
  "uptime": 3600,
  "mode": "single_node"
}
```

#### Distributed Mode (All Services Healthy)
```json
{
  "status": "healthy",
  "timestamp": "2025-01-15T10:30:00.000000+00:00",
  "version": "2.3.0",
  "token_valid": true,
  "cache_size": 42,
  "cache_last_update": 1705315800,
  "uptime": 3600,
  "mode": "distributed",
  "node_id": "abc12345",
  "postgres": "connected",
  "redis": "connected"
}
```

#### Distributed Mode (PostgreSQL Down)
```json
{
  "status": "unhealthy",
  "timestamp": "2025-01-15T10:30:00.000000+00:00",
  "version": "2.3.0",
  "token_valid": true,
  "cache_size": 42,
  "cache_last_update": 1705315800,
  "uptime": 3600,
  "mode": "distributed",
  "node_id": "abc12345",
  "postgres": "disconnected"
}
```
**HTTP Status Code**: 503 Service Unavailable

### Requirements Satisfied

- ✅ **需求 10.1**: Returns PostgreSQL and Redis connection status in distributed mode
- ✅ **需求 10.2**: Returns deployment mode, node ID, and uptime
- ✅ **需求 10.5**: PostgreSQL disconnection returns 503 status code
- ✅ **需求 10.6**: Redis disconnection is handled gracefully (degraded mode)
- ✅ Single-node mode does not display PostgreSQL and Redis fields

### Technical Details

1. **PostgreSQL Connection Check**:
   - Uses `user_db._backend.fetch_one("SELECT 1 as test")` for lightweight connection test
   - Catches all exceptions and logs them at debug level
   - Returns 503 immediately if PostgreSQL is down (critical dependency)

2. **Redis Connection Check**:
   - Uses `redis_manager.is_available` property first
   - Then calls `client.ping()` to verify active connection
   - Does not return 503 if Redis is down (non-critical, can degrade)

3. **Uptime Calculation**:
   - Uses `metrics._start_time` which is set when the metrics system initializes
   - Calculates as `current_timestamp - start_time` in seconds

4. **Backward Compatibility**:
   - Single-node mode response format unchanged (except added `uptime` and `mode` fields)
   - All existing fields preserved

### Testing Recommendations

1. **Single-Node Mode**:
   ```bash
   curl http://localhost:8000/health
   # Should return mode: "single_node", no postgres/redis fields
   ```

2. **Distributed Mode (Healthy)**:
   ```bash
   # Set DATABASE_URL=postgresql://... and REDIS_URL=redis://...
   curl http://localhost:8000/health
   # Should return mode: "distributed", postgres: "connected", redis: "connected"
   ```

3. **PostgreSQL Down**:
   ```bash
   # Stop PostgreSQL service
   curl http://localhost:8000/health
   # Should return 503, status: "unhealthy", postgres: "disconnected"
   ```

4. **Redis Down**:
   ```bash
   # Stop Redis service
   curl http://localhost:8000/health
   # Should return 200, redis: "disconnected" (graceful degradation)
   ```

### Notes

- The implementation follows the design document specifications exactly
- PostgreSQL is treated as a critical dependency (503 on failure)
- Redis is treated as a non-critical dependency (graceful degradation)
- The endpoint is used by Nginx for health checks in load balancing
- Reconnection attempts are handled by `redis_manager._reconnect_loop()` (every 30s) and database connection pool retry logic (every 10s)
