// Display helpers. Steps are stored as minutes and shown Anki-style ("1m 10m").

export function formatSteps(steps: number[]): string {
  return steps
    .map((s) => (s % 1440 === 0 ? `${s / 1440}d` : s % 60 === 0 ? `${s / 60}h` : `${s}m`))
    .join(' ')
}

export function formatBytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`
  if (n >= 1e6) return `${(n / 1e6).toFixed(0)} MB`
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)} KB`
  return `${n} B`
}

/** "in 10 min", "in 3 days", "tomorrow". `due` is a UTC ISO time or a local YYYY-MM-DD date. */
export function formatDue(due: string | null): string {
  if (!due) return '—'
  if (/^\d{4}-\d{2}-\d{2}$/.test(due)) {
    const [y, m, d] = due.split('-').map(Number)
    const target = new Date(y, m - 1, d)
    const today = new Date()
    today.setHours(0, 0, 0, 0)
    const days = Math.round((target.getTime() - today.getTime()) / 86_400_000)
    if (days <= 0) return 'today'
    if (days === 1) return 'tomorrow'
    if (days < 30) return `in ${days} days`
    if (days < 365) return `in ${Math.round(days / 30)} months`
    return `in ${(days / 365).toFixed(1)} years`
  }
  return `in ${formatDuration(new Date(due).getTime() - Date.now())}`
}

export function formatDuration(ms: number): string {
  const min = Math.max(0, Math.round(ms / 60_000))
  if (min < 1) return 'less than a minute'
  if (min < 60) return `${min} min`
  const h = Math.round(min / 60)
  if (h < 24) return `${h} h`
  return `${Math.round(h / 24)} days`
}

export const SCORE_LABEL = ['Off-track', 'Wrong', 'Close', 'Correct'] as const

export const isMac = typeof navigator !== 'undefined' && /Mac/i.test(navigator.platform)
