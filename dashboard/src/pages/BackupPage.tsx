import { useState } from 'react'
import { Button, Card, ErrorBox, PageHeader } from '../components/ui'
import { apiFetch, apiJson } from '../lib/api'

type ImportResult = {
  dry_run: boolean
  tables_created: number
  tables_updated: number
  tables_disabled: number
  cameras: number
  table_views: number
  staff_zones: number
  ignore_masks: number
  thresholds: number
  warnings: string[]
}

export function BackupPage() {
  const [error, setError] = useState<unknown>(null)
  const [fileText, setFileText] = useState<string | null>(null)
  const [result, setResult] = useState<ImportResult | null>(null)

  async function download() {
    setError(null)
    try {
      const resp = await apiFetch('/config/export')
      const blob = await resp.blob()
      const name = resp.headers.get('content-disposition')?.match(/filename="(.+)"/)?.[1] ?? 'mesa-monitor-config.yaml'
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = name
      a.click()
      URL.revokeObjectURL(a.href)
    } catch (err) {
      setError(err)
    }
  }

  async function check(text: string, apply: boolean) {
    setError(null)
    try {
      const r = await apiJson<ImportResult>(`/config/import?dry_run=${apply ? 'false' : 'true'}`, {
        method: 'POST',
        body: text,
        headers: { 'Content-Type': 'application/yaml' },
      })
      setResult(r)
      if (apply) setFileText(null)
    } catch (err) {
      setError(err)
      setResult(null)
    }
  }

  return (
    <>
      <PageHeader title="Respaldo de configuración" subtitle="Mesas, zonas, umbrales, horarios y perfil de personal en un archivo YAML" />
      <ErrorBox error={error} />
      <div className="grid max-w-4xl gap-4 md:grid-cols-2">
        <Card title="Exportar">
          <p className="mb-3 text-sm text-slate-400">Descarga toda la configuración editable. No incluye contraseñas ni claves.</p>
          <Button onClick={download}>Descargar respaldo</Button>
        </Card>
        <Card title="Importar">
          <p className="mb-3 text-sm text-slate-400">
            Reemplaza la configuración actual. Las mesas con la misma etiqueta conservan su historial; las que no estén en el archivo se deshabilitan.
          </p>
          <input
            type="file"
            accept=".yaml,.yml"
            className="text-sm"
            onChange={async (e) => {
              const f = e.target.files?.[0]
              if (!f) return
              const text = await f.text()
              setFileText(text)
              await check(text, false)
            }}
          />
          {result && (
            <div className="mt-3 space-y-2 text-sm">
              <p className="font-medium">{result.dry_run ? 'Vista previa (todavía no se guardó nada):' : 'Importación aplicada:'}</p>
              <ul className="list-inside list-disc text-slate-300">
                <li>Mesas nuevas: {result.tables_created} · actualizadas: {result.tables_updated} · deshabilitadas: {result.tables_disabled}</li>
                <li>Vistas de mesa: {result.table_views} en {result.cameras} cámaras</li>
                <li>Zonas de personal: {result.staff_zones} · áreas ignoradas: {result.ignore_masks}</li>
                <li>Umbrales: {result.thresholds}</li>
              </ul>
              {result.warnings.map((w) => (
                <p key={w} className="text-amber-300">
                  ⚠ {w}
                </p>
              ))}
              {result.dry_run && fileText && <Button onClick={() => check(fileText, true)}>Confirmar importación</Button>}
            </div>
          )}
        </Card>
      </div>
    </>
  )
}
