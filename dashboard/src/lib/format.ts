export const TIMEZONE = 'America/Argentina/Cordoba'

const timeFmt = new Intl.DateTimeFormat('es-AR', {
  timeZone: TIMEZONE,
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
})
const dateTimeFmt = new Intl.DateTimeFormat('es-AR', {
  timeZone: TIMEZONE,
  day: '2-digit',
  month: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

export function formatTime(iso: string | null | undefined): string {
  return iso ? timeFmt.format(new Date(iso)) : '—'
}

export function formatDateTime(iso: string | null | undefined): string {
  return iso ? dateTimeFmt.format(new Date(iso)) : '—'
}

/** 184 → "3:04"; 3700 → "1:01:40". */
export function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const pad = (n: number) => n.toString().padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`
}

export function formatMinutes(seconds: number | null | undefined): string {
  if (seconds == null) return '—'
  return `${(seconds / 60).toFixed(1).replace('.', ',')} min`
}
