// KiroGate 多账号智能调度池
// 移植自源项目 accountPool.ts，适配 Deno 环境
import type { ProxyAccount, AccountStats, LoadBalancingMode } from './types.ts'
import { logger } from './logger.ts'

export interface AccountPoolConfig {
  cooldownMs: number
  maxErrorCount: number
  autoResetIntervalMs: number
  healthScoreDecay: number
  healthScoreRecovery: number
}

export type QuotaExhaustedCallback = (accountId: string, email?: string, reason?: 'banned' | 'quota') => void

const DEFAULT_CONFIG: AccountPoolConfig = {
  cooldownMs: 60000,
  maxErrorCount: 5,
  autoResetIntervalMs: 300000,
  healthScoreDecay: 20,
  healthScoreRecovery: 10
}

interface AccountHealth {
  score: number
  successRate: number
  avgResponseTime: number
  recentErrors: number
  lastSuccessTime: number
}

export class AccountPool {
  private accounts = new Map<string, ProxyAccount>()
  private accountStats = new Map<string, AccountStats>()
  private accountHealth = new Map<string, AccountHealth>()
  private inflightRequests = new Map<string, number>()
  private recentRequestWindows = new Map<string, number[]>()
  private readonly WINDOW_SIZE_MS = 300000
  private currentIndex = 0
  private config: AccountPoolConfig
  private lastAutoReset = 0
  private loadBalancingMode: LoadBalancingMode = 'smart'
  private onQuotaExhausted?: QuotaExhaustedCallback

  constructor(config: Partial<AccountPoolConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config }
  }

  setQuotaExhaustedCallback(cb: QuotaExhaustedCallback): void {
    this.onQuotaExhausted = cb
  }

  // 添加账号
  addAccount(account: ProxyAccount): void {
    this.accounts.set(account.id, {
      ...account,
      isAvailable: true,
      requestCount: 0,
      errorCount: 0,
      lastUsed: 0
    })
    this.accountStats.set(account.id, {
      requests: 0, tokens: 0, inputTokens: 0, outputTokens: 0,
      errors: 0, lastUsed: 0, avgResponseTime: 0, totalResponseTime: 0
    })
    this.accountHealth.set(account.id, {
      score: 100, successRate: 1.0, avgResponseTime: 0,
      recentErrors: 0, lastSuccessTime: Date.now()
    })
    this.inflightRequests.set(account.id, 0)
    this.recentRequestWindows.set(account.id, [])
    logger.info('AccountPool', `Added account: ${account.email || account.id}`)
  }

  removeAccount(accountId: string): void {
    this.accounts.delete(accountId)
    this.accountStats.delete(accountId)
    this.accountHealth.delete(accountId)
    this.inflightRequests.delete(accountId)
    this.recentRequestWindows.delete(accountId)
    logger.info('AccountPool', `Removed account: ${accountId}`)
  }

  updateAccount(accountId: string, updates: Partial<ProxyAccount>): void {
    const account = this.accounts.get(accountId)
    if (account) {
      if (updates.accessToken || updates.expiresAt) {
        updates.isAvailable = true
        updates.errorCount = 0
        updates.cooldownUntil = undefined
      }
      this.accounts.set(accountId, { ...account, ...updates })
      logger.info('AccountPool', `Updated account: ${account.email || accountId}`)
    }
  }

  // Free 账号不支持 Opus
  private supportsModel(account: ProxyAccount, model?: string): boolean {
    if (!model) return true
    const isOpus = model.toLowerCase().includes('opus')
    if (!isOpus) return true
    if (account.subscriptionType && account.subscriptionType === 'Free') return false
    return true
  }

  // 核心调度：零空档期版本
  getNextAccount(model?: string): ProxyAccount | null {
    const accountList = Array.from(this.accounts.values())
    if (accountList.length === 0) {
      logger.warn('AccountPool', 'No accounts in pool')
      return null
    }
    const now = Date.now()

    // 单账号快速路径
    if (accountList.length === 1) {
      const account = accountList[0]
      const availability = this.checkAccountAvailability(account, now)
      if (availability.available) {
        this.acquireAccount(account.id)
        return account
      }
      logger.warn('AccountPool', `Single account unavailable (${availability.reason}), forcing use`)
      this.accounts.set(account.id, {
        ...account, isAvailable: true,
        errorCount: Math.max(0, (account.errorCount || 0) - 1),
        cooldownUntil: undefined
      })
      this.acquireAccount(account.id)
      return account
    }

    this.checkAutoReset(now)

    const availableAccounts = accountList.filter(a =>
      this.checkAccountAvailability(a, now).available && this.supportsModel(a, model)
    )

    if (availableAccounts.length === 0) {
      const fallback = this.zeroDowntimeFallback(accountList, now)
      if (fallback) this.acquireAccount(fallback.id)
      return fallback
    }

    let selected: ProxyAccount
    switch (this.loadBalancingMode) {
      case 'priority':
        selected = this.selectByPriority(availableAccounts)
        break
      case 'balanced':
        selected = this.selectByLeastUsed(availableAccounts)
        break
      case 'smart':
      default:
        selected = this.selectBySmart(availableAccounts, now)
        break
    }
    this.acquireAccount(selected.id)
    return selected
  }

  private acquireAccount(accountId: string): void {
    const current = this.inflightRequests.get(accountId) || 0
    this.inflightRequests.set(accountId, current + 1)
    this.recordRequestToWindow(accountId)
  }

  releaseAccount(accountId: string): void {
    const current = this.inflightRequests.get(accountId) || 0
    this.inflightRequests.set(accountId, Math.max(0, current - 1))
  }

  getInflight(accountId: string): number {
    return this.inflightRequests.get(accountId) || 0
  }

  private getRecentRequestCount(accountId: string): number {
    const window = this.recentRequestWindows.get(accountId)
    if (!window || window.length === 0) return 0
    const cutoff = Date.now() - this.WINDOW_SIZE_MS
    const recent = window.filter(t => t > cutoff)
    if (recent.length !== window.length) this.recentRequestWindows.set(accountId, recent)
    return recent.length
  }

  private recordRequestToWindow(accountId: string): void {
    const window = this.recentRequestWindows.get(accountId) || []
    window.push(Date.now())
    if (window.length > 200) {
      const cutoff = Date.now() - this.WINDOW_SIZE_MS
      this.recentRequestWindows.set(accountId, window.filter(t => t > cutoff))
    } else {
      this.recentRequestWindows.set(accountId, window)
    }
  }

  private selectByPriority(availableAccounts: ProxyAccount[]): ProxyAccount {
    const selected = availableAccounts[0]
    logger.debug('AccountPool', `Priority mode: selected ${selected.email || selected.id}`)
    return selected
  }

  private selectByLeastUsed(availableAccounts: ProxyAccount[]): ProxyAccount {
    let bestScore = Infinity
    let selected = availableAccounts[0]
    for (const account of availableAccounts) {
      const recentRequests = this.getRecentRequestCount(account.id)
      const inflight = this.inflightRequests.get(account.id) || 0
      const score = inflight * 1000 + recentRequests
      if (score < bestScore) { bestScore = score; selected = account }
    }
    logger.debug('AccountPool', `Balanced mode: selected ${selected.email || selected.id}`)
    return selected
  }

  private selectBySmart(availableAccounts: ProxyAccount[], now: number): ProxyAccount {
    if (availableAccounts.length > 1) {
      let totalRecentRequests = 0
      for (const acc of availableAccounts) totalRecentRequests += this.getRecentRequestCount(acc.id)
      const avgRecentRequests = totalRecentRequests / availableAccounts.length

      const scored = availableAccounts.map(account => {
        const health = this.accountHealth.get(account.id)
        const stats = this.accountStats.get(account.id)
        const recentRequests = this.getRecentRequestCount(account.id)
        const inflight = this.inflightRequests.get(account.id) || 0
        let score = health?.score || 50
        score -= inflight * 30
        if (avgRecentRequests > 0) {
          const usageRatio = recentRequests / avgRecentRequests
          score += Math.max(-40, 40 * (1 - usageRatio))
        } else if (recentRequests === 0) {
          score += 30
        }
        const timeSinceLastUse = now - (account.lastUsed || 0)
        if (timeSinceLastUse > 30000) score += Math.min(20, timeSinceLastUse / 60000 * 5)
        if (stats && stats.avgResponseTime > 0 && stats.avgResponseTime < 5000) score += 10
        if (account.expiresAt) {
          const timeToExpiry = account.expiresAt - now
          if (timeToExpiry < 300000) score -= 15
          else if (timeToExpiry < 600000) score -= 5
        }
        return { account, score, inflight, recentRequests }
      })

      scored.sort((a, b) => b.score - a.score)
      const bestScore = scored[0].score
      const threshold = Math.max(5, Math.abs(bestScore) * 0.15)
      const topCandidates = scored.filter(s => s.score >= bestScore - threshold)
      const selected = topCandidates[Math.floor(Math.random() * topCandidates.length)]
      logger.debug('AccountPool', `Smart: ${selected.account.email || selected.account.id} (score:${selected.score.toFixed(1)} inflight:${selected.inflight} candidates:${topCandidates.length}/${scored.length})`)
      return selected.account
    }
    return availableAccounts[0]
  }

  // 零空档期降级
  private zeroDowntimeFallback(accountList: ProxyAccount[], now: number): ProxyAccount {
    logger.warn('AccountPool', 'No fully available accounts, applying zero-downtime fallback')

    // 优先级1：冷却时间最短的
    const shortestCooldown = this.getAccountWithShortestCooldown(accountList, now)
    if (shortestCooldown?.cooldownUntil && (shortestCooldown.cooldownUntil - now) < 5000) {
      logger.warn('AccountPool', `Using ${shortestCooldown.email || shortestCooldown.id} (cooldown ends in ${Math.round((shortestCooldown.cooldownUntil - now) / 1000)}s)`)
      this.accounts.set(shortestCooldown.id, { ...shortestCooldown, isAvailable: true, cooldownUntil: undefined })
      return shortestCooldown
    }

    // 优先级2：错误次数最少的（排除 disabled 和 quotaExhausted）
    const leastErrors = accountList
      .filter(a => !a.disabled && !a.quotaExhausted && (a.errorCount || 0) < this.config.maxErrorCount + 2)
      .sort((a, b) => (a.errorCount || 0) - (b.errorCount || 0))[0]
    if (leastErrors) {
      logger.warn('AccountPool', `Using ${leastErrors.email || leastErrors.id} with ${leastErrors.errorCount || 0} errors (zero-downtime)`)
      this.accounts.set(leastErrors.id, {
        ...leastErrors, isAvailable: true,
        errorCount: Math.max(0, (leastErrors.errorCount || 0) - 1),
        cooldownUntil: undefined
      })
      return leastErrors
    }

    // 优先级3：强制使用任意账号
    const usableAccounts = accountList.filter(a => !a.disabled && !a.quotaExhausted)
    const forceAccount = usableAccounts.length > 0 ? usableAccounts[0] : accountList[0]
    if (!forceAccount) {
      logger.error('AccountPool', 'CRITICAL: No accounts available at all')
      return accountList[0]
    }
    if (forceAccount.disabled || forceAccount.quotaExhausted) {
      logger.error('AccountPool', `CRITICAL: ALL accounts disabled/exhausted, forcing ${forceAccount.email || forceAccount.id}`)
    } else {
      logger.error('AccountPool', `CRITICAL: Forcing ${forceAccount.email || forceAccount.id} to prevent downtime`)
    }
    this.accounts.set(forceAccount.id, { ...forceAccount, isAvailable: true, errorCount: 0, cooldownUntil: undefined })
    return forceAccount
  }

  private checkAccountAvailability(account: ProxyAccount, now: number): { available: boolean; reason: string } {
    if (account.disabled) return { available: false, reason: 'disabled by user' }
    if (account.quotaExhausted) return { available: false, reason: 'quota exhausted' }
    if (account.cooldownUntil && account.cooldownUntil > now) {
      return { available: false, reason: `cooldown (${Math.round((account.cooldownUntil - now) / 1000)}s)` }
    }
    if ((account.errorCount || 0) >= this.config.maxErrorCount) {
      return { available: false, reason: `too many errors (${account.errorCount}/${this.config.maxErrorCount})` }
    }
    if (account.expiresAt && account.expiresAt < now) {
      const expiredMs = now - account.expiresAt
      if (expiredMs > 30000) return { available: false, reason: `token expired ${Math.round(expiredMs / 1000)}s ago` }
    }
    if (account.isAvailable === false) return { available: false, reason: 'needs refresh' }
    return { available: true, reason: 'ok' }
  }

  private checkAutoReset(now: number): void {
    if (now - this.lastAutoReset < this.config.autoResetIntervalMs) return
    const accountList = Array.from(this.accounts.values())
    const availableCount = accountList.filter(a => this.checkAccountAvailability(a, now).available).length
    if (availableCount === 0 && accountList.length > 0) {
      logger.warn('AccountPool', `All ${accountList.length} accounts unavailable, attempting self-heal...`)
      if (!this.selfHeal()) {
        logger.warn('AccountPool', 'Self-heal not applicable, performing full reset')
        this.reset()
      }
      this.lastAutoReset = now
    }
  }

  private getAccountWithShortestCooldown(accounts: ProxyAccount[], now: number): ProxyAccount | null {
    let bestAccount: ProxyAccount | null = null
    let shortestWait = Infinity
    for (const account of accounts) {
      const wait = Math.max(0, (account.cooldownUntil || 0) - now)
      if (wait < shortestWait) { shortestWait = wait; bestAccount = account }
    }
    return bestAccount
  }

  // ============ 公共查询方法 ============
  getAccount(accountId: string): ProxyAccount | null {
    return this.accounts.get(accountId) || null
  }

  getNextAvailableAccount(excludeAccountId: string): ProxyAccount | null {
    const accountList = Array.from(this.accounts.values())
    if (accountList.length <= 1) return null
    const now = Date.now()
    for (const account of accountList) {
      if (account.id !== excludeAccountId && this.checkAccountAvailability(account, now).available) return account
    }
    return this.getAccountWithShortestCooldown(accountList.filter(a => a.id !== excludeAccountId), now)
  }

  getAllAccounts(): ProxyAccount[] {
    return Array.from(this.accounts.values())
  }

  getQuotaExhaustedAccounts(): ProxyAccount[] {
    return Array.from(this.accounts.values()).filter(a => a.quotaExhausted)
  }

  // ============ 记录成功/失败 ============
  recordSuccess(accountId: string, tokens: number = 0, responseTime: number = 0): void {
    this.releaseAccount(accountId)
    const account = this.accounts.get(accountId)
    if (account) {
      this.accounts.set(accountId, {
        ...account, requestCount: (account.requestCount || 0) + 1,
        errorCount: 0, lastUsed: Date.now(), isAvailable: true, cooldownUntil: undefined
      })
    }
    const stats = this.accountStats.get(accountId)
    if (stats) {
      const newTotalTime = stats.totalResponseTime + responseTime
      const newRequests = stats.requests + 1
      this.accountStats.set(accountId, {
        ...stats, requests: newRequests, tokens: stats.tokens + tokens,
        lastUsed: Date.now(), totalResponseTime: newTotalTime,
        avgResponseTime: newTotalTime / newRequests
      })
    }
    const health = this.accountHealth.get(accountId)
    if (health) {
      const totalRequests = (stats?.requests || 0) + 1
      const totalErrors = stats?.errors || 0
      this.accountHealth.set(accountId, {
        ...health, score: Math.min(100, health.score + this.config.healthScoreRecovery),
        successRate: (totalRequests - totalErrors) / totalRequests,
        avgResponseTime: stats?.avgResponseTime || responseTime,
        recentErrors: Math.max(0, health.recentErrors - 1),
        lastSuccessTime: Date.now()
      })
    }
  }

  recordError(accountId: string, errorType: 'network' | 'quota' | 'auth' | 'banned' | 'other' = 'other'): void {
    this.releaseAccount(accountId)
    const account = this.accounts.get(accountId)
    if (!account) return
    const now = Date.now()

    // 网络错误不计入错误计数
    if (errorType === 'network') {
      const stats = this.accountStats.get(accountId)
      if (stats) this.accountStats.set(accountId, { ...stats, errors: stats.errors + 1, lastUsed: now })
      return
    }

    const errorCount = (account.errorCount || 0) + 1
    let cooldownUntil = account.cooldownUntil || 0
    let isAvailable = account.isAvailable !== false
    let quotaExhausted = account.quotaExhausted || false

    if (errorType === 'banned') {
      this.accounts.set(accountId, {
        ...account, errorCount, cooldownUntil, isAvailable: false,
        quotaExhausted: true, disabled: true, lastUsed: now
      })
      logger.error('AccountPool', `Account ${account.email || accountId} BANNED, permanently disabled`)
      if (this.onQuotaExhausted) this.onQuotaExhausted(accountId, account.email, 'banned')
      this.updateHealthOnError(accountId, 50)
      const stats = this.accountStats.get(accountId)
      if (stats) this.accountStats.set(accountId, { ...stats, errors: stats.errors + 1, lastUsed: now })
      return
    } else if (errorType === 'quota') {
      quotaExhausted = true; isAvailable = false
      logger.info('AccountPool', `Account ${account.email || accountId} quota exhausted`)
      if (this.onQuotaExhausted) this.onQuotaExhausted(accountId, account.email, 'quota')
    } else if (errorType === 'auth') {
      isAvailable = false
      logger.info('AccountPool', `Account ${account.email || accountId} auth error, marking for refresh`)
    } else if (errorCount >= this.config.maxErrorCount) {
      cooldownUntil = now + this.config.cooldownMs
      logger.info('AccountPool', `Account ${account.email || accountId} too many errors (${errorCount}), cooldown`)
    }

    this.accounts.set(accountId, { ...account, errorCount, cooldownUntil, isAvailable, quotaExhausted, lastUsed: now })
    const stats = this.accountStats.get(accountId)
    if (stats) this.accountStats.set(accountId, { ...stats, errors: stats.errors + 1, lastUsed: now })

    let decay = this.config.healthScoreDecay
    if (errorType === 'quota') decay = 30
    else if (errorType === 'auth') decay = 40
    this.updateHealthOnError(accountId, decay)
  }

  private updateHealthOnError(accountId: string, decay: number): void {
    const health = this.accountHealth.get(accountId)
    if (health) {
      this.accountHealth.set(accountId, {
        ...health, score: Math.max(0, health.score - decay),
        recentErrors: health.recentErrors + 1
      })
    }
  }

  // ============ Token 刷新 ============
  markNeedsRefresh(accountId: string): void {
    const account = this.accounts.get(accountId)
    if (account) {
      this.accounts.set(accountId, { ...account, isAvailable: false })
      logger.info('AccountPool', `Account ${account.email || accountId} marked as needs refresh`)
    }
  }

  markRefreshComplete(accountId: string, success: boolean, usage?: { current: number; limit: number }, permanent?: boolean): void {
    const account = this.accounts.get(accountId)
    if (!account) return
    if (success) {
      let quotaRecovered = false
      if (account.quotaExhausted && usage) {
        const remaining = usage.limit - usage.current
        if (remaining > 0) {
          quotaRecovered = true
          logger.info('AccountPool', `Account ${account.email || accountId} quota recovered! (${remaining.toFixed(2)} remaining)`)
        }
      }
      this.accounts.set(accountId, {
        ...account, isAvailable: true, errorCount: 0, cooldownUntil: undefined,
        quotaExhausted: account.quotaExhausted && !quotaRecovered
      })
    } else {
      if (permanent) {
        this.accounts.set(accountId, { ...account, isAvailable: false, disabled: true, quotaExhausted: true })
        logger.error('AccountPool', `Account ${account.email || accountId} refresh permanently failed, disabled`)
      } else {
        this.accounts.set(accountId, { ...account, isAvailable: false, cooldownUntil: Date.now() + 30000 })
        logger.info('AccountPool', `Account ${account.email || accountId} refresh failed, cooldown 30s`)
      }
    }
  }

  checkQuotaRecovery(accountId: string, usage: { current: number; limit: number }): boolean {
    const account = this.accounts.get(accountId)
    if (!account || !account.quotaExhausted) return false
    const remaining = usage.limit - usage.current
    if (remaining > 0) {
      this.accounts.set(accountId, {
        ...account, isAvailable: true, errorCount: 0, cooldownUntil: undefined, quotaExhausted: false
      })
      logger.info('AccountPool', `Account ${account.email || accountId} quota recovered, rejoined rotation`)
      return true
    }
    return false
  }

  // ============ 自愈 & 重置 ============
  selfHeal(): boolean {
    const accountList = Array.from(this.accounts.values())
    if (accountList.length === 0) return false
    const now = Date.now()
    const unavailable = accountList.filter(a => !this.checkAccountAvailability(a, now).available)
    if (unavailable.length < accountList.length) return false
    const errorDisabled = accountList.filter(a =>
      !a.disabled && !a.quotaExhausted && (a.errorCount || 0) >= this.config.maxErrorCount
    )
    if (errorDisabled.length === 0) return false
    logger.warn('AccountPool', `Self-healing: resetting ${errorDisabled.length} error-disabled accounts`)
    for (const account of errorDisabled) {
      this.accounts.set(account.id, {
        ...account, isAvailable: true,
        errorCount: Math.floor((account.errorCount || 0) / 2),
        cooldownUntil: undefined
      })
      const health = this.accountHealth.get(account.id)
      if (health) {
        this.accountHealth.set(account.id, {
          ...health, score: Math.max(50, health.score),
          recentErrors: Math.floor(health.recentErrors / 2)
        })
      }
    }
    return true
  }

  reset(): void {
    logger.info('AccountPool', `Resetting all ${this.accounts.size} accounts`)
    for (const [id, account] of this.accounts) {
      this.accounts.set(id, { ...account, isAvailable: true, errorCount: 0, cooldownUntil: undefined })
      const health = this.accountHealth.get(id)
      if (health) this.accountHealth.set(id, { ...health, score: 80, recentErrors: 0 })
    }
    this.currentIndex = 0
  }

  clear(): void {
    this.accounts.clear()
    this.accountStats.clear()
    this.accountHealth.clear()
    this.inflightRequests.clear()
    this.recentRequestWindows.clear()
    this.currentIndex = 0
  }

  // ============ 统计 & 诊断 ============
  getStats(): { accounts: Map<string, AccountStats>; total: { requests: number; tokens: number; errors: number } } {
    let totalRequests = 0, totalTokens = 0, totalErrors = 0
    for (const stats of this.accountStats.values()) {
      totalRequests += stats.requests; totalTokens += stats.tokens; totalErrors += stats.errors
    }
    return { accounts: new Map(this.accountStats), total: { requests: totalRequests, tokens: totalTokens, errors: totalErrors } }
  }

  getHealthReport() {
    const accountList = Array.from(this.accounts.values())
    const now = Date.now()
    let availableCount = 0, healthyCount = 0, degradedCount = 0, unhealthyCount = 0, totalScore = 0
    for (const account of accountList) {
      if (this.checkAccountAvailability(account, now).available) availableCount++
      const health = this.accountHealth.get(account.id)
      if (health) {
        totalScore += health.score
        if (health.score >= 80) healthyCount++
        else if (health.score >= 50) degradedCount++
        else unhealthyCount++
      }
    }
    return {
      totalAccounts: accountList.length, availableAccounts: availableCount,
      healthyAccounts: healthyCount, degradedAccounts: degradedCount,
      unhealthyAccounts: unhealthyCount,
      averageHealthScore: accountList.length > 0 ? totalScore / accountList.length : 0
    }
  }

  getDiagnostics() {
    const now = Date.now()
    const accounts = Array.from(this.accounts.values()).map(account => {
      const availability = this.checkAccountAvailability(account, now)
      const health = this.accountHealth.get(account.id)
      const stats = this.accountStats.get(account.id)
      return {
        id: account.id, email: account.email,
        status: availability.available ? 'available' : availability.reason,
        healthScore: health?.score, successRate: health?.successRate,
        avgResponseTime: stats?.avgResponseTime,
        errorCount: account.errorCount, lastUsed: account.lastUsed
      }
    }).sort((a, b) => (b.healthScore || 0) - (a.healthScore || 0))
    return { total: this.accounts.size, available: this.availableCount, accounts }
  }

  get size(): number { return this.accounts.size }

  get availableCount(): number {
    const now = Date.now()
    let count = 0
    for (const account of this.accounts.values()) {
      if (this.checkAccountAvailability(account, now).available) count++
    }
    return count
  }

  setLoadBalancingMode(mode: LoadBalancingMode): void {
    this.loadBalancingMode = mode
    logger.info('AccountPool', `Load balancing mode: ${mode}`)
  }

  getLoadBalancingMode(): LoadBalancingMode { return this.loadBalancingMode }

  getAccountHealth(accountId: string): AccountHealth | undefined {
    return this.accountHealth.get(accountId)
  }
}
