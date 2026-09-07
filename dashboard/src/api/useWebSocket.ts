import { useState, useEffect, useRef, useCallback } from 'react'
import type { WSEvent } from '../types'

export function useWebSocket(onMessage?: (event: WSEvent) => void) {
  const [isConnected, setIsConnected] = useState(false)
  const [lastEvent, setLastEvent] = useState<WSEvent | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const retriesRef = useRef(0)
  const onMessageRef = useRef(onMessage)
  const connectRef = useRef<() => void>(() => {})

  useEffect(() => { onMessageRef.current = onMessage }, [onMessage])

  const connect = useCallback(() => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${proto}//${window.location.host}/ws/dashboard`)
    wsRef.current = ws

    ws.onopen = () => {
      setIsConnected(true)
      retriesRef.current = 0
    }

    ws.onmessage = (e) => {
      try {
        const event: WSEvent = JSON.parse(e.data)
        setLastEvent(event)
        onMessageRef.current?.(event)
      } catch {
        // malformed frame — ignore
      }
    }

    ws.onclose = () => {
      setIsConnected(false)
      wsRef.current = null
      const delay = Math.min(1000 * 2 ** retriesRef.current, 30000)
      retriesRef.current++
      setTimeout(() => connectRef.current(), delay)
    }

    ws.onerror = () => ws.close()
  }, [])

  useEffect(() => { connectRef.current = connect }, [connect])

  useEffect(() => {
    connect()
    return () => { wsRef.current?.close() }
  }, [connect])

  return { isConnected, lastEvent }
}
