import type { AlertType, Severity } from './types'

export const ALERT_LABEL: Record<AlertType, string> = {
  SIN_ATENCION: 'Sin atención',
  SIN_VISITA: 'Sin visita',
  MESA_SUCIA: 'Mesa sucia',
  ESTADIA_LARGA: 'Estadía larga',
  CAMARA_CAIDA: 'Cámara sin imagen',
}

export const SEVERITY_LABEL: Record<Severity, string> = { info: 'Info', warning: 'Aviso', critical: 'Crítica' }
export const SEVERITY_RANK: Record<Severity, number> = { info: 0, warning: 1, critical: 2 }
