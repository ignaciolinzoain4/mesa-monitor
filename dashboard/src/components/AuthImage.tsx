import { useEffect, useState } from 'react'
import { apiBlobUrl } from '../lib/api'

type Loaded = { path: string; url: string | null; error: string | null }

/** Imagen que requiere token (no se puede usar <img src> directo). */
export function AuthImage({
  path,
  alt,
  className,
  onLoad,
}: {
  path: string | null
  alt: string
  className?: string
  onLoad?: (img: HTMLImageElement) => void
}) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)

  useEffect(() => {
    if (!path) return
    let cancelled = false
    let objectUrl: string | null = null
    apiBlobUrl(path)
      .then((u) => {
        if (cancelled) URL.revokeObjectURL(u)
        else {
          objectUrl = u
          setLoaded({ path, url: u, error: null })
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoaded({ path, url: null, error: err instanceof Error ? err.message : String(err) })
      })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [path])

  const current = loaded && loaded.path === path ? loaded : null
  if (current?.error) {
    return (
      <div className={`flex items-center justify-center bg-slate-900 p-4 text-center text-sm text-slate-400 ${className ?? ''}`}>
        {current.error}
      </div>
    )
  }
  if (!current?.url) return <div className={`animate-pulse bg-slate-800 ${className ?? ''}`} />
  return <img src={current.url} alt={alt} className={className} onLoad={(e) => onLoad?.(e.currentTarget)} />
}
