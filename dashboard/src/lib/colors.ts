import type { TableState } from './types'

const SECTOR_COLORS = ['#0369a1', '#7c3aed', '#047857', '#b45309', '#be185d', '#4d7c0f']

export function sectorColor(sector: string, sectors: string[]): string {
  const i = sectors.indexOf(sector)
  return SECTOR_COLORS[(i < 0 ? 0 : i) % SECTOR_COLORS.length]
}

/** Colores de estado de mesa: iguales en el dashboard, en /alertas y en el video del replay
 * (`vision/pipeline.py` STATE_COLORS). Siempre van con su nombre: el color nunca es la única pista. */
export const STATE_STYLE: Record<TableState, { fill: string; label: string }> = {
  LIBRE: { fill: '#16a34a', label: 'Libre' },
  OCUPADA_SIN_ATENDER: { fill: '#dc2626', label: 'Sin atender' },
  ATENDIDA: { fill: '#1d4ed8', label: 'Atendidos' },
  CONSUMIENDO: { fill: '#0891b2', label: 'Consumiendo' },
  DESOCUPADA_SUCIA: { fill: '#a855f7', label: 'Sucia' },
  SIN_VISION: { fill: '#475569', label: 'Sin visión' },
  DESHABILITADA: { fill: '#1e293b', label: 'Deshabilitada' },
}
