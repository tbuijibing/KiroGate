// KiroGate 错误处理 + 熔断器
import type { ErrorType, CircuitState } from './types.ts'
import { logger } from './logger.ts'

// ============ 错误分类 ============
export interface ClassifiedError {
  type: ErrorType
  message: string
  retryable: boolean
  shouldRefreshToken: boolean
  shouldDisableAccount: boolean
  suggestedDelayMs: number
}

export function classifyError(error: Error | string, statusCode?: number): ClassifiedError {
  const msg = typeof error === 'string' ? error : error.message || ''
  const lower = msg.toLowerCase()

  // 封禁/暂停
  if (lower.includes('suspended') || lower.includes('banned') || lower.includes('not authorized')) {
    return { type: 'BANNED', message: msg, retryable: false, shouldRefreshToken: false, shouldDisableAccount: true, suggestedDelayMs: 0 }
  }

  // 配额耗尽
  if (statusCode === 402 || lower.includes('quota') || lower.includes('exhausted') || lower.includes('quota_exhausted')) {
    return { type: 'QUOTA', message: msg, retryable: false, shouldRefreshToken: false, shouldDisableAccount: false, suggestedDelayMs: 0 }
  }

  // 认证错误
  if (statusCode === 401 || statusCode === 403 || lower.includes('auth') || lower.includes('token') || lower.includes('unauthorized')) {
    return { type: 'AUTH', message: msg, retryable: true, shouldRefreshToken: true, shouldDisableAccount: false, suggestedDelayMs: 1000 }
  }

  // 限流
  if (statusCode === 429 || lower.includes('rate limit') || lower.includes('too many')) {
    return { type: 'RATE_LIMIT', message: msg, retryable: true, shouldRefreshToken: false, shouldDisableAccount: false, suggestedDelayMs: 2000 }
  }

  // 内容过长
  if (lower.includes('too long') || lower.includes('content_length') || lower.includes('context_length')) {
    return { type: 'CONTENT_TOO_LONG', message: msg, retryable: false, shouldRefreshToken: false, shouldDisableAccount: false, suggestedDelayMs: 0 }
  }

  // 无效模型
  if (lower.includes('model') && (lower.includes('not found') || lower.includes('not available') || lower.includes('invalid'))) {
    return { type: 'INVALID_MODEL', message: msg, retryable: false, shouldRefreshToken: false, shouldDisableAccount: false, suggestedDelayMs: 0 }
  }
  // 客户端错误
  if (statusCode === 400 || lower.includes('bad request') || lower.includes('invalid')) {
    return { type: 'CLIENT', message: msg, retryable: false, shouldRefreshToken: false, shouldDisableAccount: false, suggestedDelayMs: 0 }
  }

  // 服务器错误
  if (statusCode && statusCode >= 500) {
    return { type: 'SERVER', message: msg, retryable: true, shouldRefreshToken: false, shouldDisableAccount: false, suggestedDelayMs: 1000 }
  }

  // 网络错误
  const networkErrors = ['ECONNRESET', 'ETIMEDOUT', 'ENOTFOUND', 'EAI_AGAIN', 'EPIPE', 'ECONNREFUSED', 'fetch failed', 'timeout', 'aborted']
  if (networkErrors.some(e => lower.includes(e.toLowerCase()))) {
    return { type: 'NETWORK', message: msg, retryable: true, shouldRefreshToken: false, shouldDisableAccount: false, suggestedDelayMs: 500 }
  }

  return { type: 'UNKNOWN', message: msg, retryable: true, shouldRefreshToken: false, shouldDisableAccount: false, suggestedDelayMs: 1000 }
}

// ============ 熔断器 ============
export class CircuitBreaker {
  private state: CircuitState = 'CLOSED'
  private failureCount = 0
  private successCount = 0
  private lastFailTime = 0
  private readonly failureThreshold: number
  private readonly resetTimeoutMs: number
  private readonly halfOpenMaxAttempts: number

  constructor(
    failureThreshold: number = 5,
    resetTimeoutMs: number = 30000,
    halfOpenMaxAttempts: number = 3
  ) {
    this.failureThreshold = failureThreshold
    this.resetTimeoutMs = resetTimeoutMs
    this.halfOpenMaxAttempts = halfOpenMaxAttempts
  }

  // 检查是否允许请求通过
  canExecute(): boolean {
    switch (this.state) {
      case 'CLOSED':
        return true
      case 'OPEN': {
        // 检查是否到了重试时间
        if (Date.now() - this.lastFailTime >= this.resetTimeoutMs) {
          this.state = 'HALF_OPEN'
          this.successCount = 0
          logger.info('CircuitBreaker', `State: OPEN -> HALF_OPEN`)
          return true
        }
        return false
      }
      case 'HALF_OPEN':
        return true
      default:
        return true
    }
  }

  // 记录成功
  recordSuccess(): void {
    switch (this.state) {
      case 'HALF_OPEN':
        this.successCount++
        if (this.successCount >= this.halfOpenMaxAttempts) {
          this.state = 'CLOSED'
          this.failureCount = 0
          logger.info('CircuitBreaker', `State: HALF_OPEN -> CLOSED (recovered)`)
        }
        break
      case 'CLOSED':
        this.failureCount = Math.max(0, this.failureCount - 1)
        break
    }
  }

  // 记录失败
  recordFailure(): void {
    this.lastFailTime = Date.now()
    switch (this.state) {
      case 'CLOSED':
        this.failureCount++
        if (this.failureCount >= this.failureThreshold) {
          this.state = 'OPEN'
          logger.warn('CircuitBreaker', `State: CLOSED -> OPEN (${this.failureCount} failures)`)
        }
        break
      case 'HALF_OPEN':
        this.state = 'OPEN'
        logger.warn('CircuitBreaker', `State: HALF_OPEN -> OPEN (failure during probe)`)
        break
    }
  }

  getState(): CircuitState {
    return this.state
  }

  getStats(): { state: CircuitState; failures: number; lastFailTime: number } {
    return { state: this.state, failures: this.failureCount, lastFailTime: this.lastFailTime }
  }

  reset(): void {
    this.state = 'CLOSED'
    this.failureCount = 0
    this.successCount = 0
    this.lastFailTime = 0
  }
}
