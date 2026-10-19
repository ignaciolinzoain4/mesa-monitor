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

export type Severity = 'info' | 'warning' | 'critical'
export type AlertType = 'SIN_ATENCION' | 'SIN_VISITA' | 'MESA_SUCIA' | 'ESTADIA_LARGA' | 'CAMARA_CAIDA'
export type AlertStatus = 'open' | 'acknowledged' | 'resolved'

export type TableRef = { id: string; label: string; sector: string }

export type TableStateData = {
  table: TableRef
  state: TableState
  state_since: string | null
  elapsed_seconds: number | null
  previous_state?: TableState | null
  reason?: string | null
  session_id: string | null
  seated_at: string | null
  first_attention_at: string | null
  last_visit_at: string | null
  vacated_at: string | null
  people: number
}

export type AlertData = {
  alert_id: string
  alert_type: AlertType
  severity: Severity
  status: AlertStatus
  table: TableRef | null
  camera_id: string | null
  state: TableState | null
  state_since: string | null
  elapsed_seconds: number | null
  threshold_seconds: number
  opened_at: string
  acknowledged_at: string | null
  snoozed_until: string | null
  resolved_at: string | null
}

export type CameraStatusData = { camera_id: string; name: string; status: CameraStatus; detail: string | null }

export type Envelope<T extends string, D> = { schema_version: 1; id: string; type: T; ts: string; data: D }

export type LiveEvent =
  | Envelope<'snapshot', { tables: TableStateData[]; alerts: AlertData[]; cameras: CameraStatusData[] }>
  | Envelope<'table.state_changed', TableStateData>
  | Envelope<'table.staff_visit', { table: TableRef; session_id: string; visit_id: string; started_at: string }>
  | Envelope<'alert.opened' | 'alert.updated' | 'alert.resolved', AlertData>
  | Envelope<'camera.status', CameraStatusData>
  | Envelope<'heartbeat', Record<string, string>>
