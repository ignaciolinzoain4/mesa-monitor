import { useEffect, useState } from 'react'
import { Button, Card, ErrorBox, Field, inputClass } from '../../components/ui'
import { apiJson } from '../../lib/api'

type Vision = {
  model: string | null
  device: string | null
  person_confidence: number
  tiling: { enabled: boolean; tile_w: number; tile_h: number; overlap: number; batch_size: number }
  standing_ratio: number
  moving_ratio: number
  seats_expand: number
  debug_overlay: boolean
}

export function VisionTab() {
  const [v, setV] = useState<Vision | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)
  useEffect(() => {
    apiJson<Vision>('/config/vision').then(setV).catch(setError)
  }, [])
  if (!v) return <ErrorBox error={error} />
  const upd = (p: Partial<Vision>) => {
    setSaved(false)
    setV({ ...v, ...p })
  }
  const num = (k: keyof Vision) => (e: React.ChangeEvent<HTMLInputElement>) => upd({ [k]: Number(e.target.value) } as Partial<Vision>)
  async function save() {
    setError(null)
    try {
      setV(await apiJson<Vision>('/config/vision', { method: 'PUT', body: JSON.stringify(v) }))
      setSaved(true)
    } catch (err) {
      setError(err)
    }
  }
  return (
    <Card title="Detección" actions={<Button onClick={save}>{saved ? 'Guardado ✓' : 'Guardar'}</Button>}>
      <ErrorBox error={error} />
      <div className="grid gap-4 md:grid-cols-3">
        <Field label="Modelo" hint="Vacío = el que recomendó el benchmark. Más grande = más preciso y más lento.">
          <select className={inputClass} value={v.model ?? ''} onChange={(e) => upd({ model: e.target.value || null })}>
            <option value="">Recomendado</option>
            <option value="nano">Nano</option>
            <option value="small">Small</option>
            <option value="medium">Medium</option>
            <option value="large">Large</option>
          </select>
        </Field>
        <Field label="Confianza mínima de persona">
          <input type="number" step={0.05} min={0.05} max={0.95} className={inputClass} value={v.person_confidence} onChange={num('person_confidence')} />
        </Field>
        <Field label="Ampliación del área de asientos" hint="Dónde cuenta que el mozo 'visitó' la mesa (1.25 = 25 % más grande).">
          <input type="number" step={0.05} min={1} max={2} className={inputClass} value={v.seats_expand} onChange={num('seats_expand')} />
        </Field>
        <Field label="Proporción alto/ancho para 'parado'" hint="Cámaras en altura: ~2.6. Medila con el replay.">
          <input type="number" step={0.1} className={inputClass} value={v.standing_ratio} onChange={num('standing_ratio')} />
        </Field>
        <Field label="Proporción mínima si se está moviendo">
          <input type="number" step={0.1} className={inputClass} value={v.moving_ratio} onChange={num('moving_ratio')} />
        </Field>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={v.debug_overlay} onChange={(e) => upd({ debug_overlay: e.target.checked })} />
          Overlay de debug (solo para calibrar; consume recursos)
        </label>
      </div>
      <h3 className="mb-2 mt-6 font-medium">Detección por mosaicos</h3>
      <p className="mb-3 text-sm text-slate-400">Divide la imagen en partes para ver a las personas lejanas. Imprescindible con cámaras en altura y gran angular; cuesta ~4 veces más.</p>
      <div className="grid gap-4 md:grid-cols-5">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={v.tiling.enabled} onChange={(e) => upd({ tiling: { ...v.tiling, enabled: e.target.checked } })} />
          Activada
        </label>
        {(['tile_w', 'tile_h', 'overlap', 'batch_size'] as const).map((k) => (
          <Field key={k} label={{ tile_w: 'Ancho (px)', tile_h: 'Alto (px)', overlap: 'Solapamiento (px)', batch_size: 'Lote' }[k]}>
            <input type="number" className={inputClass} value={v.tiling[k]} onChange={(e) => upd({ tiling: { ...v.tiling, [k]: Number(e.target.value) } })} />
          </Field>
        ))}
      </div>
    </Card>
  )
}
