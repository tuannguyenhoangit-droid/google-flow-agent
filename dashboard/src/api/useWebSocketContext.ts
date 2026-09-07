import { createContext, useContext } from 'react'
import type { WSEvent } from '../types'

export interface WorkerSnapshot {
  active: number
  slots: number
}

export interface WebSocketContextValue {
  isConnected: boolean
  lastEvent: WSEvent | null
  /** Rolling log of real event_bus events (request_update, worker_tick, urls_refreshed), newest first. */
  events: WSEvent[]
  /** From the initial /ws/dashboard snapshot message: real active/slots worker counts. */
  worker: WorkerSnapshot | null
}

export const WebSocketContext = createContext<WebSocketContextValue | null>(null)

export function useWebSocketContext() {
  const ctx = useContext(WebSocketContext)
  if (!ctx) throw new Error('useWebSocketContext must be used within a WebSocketProvider')
  return ctx
}
