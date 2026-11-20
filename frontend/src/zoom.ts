// Interface scale (the `ui_scale` setting): Ctrl+scroll, Ctrl+= / Ctrl+- / Ctrl+0 anywhere,
// or the slider in Settings. It sets the root font size; Tailwind sizes (text, spacing,
// widths) are all in rem, so the whole UI scales with it and layouts reflow normally.
import { api } from './api'

export const SCALE_MIN = 0.5 // matches config.UI_SCALE_MIN / MAX on the Python side
export const SCALE_MAX = 2.5
const STEP = 0.1
const SAVE_DELAY_MS = 500
const WHEEL_STEP_DELTA = 50 // pixels of scroll per step (one mouse-wheel notch is ~100)

let scale = 1
let saveTimer: number | undefined
let wheelAcc = 0
const listeners = new Set<() => void>()

const clamp = (s: number) => Math.round(Math.min(SCALE_MAX, Math.max(SCALE_MIN, s)) * 100) / 100

/** Apply a scale without saving it (startup, or a value just saved from Settings). */
export function applyScale(s: number) {
  scale = clamp(s)
  document.documentElement.style.fontSize = `${scale * 100}%`
  listeners.forEach((l) => l())
}

/** Apply and save (debounced, so a burst of scroll steps is one settings write). */
export function setScale(s: number) {
  applyScale(s)
  window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(() => api.updateSettings({ ui_scale: scale }).catch(() => {}), SAVE_DELAY_MS)
}

export const getScale = () => scale
export function subscribeScale(l: () => void) {
  listeners.add(l)
  return () => listeners.delete(l)
}

const step = (dir: number) => setScale(Math.round((scale + dir * STEP) * 10) / 10)

/** Global Ctrl+scroll and Ctrl+= / - / 0 handlers. Called once at startup. */
export function installZoomShortcuts() {
  window.addEventListener(
    'wheel',
    (e) => {
      if (!e.ctrlKey) return
      e.preventDefault()
      wheelAcc += e.deltaMode === 1 ? e.deltaY * 33 : e.deltaY // lines -> pixels
      if (Math.abs(wheelAcc) < WHEEL_STEP_DELTA) return
      step(wheelAcc < 0 ? 1 : -1)
      wheelAcc = 0
    },
    { passive: false },
  )
  window.addEventListener('keydown', (e) => {
    if (!e.ctrlKey || e.altKey || e.metaKey) return
    if (e.key === '=' || e.key === '+') step(1)
    else if (e.key === '-' || e.key === '_') step(-1)
    else if (e.key === '0') setScale(1)
    else return
    e.preventDefault()
  })
}
