import type { TableState } from './types'

const SECTOR_COLORS = ['#0369a1', '#7c3aed', '#047857', '#b45309', '#be185d', '#4d7c0f']

export function sectorColor(sector: string, sectors: string[]): string {
  const i = sectors.indexOf(sector)
  return SECTOR_COLORS[(i < 0 ? 0 : i) % SECTOR_COLORS.length]
}

/** Colores de estado de mesa: iguales en el dashboard y en /alertas. */
export const STATE_STYLE: Record<TableState, { fill: string; label: string }> = {
  LIBRE: { fill: '#166534', label: 'Libre' },
  OCUPADA_SIN_ATENDER: { fill: '#b45309', label: 'Sin atender' },
  ATENDIDA: { fill: '#1d4ed8', label: 'Atendida' },
  DESOCUPADA_SUCIA: { fill: '#7e22ce', label: 'Sucia' },
  SIN_VISION: { fill: '#475569', label: 'Sin visión' },
  DESHABILITADA: { fill: '#1e293b', label: 'Deshabilitada' },
}
