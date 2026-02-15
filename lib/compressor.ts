// KiroGate 上下文压缩模块
// 三层缓存（增量内存 → LRU 内存 → Deno KV 持久化）+ Factory AI 结构化摘要
import type { ProxyAccount, KiroToolUse } from './types.ts'
import { logger } from './logger.ts'
import { claudeToKiro } from './translator.ts'
import { callKiroApi } from './kiroApi.ts'

// ============ 配置 ============
export interface CompressorConfig {
  enabled: boolean
  autoCompressEnabled: boolean
  tokenThreshold: number
  maxMessagesPerSession: number
  keepMessageCount: number
  toolLookback: number
  batchMessages: number
  batchChars: number
  compressionRatio: number
  ttlMinutes: number
  maxKvEntries: number
}

const DEFAULT_CONFIG: CompressorConfig = {
  enabled: true,
  autoCompressEnabled: true,
  tokenThreshold: 100000,
  maxMessagesPerSession: 200,
  keepMessageCount: 30,
  toolLookback: 8,
  batchMessages: 8,
  batchChars: 40000,
  compressionRatio: 0.15,
  ttlMinutes: 30,
  maxKvEntries: 500
}

let config: CompressorConfig = { ...DEFAULT_CONFIG }

export function getCompressorConfig(): CompressorConfig { return { ...config } }

export function updateCompressorConfig(updates: Partial<CompressorConfig>): CompressorConfig {
  const old = { ...config }
  if (updates.enabled !== undefined) config.enabled = updates.enabled
  if (updates.autoCompressEnabled !== undefined) config.autoCompressEnabled = updates.autoCompressEnabled
  if (updates.tokenThreshold !== undefined) config.tokenThreshold = Math.max(10000, Math.min(1000000, updates.tokenThreshold))
  if (updates.maxMessagesPerSession !== undefined) config.maxMessagesPerSession = Math.max(20, Math.min(2000, updates.maxMessagesPerSession))
  if (updates.keepMessageCount !== undefined) config.keepMessageCount = Math.max(4, Math.min(100, updates.keepMessageCount))
  if (updates.toolLookback !== undefined) config.toolLookback = Math.max(4, Math.min(20, updates.toolLookback))
  if (updates.batchMessages !== undefined) config.batchMessages = Math.max(2, Math.min(20, updates.batchMessages))
  if (updates.batchChars !== undefined) config.batchChars = Math.max(10000, Math.min(100000, updates.batchChars))
  if (updates.compressionRatio !== undefined) config.compressionRatio = Math.max(0.05, Math.min(0.5, updates.compressionRatio))
  if (updates.ttlMinutes !== undefined) config.ttlMinutes = Math.max(1, Math.min(60, updates.ttlMinutes))
  if (updates.maxKvEntries !== undefined) config.maxKvEntries = Math.max(10, Math.min(2000, updates.maxKvEntries))
  const changes: string[] = []
  for (const key of Object.keys(updates) as (keyof CompressorConfig)[]) {
    if (old[key] !== config[key]) changes.push(`${key}: ${old[key]} -> ${config[key]}`)
  }
  if (changes.length > 0) logger.info('Compressor', `Config updated: ${changes.join(', ')}`)
  if (!config.enabled && old.enabled) { clearAllCaches(); logger.info('Compressor', 'Disabled, caches cleared') }
  return { ...config }
}

// ============ Claude 消息类型（简化） ============
interface ClaudeMessage {
  role: 'user' | 'assistant'
  content: string | Array<{ type: string; text?: string; name?: string; id?: string; input?: unknown; content?: unknown; tool_use_id?: string }>
}

// ============ 压缩统计 ============
export interface CompressionStats {
  compressionCount: number; tokensSaved: number
  cacheHits: number; cacheMisses: number; cacheExpired: number
  prefixHits: number; requestCount: number
  incrementalCacheSize: number
}

const stats: CompressionStats = {
  compressionCount: 0, tokensSaved: 0,
  cacheHits: 0, cacheMisses: 0, cacheExpired: 0,
  prefixHits: 0, requestCount: 0, incrementalCacheSize: 0
}

export function getCompressionStats() {
  const total = stats.cacheHits + stats.cacheMisses
  return {
    ...stats,
    hitRate: total > 0 ? Math.round((stats.cacheHits / total) * 100) : 0,
    totalRequests: total,
    enabled: config.enabled,
    memoryCacheEntries: memoryCache.size,
    incrementalCacheSize: incrementalCache.size
  }
}

export function resetCompressionStats(): void {
  stats.compressionCount = 0; stats.tokensSaved = 0
  stats.cacheHits = 0; stats.cacheMisses = 0; stats.cacheExpired = 0
  stats.prefixHits = 0; stats.requestCount = 0
  incrementalCache.clear()
  logger.info('Compressor', 'Stats reset')
}

// ============ Token 估算 ============
function countChinese(text: string): number {
  let count = 0
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i)
    if ((code >= 0x4e00 && code <= 0x9fff) || (code >= 0x3400 && code <= 0x4dbf)) count++
  }
  return count
}

export function estimateTokens(messages: ClaudeMessage[]): number {
  let totalChars = 0, chineseChars = 0
  for (const msg of messages) {
    if (typeof msg.content === 'string') {
      totalChars += msg.content.length; chineseChars += countChinese(msg.content)
    } else if (Array.isArray(msg.content)) {
      for (const block of msg.content) {
        if (block.type === 'text' && block.text) {
          totalChars += block.text.length; chineseChars += countChinese(block.text)
        } else if (block.type === 'tool_use' && block.input) {
          totalChars += JSON.stringify(block.input).length
        } else if (block.type === 'tool_result' && block.content) {
          if (typeof block.content === 'string') {
            totalChars += block.content.length; chineseChars += countChinese(block.content)
          } else if (Array.isArray(block.content)) {
            for (const c of block.content as Array<{ text?: string }>) {
              if (c.text) { totalChars += c.text.length; chineseChars += countChinese(c.text) }
            }
          }
        }
      }
    }
  }
  const otherChars = totalChars - chineseChars
  return Math.ceil(chineseChars / 1.2 + otherChars / 3.5)
}

// ============ 内存 LRU 缓存 ============
interface MemoryCacheEntry { value: string; createdAt: number; ttl: number; size: number }

class MemoryLRUCache {
  private cache = new Map<string, MemoryCacheEntry>()
  private maxSize: number
  private maxEntries: number
  private currentSize = 0

  constructor(maxSize = 100 * 1024 * 1024, maxEntries = 500) {
    this.maxSize = maxSize; this.maxEntries = maxEntries
  }

  get(key: string): string | null {
    const entry = this.cache.get(key)
    if (!entry) return null
    if (Date.now() > entry.createdAt + entry.ttl) {
      this.cache.delete(key); this.currentSize -= entry.size; return null
    }
    // LRU: 移到末尾
    this.cache.delete(key); this.cache.set(key, entry)
    return entry.value
  }

  set(key: string, value: string, ttl: number): void {
    const size = new TextEncoder().encode(value).length
    const existing = this.cache.get(key)
    if (existing) { this.currentSize -= existing.size; this.cache.delete(key) }
    // 淘汰
    while (this.cache.size >= this.maxEntries || this.currentSize + size > this.maxSize) {
      const firstKey = this.cache.keys().next().value
      if (firstKey === undefined) break
      const first = this.cache.get(firstKey)!
      this.cache.delete(firstKey); this.currentSize -= first.size
    }
    this.cache.set(key, { value, createdAt: Date.now(), ttl, size })
    this.currentSize += size
  }

  has(key: string): boolean {
    const entry = this.cache.get(key)
    if (!entry) return false
    return Date.now() <= entry.createdAt + entry.ttl
  }

  clear(): void { this.cache.clear(); this.currentSize = 0 }
  get size(): number { return this.cache.size }

  cleanup(): void {
    const now = Date.now()
    for (const [key, entry] of this.cache) {
      if (now > entry.createdAt + entry.ttl) {
        this.cache.delete(key); this.currentSize -= entry.size
      }
    }
  }
}

const memoryCache = new MemoryLRUCache()

// ============ 增量摘要缓存 ============
interface IncrementalEntry { summary: string; timestamp: number; messageCount: number }
const incrementalCache = new Map<string, IncrementalEntry>()
const MAX_INCREMENTAL_ENTRIES = 100
const MIN_BATCH_SIZE = 3

// ============ 压缩锁 ============
interface CompressionLock { promise: Promise<ClaudeMessage[]>; startTime: number; messageCount: number }
const compressionLocks = new Map<string, CompressionLock>()
const COMPRESSION_TIMEOUT = 120000
const MAX_COMPRESSION_LOCKS = 20

// ============ Factory AI 结构化摘要 ============
interface ArtifactRecord { path: string; action: 'created' | 'modified' | 'deleted' | 'read'; summary: string }
interface AnchoredSummary {
  conversationId: string; anchorMessageIndex: number; timestamp: number
  sessionIntent: string; playByPlay: string[]; artifactTrail: ArtifactRecord[]
  decisions: string[]; breadcrumbs: string[]
}
const anchoredSummaries = new Map<string, AnchoredSummary>()
const MAX_ANCHORED_SUMMARIES = 50

// ============ 哈希工具 ============
async function sha256Short(content: string): Promise<string> {
  const data = new TextEncoder().encode(content)
  const hash = await crypto.subtle.digest('SHA-256', data)
  return Array.from(new Uint8Array(hash)).map(b => b.toString(16).padStart(2, '0')).join('').substring(0, 16)
}

// 同步哈希（用于高频场景，使用简单 FNV-1a）
function hashSync(content: string): string {
  let h = 0x811c9dc5
  for (let i = 0; i < content.length; i++) {
    h ^= content.charCodeAt(i); h = Math.imul(h, 0x01000193)
  }
  return (h >>> 0).toString(16).padStart(8, '0')
}

// ============ Deno KV 持久化缓存 ============
let kvInstance: Deno.Kv | null = null

async function getKv(): Promise<Deno.Kv> {
  if (!kvInstance) kvInstance = await Deno.openKv()
  return kvInstance
}

async function kvGet(key: string): Promise<string | null> {
  try {
    const kv = await getKv()
    const result = await kv.get<{ summary: string; timestamp: number }>(['compressor', 'cache', key])
    if (!result.value) return null
    const age = Date.now() - result.value.timestamp
    if (age > config.ttlMinutes * 60 * 1000) {
      kv.delete(['compressor', 'cache', key]).catch(() => {})
      return null
    }
    return result.value.summary
  } catch { return null }
}

async function kvSet(key: string, summary: string): Promise<void> {
  try {
    const kv = await getKv()
    await kv.set(['compressor', 'cache', key], { summary, timestamp: Date.now() })
  } catch (e) { logger.warn('Compressor', `KV write error: ${(e as Error).message}`) }
}

// ============ 三层缓存读取 ============
async function readCache(key: string): Promise<string | null> {
  stats.requestCount++
  // L1: 增量缓存
  const incr = incrementalCache.get(key)
  if (incr && Date.now() - incr.timestamp < config.ttlMinutes * 60 * 1000) {
    stats.cacheHits++; stats.prefixHits++; return incr.summary
  }
  // L2: LRU 内存缓存
  const mem = memoryCache.get(key)
  if (mem) { stats.cacheHits++; return mem }
  // L3: Deno KV 持久化
  const kv = await kvGet(key)
  if (kv) { stats.cacheHits++; memoryCache.set(key, kv, config.ttlMinutes * 60 * 1000); return kv }
  stats.cacheMisses++; return null
}

async function writeCache(key: string, summary: string): Promise<void> {
  memoryCache.set(key, summary, config.ttlMinutes * 60 * 1000)
  await kvSet(key, summary)
}

// ============ 消息文本提取 ============
function extractMessageText(msg: ClaudeMessage): string {
  if (typeof msg.content === 'string') return msg.content
  if (!Array.isArray(msg.content)) return ''
  const parts: string[] = []
  for (const block of msg.content) {
    if (block.type === 'text' && block.text) parts.push(block.text)
    else if (block.type === 'tool_use' && block.name) parts.push(`[Tool: ${block.name}]`)
    else if (block.type === 'tool_result') {
      if (typeof block.content === 'string') parts.push(`[Result: ${block.content.slice(0, 200)}]`)
      else if (Array.isArray(block.content)) {
        for (const c of block.content as Array<{ text?: string }>) {
          if (c.text) parts.push(`[Result: ${c.text.slice(0, 200)}]`)
        }
      }
    }
  }
  return parts.join('\n')
}

// ============ 工具边界检测 ============
function findToolUseBoundary(messages: ClaudeMessage[], startIdx: number): number {
  for (let i = startIdx; i < messages.length; i++) {
    const msg = messages[i]
    if (msg.role !== 'assistant' || typeof msg.content === 'string') continue
    if (!Array.isArray(msg.content)) continue
    const hasToolUse = msg.content.some(b => b.type === 'tool_use')
    if (hasToolUse) {
      // 确保下一条 tool_result 也包含在内
      if (i + 1 < messages.length) return i + 2
      return i + 1
    }
  }
  return startIdx
}

function findKeepBoundary(messages: ClaudeMessage[], keepCount: number): number {
  const baseKeep = Math.max(config.keepMessageCount, keepCount)
  let boundary = Math.max(0, messages.length - baseKeep)
  // 向前扩展到工具边界
  boundary = findToolUseBoundary(messages, Math.max(0, boundary - config.toolLookback))
  if (boundary <= 0) boundary = Math.max(0, messages.length - baseKeep)
  return boundary
}

// ============ 批次分割 ============
function splitIntoBatches(messages: ClaudeMessage[]): ClaudeMessage[][] {
  const batches: ClaudeMessage[][] = []
  let current: ClaudeMessage[] = []
  let currentChars = 0
  for (const msg of messages) {
    const text = extractMessageText(msg)
    current.push(msg)
    currentChars += text.length
    if (current.length >= config.batchMessages || currentChars >= config.batchChars) {
      // 确保不在工具调用中间断开
      const last = current[current.length - 1]
      if (last.role === 'assistant' && Array.isArray(last.content) && last.content.some(b => b.type === 'tool_use')) {
        // 不切割，继续
        continue
      }
      batches.push(current); current = []; currentChars = 0
    }
  }
  if (current.length > 0) batches.push(current)
  return batches
}

// ============ Factory AI 信息提取 ============
function extractArtifacts(messages: ClaudeMessage[]): ArtifactRecord[] {
  const artifacts: ArtifactRecord[] = []
  const pathPattern = /(?:(?:created?|modif(?:y|ied)|delet(?:e|ed)|read|writ(?:e|ten)|updat(?:e|ed)|edit(?:ed)?)\s+)?(?:file\s+)?[`"']?([a-zA-Z0-9_/\\.-]+\.[a-zA-Z]{1,10})[`"']?/gi
  for (const msg of messages) {
    if (msg.role !== 'assistant') continue
    const text = extractMessageText(msg)
    if (!text) continue
    const matches = text.matchAll(pathPattern)
    for (const match of matches) {
      const path = match[1]
      if (path.length < 3 || path.length > 200) continue
      let action: ArtifactRecord['action'] = 'read'
      const context = match[0].toLowerCase()
      if (context.includes('creat') || context.includes('writ')) action = 'created'
      else if (context.includes('modif') || context.includes('updat') || context.includes('edit')) action = 'modified'
      else if (context.includes('delet')) action = 'deleted'
      if (!artifacts.some(a => a.path === path && a.action === action)) {
        artifacts.push({ path, action, summary: context.slice(0, 100) })
      }
    }
  }
  return artifacts.slice(0, 50)
}

function extractDecisions(messages: ClaudeMessage[]): string[] {
  const decisions: string[] = []
  const patterns = [/decided?\s+to\s+(.{10,100})/gi, /chose?\s+(.{10,100})/gi, /选择了?\s*(.{5,80})/gi, /决定\s*(.{5,80})/gi]
  for (const msg of messages) {
    if (msg.role !== 'assistant') continue
    const text = extractMessageText(msg)
    for (const pattern of patterns) {
      pattern.lastIndex = 0
      const matches = text.matchAll(pattern)
      for (const m of matches) decisions.push(m[1].replace(/[.。,，]$/, '').trim())
    }
  }
  return [...new Set(decisions)].slice(0, 20)
}

function extractBreadcrumbs(messages: ClaudeMessage[]): string[] {
  const crumbs: string[] = []
  for (let i = Math.max(0, messages.length - 6); i < messages.length; i++) {
    const msg = messages[i]
    const text = extractMessageText(msg).slice(0, 150)
    if (text.trim()) crumbs.push(`[${msg.role}] ${text}`)
  }
  return crumbs
}

// ============ 结构化摘要生成 ============
function generateStructuredSummary(conversationId: string, messages: ClaudeMessage[], anchorIdx: number): AnchoredSummary {
  const playByPlay: string[] = []
  for (const msg of messages) {
    const text = extractMessageText(msg).slice(0, 200)
    if (text.trim()) playByPlay.push(`[${msg.role}] ${text}`)
  }
  // 提取会话意图（第一条用户消息）
  let sessionIntent = 'General conversation'
  for (const msg of messages) {
    if (msg.role === 'user') {
      sessionIntent = extractMessageText(msg).slice(0, 300) || sessionIntent
      break
    }
  }
  return {
    conversationId, anchorMessageIndex: anchorIdx, timestamp: Date.now(),
    sessionIntent, playByPlay: playByPlay.slice(0, 50),
    artifactTrail: extractArtifacts(messages),
    decisions: extractDecisions(messages),
    breadcrumbs: extractBreadcrumbs(messages)
  }
}

function formatStructuredSummary(summary: AnchoredSummary): string {
  const parts = [`## Session Intent\n${summary.sessionIntent}`]
  if (summary.playByPlay.length > 0) parts.push(`## Play-by-Play\n${summary.playByPlay.join('\n')}`)
  if (summary.artifactTrail.length > 0) {
    const artifacts = summary.artifactTrail.map(a => `- ${a.action}: ${a.path} (${a.summary})`).join('\n')
    parts.push(`## Artifacts\n${artifacts}`)
  }
  if (summary.decisions.length > 0) parts.push(`## Decisions\n${summary.decisions.map(d => `- ${d}`).join('\n')}`)
  if (summary.breadcrumbs.length > 0) parts.push(`## Recent Context\n${summary.breadcrumbs.join('\n')}`)
  return parts.join('\n\n')
}

// ============ 批次摘要生成（调用 Kiro API） ============
async function generateBatchSummary(
  account: ProxyAccount, batch: ClaudeMessage[], batchIndex: number, existingSummary?: string
): Promise<string> {
  const batchText = batch.map(m => `[${m.role}]: ${extractMessageText(m).slice(0, 1000)}`).join('\n---\n')
  const targetLen = Math.ceil(batchText.length * config.compressionRatio)
  let prompt = `Summarize this conversation segment concisely (target ~${targetLen} chars). Preserve: key decisions, file paths, tool actions, technical details, and user intent.\n\n`
  if (existingSummary) prompt += `Previous context:\n${existingSummary}\n\n`
  prompt += `Conversation:\n${batchText}`

  try {
    const payload = claudeToKiro({
      model: 'claude-haiku-4.5', max_tokens: 2048, stream: false,
      messages: [{ role: 'user', content: prompt }],
      system: 'You are a conversation summarizer. Output only the summary, no preamble.'
    })
    const result = await callKiroApi(account, payload)
    const summary = result.content.trim()
    if (summary) {
      logger.debug('Compressor', `Batch ${batchIndex}: ${batchText.length} -> ${summary.length} chars`)
      return summary
    }
  } catch (e) {
    logger.warn('Compressor', `Batch ${batchIndex} summary failed: ${(e as Error).message}`)
  }
  // 降级：简单截断
  return batchText.slice(0, targetLen) + '...'
}

// ============ 压缩需求检测 ============
export function needsCompression(messages: ClaudeMessage[]): boolean {
  if (!config.enabled || !config.autoCompressEnabled) return false
  if (messages.length > config.maxMessagesPerSession) return true
  return estimateTokens(messages) > config.tokenThreshold
}

// ============ 简单截断（降级方案） ============
export function truncateToMaxMessages(messages: ClaudeMessage[]): ClaudeMessage[] {
  if (messages.length <= config.maxMessagesPerSession) return messages
  const boundary = findKeepBoundary(messages, config.keepMessageCount)
  return messages.slice(boundary)
}

// ============ 主压缩函数 ============
export async function compressHistory(
  account: ProxyAccount, messages: ClaudeMessage[], conversationId?: string
): Promise<ClaudeMessage[]> {
  if (!config.enabled) return messages
  if (!needsCompression(messages)) return messages
  const convId = conversationId || hashSync(JSON.stringify(messages.slice(0, 3)))
  // 检查压缩锁（防止同一会话重复压缩）
  const existingLock = compressionLocks.get(convId)
  if (existingLock) {
    if (Date.now() - existingLock.startTime < COMPRESSION_TIMEOUT) {
      logger.debug('Compressor', `Waiting for existing compression: ${convId.slice(0, 8)}...`)
      try { return await existingLock.promise } catch { return truncateToMaxMessages(messages) }
    }
    compressionLocks.delete(convId)
  }
  // 清理过期锁
  if (compressionLocks.size > MAX_COMPRESSION_LOCKS) {
    const now = Date.now()
    for (const [k, v] of compressionLocks) {
      if (now - v.startTime > COMPRESSION_TIMEOUT) compressionLocks.delete(k)
    }
  }
  const promise = doCompressHistory(account, messages, convId)
  compressionLocks.set(convId, { promise, startTime: Date.now(), messageCount: messages.length })
  try {
    const result = await promise
    compressionLocks.delete(convId)
    return result
  } catch (e) {
    compressionLocks.delete(convId)
    logger.error('Compressor', `Compression failed: ${(e as Error).message}`)
    return truncateToMaxMessages(messages)
  }
}

// ============ 实际压缩逻辑 ============
async function doCompressHistory(
  account: ProxyAccount, messages: ClaudeMessage[], conversationId: string
): Promise<ClaudeMessage[]> {
  const startTime = Date.now()
  const originalTokens = estimateTokens(messages)
  const boundary = findKeepBoundary(messages, config.keepMessageCount)
  if (boundary <= MIN_BATCH_SIZE) return messages
  const toCompress = messages.slice(0, boundary)
  const toKeep = messages.slice(boundary)
  // 尝试缓存命中
  const contentHash = await sha256Short(toCompress.map(m => extractMessageText(m).slice(0, 500)).join('|'))
  const cacheKey = `${conversationId}:${contentHash}`
  const cached = await readCache(cacheKey)
  if (cached) {
    logger.info('Compressor', `Cache hit for ${conversationId.slice(0, 8)}...`)
    const summaryMsg: ClaudeMessage = { role: 'user', content: `[Previous conversation summary]\n${cached}` }
    const ackMsg: ClaudeMessage = { role: 'assistant', content: 'I understand the context. Let me continue.' }
    return [summaryMsg, ackMsg, ...toKeep]
  }
  // 分批压缩
  const batches = splitIntoBatches(toCompress)
  logger.info('Compressor', `Compressing ${toCompress.length} msgs in ${batches.length} batches`)
  // 并行处理（限制并发为 3）
  const CONCURRENCY = 3
  const summaries: string[] = new Array(batches.length).fill('')
  for (let i = 0; i < batches.length; i += CONCURRENCY) {
    const chunk = batches.slice(i, i + CONCURRENCY)
    const results = await Promise.allSettled(
      chunk.map((batch, j) => {
        const idx = i + j
        const prevSummary = idx > 0 ? summaries[idx - 1] : undefined
        return generateBatchSummary(account, batch, idx, prevSummary)
      })
    )
    for (let j = 0; j < results.length; j++) {
      const r = results[j]
      summaries[i + j] = r.status === 'fulfilled' ? r.value : `[Batch ${i + j} summary unavailable]`
    }
  }
  // 生成结构化摘要
  const structured = generateStructuredSummary(conversationId, toCompress, boundary)
  const formattedStructured = formatStructuredSummary(structured)
  const combinedSummary = `${formattedStructured}\n\n## Conversation Summary\n${summaries.join('\n\n')}`
  // 写入缓存
  await writeCache(cacheKey, combinedSummary)
  // 更新增量缓存
  if (incrementalCache.size >= MAX_INCREMENTAL_ENTRIES) {
    const oldest = incrementalCache.keys().next().value
    if (oldest !== undefined) incrementalCache.delete(oldest)
  }
  incrementalCache.set(cacheKey, { summary: combinedSummary, timestamp: Date.now(), messageCount: toCompress.length })
  stats.incrementalCacheSize = incrementalCache.size
  // 保存锚定摘要
  if (anchoredSummaries.size >= MAX_ANCHORED_SUMMARIES) {
    const oldest = anchoredSummaries.keys().next().value
    if (oldest !== undefined) anchoredSummaries.delete(oldest)
  }
  anchoredSummaries.set(conversationId, structured)

  const summaryMsg: ClaudeMessage = { role: 'user', content: `[Previous conversation summary]\n${combinedSummary}` }
  const ackMsg: ClaudeMessage = { role: 'assistant', content: 'I understand the context. Let me continue.' }
  const result = [summaryMsg, ackMsg, ...toKeep]
  const compressedTokens = estimateTokens(result)
  stats.compressionCount++
  stats.tokensSaved += originalTokens - compressedTokens
  logger.info('Compressor', `Compressed: ${messages.length} -> ${result.length} msgs, ${originalTokens} -> ${compressedTokens} tokens (${Math.round((1 - compressedTokens / originalTokens) * 100)}% reduction) in ${Date.now() - startTime}ms`)
  return result
}

// ============ 自动压缩包装器 ============
export async function autoCompress(
  account: ProxyAccount, messages: ClaudeMessage[], conversationId?: string
): Promise<ClaudeMessage[]> {
  if (!config.enabled || !config.autoCompressEnabled) return messages
  if (messages.length <= config.keepMessageCount) return messages
  try { return await compressHistory(account, messages, conversationId) }
  catch { return truncateToMaxMessages(messages) }
}

// ============ 缓存清理 ============
export function clearAllCaches(): void {
  memoryCache.clear()
  incrementalCache.clear()
  anchoredSummaries.clear()
  compressionLocks.clear()
  stats.incrementalCacheSize = 0
  logger.info('Compressor', 'All caches cleared')
}

export async function cleanupExpiredCache(): Promise<number> {
  let cleaned = 0
  memoryCache.cleanup()
  // 清理增量缓存
  const now = Date.now()
  const ttlMs = config.ttlMinutes * 60 * 1000
  for (const [key, entry] of incrementalCache) {
    if (now - entry.timestamp > ttlMs) { incrementalCache.delete(key); cleaned++ }
  }
  // 清理锚定摘要
  for (const [key, summary] of anchoredSummaries) {
    if (now - summary.timestamp > ttlMs * 2) { anchoredSummaries.delete(key); cleaned++ }
  }
  // 清理过期压缩锁
  for (const [key, lock] of compressionLocks) {
    if (now - lock.startTime > COMPRESSION_TIMEOUT) { compressionLocks.delete(key); cleaned++ }
  }
  // 清理 KV 过期条目
  try {
    const kv = await getKv()
    const iter = kv.list<{ summary: string; timestamp: number }>({ prefix: ['compressor', 'cache'] })
    let kvCleaned = 0
    for await (const entry of iter) {
      if (entry.value && now - entry.value.timestamp > ttlMs) {
        await kv.delete(entry.key); kvCleaned++
        if (kvCleaned >= 50) break // 每次最多清理 50 条
      }
    }
    cleaned += kvCleaned
  } catch (e) { logger.warn('Compressor', `KV cleanup error: ${(e as Error).message}`) }
  if (cleaned > 0) logger.info('Compressor', `Cleaned ${cleaned} expired entries`)
  stats.cacheExpired += cleaned
  stats.incrementalCacheSize = incrementalCache.size
  return cleaned
}

export function getCacheInfo() {
  return {
    memoryCacheSize: memoryCache.size,
    incrementalCacheSize: incrementalCache.size,
    anchoredSummaryCount: anchoredSummaries.size,
    activeCompressionLocks: compressionLocks.size,
    config: { ttlMinutes: config.ttlMinutes, maxKvEntries: config.maxKvEntries }
  }
}

// ============ 定期清理定时器 ============
let cleanupTimer: ReturnType<typeof setInterval> | null = null
const CLEANUP_INTERVAL = 5 * 60 * 1000 // 5 分钟

export function startPeriodicCleanup(): void {
  if (cleanupTimer) return
  cleanupTimer = setInterval(() => {
    cleanupExpiredCache().catch(e => logger.warn('Compressor', `Periodic cleanup error: ${(e as Error).message}`))
  }, CLEANUP_INTERVAL)
  logger.info('Compressor', 'Periodic cleanup started (5min interval)')
}

export function stopPeriodicCleanup(): void {
  if (cleanupTimer) { clearInterval(cleanupTimer); cleanupTimer = null }
}

