// KiroGate 令牌桶限流器
import { logger } from './logger.ts'

// ============ 令牌桶 ============
class TokenBucket {
  private tokens: number
  private lastRefill: number
  private readonly maxTokens: number
  private readonly refillRate: number // tokens per second

  constructor(maxTokens: number, refillRatePerMinute: number) {
    this.maxTokens = maxTokens
    this.tokens = maxTokens
    this.refillRate = refillRatePerMinute / 60
    this.lastRefill = Date.now()
  }

  tryConsume(count: number = 1): boolean {
    this.refill()
    if (this.tokens >= count) {
      this.tokens -= count
      return true
    }
    return false
  }

  private refill(): void {
    const now = Date.now()
    const elapsed = (now - this.lastRefill) / 1000
    this.tokens = Math.min(this.maxTokens, this.tokens + elapsed * this.refillRate)
    this.lastRefill = now
  }

  getAvailable(): number {
    this.refill()
    return Math.floor(this.tokens)
  }
}

// ============ 限流管理器 ============
export class RateLimiter {
  private globalBucket: TokenBucket
  private accountBuckets = new Map<string, TokenBucket>()
  private readonly globalRate: number
  private readonly perAccountRate: number
  private readonly burstMultiplier: number
  private cleanupTimer: number | null = null

  constructor(
    globalRatePerMinute: number = 600,
    perAccountRatePerMinute: number = 60,
    burstMultiplier: number = 3
  ) {
    this.globalRate = globalRatePerMinute
    this.perAccountRate = perAccountRatePerMinute
    this.burstMultiplier = burstMultiplier
    // 全局桶：突发容量 = 速率 * 倍率
    this.globalBucket = new TokenBucket(globalRatePerMinute * burstMultiplier, globalRatePerMinute)

    // 定期清理不活跃的账号桶（5分钟）
    this.cleanupTimer = setInterval(() => this.cleanup(), 300000) as unknown as number
  }

  // 检查是否允许请求
  tryAcquire(accountId?: string): { allowed: boolean; reason?: string } {
    // 全局限流
    if (!this.globalBucket.tryConsume()) {
      return { allowed: false, reason: `Global rate limit exceeded (${this.globalRate}/min)` }
    }

    // 每账号限流
    if (accountId) {
      let bucket = this.accountBuckets.get(accountId)
      if (!bucket) {
        bucket = new TokenBucket(this.perAccountRate * this.burstMultiplier, this.perAccountRate)
        this.accountBuckets.set(accountId, bucket)
      }
      if (!bucket.tryConsume()) {
        return { allowed: false, reason: `Account rate limit exceeded (${this.perAccountRate}/min)` }
      }
    }

    return { allowed: true }
  }

  // 获取状态
  getStatus(): { globalAvailable: number; accountBuckets: number } {
    return {
      globalAvailable: this.globalBucket.getAvailable(),
      accountBuckets: this.accountBuckets.size
    }
  }

  // 更新速率
  updateRates(globalRate?: number, perAccountRate?: number): void {
    if (globalRate) {
      this.globalBucket = new TokenBucket(globalRate * this.burstMultiplier, globalRate)
    }
    if (perAccountRate) {
      this.accountBuckets.clear() // 重建所有桶
    }
  }

  // 清理不活跃桶
  private cleanup(): void {
    // 简单策略：如果桶数超过 200，清空所有（下次请求会重建）
    if (this.accountBuckets.size > 200) {
      logger.info('RateLimiter', `Cleaning up ${this.accountBuckets.size} account buckets`)
      this.accountBuckets.clear()
    }
  }

  destroy(): void {
    if (this.cleanupTimer) {
      clearInterval(this.cleanupTimer)
      this.cleanupTimer = null
    }
    this.accountBuckets.clear()
  }
}
