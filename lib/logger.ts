// KiroGate 日志系统 - 环形缓冲区 + 分级日志
import type { RequestLog } from './types.ts'

export type LogLevel = 'debug' | 'info' | 'warn' | 'error'

const LOG_LEVELS: Record<LogLevel, number> = { debug: 0, info: 1, warn: 2, error: 3 }

let currentLevel: LogLevel = 'info'

export function setLogLevel(level: LogLevel): void {
  currentLevel = level
}

export function getLogLevel(): LogLevel {
  return currentLevel
}

function shouldLog(level: LogLevel): boolean {
  return LOG_LEVELS[level] >= LOG_LEVELS[currentLevel]
}

function formatTime(): string {
  return new Date().toISOString().replace('T', ' ').substring(0, 19)
}

export const logger = {
  debug(tag: string, msg: string, data?: unknown): void {
    if (!shouldLog('debug')) return
    const extra = data ? ' ' + (typeof data === 'string' ? data : JSON.stringify(data)).substring(0, 500) : ''
    console.debug(`[${formatTime()}] [DEBUG] [${tag}] ${msg}${extra}`)
  },
  info(tag: string, msg: string, data?: unknown): void {
    if (!shouldLog('info')) return
    const extra = data ? ' ' + (typeof data === 'string' ? data : JSON.stringify(data)).substring(0, 500) : ''
    console.log(`[${formatTime()}] [INFO] [${tag}] ${msg}${extra}`)
  },
  warn(tag: string, msg: string, data?: unknown): void {
    if (!shouldLog('warn')) return
    const extra = data ? ' ' + (typeof data === 'string' ? data : JSON.stringify(data)).substring(0, 500) : ''
    console.warn(`[${formatTime()}] [WARN] [${tag}] ${msg}${extra}`)
  },
  error(tag: string, msg: string, data?: unknown): void {
    if (!shouldLog('error')) return
    const extra = data ? ' ' + (typeof data === 'string' ? data : JSON.stringify(data)).substring(0, 500) : ''
    console.error(`[${formatTime()}] [ERROR] [${tag}] ${msg}${extra}`)
  }
}

// ============ 环形缓冲区请求日志 ============
const MAX_REQUEST_LOGS = 500

class RingBuffer<T> {
  private buffer: T[] = []
  private maxSize: number

  constructor(maxSize: number) {
    this.maxSize = maxSize
  }

  push(item: T): void {
    if (this.buffer.length >= this.maxSize) {
      this.buffer.shift()
    }
    this.buffer.push(item)
  }

  getAll(): T[] {
    return [...this.buffer]
  }

  getRecent(count: number): T[] {
    return this.buffer.slice(-count)
  }

  clear(): void {
    this.buffer = []
  }

  get size(): number {
    return this.buffer.length
  }
}

// 全局请求日志存储
const requestLogBuffer = new RingBuffer<RequestLog>(MAX_REQUEST_LOGS)

export function addRequestLog(log: RequestLog): void {
  requestLogBuffer.push(log)
}

export function getRequestLogs(count?: number): RequestLog[] {
  return count ? requestLogBuffer.getRecent(count) : requestLogBuffer.getAll()
}

export function clearRequestLogs(): void {
  requestLogBuffer.clear()
}

export function getRequestLogCount(): number {
  return requestLogBuffer.size
}
