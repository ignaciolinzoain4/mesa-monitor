import { useRef, useState, type ReactNode } from 'react'
import type { Table } from '../lib/types'

export const PLAN_W = 100
export const PLAN_H = 60

type Props = {
  tables: Table[]
  fill?: (t: Table) => string
  stroke?: (t: Table) => string
  /** Contenido extra dentro de cada mesa (ej. cronómetro). Coordenadas relativas al centro. */
  content?: (t: Table) => ReactNode
  selectedId?: string | null
  onSelect?: (t: Table | null) => void
  /** Si está, las mesas se pueden arrastrar. */
  onMove?: (id: string, x: number, y: number) => void
  className?: string
}

const snap = (v: number) => Math.round(v * 2) / 2

/** Tamaño de letra que entra en la mesa según el largo de la etiqueta. */
// eslint-disable-next-line react-refresh/only-export-components
export function labelFontSize(t: Table, scale = 0.32): number {
  const byHeight = Math.min(t.plan_w, t.plan_h) * scale
  const byWidth = (t.plan_w * 0.9) / Math.max(1, t.label.length * 0.62)
  return Math.min(byHeight, byWidth)
}

export function FloorPlan({ tables, fill, stroke, content, selectedId, onSelect, onMove, className }: Props) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [drag, setDrag] = useState<{ id: string; dx: number; dy: number } | null>(null)

  const maxX = Math.max(PLAN_W, ...tables.map((t) => t.plan_x + t.plan_w + 2))
  const maxY = Math.max(PLAN_H, ...tables.map((t) => t.plan_y + t.plan_h + 2))

  function toPlan(e: { clientX: number; clientY: number }) {
    const svg = svgRef.current!
    const pt = svg.createSVGPoint()
    pt.x = e.clientX
    pt.y = e.clientY
    const p = pt.matrixTransform(svg.getScreenCTM()!.inverse())
    return { x: p.x, y: p.y }
  }

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${maxX} ${maxY}`}
      className={`w-full rounded-md border border-slate-800 bg-slate-900 ${className ?? ''}`}
      onPointerMove={(e) => {
        if (!drag || !onMove) return
        const p = toPlan(e)
        onMove(drag.id, Math.max(0, snap(p.x - drag.dx)), Math.max(0, snap(p.y - drag.dy)))
      }}
      onPointerUp={() => setDrag(null)}
      onPointerLeave={() => setDrag(null)}
      onPointerDown={(e) => e.target === svgRef.current && onSelect?.(null)}
    >
      <defs>
        <pattern id="grid" width="5" height="5" patternUnits="userSpaceOnUse">
          <path d="M 5 0 L 0 0 0 5" fill="none" stroke="#1e293b" strokeWidth="0.1" />
        </pattern>
      </defs>
      <rect width={maxX} height={maxY} fill="url(#grid)" pointerEvents="none" />
      {tables.map((t) => {
        const cx = t.plan_x + t.plan_w / 2
        const cy = t.plan_y + t.plan_h / 2
        const selected = t.id === selectedId
        return (
          <g
            key={t.id}
            transform={`rotate(${t.plan_rotation} ${cx} ${cy})`}
            className={onMove ? 'cursor-move' : onSelect ? 'cursor-pointer' : ''}
            onPointerDown={(e) => {
              e.stopPropagation()
              onSelect?.(t)
              if (onMove) {
                ;(e.target as Element).setPointerCapture?.(e.pointerId)
                const p = toPlan(e)
                setDrag({ id: t.id, dx: p.x - t.plan_x, dy: p.y - t.plan_y })
              }
            }}
          >
            <rect
              x={t.plan_x}
              y={t.plan_y}
              width={t.plan_w}
              height={t.plan_h}
              rx={0.6}
              fill={fill?.(t) ?? '#334155'}
              stroke={selected ? '#f8fafc' : (stroke?.(t) ?? '#64748b')}
              strokeWidth={selected ? 0.4 : 0.2}
              opacity={t.enabled ? 1 : 0.35}
            />
            <g transform={`rotate(${-t.plan_rotation} ${cx} ${cy})`} pointerEvents="none">
              {content ? (
                content(t)
              ) : (
                <text x={cx} y={cy} textAnchor="middle" dominantBaseline="central" fontSize={labelFontSize(t, 0.4)} fill="#f8fafc" fontWeight={600}>
                  {t.label}
                </text>
              )}
            </g>
          </g>
        )
      })}
    </svg>
  )
}
