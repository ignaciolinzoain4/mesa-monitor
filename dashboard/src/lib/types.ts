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

export type Point = [number, number]
export type Polygon = Point[]

export type Table = {
  id: string
  label: string
  sector: string
  capacity: number | null
  enabled: boolean
  plan_x: number
  plan_y: number
  plan_w: number
  plan_h: number
  plan_rotation: number
  group_id: string | null
  camera_ids: string[]
}

export type CleanMode = 'objects_and_staff' | 'staff_visit_only'
export type StaffZoneKind = 'bar' | 'kitchen_pass' | 'cashier' | 'other'

export type TableView = {
  id?: string
  table_id: string
  table_polygon: Polygon
  seats_polygon: Polygon
  priority: number
  clean_mode: CleanMode
}

export type StaffZone = { id?: string; kind: StaffZoneKind; name: string; polygon: Polygon }
export type IgnoreMask = { id?: string; name: string; polygon: Polygon }

export type CameraZones = {
  camera_id: string
  table_views: TableView[]
  staff_zones: StaffZone[]
  ignore_masks: IgnoreMask[]
}

export type TableState = 'LIBRE' | 'OCUPADA_SIN_ATENDER' | 'ATENDIDA' | 'DESOCUPADA_SUCIA' | 'SIN_VISION' | 'DESHABILITADA'
