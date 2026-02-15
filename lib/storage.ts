// KiroGate 存储层 - Deno KV 持久化 + 内存缓存
import type { ProxyAccount, ProxyConfig, ApiKey, AppSettings, ProxyStats, RequestLog } from './types.ts'
import { logger } from './logger.ts'

// Deno KV 实例（延迟初始化）
let kv: Deno.Kv | null = null

async function getKV(): Promise<Deno.Kv> {
  if (!kv) {
    kv = await Deno.openKv()
    logger.info('Storage', 'Deno KV initialized')
  }
  return kv
}

// ============ 内存缓存层 ============
const cache = {
  accounts: new Map<string, ProxyAccount>(),
  apiKeys: new Map<string, ApiKey>(),
  settings: null as AppSettings | null,
  proxyConfig: null as ProxyConfig | null,
  initialized: false
}

// ============ 账号管理 ============
export async function getAccount(id: string): Promise<ProxyAccount | null> {
  if (cache.accounts.has(id)) return cache.accounts.get(id)!
  const db = await getKV()
  const result = await db.get<ProxyAccount>(['accounts', id])
  if (result.value) {
    cache.accounts.set(id, result.value)
  }
  return result.value
}

export async function setAccount(account: ProxyAccount): Promise<void> {
  cache.accounts.set(account.id, account)
  const db = await getKV()
  await db.set(['accounts', account.id], account)
}

export async function deleteAccount(id: string): Promise<void> {
  cache.accounts.delete(id)
  const db = await getKV()
  await db.delete(['accounts', id])
}

export async function getAllAccounts(): Promise<ProxyAccount[]> {
  if (cache.initialized && cache.accounts.size > 0) {
    return Array.from(cache.accounts.values())
  }
  const db = await getKV()
  const accounts: ProxyAccount[] = []
  for await (const entry of db.list<ProxyAccount>({ prefix: ['accounts'] })) {
    if (entry.value) {
      accounts.push(entry.value)
      cache.accounts.set(entry.value.id, entry.value)
    }
  }
  return accounts
}

// ============ API Key 管理 ============
export async function getApiKey(id: string): Promise<ApiKey | null> {
  if (cache.apiKeys.has(id)) return cache.apiKeys.get(id)!
  const db = await getKV()
  const result = await db.get<ApiKey>(['apikeys', id])
  if (result.value) cache.apiKeys.set(id, result.value)
  return result.value
}

export async function getApiKeyByKey(key: string): Promise<ApiKey | null> {
  // 先查缓存
  for (const ak of cache.apiKeys.values()) {
    if (ak.key === key) return ak
  }
  // 查 KV
  const all = await getAllApiKeys()
  return all.find(ak => ak.key === key) || null
}

export async function setApiKey(apiKey: ApiKey): Promise<void> {
  cache.apiKeys.set(apiKey.id, apiKey)
  const db = await getKV()
  await db.set(['apikeys', apiKey.id], apiKey)
}

export async function deleteApiKey(id: string): Promise<void> {
  cache.apiKeys.delete(id)
  const db = await getKV()
  await db.delete(['apikeys', id])
}

export async function getAllApiKeys(): Promise<ApiKey[]> {
  if (cache.initialized && cache.apiKeys.size > 0) {
    return Array.from(cache.apiKeys.values())
  }
  const db = await getKV()
  const keys: ApiKey[] = []
  for await (const entry of db.list<ApiKey>({ prefix: ['apikeys'] })) {
    if (entry.value) {
      keys.push(entry.value)
      cache.apiKeys.set(entry.value.id, entry.value)
    }
  }
  return keys
}

// ============ 代理配置 ============
export async function getProxyConfig(): Promise<ProxyConfig | null> {
  if (cache.proxyConfig) return cache.proxyConfig
  const db = await getKV()
  const result = await db.get<ProxyConfig>(['config', 'proxy'])
  if (result.value) cache.proxyConfig = result.value
  return result.value
}

export async function setProxyConfig(config: ProxyConfig): Promise<void> {
  cache.proxyConfig = config
  const db = await getKV()
  await db.set(['config', 'proxy'], config)
}

// ============ 全局设置 ============
export async function getSettings(): Promise<AppSettings | null> {
  if (cache.settings) return cache.settings
  const db = await getKV()
  const result = await db.get<AppSettings>(['config', 'settings'])
  if (result.value) cache.settings = result.value
  return result.value
}

export async function setSettings(settings: AppSettings): Promise<void> {
  cache.settings = settings
  const db = await getKV()
  await db.set(['config', 'settings'], settings)
}

// ============ 统计数据持久化 ============
export async function saveStats(stats: Record<string, unknown>): Promise<void> {
  const db = await getKV()
  await db.set(['stats', 'proxy'], stats)
}

export async function loadStats(): Promise<Record<string, unknown> | null> {
  const db = await getKV()
  const result = await db.get<Record<string, unknown>>(['stats', 'proxy'])
  return result.value
}

// ============ 请求日志持久化 ============
export async function saveRequestLogs(logs: RequestLog[]): Promise<void> {
  const db = await getKV()
  await db.set(['logs', 'requests'], logs)
}

export async function loadRequestLogs(): Promise<RequestLog[]> {
  const db = await getKV()
  const result = await db.get<RequestLog[]>(['logs', 'requests'])
  return result.value || []
}

// ============ 通用 KV 操作 ============
export async function kvGet<T>(key: string[]): Promise<T | null> {
  const db = await getKV()
  const result = await db.get<T>(key)
  return result.value
}

export async function kvSet<T>(key: string[], value: T): Promise<void> {
  const db = await getKV()
  await db.set(key, value)
}

export async function kvDelete(key: string[]): Promise<void> {
  const db = await getKV()
  await db.delete(key)
}

// ============ 初始化 ============
export async function initStorage(): Promise<void> {
  if (cache.initialized) return
  logger.info('Storage', 'Initializing storage and warming up cache...')

  // 并行预热缓存
  await Promise.all([
    getAllAccounts(),
    getAllApiKeys(),
    getProxyConfig(),
    getSettings()
  ])

  cache.initialized = true
  logger.info('Storage', `Cache warmed: ${cache.accounts.size} accounts, ${cache.apiKeys.size} API keys`)
}

// ============ 清理 ============
export function clearCache(): void {
  cache.accounts.clear()
  cache.apiKeys.clear()
  cache.settings = null
  cache.proxyConfig = null
  cache.initialized = false
}

export async function closeStorage(): Promise<void> {
  if (kv) {
    kv.close()
    kv = null
  }
  clearCache()
}
