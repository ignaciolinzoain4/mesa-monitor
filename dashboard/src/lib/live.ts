// Estado en vivo por WebSocket: snapshot inicial + eventos, con reconexión automática.
import { useEffect, useRef, useState } from 'react'
import type { AlertData, CameraStatusData, LiveEvent, TableStateData } from './types'

export type LiveTable = TableStateData & { receivedAt: number }

export type LiveState = {
  connected: boolean
  lastMessageAt: number | null
  tables: Record<string, LiveTable>
  alerts: Record<string, AlertData>
  cameras: Record<string, CameraStatusData>
  lastVisit: Record<string, number> // table_id → ms (para resaltar una visita reciente)
}

const empty: LiveState = { connected: false, lastMessageAt: null, tables: {}, alerts: {}, cameras: {}, lastVisit: {} }

export function reduce(state: LiveState, ev: LiveEvent, now: number): LiveState {
  const s = { ...state, lastMessageAt: now }
  switch (ev.type) {
    case 'snapshot':
      return {
        ...s,
        tables: Object.fromEntries(ev.data.tables.map((t) => [t.table.id, { ...t, receivedAt: now }])),
        alerts: Object.fromEntries(ev.data.alerts.map((a) => [a.alert_id, a])),
        cameras: Object.fromEntries(ev.data.cameras.map((c) => [c.camera_id, c])),
      }
    case 'table.state_changed':
      return { ...s, tables: { ...s.tables, [ev.data.table.id]: { ...ev.data, receivedAt: now } } }
    case 'table.staff_visit': {
      const t = s.tables[ev.data.table.id]
      return {
        ...s,
        lastVisit: { ...s.lastVisit, [ev.data.table.id]: now },
        tables: t ? { ...s.tables, [t.table.id]: { ...t, last_visit_at: ev.data.started_at } } : s.tables,
      }
    }
    case 'alert.opened':
    case 'alert.updated':
      return { ...s, alerts: { ...s.alerts, [ev.data.alert_id]: ev.data } }
    case 'alert.resolved': {
      const alerts = { ...s.alerts }
      delete alerts[ev.data.alert_id]
      return { ...s, alerts }
    }
    case 'camera.status':
      return { ...s, cameras: { ...s.cameras, [ev.data.camera_id]: ev.data } }
    default:
      return s
  }
}

/** Segundos transcurridos en el estado actual, actualizados en el cliente. */
export function elapsedNow(t: LiveTable, now: number): number | null {
  if (t.elapsed_seconds == null) return null
  return t.elapsed_seconds + (now - t.receivedAt) / 1000
}

export function useLive(token: string | null): LiveState {
  const [state, setState] = useState<LiveState>(empty)
  const retry = useRef(0)

  useEffect(() => {
    if (!token) return
    let ws: WebSocket | null = null
    let timer: ReturnType<typeof setTimeout> | null = null
    let watchdog: ReturnType<typeof setInterval> | null = null
    let closed = false
    let last = Date.now()

    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/api/v1/ws?token=${encodeURIComponent(token)}`)
      ws.onopen = () => {
        retry.current = 0
        setState((s) => ({ ...s, connected: true }))
      }
      ws.onmessage = (msg) => {
        last = Date.now()
        try {
          const ev = JSON.parse(msg.data as string) as LiveEvent
          setState((s) => ({ ...reduce(s, ev, Date.now()), connected: true }))
        } catch {
          // mensaje inválido: se ignora
        }
      }
      ws.onclose = () => {
        setState((s) => ({ ...s, connected: false }))
        if (closed) return
        const delay = Math.min(30_000, 1000 * 2 ** retry.current) // 1 s, 2 s, 4 s … 30 s
        retry.current += 1
        timer = setTimeout(connect, delay)
      }
      ws.onerror = () => ws?.close()
    }
    connect()
    // Si no llega nada (ni heartbeat) en 45 s, la conexión está muerta aunque no se haya cerrado.
    watchdog = setInterval(() => {
      if (Date.now() - last > 45_000) {
        last = Date.now()
        ws?.close()
      }
    }, 5_000)
    return () => {
      closed = true
      if (timer) clearTimeout(timer)
      if (watchdog) clearInterval(watchdog)
      ws?.close()
    }
  }, [token])

  return state
}

export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])
  return now
}
