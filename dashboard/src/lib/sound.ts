// Sonidos de alerta con Web Audio API (sin archivos). El navegador exige un clic previo
// ("Activar sonido") para poder reproducir audio.
let ctx: AudioContext | null = null

export function soundEnabled(): boolean {
  return ctx !== null && ctx.state === 'running'
}

export async function enableSound(): Promise<boolean> {
  ctx ??= new AudioContext()
  if (ctx.state !== 'running') await ctx.resume()
  playTone(880, 0.12, 0)
  return ctx.state === 'running'
}

function playTone(freq: number, dur: number, at: number, gain = 0.25) {
  if (!ctx) return
  const t0 = ctx.currentTime + at
  const osc = ctx.createOscillator()
  const g = ctx.createGain()
  osc.type = 'sine'
  osc.frequency.value = freq
  g.gain.setValueAtTime(0, t0)
  g.gain.linearRampToValueAtTime(gain, t0 + 0.02)
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur)
  osc.connect(g).connect(ctx.destination)
  osc.start(t0)
  osc.stop(t0 + dur + 0.05)
}

/** Aviso: dos tonos suaves. Crítica: tres tonos agudos y rápidos, más fuertes. */
export function playAlert(severity: 'info' | 'warning' | 'critical') {
  if (!soundEnabled()) return
  if (severity === 'critical') {
    ;[0, 0.18, 0.36].forEach((t) => playTone(1320, 0.14, t, 0.4))
  } else if (severity === 'warning') {
    playTone(660, 0.22, 0)
    playTone(880, 0.3, 0.25)
  }
}
