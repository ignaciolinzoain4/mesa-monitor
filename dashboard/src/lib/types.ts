export type UserRole = 'admin' | 'viewer'

export type User = { username: string; role: UserRole }

export type CameraStatus = 'online' | 'degraded' | 'offline'

export type Camera = {
  id: string
  name: string
  enabled: boolean
  channel: number | null
  fps_detection: number
  expected_resolution: [number, number] | null
  status: CameraStatus
  status_detail: string | null
  resolution: [number, number] | null
  last_ok_at: string | null
  last_checked_at: string | null
}

export type Health = {
  status: 'ok' | 'degraded'
  version: string
  database: 'ok' | 'error'
  ts: string
}
