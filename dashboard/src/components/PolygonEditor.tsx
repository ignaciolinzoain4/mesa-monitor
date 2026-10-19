import { useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import type { Point, Polygon } from '../lib/types'

export type Shape = {
  key: string
  points: Polygon
  stroke: string
  fill: string
  dashed?: boolean
  label?: string
}

type Props = {
  shapes: Shape[]
  selectedKey: string | null
  drawing: { points: Polygon; stroke: string } | null
  onSelect: (key: string | null) => void
  onAddPoint: (p: Point) => void
  onFinishDrawing: () => void
  onMovePoint: (key: string, index: number, p: Point) => void
  onDeletePoint: (key: string, index: number) => void
}

const clamp = (v: number) => Math.min(1, Math.max(0, v))

/** Capa SVG sobre una imagen. Coordenadas normalizadas 0–1 (viewBox 0 0 1 1). */
export function PolygonEditor({ shapes, selectedKey, drawing, onSelect, onAddPoint, onFinishDrawing, onMovePoint, onDeletePoint }: Props) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [drag, setDrag] = useState<{ key: string; index: number } | null>(null)

  function toPoint(e: { clientX: number; clientY: number }): Point {
    const r = svgRef.current!.getBoundingClientRect()
    return [clamp((e.clientX - r.left) / r.width), clamp((e.clientY - r.top) / r.height)]
  }

  function onBackgroundClick(e: ReactPointerEvent<SVGSVGElement>) {
    if (drag) return
    if (drawing) {
      const p = toPoint(e)
      const first = drawing.points[0]
      const r = svgRef.current!.getBoundingClientRect()
      if (first && drawing.points.length >= 3 && Math.hypot((p[0] - first[0]) * r.width, (p[1] - first[1]) * r.height) < 12) {
        onFinishDrawing()
      } else {
        onAddPoint(p)
      }
    } else if (e.target === svgRef.current) {
      onSelect(null)
    }
  }

  const selected = shapes.find((s) => s.key === selectedKey)
  const pts = (poly: Polygon) => poly.map((p) => `${p[0]},${p[1]}`).join(' ')

  return (
    <svg
      ref={svgRef}
      viewBox="0 0 1 1"
      preserveAspectRatio="none"
      className={`absolute inset-0 h-full w-full ${drawing ? 'cursor-crosshair' : ''}`}
      onPointerDown={onBackgroundClick}
      onPointerMove={(e) => drag && onMovePoint(drag.key, drag.index, toPoint(e))}
      onPointerUp={() => setDrag(null)}
      onPointerLeave={() => setDrag(null)}
      onDoubleClick={() => drawing && drawing.points.length >= 3 && onFinishDrawing()}
    >
      {shapes.map((s) => (
        <g key={s.key}>
          <polygon
            points={pts(s.points)}
            fill={s.fill}
            stroke={s.stroke}
            strokeWidth={s.key === selectedKey ? 3 : 1.5}
            strokeDasharray={s.dashed ? '6 4' : undefined}
            vectorEffect="non-scaling-stroke"
            className={drawing ? 'pointer-events-none' : 'cursor-pointer'}
            onPointerDown={(e) => {
              if (drawing) return
              e.stopPropagation()
              onSelect(s.key)
            }}
          />
        </g>
      ))}
      {selected &&
        !drawing &&
        selected.points.map((p, i) => (
          <circle
            key={i}
            cx={p[0]}
            cy={p[1]}
            r={0.008}
            fill="white"
            stroke={selected.stroke}
            strokeWidth={2}
            vectorEffect="non-scaling-stroke"
            className="cursor-move"
            onPointerDown={(e) => {
              e.stopPropagation()
              ;(e.target as Element).setPointerCapture?.(e.pointerId)
              setDrag({ key: selected.key, index: i })
            }}
            onContextMenu={(e) => {
              e.preventDefault()
              if (selected.points.length > 3) onDeletePoint(selected.key, i)
            }}
          />
        ))}
      {drawing && drawing.points.length > 0 && (
        <g className="pointer-events-none">
          <polyline points={pts(drawing.points)} fill="none" stroke={drawing.stroke} strokeWidth={2} vectorEffect="non-scaling-stroke" />
          {drawing.points.map((p, i) => (
            <circle key={i} cx={p[0]} cy={p[1]} r={0.006} fill={i === 0 ? drawing.stroke : 'white'} vectorEffect="non-scaling-stroke" />
          ))}
        </g>
      )}
    </svg>
  )
}
